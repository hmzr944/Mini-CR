"""RECOMPENSES DE LIQUIDITE POLYMARKET : le revenu contre le risque d'inventaire.

POURQUOI CETTE PISTE EXISTE. Ce n'est pas une prediction de prix. La venue
PAIE un montant CONTRACTUEL et publie ses parametres : `rewardsDailyRate`,
`rewardsMinSize`, `rewardsMaxSpread`, et `feeSchedule.takerOnly = true` — le
maker n'y paie aucun frais. Le pool recense vaut ~50 000 USD/jour sur 496
marches. C'est la seule source de revenu identifiee dans ce projet qui ne
depende pas de deviner le prochain mouvement du prix.

POURQUOI LE PLAFOND DE L'AUDIT NE S'APPLIQUE PAS ICI. L'audit precedent avait
conclu « meme en captant 100 %, le revenu est borne a 912 500 USD/an, et un
operateur plus gros se dilue lui-meme ». C'est le plafond d'un GROS operateur.
Le pool etant fixe et partage AU PRORATA, un petit capital est dans la
situation inverse : sa part est grande la ou personne ne cote.

LA FORMULE, APPLIQUEE TELLE QUE PUBLIEE. Un ordre de taille v a une distance
s du milieu marque

    S(v, s) = v * ((maxSpread - s) / maxSpread)^2      si s <= maxSpread

et le score d'un participant est min(S_bid, S_ask) : la cotation doit etre
BILATERALE. La part de chacun est son score sur la somme des scores.

CE QUE LA FORMULE IMPOSE, ET QUI EST TOUTE L'ECONOMIE. Le poids decroit en
carre avec la distance au milieu. Coter loin du milieu est sur mais ne marque
presque rien ; coter au milieu marque le maximum mais se fait ramasser par
tout flux informe. Le revenu et le risque sont donc gouvernes par le MEME
parametre `s`, en sens opposes. Ce module balaie `s` et lit l'economie nette.

LE COUT, MESURE ET NON SUPPOSE. Un ordre passif n'est rempli que lorsque le
prix vient le chercher — c'est-a-dire lorsqu'il bouge CONTRE lui. La
simulation applique exactement cette regle sur les trajectoires reelles :
remplissage des que le prix traverse la cotation, inventaire accumule,
valorisation finale au dernier prix. Aucune probabilite de remplissage n'est
supposee favorable.

UNITE DE COMPTE : le DOLLAR PAR PART. Un contrat binaire regle a 0 ou 1 ; un
« bps » n'y a pas de sens stable, le meme mouvement de 0,01 $ valant 1 % du
notionnel a p = 0,5 et 20 % a p = 0,05. Tout est ramene au capital immobilise.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence, Tuple


def book_score(levels: Sequence[dict], mid: float, max_spread_c: float,
               side: str) -> float:
    """Score total d'un cote du carnet, formule Polymarket."""
    tot = 0.0
    for lv in levels:
        try:
            px, sz = float(lv["price"]), float(lv["size"])
        except (KeyError, TypeError, ValueError):
            continue
        s = (mid - px) * 100.0 if side == "bid" else (px - mid) * 100.0
        if s < 0 or s > max_spread_c:
            continue
        tot += sz * ((max_spread_c - s) / max_spread_c) ** 2
    return tot


def my_score(v: float, s_c: float, max_spread_c: float) -> float:
    if s_c > max_spread_c or max_spread_c <= 0:
        return 0.0
    return v * ((max_spread_c - s_c) / max_spread_c) ** 2


def simulate(hist: Sequence[dict], s_c: float, v: float,
             inv_cap: Optional[float] = None) -> Tuple[float, float, float, int]:
    """Cotation bilaterale passive sur une trajectoire reelle.

    Rend (PnL d'inventaire en USD, inventaire final en parts,
          |inventaire| maximal, nombre de remplissages).

    REGLE DE REMPLISSAGE. Un ordre passif n'est touche que si le prix vient a
    lui. On remplit donc des que le prix traverse la cotation — jamais avant,
    jamais a un prix meilleur. C'est la seule regle qui ne suppose rien de
    favorable : elle fait payer l'integralite de la selection adverse.
    """
    d = s_c / 100.0
    inv = 0.0
    cash = 0.0
    fills = 0
    peak = 0.0
    prices = [float(h["p"]) for h in hist if h.get("p") is not None]
    if len(prices) < 10:
        return 0.0, 0.0, 0.0, 0
    for px in prices[1:]:
        bid, ask = prices[0] - d, prices[0] + d
        # le prix descend jusqu'a notre achat
        if px <= bid and (inv_cap is None or inv < inv_cap):
            inv += v
            cash -= v * bid
            fills += 1
        # le prix monte jusqu'a notre vente
        elif px >= ask and (inv_cap is None or inv > -inv_cap):
            inv -= v
            cash += v * ask
            fills += 1
        peak = max(peak, abs(inv))
        prices[0] = px            # on recote autour du nouveau milieu
    return cash + inv * prices[0], inv, peak, fills


def market_economics(m: dict, s_c: float, capital: float,
                     inv_cap_mult: float = 3.0) -> Optional[dict]:
    """Economie COMPLETE d'un marche : revenu, cout, capital, risque.

    SIZING, ET POURQUOI IL DOIT ETRE UNIQUE. Le score se calcule sur la taille
    COTEE et le risque se subit sur la taille REMPLIE : ce sont la meme
    grandeur, et les separer fabrique un revenu sans le cout qui va avec.
    Une premiere version de ce module cotait 1 000 parts et n'en remplissait
    que 20 ; l'inventaire en ressortait cinquante fois trop petit et le net
    paraissait positif partout.

    CONTRAINTE DE CAPITAL. Coter v parts des deux cotes immobilise v dollars
    (v*p sur le YES plus v*(1-p) sur le NO). Porter jusqu'a `inv_cap_mult`
    fois v en inventaire en exige autant de plus. La taille cotee est donc
    v = capital / (1 + inv_cap_mult), et non le capital entier.
    """
    try:
        msp = float(m.get("maxSpread") or 0)
        mnsz = float(m.get("minSize") or 0)
        rate = float(m.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    if msp <= 0 or rate <= 0 or not m.get("bids") or not m.get("asks"):
        return None
    best_bid = max(float(b["price"]) for b in m["bids"])
    best_ask = min(float(a["price"]) for a in m["asks"])
    mid = (best_bid + best_ask) / 2.0
    if not (0.0 < mid < 1.0):
        return None

    v = capital / (1.0 + inv_cap_mult)
    if v < mnsz:
        return None                       # taille minimale non atteinte

    qc = min(book_score(m["bids"], mid, msp, "bid"),
             book_score(m["asks"], mid, msp, "ask"))
    qm = my_score(v, s_c, msp)
    if qm <= 0:
        return None
    reward = rate * qm / (qm + qc)

    pnl, inv, peak, fills = simulate(m["hist"], s_c, v,
                                     inv_cap=inv_cap_mult * v)
    return {"q": m.get("q"), "rate": rate, "maxSpread": msp, "minSize": mnsz,
            "mid": mid, "qc": qc, "qm": qm, "share": qm / (qm + qc),
            "reward": reward, "pnl": pnl, "fills": fills, "peak": peak,
            "net": reward + pnl, "end": m.get("end"),
            "vol24": float(m.get("vol24") or 0)}


def main() -> int:
    path = os.environ.get("PRISM_PM_DATA", "")
    if not path or not os.path.exists(path):
        print("PRISM_PM_DATA doit pointer sur la collecte pm_data.json")
        return 2
    data = json.load(open(path))
    CAP = float(os.environ.get("PRISM_PM_CAPITAL", "1000"))
    print(f"{len(data)} marches recompenses · pool "
          f"{sum(float(m.get('rate') or 0) for m in data):,.0f} USD/jour · "
          f"capital {CAP:,.0f} USD\n")

    print("Balayage de la distance de cotation `s` — le revenu et le risque")
    print("sont gouvernes par le MEME parametre, en sens opposes.\n")
    print(f"{'s (c)':>6s} {'marches':>8s} {'recompense':>11s} {'PnL invent.':>12s} "
          f"{'NET $/j':>9s} {'bps/j':>8s} {'remplis.':>9s} {'% marches >0':>13s}")
    for s_c in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
        rows = [r for r in (market_economics(m, s_c, CAP) for m in data) if r]
        if not rows:
            continue
        _ = rows
        # Le capital est UNIQUE : on ne peut pas etre sur tous les marches.
        # On lit donc l'economie du MEILLEUR marche accessible, puis la
        # distribution, jamais la somme.
        best = max(rows, key=lambda r: r["net"])
        med_r = st.median([r["reward"] for r in rows])
        med_p = st.median([r["pnl"] for r in rows])
        med_n = st.median([r["net"] for r in rows])
        pos = sum(1 for r in rows if r["net"] > 0) / len(rows) * 100
        print(f"{s_c:6.1f} {len(rows):8d} {med_r:11.2f} {med_p:12.2f} "
              f"{med_n:9.2f} {med_n/CAP*1e4:8.0f} "
              f"{st.median([r['fills'] for r in rows]):9.0f} {pos:12.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
