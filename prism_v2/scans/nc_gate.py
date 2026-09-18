"""PORTE ECONOMIQUE COMPLETE sur l'ecart perp/index des actions tokenisees.

Ce script produit le verdict. Toutes les gardes sont declarees ici, avant
les chiffres, et aucune n'est relachee si le resultat deplait.

  EXECUTION     entree et sortie a l'OUVERTURE d'une barre POSTERIEURE a celle
                qui porte le signal. Jamais au prix qui a declenche le signal.
  DECALAGE      `lag` barres supplementaires entre signal et entree. A 5 min,
                lag=1 laisse 5 minutes pleines : plus lent que tout systeme
                reel, donc conservateur.
  BLOCS         une position occupe sa fenetre ; aucune autre n'y entre. Une
                observation = un bloc.
  COUTS         2 x (frais publics + demi-spread MESURE en direct). Aucun
                rabais, aucun palier VIP, aucun rebate suppose.
  SEUILS        ecart-type de z estime sur le passe STRICT, glissant.
  DECOUPE       60 % initiaux = IS, 40 % finaux = OOS. Les instruments sont
                choisis sur l'IS ; le verdict se lit sur l'OOS.
  TEMOIN        memes tests, meme code, sur des perpetuels crypto dont le
                sous-jacent ne ferme jamais.
  BH            Benjamini-Hochberg sur TOUS les tests menes.

Usage :
    PRISM_NC_RAW=<dir> PRISM_NC_TICKS=<f> PRISM_NC_BAR=5m \\
        python3 -m prism_v2.scans.nc_gate
"""
from __future__ import annotations

import math
import os
import statistics as st
import sys
from typing import Dict, List, Sequence, Tuple

from prism_v2.scans.nc_basis import (BAR_MS, FILL_OPEN, benjamini_hochberg,
                                     norm_sf, trades, tstat)
from prism_v2.scans.nc_reversion import (CRYPTO, MAX_ZERO_RETURN,
                                         MAX_ZERO_VOLUME, MAKER_BPS, TAKER_BPS,
                                         build, half_spreads)

KS = (2.0, 3.0, 4.0, 5.0, 6.0)
HS = (1, 2, 3, 6, 12)
LAGS = (0, 1, 2)


def run(book: dict, syms: Sequence[str], hs: Dict[str, float], k: float,
        h: int, lag: int, t_lo: int = 0, t_hi: int = 10 ** 18,
        taker: float = TAKER_BPS) -> Tuple[List[float], Dict[str, float]]:
    """Rend (nets pooles, PnL total par instrument)."""
    nets: List[float] = []
    per: Dict[str, float] = {}
    for s in syms:
        d = book[s]
        cost = 2.0 * (taker + hs[s])
        tr = [x for x in trades(d["bas"], d["z"], d["sig"], k, h, lag, cost,
                                d["bar_ms"], FILL_OPEN)
              if t_lo <= x[0] < t_hi]
        nets.extend(x[2] for x in tr)
        per[s] = sum(x[2] for x in tr)
    return nets, per


def describe(nets: Sequence[float], per: Dict[str, float]) -> dict:
    m, se, t_ = tstat(nets)
    tot = sum(nets)
    top = max(per.values()) if per else 0.0
    return {"n": len(nets), "mean": m, "t": t_, "med": st.median(nets) if nets else 0.0,
            "pos": (sum(1 for x in nets if x > 0) / len(nets) * 100) if nets else 0.0,
            "top_share": (top / tot * 100) if tot else float("nan"),
            "p": norm_sf(t_)}


def main() -> int:
    raw = os.environ.get("PRISM_NC_RAW", "")
    ticks = os.environ.get("PRISM_NC_TICKS", "")
    bar = os.environ.get("PRISM_NC_BAR", "5m")
    if not raw or not os.path.isdir(raw):
        print("PRISM_NC_RAW doit pointer sur le repertoire de collecte")
        return 2
    hs = half_spreads(ticks)
    if not hs:
        print("PRISM_NC_TICKS doit pointer sur des releves de carnet reels : "
              "sans demi-spread mesure, aucun cout n'est MESURE et la porte "
              "economique ne peut pas etre franchie")
        return 2
    book = build(raw, bar)
    mzr, mzv = MAX_ZERO_RETURN[bar], MAX_ZERO_VOLUME[bar]
    ok = [s for s, d in book.items()
          if d["zero_ret"] <= mzr and d["zero_vol"] <= mzv and s in hs]
    EQ = [s for s in ok if s not in CRYPTO]
    CR = [s for s in ok if s in CRYPTO]
    if not EQ:
        print("aucun instrument ne passe les filtres de fraicheur")
        return 2

    all_t = sorted({t for s in EQ for t in book[s]["sig"]})
    split = all_t[int(len(all_t) * 0.60)]
    per_day = 86_400_000 // BAR_MS[bar]
    print(f"barre={bar} · actions={len(EQ)} · temoin crypto={len(CR)} · "
          f"{len(all_t)} barres · coupure IS/OOS a 60 %\n")

    rows = []
    for k in KS:
        for h in HS:
            for lag in LAGS:
                nets, per = run(book, EQ, hs, k, h, lag, 0, split)
                if len(nets) < 40:
                    continue
                d = describe(nets, per)
                d.update(k=k, h=h, lag=lag)
                rows.append(d)
    if not rows:
        print("aucune cellule ne reunit assez d'observations")
        return 1

    keep = benjamini_hochberg([r["p"] for r in rows], q=0.10)
    print(f"=== IS · {len(rows)} cellules testees (k x h x lag) ===")
    print(f"{'k':>4s} {'h':>3s} {'lag':>3s} {'n':>6s} {'NET moy':>8s} {'med':>7s} "
          f"{'t':>6s} {'%>0':>4s} {'top1%':>6s} {'BH':>3s}")
    for r, kp in zip(rows, keep):
        if not kp and r["t"] < 1.5:
            continue
        print(f"{r['k']:4.1f} {r['h']:3d} {r['lag']:3d} {r['n']:6d} "
              f"{r['mean']:8.2f} {r['med']:7.2f} {r['t']:6.2f} {r['pos']:4.0f} "
              f"{r['top_share']:6.0f} {'OUI' if kp else '.':>3s}")
    surv = [r for r, kp in zip(rows, keep) if kp and r["mean"] > 0]
    print(f"\nsurvivants BH q=0,10 a net POSITIF : {len(surv)} / {len(rows)}")
    if not surv:
        print("\nPORTE NON FRANCHIE en echantillon. Rien a valider hors "
              "echantillon : il n'y a pas de modele.")
        return 1

    best = max(surv, key=lambda r: r["t"])
    print(f"\ncellule retenue sur l'IS (t maximal) : k={best['k']} h={best['h']} "
          f"lag={best['lag']}\n")

    print("=== OOS · 40 % finaux, meme cellule, aucun reglage ===")
    for label, syms in (("ACTIONS", EQ), ("TEMOIN CRYPTO", CR)):
        if not syms:
            continue
        nets, per = run(book, syms, hs, best["k"], best["h"], best["lag"],
                        split, 10 ** 18)
        if len(nets) < 10:
            print(f"  {label:14s} trop peu d'observations ({len(nets)})")
            continue
        d = describe(nets, per)
        jours = (all_t[-1] - split) / 86_400_000
        print(f"  {label:14s} n={d['n']:5d}  NET={d['mean']:7.2f} bps  "
              f"t={d['t']:5.2f}  med={d['med']:6.2f}  %>0={d['pos']:3.0f}%  "
              f"trades/jour={d['n']/jours:5.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
