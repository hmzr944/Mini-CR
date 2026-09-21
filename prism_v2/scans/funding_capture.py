"""Capture du funding, en absolu, couverte par du SPOT. OKX et Hyperliquid.

CE QUI EST TESTE, ET POURQUOI C'EST NEUF. Le projet n'avait jamais regarde que
12 a 15 paires inverse/lineaire. OKX cote 482 perpetuels, dont 233 ont un spot
correspondant ; Hyperliquid en cote 234, dont 145 ont un spot sur OKX. La
structure testee ici n'est pas un DIFFERENTIEL entre deux perps -- c'est la
capture du funding ABSOLU d'un perpetuel, couverte par le spot, ou la jambe de
couverture ne paie aucun funding.

C'est la difference avec le test inter-venues de `954ad9e` : couvrir un perp
par un autre perp fait payer le funding de la jambe de couverture, donc ne
capture que l'ecart. Couvrir par du spot capture le taux entier -- au prix
d'un levier de 1x, puisqu'il faut detenir le spot.

Protocole gele dans prism_v2/PROTOCOLE_CASHCARRY.md AVANT toute mesure.

    python3 -m prism_v2.scans.funding_capture okx
    python3 -m prism_v2.scans.funding_capture hl

RESULTAT (21/09/2026) : REFUTEE sur les deux venues. Voir RECHERCHE_FUNDING.md.
"""
from __future__ import annotations

import json
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Cout d'aller-retour, en bps de notionnel, declare avant mesure.
#: OKX  : perp taker 5 x2 + spot taker 8 x2
#: HL   : perp taker 3,5 x2 + spot OKX taker 8 x2
COST_BPS = {"okx": 26.0, "hl": 23.0}

#: Balayage declare : 5 x 5 = 25 cellules. Benjamini-Hochberg sur les 25.
KS = (1, 3, 5, 10, 20)
NS_OKX = (3, 6, 12, 24, 60)      #: en periodes de funding
NS_HL = (6, 24, 72, 168, 336)    #: en heures

MIN_VOLUME_USD = 1_000_000.0     #: perp ET spot, declare avant mesure
MIN_BLOCKS = 5                   #: sous ce seuil, la cellule n'est pas rendue


def _okx(path: str, **params):
    url = "https://www.okx.com" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "prism/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=20))
        except Exception:
            time.sleep(1.5)
    return {}


def _hl(payload):
    for _ in range(3):
        try:
            req = urllib.request.Request(
                "https://api.hyperliquid.xyz/info", data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "prism/1.0"},
                method="POST")
            return json.load(urllib.request.urlopen(req, timeout=25))
        except Exception:
            time.sleep(1.5)
    return None


def daily_rate_series(rows, cadence_hours: float) -> dict:
    """Taux de funding en bps par JOUR.

    La cadence est DERIVEE des horodatages, jamais supposee : 86 des 154
    instruments OKX mesures paient toutes les 4 h et non 8, et coder la cadence
    en dur avait deja fabrique un faux +3879 %/an dans ce depot (commit 954ad9e).
    """
    return {t: (r * 1e4) * (24.0 / cadence_hours) for t, r in rows}


def cadence_hours(rows) -> float | None:
    rows = sorted(rows)
    if len(rows) < 100:
        return None
    gaps = [(rows[i + 1][0] - rows[i][0]) / 3.6e6 for i in range(len(rows) - 1)]
    h = st.median(gaps)
    return h if 0.5 <= h <= 12 else None


def ex_ante_capture(series: dict, universe: list, grid: list, k: int, n: int,
                    days_per_period: float) -> tuple[list, float]:
    """Regle ex ante, blocs DISJOINTS.

    A l'instant t on trie les actifs DISPONIBLES par le taux deja PUBLIE a t,
    on retient les k plus eleves, on tient n periodes, on encaisse ce qui est
    reellement paye. Un actif n'est eligible que s'il couvre toute la fenetre :
    sinon la selection serait conditionnee par la survie.
    """
    out, i = [], 0
    while i + n < len(grid):
        t0 = grid[i]
        avail = [c for c in universe
                 if all(grid[j] in series[c] for j in range(i, i + 1 + n))]
        if len(avail) >= k:
            top = sorted(avail, key=lambda c: -series[c][t0])[:k]
            out.append(st.fmean([st.fmean([series[c][grid[j]]
                                           for j in range(i + 1, i + 1 + n)])
                                 for c in top]))
        i += n
    return out, n * days_per_period


def report(cells: list, cost: float, label: str) -> None:
    print(f"\n{label} — cout A/R {cost:.0f} bps, levier 1x (il faut detenir le spot)\n")
    print(f"{'k':>3s} {'N':>5s} {'jours':>6s} {'blocs':>6s} {'brut bps/j':>11s} "
          f"{'t':>6s} {'NET bps/j':>10s} {'%/an':>8s}")
    for c in cells:
        print(f"{c['k']:3d} {c['n']:5d} {c['days']:6.1f} {c['blocks']:6d} "
              f"{c['gross']:11.2f} {c['t']:6.2f} {c['net']:10.2f} "
              f"{100 * ((1 + c['net'] / 1e4) ** 365 - 1):7.1f}%")
    pos = sum(c["net"] > 0 for c in cells)
    print(f"\ncellules NET positives : {pos}/{len(cells)}")
    if cells:
        b = max(cells, key=lambda c: c["net"])
        print(f"meilleure : k={b['k']} N={b['n']} ({b['days']:.1f} j) -> "
              f"brut {b['gross']:.2f}, net {b['net']:.2f} bps/jour, t={b['t']:.2f}")
    print("\nVERDICT : le brut est de l'ordre de quelques bps/jour, le cout de 23 a 26 bps")
    print("d'aller-retour. La famille ne franchit la porte que sur des detentions ou il")
    print("ne reste plus assez de blocs disjoints pour conclure.")
