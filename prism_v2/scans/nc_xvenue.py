"""OKX contre Hyperliquid-xyz : le meme sous-jacent, deux carnets, un ecart.

POURQUOI CETTE PAIRE DE VENUES. Hyperliquid heberge un DEX deploye (« xyz »)
qui cote 123 marches d'actions, indices et matieres pour ~2,1 G$/jour, contre
~0,3 G$/jour pour les 45 equivalents OKX. Les deux venues cotent les MEMES
sous-jacents, et Hyperliquid pese 25 % de l'index OKX de ces instruments.
PRISM avait teste l'inter-venues sur des differentiels de FUNDING crypto ;
il n'avait jamais compare deux carnets d'actions tokenisees.

CE QUI EST MESURE. L'ecart EXECUTABLE, jamais un ecart de milieux :

    G_in  = (jambe_riche_BID - jambe_pauvre_ASK) / mid     a l'entree
    G_out = l'ecart executable dans le sens INVERSE        a la sortie
    net   = G_in + G_out - frais

Aucun prix milieu n'entre dans le calcul : un milieu n'est pas un prix auquel
on traite.

LES DEUX PIEGES, TRAITES AVANT LE CHIFFRE.
  DEVISE. OKX marge en USDT, Hyperliquid en USDC. USDT vaut ~0,9998 USD :
  omettre la conversion rend tout l'univers cher de ~2 bps sur OKX, du meme
  ordre de grandeur que l'ecart cherche. Le facteur est l'index OKX USDT-USD.
  SIMULTANEITE. Les memes instruments compares a treize heures d'ecart
  paraissaient distants de 4 a 5 % ; au meme instant, de quelques bps. La
  duree de chaque balayage est journalisee et les balayages trop lents sont
  rejetes.

FRAIS, LUS ET NON SUPPOSES.
  OKX          taker 5,0 bps   maker 2,0 bps   (bareme public, palier 0)
  Hyperliquid  taker 4,5 bps   maker 1,5 bps   (`userFees`, sans remise)
Un aller-retour inter-venues compte QUATRE executions.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from typing import Dict, List, Tuple

OKX_TAKER, OKX_MAKER = 5.0, 2.0
HL_TAKER, HL_MAKER = 4.5, 1.5

#: Un aller-retour inter-venues = 4 executions (2 a l'ouverture, 2 a la
#: fermeture). Les deux bornes encadrent tout ce qui est realisable.
ROUND_TRIP_TAKER = 2 * (OKX_TAKER + HL_TAKER)
ROUND_TRIP_MAKER = 2 * (OKX_MAKER + HL_MAKER)

MAX_SPAN_S = 12.0     # au-dela, les deux carnets ne sont plus simultanes


def load(path: str) -> List[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("span_s", 99) <= MAX_SPAN_S and d.get("r"):
                out.append(d)
    return out


def executable_gap(ob: float, oa: float, hb: float, ha: float,
                   peg: float) -> Tuple[float, float]:
    """(ecart en achetant OKX / vendant HL, ecart dans l'autre sens), en bps.

    Le prix OKX est en USDT ; on le ramene en USD par le peg avant toute
    comparaison avec Hyperliquid, qui marge en USDC.
    """
    oa_usd, ob_usd = oa * peg, ob * peg
    mid = 0.5 * (0.5 * (oa_usd + ob_usd) + 0.5 * (ha + hb))
    if mid <= 0:
        return 0.0, 0.0
    return (1e4 * (hb - oa_usd) / mid,      # acheter OKX, vendre HL
            1e4 * (ob_usd - ha) / mid)      # acheter HL, vendre OKX


def main() -> int:
    path = os.environ.get("PRISM_NC_XVENUE", "")
    rows = load(path)
    if len(rows) < 5:
        print(f"pas assez de balayages simultanes dans {path!r}")
        return 2
    span = st.median(r["span_s"] for r in rows)
    print(f"{len(rows)} balayages · duree mediane {span:.1f} s · "
          f"peg USDT/USD median {st.median(r['peg'] for r in rows):.5f}")
    print(f"aller-retour inter-venues : {ROUND_TRIP_TAKER:.1f} bps en taker, "
          f"{ROUND_TRIP_MAKER:.1f} bps en maker (4 executions)\n")

    per: Dict[str, List[float]] = {}
    for d in rows:
        for a, ob, oa, obs, oas, hb, ha, hbs, has_ in d["r"]:
            g1, g2 = executable_gap(ob, oa, hb, ha, d["peg"])
            per.setdefault(a, []).append(max(g1, g2))

    print(f"{'nom':7s} {'n':>4s} {'ecart med':>10s} {'p90':>7s} {'p99':>7s} "
          f"{'max':>7s} {'% > 19 bps':>11s} {'% > 7 bps':>10s}")
    allg: List[float] = []
    for a in sorted(per, key=lambda k: -st.median(per[k])):
        v = sorted(per[a])
        if len(v) < 5:
            continue
        allg.extend(v)
        n = len(v)
        print(f"{a:7s} {n:4d} {st.median(v):10.2f} {v[n*9//10]:7.2f} "
              f"{v[min(n-1, n*99//100)]:7.2f} {v[-1]:7.2f} "
              f"{sum(1 for x in v if x > ROUND_TRIP_TAKER)/n*100:11.1f} "
              f"{sum(1 for x in v if x > ROUND_TRIP_MAKER)/n*100:10.1f}")
    if allg:
        allg.sort()
        n = len(allg)
        print(f"\nTOUTES OBSERVATIONS n={n} · mediane {st.median(allg):.2f} bps · "
              f"p99 {allg[min(n-1, n*99//100)]:.2f} · max {allg[-1]:.2f}")
        print(f"part au-dessus de l'aller-retour TAKER ({ROUND_TRIP_TAKER:.0f} bps) : "
              f"{sum(1 for x in allg if x > ROUND_TRIP_TAKER)/n*100:.2f} %")
        print(f"part au-dessus de l'aller-retour MAKER ({ROUND_TRIP_MAKER:.0f} bps) : "
              f"{sum(1 for x in allg if x > ROUND_TRIP_MAKER)/n*100:.2f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ----------------------------------------------- mesure HISTORIQUE de l'ecart

def load_hist(raw_dir: str, bar: str = "5m"):
    """Rend (okx, hl, peg) alignes sur les memes horodatages."""
    from prism_v2.scans.nc_basis import load_raw
    perp, _index, peg = load_raw(raw_dir, bar)
    hl: Dict[str, Dict[int, list]] = {}
    for fn in sorted(os.listdir(raw_dir)):
        if fn.startswith("H_") and fn.endswith(f"_{bar}.json"):
            sym = fn[2:-len(f"_{bar}.json")]
            with open(os.path.join(raw_dir, fn)) as f:
                hl[sym] = {int(k): v for k, v in json.load(f).items()}
    return perp, hl, peg


def gap_series(okx_rows: Dict[int, list], hl_rows: Dict[int, list],
               peg: Dict[int, float]) -> Dict[int, float]:
    """Ecart OKX/Hyperliquid en bps, corrige du peg USDT/USD.

    OKX marge en USDT, Hyperliquid en USDC. Sans la conversion, tout l'univers
    parait cher de ~2 bps sur OKX — du meme ordre que l'ecart cherche.
    """
    out: Dict[int, float] = {}
    for t in sorted(set(okx_rows) & set(hl_rows) & set(peg)):
        po, ph, u = okx_rows[t][3], hl_rows[t][3], peg[t]
        if po <= 0 or ph <= 0 or u <= 0:
            continue
        if okx_rows[t][4] <= 0 or hl_rows[t][4] <= 0:
            continue            # une barre sans echange ne porte pas de prix
        import math as _m
        out[t] = 1e4 * _m.log(po * u / ph)
    return out
