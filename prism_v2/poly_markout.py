#!/usr/bin/env python3
"""L'EQUATION MAKER SUR POLYMARKET — rebate contre adverse selection.

    demi-spread encaisse + markout + rebate  >  0 ?

Sur OKX la reponse etait NON, et negative meme AVANT frais : l'adverse
selection depassait le demi-spread d'un facteur 2. La conclusion portait sur
la tarification — il faudrait etre PAYE pour coter. Polymarket paie.

CE QUI CHANGE DANS LA MESURE. Sur OKX il fallait SIMULER les fills : je
supposais la premiere place dans la file, hypothese genereuse et invérifiable.
Ici chaque trade execute IMPLIQUE un maker de l'autre cote : les fills sont
reels, il n'y a rien a deviner. C'est un jeu de donnees strictement superieur
pour cette question.

REFERENCE DE PRIX. Le carnet collecte toutes les 10 s, pas la serie de prix
publique a 600 s — un raccourci tente puis ecarte, qui donnait un ecart median
de 5,5 fois le spread publie et mesurait de la derive au lieu du spread.

Aucun ordre reel. Aucune cle.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.polymarket import (
    BinaryMarket, MakerFill, Side, Trade, load_trades,
)

DEFAULT_BOOKS = Path(__file__).parent / "data" / "polymarket" / "books.jsonl.gz"
DEFAULT_MARKETS = Path(__file__).parent / "data" / "polymarket" / "markets.json"

#: Horizons de markout, en secondes. Le pas de collecte est de 10 s : tout
#: horizon inferieur comparerait un instantane a lui-meme.
HORIZONS_S = (60, 300, 1_800)

#: Anciennete maximale d'un carnet servant de reference. Au-dela, il ne decrit
#: plus le marche a l'instant du fill — on refuse plutot que d'extrapoler.
MAX_BOOK_AGE_S = 30


@dataclass
class BookSeries:
    """Carnets collectes pour UN token, interrogeables sans lire le futur."""

    token_id: str
    ts: List[int]
    mids: List[float]
    spreads: List[float]

    @classmethod
    def from_records(cls, token_id: str,
                     recs: Sequence[Dict[str, Any]]) -> "BookSeries":
        rows = sorted((r for r in recs
                       if r.get("i") == token_id and r.get("ok")
                       and r.get("b") and r.get("a")),
                      key=lambda r: r["recv"])
        ts, mids, spreads = [], [], []
        for r in rows:
            bid, ask = r["b"][0][0], r["a"][0][0]
            if not (0.0 < bid < ask < 1.0):
                continue            # carnet croise ou hors domaine : ecarte
            ts.append(r["recv"] // 1000)
            mids.append((bid + ask) / 2.0)
            spreads.append(ask - bid)
        return cls(token_id=token_id, ts=ts, mids=mids, spreads=spreads)

    def __len__(self) -> int:
        return len(self.ts)

    def at(self, t: int, max_age_s: int = MAX_BOOK_AGE_S
           ) -> Optional[Tuple[float, float]]:
        """(mid, spread) au dernier carnet <= t, s'il n'est pas trop ancien."""
        if not self.ts:
            return None
        i = bisect_right(self.ts, t) - 1
        if i < 0 or t - self.ts[i] > max_age_s:
            return None
        return self.mids[i], self.spreads[i]


def load_books(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Relit l'observatoire Polymarket, en tolerant un flux tronque."""
    import zlib
    meta: Dict[str, Any] = {}
    out: List[Dict[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            while True:
                try:
                    line = fh.readline()
                except (EOFError, OSError, zlib.error):
                    meta["truncated"] = True
                    break
                if not line:
                    break
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_meta"):
                    meta.update(rec)
                    continue
                out.append(rec)
    except (EOFError, OSError, zlib.error) as exc:
        meta["truncated"] = True
        meta["truncation_reason"] = str(exc)[:120]
    meta.setdefault("truncated", False)
    return meta, out


#: Part de markouts EXACTEMENT nuls au-dela de laquelle la mesure est
#: degeneree : le mid n'a pas bouge, donc le markout ne mesure RIEN.
DEGENERATE_ZERO_SHARE = 0.60


def markout_is_degenerate(markouts: Sequence[float]) -> Tuple[bool, float]:
    """Le markout mesure-t-il quelque chose, ou un carnet gele ?

    DEFAUT REEL. Sur les marches de prediction collectes, 97,3 % des markouts
    a 60 s valaient EXACTEMENT zero : le mid ne bouge pas a cette resolution.
    La mesure rendait alors « demi-spread encaisse, aucune adverse selection »,
    c'est-a-dire un resultat positif qui ne mesurait aucun risque.

    Le risque reel d'un contrat binaire n'est pas la derive du mid a 60 s :
    c'est le reglement terminal a 0 ou 1 dollar. Un markout court y est
    structurellement AVEUGLE. Un outil qui rend un chiffre dans ce cas
    invite a le publier.
    """
    if not markouts:
        return True, 1.0
    share = sum(1 for x in markouts if x == 0.0) / len(markouts)
    return share >= DEGENERATE_ZERO_SHARE, share


def _stats(xs: Sequence[float]) -> Dict[str, Any]:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "stderr": None, "t_stat": None}
    mean = sum(xs) / n
    if n < 2:
        return {"n": n, "mean": mean, "stderr": None, "t_stat": None}
    sd = statistics.stdev(xs)
    se = sd / math.sqrt(n)
    return {"n": n, "mean": mean, "std": sd, "stderr": se,
            "t_stat": mean / se if se > 0 else None,
            "share_positive": sum(1 for x in xs if x > 0) / n}


def measure(market: BinaryMarket, trades: Sequence[Trade], series: BookSeries,
            horizons: Sequence[int] = HORIZONS_S) -> List[MakerFill]:
    """Markout de chaque fill maker REEL, contre le carnet collecte.

    CAUSALITE : le demi-spread se mesure au carnet de l'instant du fill ; le
    markout est la derive POSTERIEURE du meme mid. Aucune information future
    n'entre dans le premier terme.
    """
    out: List[MakerFill] = []
    if not len(series) or not trades:
        return out
    last = series.ts[-1]
    max_h = max(horizons)
    for t in trades:
        if t.ts + max_h > last:
            continue                    # issue non observable : aucun chiffre
        ref = series.at(t.ts)
        if ref is None:
            continue                    # carnet trop ancien : on refuse
        mid, _spread = ref
        sign = 1.0 if t.maker_side is Side.BUY else -1.0
        f = MakerFill(ts=t.ts, condition_id=market.condition_id,
                      maker_side=t.maker_side, price=t.price, size=t.size,
                      mid_at_fill=mid, half_spread=sign * (mid - t.price),
                      rebate=market.maker_rebate_per_share(t.price))
        for h in horizons:
            after = series.at(t.ts + h)
            if after is not None:
                f.markout[h] = sign * (after[0] - mid)
        if f.markout:
            out.append(f)
    return out


def run(books_path: Path = DEFAULT_BOOKS, markets_path: Path = DEFAULT_MARKETS,
        json_out: Optional[Path] = None) -> Dict[str, Any]:
    from prism_v2.core_types import utc_now_iso

    report: Dict[str, Any] = {"started_at": utc_now_iso(),
                              "mode": "RECHERCHE — aucun ordre reel"}
    print("=" * 84); print("1. DONNEES"); print("=" * 84)
    meta, recs = load_books(Path(books_path))
    raw = json.loads(Path(markets_path).read_text(encoding="utf-8"))
    markets = {m["token_ids"][0]: BinaryMarket(
        condition_id=m["condition_id"], question=m["question"],
        token_ids=tuple(m["token_ids"]), fees_enabled=True,
        fee_rate=m["fee_rate"], rebate_rate=m["rebate_rate"],
        taker_only=m["taker_only"], fee_exponent=m["fee_exponent"],
        rewards_daily_rate=m.get("rewards_daily_rate"),
        rewards_min_size=m.get("rewards_min_size"),
        rewards_max_spread=m.get("rewards_max_spread"),
        volume_24h=m.get("volume_24h"), liquidity=m.get("liquidity"),
        spread=m.get("spread")) for m in raw}
    ok = [r for r in recs if r.get("ok")]
    ts = [r["recv"] // 1000 for r in ok]
    print(f"instantanes de carnet : {len(ok):,} valides sur {len(recs):,}")
    print(f"marches suivis        : {len(markets)}")
    if ts:
        print(f"fenetre               : {(max(ts)-min(ts))/3600:.2f} h")
    print(f"pas de collecte       : {meta.get('snapshot_s')} s "
          f"(reference a moins de {MAX_BOOK_AGE_S} s du fill, jamais plus)")
    report["data"] = {"n_books": len(ok), "n_markets": len(markets),
                      "window_hours": (max(ts) - min(ts)) / 3600 if ts else 0,
                      "truncated": meta.get("truncated")}

    print(); print("=" * 84)
    print("2. FILLS MAKER REELS — chaque trade implique un maker de l'autre cote")
    print("=" * 84)
    all_fills: List[MakerFill] = []
    per_market: Dict[str, Any] = {}
    lo_ts = min(ts) if ts else 0
    for token_id, m in markets.items():
        series = BookSeries.from_records(token_id, ok)
        if len(series) < 10:
            continue
        trades = [t for t in load_trades(m.condition_id, pages=6)
                  if t.ts >= lo_ts]
        fills = measure(m, trades, series)
        all_fills.extend(fills)
        if fills:
            per_market[m.question[:48]] = {
                "n_fills": len(fills), "n_trades": len(trades),
                "fee_rate": m.fee_rate, "rebate_rate": m.rebate_rate,
                "median_price": statistics.median(f.price for f in fills)}
    print(f"fills maker mesurables : {len(all_fills):,} "
          f"sur {len(per_market)} marches\n")
    if not all_fills:
        print("aucun fill mesurable : collecte trop courte ou trades hors fenetre")
        report["finished_at"] = utc_now_iso()
        return report

    # GARDE — avant tout chiffre, la mesure mesure-t-elle quelque chose ?
    mo_all = [f.markout[HORIZONS_S[0]] for f in all_fills
              if HORIZONS_S[0] in f.markout]
    degenerate, zero_share = markout_is_degenerate(mo_all)
    report["markout_degenerate"] = {"zero_share": zero_share,
                                    "degenerate": degenerate,
                                    "threshold": DEGENERATE_ZERO_SHARE}
    if degenerate:
        print(f"MESURE DEGENEREE : {zero_share:.1%} des markouts a "
              f"{HORIZONS_S[0]} s valent EXACTEMENT zero.")
        print("  Le mid ne bouge pas a cette resolution. Le markout ne mesure")
        print("  donc AUCUN risque, et le « net » qui suit n'est qu'un")
        print("  demi-spread encaisse sur un carnet gele.")
        print()
        print("  Le risque reel d'un contrat binaire est le REGLEMENT terminal")
        print("  a 0 ou 1 dollar, auquel un markout de 60 s est structurellement")
        print("  aveugle. Les chiffres ci-dessous sont affiches pour diagnostic,")
        print("  PAS comme un resultat economique.")
        print()

    print(f"{'horizon':>9}{'N':>7}{'demi-spread':>14}{'markout':>11}"
          f"{'rebate':>10}{'NET':>11}{'t':>8}{'part>0':>9}")
    print("-" * 79)
    for h in HORIZONS_S:
        sub = [f for f in all_fills if h in f.markout]
        nets = [f.net_per_share(h) for f in sub if f.net_per_share(h) is not None]
        if not nets:
            continue
        s = _stats(nets)
        print(f"{h:>7}s{len(nets):>7,}"
              f"{statistics.mean(f.half_spread for f in sub):>14.5f}"
              f"{statistics.mean(f.markout[h] for f in sub):>11.5f}"
              f"{statistics.mean(f.rebate for f in sub):>10.5f}"
              f"{s['mean']:>11.5f}{(s['t_stat'] or 0):>8.2f}"
              f"{s['share_positive']:>9.1%}")
    report["by_horizon"] = {
        str(h): {"half_spread": statistics.mean(
                     f.half_spread for f in all_fills if h in f.markout),
                 "markout": _stats([f.markout[h] for f in all_fills
                                    if h in f.markout]),
                 "rebate": statistics.mean(f.rebate for f in all_fills
                                           if h in f.markout),
                 "net": _stats([f.net_per_share(h) for f in all_fills
                                if f.net_per_share(h) is not None]),
                 "net_without_rebate": _stats(
                     [f.net_without_rebate(h) for f in all_fills
                      if f.net_without_rebate(h) is not None])}
        for h in HORIZONS_S}
    report["per_market"] = per_market

    print(); print("=" * 84)
    print("3. LE REBATE CHANGE-T-IL LE SIGNE ?"); print("=" * 84)
    h = HORIZONS_S[0]
    with_r = [f.net_per_share(h) for f in all_fills
              if f.net_per_share(h) is not None]
    without_r = [f.net_without_rebate(h) for f in all_fills
                 if f.net_without_rebate(h) is not None]
    a, b = _stats(without_r), _stats(with_r)
    print(f"a {h} s, en dollars par part :")
    print(f"  SANS rebate (equation OKX) : {a['mean']:+.5f}  (t={a['t_stat']:+.2f})")
    print(f"  AVEC rebate (Polymarket)   : {b['mean']:+.5f}  (t={b['t_stat']:+.2f})")
    flipped = (a["mean"] is not None and b["mean"] is not None
               and a["mean"] <= 0 < b["mean"])
    print()
    if degenerate:
        print("  SANS OBJET : la mesure est degeneree (voir ci-dessus). Un net")
        print("  positif obtenu sur un mid gele ne dit rien du rebate, ni de")
        print("  l'adverse selection, ni de la rentabilite.")
        report["sign_flip"] = {"horizon_s": h, "without_rebate": a,
                               "with_rebate": b,
                               "rebate_flips_the_sign": None,
                               "void_reason": "markout degenere"}
    elif flipped:
        print("  LE REBATE CHANGE LE SIGNE. C'est la premiere fois qu'une")
        print("  equation maker devient positive dans ce projet.")
        print("  A ATTAQUER avant toute conclusion : selection des marches,")
        print("  taille des fills, part de liquidite reellement obtenue.")
    elif b["mean"] is not None and b["mean"] > 0:
        print("  L'equation est positive, mais elle l'etait DEJA sans rebate :")
        print("  le rebate n'est pas la cause.")
    else:
        print("  Le rebate ne suffit PAS a changer le signe.")
        print("  L'adverse selection depasse le demi-spread ET le rebate reunis.")
    if not degenerate:
        report["sign_flip"] = {"horizon_s": h, "without_rebate": a,
                               "with_rebate": b,
                               "rebate_flips_the_sign": flipped}

    print(); print("=" * 84)
    print("4. DEUX EFFETS QUI NE SE RECOUVRENT PAS"); print("=" * 84)
    print("Le rebate suit p(1-p) : maximal a p = 0,5, nul aux extremes.")
    print("Le biais favori/longshot documente est aux EXTREMES, et asymetrique :")
    print("  achats sous 0,10 : -19,3 c par dollar   (Polymarket, 588 M trades)")
    print("  achats au-dela de 0,90 : +0,83 c par dollar")
    print("Confondre les deux extremes dans une seule tranche masquerait cette")
    print("asymetrie : ils sont donc SEPARES.\n")
    buckets: Dict[str, List[Tuple[float, float, float]]] = {}
    for f in all_fills:
        v = f.net_per_share(h)
        if v is None:
            continue
        p = f.price
        if p < 0.15:
            k = "longshot p<0.15 (zone perdante documentee)"
        elif p > 0.85:
            k = "favori p>0.85 (zone gagnante documentee)"
        elif 0.35 <= p <= 0.65:
            k = "median 0.35-0.65 (rebate maximal)"
        else:
            k = "intermediaire"
        buckets.setdefault(k, []).append((v, f.rebate, f.markout[h]))
    print(f"{'tranche de prix':<44}{'N':>7}{'rebate':>10}{'markout':>11}"
          f"{'NET':>11}{'t':>8}")
    print("-" * 91)
    for k in ("longshot p<0.15 (zone perdante documentee)", "intermediaire",
              "median 0.35-0.65 (rebate maximal)",
              "favori p>0.85 (zone gagnante documentee)"):
        rows = buckets.get(k)
        if not rows:
            continue
        s = _stats([r[0] for r in rows])
        print(f"{k:<44}{s['n']:>7,}{statistics.mean(r[1] for r in rows):>10.5f}"
              f"{statistics.mean(r[2] for r in rows):>11.5f}"
              f"{s['mean']:>11.5f}{(s['t_stat'] or 0):>8.2f}")
    report["by_price_bucket"] = {
        k: {"n": len(v), "mean_rebate": statistics.mean(r[1] for r in v),
            "mean_markout": statistics.mean(r[2] for r in v),
            **_stats([r[0] for r in v])} for k, v in buckets.items()}

    # ── 5. LES DEUX TERMES DE REVENU NE SE COMPORTENT PAS PAREIL ──────────
    print(); print("=" * 84)
    print("5. REBATE vs RECOMPENSES DE LIQUIDITE — structures opposees")
    print("=" * 84)
    print("REBATE : proportionnel aux FILLS. rebateRate x rate x p(1-p) par part.")
    print("  Il monte avec le volume execute, et n'est PAS dilue par les")
    print("  concurrents : chacun touche sa part des frais que SES fills ont")
    print("  generes. C'est le seul terme qui PASSE A L'ECHELLE.")
    print()
    print("RECOMPENSES : part d'un POOL QUOTIDIEN FIXE, ponderee par la taille")
    print("  cotee dans la bande, divisee par le score de TOUS les makers.")
    print("  Formule officielle S(v,s) = ((v-s)/v)^2, epoque de 10 080 echantillons")
    print("  d'une minute. Elle est donc DILUEE par la concurrence et PLAFONNEE")
    print("  en valeur absolue : elle ne passe PAS a l'echelle.")
    print()
    pools = [(m.rewards_daily_rate or 0.0, m.rewards_max_spread,
              m.rewards_min_size, m.question)
             for m in markets.values()]
    total_pool = sum(p[0] for p in pools)
    paying = [p for p in pools if p[0] > 0]
    print(f"pool quotidien observable sur les {len(markets)} marches suivis : "
          f"{total_pool:,.0f} $/jour")
    print(f"  dont {len(paying)} marches versent effectivement quelque chose")
    if paying:
        for rate, spr, sz, q in sorted(paying, reverse=True)[:5]:
            print(f"    {rate:>7,.0f} $/j  bande {spr}c  taille min {sz:.0f}  {q[:38]}")
    print()
    print(f"PLAFOND STRUCTUREL : meme en captant 100 % de ces pools — ce qu'aucun")
    print(f"maker ne fait — le revenu de recompense est borne a "
          f"{total_pool*365:,.0f} $/an sur ce panier.")
    print("  Un operateur plus gros ne l'augmente pas : il se dilue lui-meme.")
    print("  Pour un objectif de revenus IMPORTANTS, seul le rebate compte —")
    print("  et le rebate est proportionnel aux fills, donc a l'adverse")
    print("  selection qui les accompagne. La question se reduit au fill.")
    report["reward_structure"] = {
        "total_daily_pool_observed": total_pool,
        "n_markets_paying": len(paying), "n_markets_tracked": len(markets),
        "annual_ceiling_if_fully_captured": total_pool * 365,
        "note": ("le rebate passe a l'echelle (proportionnel aux fills) ; la "
                 "recompense non (pool fixe, dilue par la concurrence). La "
                 "part reellement obtenue depend des concurrents, qui ne sont "
                 "pas observables : elle reste UNKNOWN.")}

    report["finished_at"] = utc_now_iso()
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                             default=str), encoding="utf-8")
        print(f"\nrapport -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Equation maker Polymarket")
    ap.add_argument("--books", type=Path, default=DEFAULT_BOOKS)
    ap.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    run(a.books, a.markets, a.json)


if __name__ == "__main__":
    main()
