#!/usr/bin/env python3
"""MARKOUT — un fill passif est-il rentable APRES avoir ete execute ?

LA QUESTION CHANGE DE NATURE. Toutes les experiences precedentes demandaient
« quel signal predit le prochain tick ? » et repondaient : aucun. Le mid est
une martingale, et traverser le spread deux fois coute plus que tout ce qu'on
recupere.

Reste une seule facon de ne pas payer le spread : le RECEVOIR. Un maker
n'achete pas au mid, il achete au bid — il encaisse un demi-spread a
l'execution. Mais il n'est execute que parce que QUELQU'UN A CHOISI son prix,
et ce quelqu'un peut en savoir plus. C'est l'adverse selection.

    PnL du fill passif = demi-spread encaisse
                       + derive du mid apres execution   (le MARKOUT)
                       - frais maker

Le markout est la seule inconnue. Ce module la mesure.

HYPOTHESE VOLONTAIREMENT FAVORABLE AU MAKER. La position dans la file est
INCONNUE : on suppose la premiere place, donc execution des qu'un trade
atteint le prix. C'est l'hypothese la PLUS GENEREUSE — elle donne au maker
tous les fills, y compris les benins. Un resultat negatif sous cette
hypothese est donc decisif ; un resultat positif ne prouverait rien, car la
vraie file degraderait le melange.

Ce que la donnee d'observatoire permet et ne permet pas :
  - elle agrege les trades par tranche de 250 ms : on sait qu'une vente a eu
    lieu et dans quelle fourchette de prix, pas quel trade exact ;
  - on ne peut donc pas reconstituer une file d'attente, seulement borner.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.core_types import utc_now_iso

DEFAULT_OBSERVATORY = (Path(__file__).parent / "data" / "observatory"
                       / "session_long.jsonl.gz")

#: Frais MAKER, bareme public OKX Lv1. Majorant : aucun frais OBSERVED sans
#: compte authentifie.
MAKER_FEE_BPS = 2.0

#: Horizons de markout, en millisecondes.
MARKOUT_HORIZONS_MS = (1_000, 5_000, 30_000)

#: Cote passif teste.
SIDE_BID = "bid"
SIDE_ASK = "ask"


@dataclass
class Fill:
    """Un fill passif hypothetique, et ce qui lui est arrive ensuite."""

    ts_ms: int
    inst_id: str
    side: str                       # bid = on a achete passivement
    quote_px: float
    mid_at_fill: float
    half_spread_bps: float          # ce que le maker encaisse a l'execution
    markout_bps: Dict[int, float] = field(default_factory=dict)
    #: Contexte a l'instant de la decision — jamais posterieur.
    spread_bps: float = 0.0
    depth_imbalance: float = 0.0
    trade_intensity: int = 0
    aggressor_ratio: float = 0.0    # part du volume dans le sens qui nous frappe

    def net_bps(self, horizon_ms: int, fee_bps: float = MAKER_FEE_BPS
                ) -> Optional[float]:
        """Demi-spread encaisse + markout - frais. Rien d'autre n'est suppose."""
        m = self.markout_bps.get(horizon_ms)
        if m is None:
            return None
        return self.half_spread_bps + m - fee_bps


def _mid(rec: Dict[str, Any]) -> Optional[float]:
    b, a = rec.get("b"), rec.get("a")
    if not b or not a:
        return None
    return (b[0][0] + a[0][0]) / 2.0


def _depth_imbalance(rec: Dict[str, Any], levels: int = 5) -> float:
    b = sum(p * s for p, s in (rec.get("b") or [])[:levels])
    a = sum(p * s for p, s in (rec.get("a") or [])[:levels])
    tot = b + a
    return (b - a) / tot if tot > 0 else 0.0


def simulate_passive_fills(recs: Sequence[Dict[str, Any]], inst_id: str,
                           side: str = SIDE_BID,
                           horizons: Sequence[int] = MARKOUT_HORIZONS_MS,
                           cooldown_ms: int = 30_000) -> List[Fill]:
    """Poste un ordre passif au touch, determine s'il est frappe, mesure apres.

    FILL. Un bid passif a B est frappe quand une VENTE agressive survient a un
    prix <= B. L'observatoire agrege les trades par tranche : on dispose du
    volume vendeur et de la fourchette de prix de la tranche. On considere le
    fill acquis si du volume vendeur existe ET que le prix le plus bas de la
    tranche atteint B.

    CAUSALITE. Le contexte (spread, desequilibre, intensite) est celui de
    l'instant de la DECISION. Le markout est mesure APRES le fill et n'entre
    dans aucune decision.
    """
    rows = [r for r in recs if r.get("i") == inst_id and r.get("ok")]
    rows.sort(key=lambda r: r["ts"])
    ts_list = [r["ts"] for r in rows]
    if len(rows) < 10:
        return []
    from bisect import bisect_left

    def mid_at(ts: int) -> Optional[float]:
        i = bisect_left(ts_list, ts)
        if i >= len(rows):
            return None
        return _mid(rows[i])

    out: List[Fill] = []
    last_fill = -10 ** 18
    max_h = max(horizons)
    for i, rec in enumerate(rows[:-1]):
        ts = rec["ts"]
        if ts - last_fill < cooldown_ms:      # une position a la fois
            continue
        if ts + max_h > ts_list[-1]:
            break
        b, a = rec.get("b"), rec.get("a")
        if not b or not a:
            continue
        mid = _mid(rec)
        if mid is None or mid <= 0:
            continue
        quote = b[0][0] if side == SIDE_BID else a[0][0]

        # Le fill est cherche dans la tranche SUIVANTE : un ordre poste a
        # l'instant t ne peut pas etre execute par un trade deja passe.
        nxt = rows[i + 1]
        t = nxt.get("t")
        if not t or not t.get("n"):
            continue
        aggressive_usd = t["su"] if side == SIDE_BID else t["bu"]
        if aggressive_usd <= 0:
            continue
        lo = min(t["fp"], t["lp"])
        hi = max(t["fp"], t["lp"])
        hit = (lo <= quote) if side == SIDE_BID else (hi >= quote)
        if not hit:
            continue

        fill_ts = nxt["ts"]
        sign = 1.0 if side == SIDE_BID else -1.0
        half = sign * (mid - quote) / mid * 10_000.0
        f = Fill(ts_ms=fill_ts, inst_id=inst_id, side=side, quote_px=quote,
                 mid_at_fill=mid, half_spread_bps=half,
                 spread_bps=(a[0][0] - b[0][0]) / mid * 10_000.0,
                 depth_imbalance=_depth_imbalance(rec),
                 trade_intensity=int(t["n"]),
                 aggressor_ratio=(aggressive_usd / (t["bu"] + t["su"])
                                  if (t["bu"] + t["su"]) > 0 else 0.0))
        for h in horizons:
            m = mid_at(fill_ts + h)
            if m is not None:
                # Achat passif : on gagne si le mid MONTE apres le fill.
                # Le markout est la derive du MID depuis la decision, mesuree
                # au meme denominateur que le demi-spread : les deux termes
                # s'additionnent alors exactement.
                f.markout_bps[h] = sign * (m - mid) / mid * 10_000.0
        if f.markout_bps:
            out.append(f)
            last_fill = fill_ts
    return out


def unconditional_markout(recs: Sequence[Dict[str, Any]], inst_id: str,
                          side: str = SIDE_BID,
                          horizons: Sequence[int] = MARKOUT_HORIZONS_MS,
                          step_ms: int = 30_000) -> List[Fill]:
    """CONTROLE : le meme calcul, mais SANS condition de fill.

    C'est la mesure qui decide si le resultat precedent est de l'adverse
    selection ou un artefact de ma detection. On poste le meme ordre au meme
    prix, a des instants REGULIERS, et on mesure la meme chose — sans exiger
    qu'un trade soit venu le frapper.

    Si le markout non conditionnel vaut zero et le markout conditionnel est
    negatif, la difference est l'ADVERSE SELECTION : elle vient du fait d'AVOIR
    ETE CHOISI, pas d'une derive du marche ni d'un defaut de mesure.

    Si les deux sont negatifs, ma mesure derive et le resultat ne vaut rien.
    """
    rows = [r for r in recs if r.get("i") == inst_id and r.get("ok")]
    rows.sort(key=lambda r: r["ts"])
    ts_list = [r["ts"] for r in rows]
    if len(rows) < 10:
        return []
    from bisect import bisect_left

    def mid_at(ts: int) -> Optional[float]:
        i = bisect_left(ts_list, ts)
        return _mid(rows[i]) if i < len(rows) else None

    out: List[Fill] = []
    last = -10 ** 18
    max_h = max(horizons)
    for rec in rows:
        ts = rec["ts"]
        if ts - last < step_ms or ts + max_h > ts_list[-1]:
            continue
        b, a = rec.get("b"), rec.get("a")
        if not b or not a:
            continue
        mid = _mid(rec)
        if mid is None or mid <= 0:
            continue
        last = ts
        quote = b[0][0] if side == SIDE_BID else a[0][0]
        sign = 1.0 if side == SIDE_BID else -1.0
        half = sign * (mid - quote) / mid * 10_000.0
        f = Fill(ts_ms=ts, inst_id=inst_id, side=side, quote_px=quote,
                 mid_at_fill=mid, half_spread_bps=half,
                 spread_bps=(a[0][0] - b[0][0]) / mid * 10_000.0,
                 depth_imbalance=_depth_imbalance(rec))
        for h in horizons:
            m = mid_at(ts + h)
            if m is not None:
                f.markout_bps[h] = sign * (m - mid) / mid * 10_000.0
        if f.markout_bps:
            out.append(f)
    return out


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


def conditional_analysis(fills: Sequence[Fill], horizon_ms: int,
                         fee_bps: float = MAKER_FEE_BPS) -> Dict[str, Any]:
    """P(fill rentable | caracteristiques) — le coeur de la question.

    Un maker ne choisit pas SI on l'execute, mais QUAND il accepte de coter.
    S'il existe des conditions ou le fill est systematiquement meilleur, c'est
    la que se trouve l'edge. Toutes les caracteristiques sont OBSERVABLES A LA
    DECISION : aucune n'utilise ce qui se passe apres.
    """
    nets = [(f, f.net_bps(horizon_ms, fee_bps)) for f in fills]
    nets = [(f, v) for f, v in nets if v is not None]
    if len(nets) < 20:
        return {"n": len(nets), "note": "echantillon insuffisant"}

    out: Dict[str, Any] = {"overall": _stats([v for _f, v in nets])}

    def terciles(key, label):
        vals = sorted(key(f) for f, _ in nets)
        lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        buckets: Dict[str, List[float]] = {"bas": [], "moyen": [], "haut": []}
        for f, v in nets:
            k = key(f)
            buckets["bas" if k <= lo else ("haut" if k >= hi else "moyen")].append(v)
        return {"cutoffs": [lo, hi],
                **{k: _stats(v) for k, v in buckets.items()}}

    out["by_spread"] = terciles(lambda f: f.spread_bps, "spread")
    out["by_depth_imbalance"] = terciles(lambda f: f.depth_imbalance, "desequilibre")
    out["by_trade_intensity"] = terciles(lambda f: f.trade_intensity, "intensite")
    out["by_aggressor_ratio"] = terciles(lambda f: f.aggressor_ratio, "agresseur")
    return out


def run(obs_path: Path = DEFAULT_OBSERVATORY, fee_bps: float = MAKER_FEE_BPS,
        max_instruments: int = 15,
        json_out: Optional[Path] = None) -> Dict[str, Any]:
    from prism_v2.observatory import load_snapshots

    report: Dict[str, Any] = {"started_at": utc_now_iso(),
                              "mode": "RECHERCHE — aucun ordre reel",
                              "maker_fee_bps": fee_bps}
    print("=" * 84); print("1. DONNEES"); print("=" * 84)
    meta, recs = load_snapshots(Path(obs_path))
    inst_ids = sorted({r["i"] for r in recs})[:max_instruments]
    ts = [r["ts"] for r in recs]
    print(f"instantanes : {len(recs):,} | instruments : {len(inst_ids)} | "
          f"fenetre : {(max(ts)-min(ts))/3.6e6:.2f} h")
    print(f"frais maker : {fee_bps} bps (bareme public Lv1, MAJORANT)")
    print("\nHYPOTHESE FAVORABLE AU MAKER : premiere place dans la file.")
    print("  Un resultat negatif sous cette hypothese est DECISIF ;")
    print("  un resultat positif ne prouverait rien.")
    report["data"] = {"n_snapshots": len(recs), "n_instruments": len(inst_ids),
                      "window_hours": (max(ts) - min(ts)) / 3.6e6}

    print(); print("=" * 84)
    print("2. FILLS PASSIFS SIMULES ET LEUR MARKOUT"); print("=" * 84)
    all_fills: List[Fill] = []
    per_inst: Dict[str, Any] = {}
    for inst_id in inst_ids:
        fills = []
        for side in (SIDE_BID, SIDE_ASK):
            fills.extend(simulate_passive_fills(recs, inst_id, side))
        all_fills.extend(fills)
        if fills:
            per_inst[inst_id] = {
                "n_fills": len(fills),
                "mean_half_spread_bps": statistics.mean(
                    f.half_spread_bps for f in fills),
                **{f"markout_{h}ms": _stats([f.markout_bps[h] for f in fills
                                             if h in f.markout_bps])
                   for h in MARKOUT_HORIZONS_MS}}
    print(f"fills passifs simules : {len(all_fills):,}\n")
    print(f"{'horizon':>9}{'N':>8}{'demi-spread':>14}{'markout':>11}"
          f"{'frais':>8}{'NET':>10}{'t':>8}{'part>0':>9}")
    print("-" * 78)
    for h in MARKOUT_HORIZONS_MS:
        hs = [f.half_spread_bps for f in all_fills if h in f.markout_bps]
        mo = [f.markout_bps[h] for f in all_fills if h in f.markout_bps]
        net = [f.net_bps(h, fee_bps) for f in all_fills
               if f.net_bps(h, fee_bps) is not None]
        if not net:
            continue
        s = _stats(net)
        print(f"{h/1000:>7.0f}s{len(net):>8,}{statistics.mean(hs):>14.4f}"
              f"{statistics.mean(mo):>11.4f}{-fee_bps:>8.1f}"
              f"{s['mean']:>10.4f}{(s['t_stat'] or 0):>8.2f}"
              f"{s['share_positive']:>9.1%}")
    report["by_horizon"] = {
        str(h): {"half_spread": statistics.mean(
                     [f.half_spread_bps for f in all_fills if h in f.markout_bps])
                 if any(h in f.markout_bps for f in all_fills) else None,
                 "markout": _stats([f.markout_bps[h] for f in all_fills
                                    if h in f.markout_bps]),
                 "net": _stats([f.net_bps(h, fee_bps) for f in all_fills
                                if f.net_bps(h, fee_bps) is not None])}
        for h in MARKOUT_HORIZONS_MS}
    report["per_instrument"] = per_inst

    print(); print("=" * 84)
    print("2bis. CONTROLE — le markout sans condition de fill")
    print("=" * 84)
    print("Si le markout non conditionnel vaut zero et le conditionnel est")
    print("negatif, la difference est l'ADVERSE SELECTION : elle vient d'AVOIR")
    print("ETE CHOISI. Si les deux derivent, ma mesure est fausse.\n")
    ctrl: List[Fill] = []
    for inst_id in inst_ids:
        for side in (SIDE_BID, SIDE_ASK):
            ctrl.extend(unconditional_markout(recs, inst_id, side))
    print(f"{'horizon':>9}{'N controle':>12}{'markout ctrl':>15}{'t':>8}"
          f"{'markout fill':>15}{'ECART':>10}")
    print("-" * 70)
    control_rows = {}
    for h in MARKOUT_HORIZONS_MS:
        cm = [f.markout_bps[h] for f in ctrl if h in f.markout_bps]
        fm = [f.markout_bps[h] for f in all_fills if h in f.markout_bps]
        if not cm or not fm:
            continue
        cs, fs = _stats(cm), _stats(fm)
        gap = fs["mean"] - cs["mean"]
        control_rows[str(h)] = {"control": cs, "conditional": fs,
                                "adverse_selection_bps": gap}
        print(f"{h/1000:>7.0f}s{cs['n']:>12,}{cs['mean']:>15.4f}"
              f"{(cs['t_stat'] or 0):>8.2f}{fs['mean']:>15.4f}{gap:>10.4f}")
    report["control"] = control_rows
    if control_rows:
        h0 = str(MARKOUT_HORIZONS_MS[0])
        c0 = control_rows.get(h0, {})
        cm, t = c0.get("control", {}).get("mean"), c0.get("control", {}).get("t_stat")
        if cm is not None and t is not None:
            if abs(t) < 3:
                print(f"\n  Le controle est indiscernable de zero (t={t:+.2f}) :")
                print(f"  l'ecart de {c0['adverse_selection_bps']:+.4f} bps est bien")
                print("  de l'ADVERSE SELECTION, pas une derive de mesure.")
            else:
                print(f"\n  ATTENTION : le controle derive (t={t:+.2f}). Une part")
                print("  du resultat conditionnel vient de la mesure, pas du fill.")

    print(); print("=" * 84)
    print("3. P(fill rentable | caracteristiques observables A LA DECISION)")
    print("=" * 84)
    h = MARKOUT_HORIZONS_MS[1]
    cond = conditional_analysis(all_fills, h, fee_bps)
    report["conditional"] = {"horizon_ms": h, **cond}
    if cond.get("overall"):
        o = cond["overall"]
        print(f"horizon {h/1000:.0f}s | ensemble : net {o['mean']:+.4f} bps "
              f"(N={o['n']:,}, t={o['t_stat']:+.2f}, part>0 {o['share_positive']:.1%})\n")
        print(f"{'caracteristique':<22}{'bas':>12}{'moyen':>12}{'haut':>12}")
        print("-" * 58)
        for key, label in (("by_spread", "spread"),
                           ("by_depth_imbalance", "desequilibre"),
                           ("by_trade_intensity", "intensite trades"),
                           ("by_aggressor_ratio", "part agresseur")):
            b = cond.get(key, {})
            cells = []
            for name in ("bas", "moyen", "haut"):
                st = b.get(name, {})
                cells.append(f"{st['mean']:+.3f}" if st.get("mean") is not None
                             else "n/a")
            print(f"{label:<22}{cells[0]:>12}{cells[1]:>12}{cells[2]:>12}")
        best = None
        for key in ("by_spread", "by_depth_imbalance", "by_trade_intensity",
                    "by_aggressor_ratio"):
            for name in ("bas", "moyen", "haut"):
                st = cond.get(key, {}).get(name, {})
                m, t = st.get("mean"), st.get("t_stat")
                if m is not None and m > 0 and t is not None and abs(t) > 3:
                    if best is None or m > best[0]:
                        best = (m, key, name, st)
        print()
        if best:
            print(f"CONDITION AU NET POSITIF ET SIGNIFICATIF : {best[1]} = {best[2]}")
            print(f"  net {best[0]:+.4f} bps (N={best[3]['n']:,}, "
                  f"t={best[3]['t_stat']:+.2f})")
            print("  A ATTAQUER : une tranche sur douze qui ressort peut etre")
            print("  un artefact de selection. Ce n'est pas encore un resultat.")
        else:
            print("AUCUNE condition ne donne un net positif significatif (|t|>3).")
            print("  Sous l'hypothese la PLUS FAVORABLE au maker — premiere")
            print("  place dans la file — coter passivement ne paie pas.")
        report["conditional"]["best"] = (
            {"feature": best[1], "bucket": best[2], **best[3]} if best else None)
    # ── 4. QUEL TARIF RENDRAIT LE MAKER VIABLE ? ──────────────────────────
    print(); print("=" * 84)
    print("4. SEUIL DE TARIFICATION"); print("=" * 84)
    h0 = MARKOUT_HORIZONS_MS[0]
    hs = [f.half_spread_bps for f in all_fills if h0 in f.markout_bps]
    mo = [f.markout_bps[h0] for f in all_fills if h0 in f.markout_bps]
    if hs and mo:
        gross = statistics.mean(hs) + statistics.mean(mo)
        print(f"demi-spread encaisse      : {statistics.mean(hs):+.4f} bps")
        print(f"adverse selection         : {statistics.mean(mo):+.4f} bps")
        print(f"                            {'-'*24}")
        print(f"AVANT FRAIS               : {gross:+.4f} bps")
        print()
        if gross < 0:
            print("L'adverse selection depasse a elle seule le demi-spread")
            print(f"encaisse, d'un facteur {abs(statistics.mean(mo))/statistics.mean(hs):.1f}.")
            print("Coter passivement perd AVANT MEME de payer le moindre frais.")
            print(f"Il faudrait un REBATE de {abs(gross):.4f} bps par fill pour")
            print("atteindre l'equilibre — c'est-a-dire etre PAYE pour coter,")
            print(f"la ou OKX facture {fee_bps} bps au palier public.")
            print()
            print("C'est une conclusion sur la STRUCTURE TARIFAIRE, pas sur la")
            print("strategie : sur une venue ou le maker est REMUNERE au lieu")
            print("d'etre facture, le signe de cette equation change.")
        else:
            print(f"Le brut est positif : un tarif sous {gross:.4f} bps suffirait.")
        report["breakeven"] = {
            "half_spread_bps": statistics.mean(hs),
            "adverse_selection_bps": statistics.mean(mo),
            "gross_before_fees_bps": gross,
            "required_rebate_bps": -gross if gross < 0 else 0.0,
            "current_public_maker_fee_bps": fee_bps}

    report["finished_at"] = utc_now_iso()
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                             default=str), encoding="utf-8")
        print(f"\nrapport -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Markout des fills passifs")
    ap.add_argument("--observatory", type=Path, default=DEFAULT_OBSERVATORY)
    ap.add_argument("--fee-bps", type=float, default=MAKER_FEE_BPS)
    ap.add_argument("--instruments", type=int, default=15)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    run(a.observatory, a.fee_bps, a.instruments, a.json)


if __name__ == "__main__":
    main()
