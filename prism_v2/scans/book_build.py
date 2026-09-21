"""Construit le livre de carry a partir des donnees OKX publiques, et l'evalue.

Aucune credential, aucun ordre. Tout est refetche a chaque passage sauf le
residu, lu dans les bougies (API + cache du depot).

    python3 -m prism_v2.scans.book_build
"""
from __future__ import annotations

import json
import statistics as st
import time
import urllib.parse
from pathlib import Path

from prism_v2.carry_book import (LIMITES, Pair, SAFETY_FACTOR,
                                 VOLUME_PARTICIPATION, select)
from prism_v2.funding_feed import OKX_BASE, _http_json

ROOT = Path(__file__).resolve().parents[2]
DEPTH_SAMPLES = 6          #: relevés de carnet espacés — jamais un instantané
DEPTH_INTERVAL_S = 2.5
RESIDUAL_WINDOW_H = 336    #: 14 jours
HOLDING_DAYS = 14.0        #: horizon de detention auquel les blocs sont evalues


def _get(path: str, **params):
    """GET JSON via le client stdlib du depot.

    prism_v2 n'a AUCUNE dependance tierce, et un garde d'architecture le
    verifie (tests/v2/test_architecture.py). On passe donc par
    funding_feed._http_json (urllib), pas par requests.
    """
    url = f"{OKX_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for _ in range(4):
        try:
            return _http_json(url)
        except Exception:
            time.sleep(1.5)
    return {}


def inverse_linear_universe() -> list[str]:
    d = _get("/api/v5/public/instruments", instType="SWAP").get("data") or []
    live = [i["instId"] for i in d if i.get("state") == "live"]
    inv = {i.split("-")[0] for i in live if i.endswith("-USD-SWAP")}
    lin = {i.split("-")[0] for i in live if i.endswith("-USDT-SWAP")}
    return sorted(inv & lin)


FUNDING_STORE = ROOT / "prism_v2" / "data" / "funding_forward"


def funding(inst: str) -> dict[int, float]:
    """Lit le magasin VERSIONNE, pas l'API.

    `tools/collect_funding_forward.py` est la seule porte d'entree du funding :
    lui seul appelle OKX, et il accumule au-dela des 95 jours que l'API rend.
    Relire l'API ici gaspillerait le quota et PERDRAIT l'historique deja
    accumule au-dela de la fenetre.
    """
    path = FUNDING_STORE / f"{inst}.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ts, rate = json.loads(line)
            out[int(ts)] = float(rate)
        except Exception:
            continue
    return out


def candles(inst: str, pages: int = 40) -> dict[int, float]:
    out, after = {}, None
    for _ in range(pages):
        p = {"instId": inst, "bar": "1H", "limit": 100}
        if after:
            p["after"] = after
        d = _get("/api/v5/market/history-candles", **p).get("data") or []
        if not d:
            break
        for r in d:
            out[int(r[0])] = float(r[4])
        nb = min(int(r[0]) for r in d)
        if after and nb >= after:
            break
        after = nb
        if len(d) < 100:
            break
        time.sleep(0.08)
    return out


def worst_adverse_drift_bps(residual: list[float], window: int = RESIDUAL_WINDOW_H) -> float:
    """Pire dérive du résidu sur une fenêtre glissante, dans les DEUX sens.

    Le livre perd quand l'inverse s'enrichit contre le linéaire ou l'inverse,
    selon le sens de la jambe — on retient donc le maximum des deux.
    """
    w = min(window, max(24, len(residual) // 3))
    worst = 0.0
    for i in range(0, max(1, len(residual) - w), 6):
        seg = residual[i:i + w]
        worst = max(worst, max(seg) - seg[0], seg[0] - min(seg))
    return worst


def volume_24h_usd(inst: str, spec: dict, tickers: dict) -> float:
    """Volume echange sur 24 h, en USD. C'est ce qui borne la taille d'une
    position patiente -- pas la profondeur a l'instant t (CORRECTION_CAPACITE.md)."""
    t = tickers.get(inst)
    if not t:
        return 0.0
    ctv = float(spec.get("ctVal", 1))
    v = float(t.get("vol24h") or 0)
    if spec.get("ctValCcy") == "USD":
        return v * ctv
    try:
        return v * ctv * float(t["last"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def sample_book(inst: str, spec: dict) -> tuple[float, float]:
    """(profondeur médiane USD sur 5 niveaux, demi-spread médian bps)."""
    depths, spreads = [], []
    ctv = float(spec.get("ctVal", 1))
    in_usd = spec.get("ctValCcy") == "USD"
    for _ in range(DEPTH_SAMPLES):
        d = (_get("/api/v5/market/books", instId=inst, sz=5).get("data") or [{}])[0]
        if not d.get("bids") or not d.get("asks"):
            time.sleep(DEPTH_INTERVAL_S); continue
        bid, ask = float(d["bids"][0][0]), float(d["asks"][0][0])
        mid = (bid + ask) / 2
        tot = sum(float(L[1]) * ctv * (1 if in_usd else float(L[0])) for L in d["bids"][:5])
        depths.append(tot)
        spreads.append(1e4 * (ask - bid) / 2 / mid)
        time.sleep(DEPTH_INTERVAL_S)
    if not depths:
        return 0.0, float("inf")
    return st.median(depths), st.median(spreads)


def build() -> None:
    coins = inverse_linear_universe()
    print(f"univers : {len(coins)} paires inverse/linéaire vivantes — {coins}\n")

    tiers = json.load(open(ROOT / "prism_v2/data/margin_tiers.json"))["families"]
    specs = {i["instId"]: i for i in (_get("/api/v5/public/instruments",
                                           instType="SWAP").get("data") or [])}
    tickers = {x["instId"]: x for x in (_get("/api/v5/market/tickers",
                                             instType="SWAP").get("data") or [])}
    cache = json.load(open(ROOT / "prism_v2/data/candles_1h.json"))
    ci = cache["fields"].index("close")
    cached = {k: {r[0]: r[ci] for r in v} for k, v in cache["data"].items()}

    pairs = []
    for c in coins:
        a, b = f"{c}-USD-SWAP", f"{c}-USDT-SWAP"
        fi, fl = funding(a), funding(b)
        ts = sorted(set(fi) & set(fl))
        if not ts:
            print(f"  {c}: pas de funding dans le magasin — lancer d'abord "
                  f"tools/collect_funding_forward.py"); continue
        diff = [(fi[t] - fl[t]) * 1e4 for t in ts]

        A = cached.get(a) or candles(a)
        B = cached.get(b) or candles(b)
        hrs = sorted(set(A) & set(B))
        residual = [1e4 * (A[t] / B[t] - 1) for t in hrs]

        di, hi = sample_book(a, specs.get(a, {}))
        dl, hl = sample_book(b, specs.get(b, {}))
        ti, tl = tiers.get(f"{c}-USD", [{}])[0], tiers.get(f"{c}-USDT", [{}])[0]
        if not ti or not tl:
            print(f"  {c}: barème de marge absent — ignorée"); continue

        pairs.append(Pair(
            coin=c, diff_bps_per_settlement=diff,
            imr_inverse=float(ti["imr"]), imr_linear=float(tl["imr"]),
            mmr_inverse=float(ti["mmr"]), mmr_linear=float(tl["mmr"]),
            depth_usd_inverse=di, depth_usd_linear=dl,
            volume_24h_usd_inverse=volume_24h_usd(a, specs.get(a, {}), tickers),
            volume_24h_usd_linear=volume_24h_usd(b, specs.get(b, {}), tickers),
            half_spread_inverse_bps=hi, half_spread_linear_bps=hl,
            worst_residual_drift_bps=worst_adverse_drift_bps(residual),
            residual_hours=len(hrs)))
        p = pairs[-1]
        bl = p.holding_blocks(HOLDING_DAYS)
        print(f"  {c:5s} r={p.r_bps_per_day:6.3f} t={p.t_stat:5.2f} "
              f"blocs={sum(x>0 for x in bl)}/{len(bl)} "
              f"vol24h={min(p.volume_24h_usd_inverse, p.volume_24h_usd_linear):13,.0f}$ "
              f"déployable={p.deployable_usd:10,.0f}$ "
              f"dérive14j={p.worst_residual_drift_bps:6.1f}bps Lmax={p.max_leverage:4.1f}x")

    book = select(pairs, holding_days=HOLDING_DAYS)

    print(f"\n{'─'*72}\nREJETS (critère déclaré dans prism_v2/carry_book.py)")
    for r in book.rejected:
        print(f"  {r.coin:5s} {r.criterion:22s} {r.value:12.2f}  seuil {r.threshold:.2f}")

    print(f"\nLIVRE RETENU : {book.coins or 'AUCUNE PAIRE'}")
    if not book.pairs:
        print("\nAucune paire ne passe. Ce n'est pas un bug : les rejets ci-dessus")
        print("nomment le critère qui a tué chacune.")
        return

    L = book.leverage()
    print(f"  r livre          : {book.r_bps_per_day():.3f} bps/jour   t = {book.t_stat():.2f}")
    print(f"  levier retenu    : {L:.1f}x  (min des leviers sûrs, facteur {SAFETY_FACTOR:.0f}x)")
    print(f"  notionnel déployable : {book.capacity_usd:,.0f} USD "
          f"({VOLUME_PARTICIPATION:.0%} du volume 24 h de la jambe contraignante)")
    print(f"  capital absorbable   : {book.max_capital_eur():,.0f} EUR")
    eur = book.capacity_usd * (book.r_bps_per_day() - 8.0 / HOLDING_DAYS) / 1e4 * 365
    print(f"  PnL à capacité pleine: {eur:,.0f} EUR/an (maker, {HOLDING_DAYS:.0f} j de détention)")

    print(f"\n  {'détention':>10s} {'maker bps/j':>12s} {'%/an':>8s} {'taker bps/j':>12s} {'%/an':>8s}")
    for T in (14, 30, 60, 90):
        mk = book.net_bps_per_day_of_capital(T, maker=True)
        tk = book.net_bps_per_day_of_capital(T, maker=False)
        print(f"  {T:9d}j {mk:12.2f} {100*book.annual_return(T, True):7.1f}% "
              f"{tk:12.2f} {100*book.annual_return(T, False):7.1f}%")
    print(f"\n{LIMITES}")


if __name__ == "__main__":
    build()
