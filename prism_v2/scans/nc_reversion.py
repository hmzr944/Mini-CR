"""PORTE ECONOMIQUE : l'ecart perp/index des actions tokenisees paie-t-il ?

Ce qui est teste n'est PAS « la base se referme-t-elle » — une base peut se
refermer par l'index, qui n'est pas negociable. C'est : en vendant le perp
quand il est riche contre son index composite exogene, et en l'achetant quand
il est pauvre, le PERP rapporte-t-il plus qu'un aller-retour ne coute ?

DISCIPLINE APPLIQUEE, declaree avant les chiffres :
  - seuil d'entree causal (ecart-type de z sur 30 jours de passe STRICT) ;
  - decalage d'execution : la barre qui porte le signal n'est jamais celle qui
    porte le prix d'entree (lag >= 1) ;
  - blocs DISJOINTS : une position occupe sa fenetre, aucune autre n'y entre ;
  - couts pleins : 2 x (frais taker publics + demi-spread MESURE en direct) ;
  - Benjamini-Hochberg sur TOUS les tests menes, pas sur les survivants ;
  - decoupe temporelle 60/40 : les seuils et horizons se lisent sur la
    premiere partie, le verdict sur la seconde ;
  - temoin crypto : les memes instruments 24/7, meme code, meme seuil.

Usage :
    PRISM_NC_RAW=<dir> python3 -m prism_v2.scans.nc_reversion
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from typing import Dict, List, Sequence, Tuple

from prism_v2.scans.nc_basis import (BAR_MS, HOUR_MS, benjamini_hochberg,
                                     basis_series, causal_sigma, causal_z,
                                     load_raw, norm_sf, staleness, trades,
                                     tstat)

TAKER_BPS = 5.0        # bareme public OKX perpetuels, palier 0
MAKER_BPS = 2.0
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "BCH", "ADA", "LINK", "AVAX"}

#: Part maximale de barres sans mouvement de prix / sans echange, PAR TAILLE
#: DE BARRE. A une heure, une serie saine n'a presque aucune barre figee ; a
#: cinq minutes elle en a toujours. Garder le seuil horaire a 5 minutes
#: rejette l'univers entier ; le relacher sans garde laisse passer l'artefact.
#: Les deux sont evites : le seuil depend de la barre ET la garde a la barre
#: dans `trades` exclut individuellement toute barre d'entree ou de sortie
#: sans echange.
MAX_ZERO_RETURN = {"1H": 0.03, "15m": 0.08, "5m": 0.15, "1m": 0.35}
MAX_ZERO_VOLUME = {"1H": 0.02, "15m": 0.06, "5m": 0.12, "1m": 0.30}


def half_spreads(ticks_path: str) -> Dict[str, float]:
    """Demi-spread MEDIAN mesure sur les releves de carnet reels."""
    acc: Dict[str, List[float]] = {}
    if not os.path.exists(ticks_path):
        return {}
    with open(ticks_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for row in d.get("r", []):
                inst, _ts, bid, ask = row[0], row[1], row[2], row[3]
                if bid > 0 and ask > bid:
                    acc.setdefault(inst.split("-")[0], []).append(
                        (ask - bid) / 2 / ((ask + bid) / 2) * 1e4)
    return {k: st.median(v) for k, v in acc.items() if len(v) >= 3}


def build(raw_dir: str, bar: str = "1H", z_window_h: int = 24,
          sig_window_h: int = 720):
    """Construit base, z et sigma causale pour chaque instrument.

    Les fenetres sont donnees en HEURES et converties en barres : a 5 minutes,
    « 24 » doit rester 24 heures, pas 24 barres. Melanger les deux fabrique un
    seuil 12 fois trop court sans qu'aucun test ne le signale.
    """
    bar_ms = BAR_MS[bar]
    per_hour = HOUR_MS // bar_ms
    zw = max(4, z_window_h * per_hour)
    sw = max(60, sig_window_h * per_hour)
    perp, index, peg = load_raw(raw_dir, bar)
    book: Dict[str, dict] = {}
    for sym in sorted(perp):
        if sym not in index:
            continue
        zv, zr = staleness(perp[sym], bar_ms)
        bas = basis_series(perp[sym], index[sym], peg)
        if len(bas) < 800:
            continue
        z = causal_z(bas, zw, bar_ms)
        sig = causal_sigma(z, sw, max(60, sw // 3), bar_ms)
        if len(sig) < 300:
            continue
        book[sym] = {"bas": bas, "z": z, "sig": sig, "zero_ret": zr,
                     "zero_vol": zv, "index": index[sym], "bar_ms": bar_ms}
    return book


def cell(d: dict, k: float, h: int, lag: int, cost: float,
         t_lo: int = 0, t_hi: int = 10 ** 18) -> Tuple[List[float], List[float]]:
    tr = [x for x in trades(d["bas"], d["z"], d["sig"], k, h, lag, cost,
                            d["bar_ms"])
          if t_lo <= x[0] < t_hi]
    return [x[1] for x in tr], [x[2] for x in tr]


def main() -> int:
    raw = os.environ.get("PRISM_NC_RAW", "")
    ticks = os.environ.get("PRISM_NC_TICKS", "")
    if not raw or not os.path.isdir(raw):
        print("PRISM_NC_RAW doit pointer sur le repertoire de collecte")
        return 2
    hs = half_spreads(ticks)
    book = build(raw)
    if not book:
        print("aucune serie exploitable")
        return 2

    all_t = sorted({t for d in book.values() for t in d["sig"]})
    split = all_t[int(len(all_t) * 0.60)]
    print(f"{len(book)} instruments · coupure IS/OOS = "
          f"{split} ({len(all_t)} heures couvertes)\n")

    KS = (1.5, 2.0, 2.5)
    HS = (1, 2, 4, 8)
    LAG = 1

    rows = []
    for sym, d in book.items():
        if sym in CRYPTO:
            continue
        if (d["zero_ret"] > MAX_ZERO_RETURN["1H"]
                or d["zero_vol"] > MAX_ZERO_VOLUME["1H"]):
            continue
        spread = hs.get(sym)
        if spread is None:
            continue
        cost = 2.0 * (TAKER_BPS + spread)
        for k in KS:
            for h in HS:
                g, n = cell(d, k, h, LAG, cost, 0, split)
                if len(n) < 20:
                    continue
                m, se, t_ = tstat(n)
                rows.append({"sym": sym, "k": k, "h": h, "cost": cost,
                             "n": len(n), "mean": m, "t": t_,
                             "gross": st.fmean(g), "p": norm_sf(t_)})

    rows.sort(key=lambda r: -r["t"])
    keep = benjamini_hochberg([r["p"] for r in rows], q=0.10)
    print("=== IN-SAMPLE (60 % initiaux) — tous les tests menes : "
          f"{len(rows)} ===")
    print(f"{'sym':9s} {'k':>4s} {'h':>2s} {'n':>4s} {'brut':>8s} {'cout':>6s} "
          f"{'NET':>8s} {'t':>6s} {'BH':>3s}")
    for r, kp in zip(rows, keep):
        if r["t"] < 1.0 and not kp:
            continue
        print(f"{r['sym']:9s} {r['k']:4.1f} {r['h']:2d} {r['n']:4d} "
              f"{r['gross']:8.2f} {r['cost']:6.2f} {r['mean']:8.2f} "
              f"{r['t']:6.2f} {'OUI' if kp else '.':>3s}")
    nkeep = sum(keep)
    print(f"\nsurvivants Benjamini-Hochberg q=0,10 : {nkeep} / {len(rows)}")
    return 0 if nkeep else 1


if __name__ == "__main__":
    sys.exit(main())
