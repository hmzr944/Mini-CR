"""BIAIS FAVORI/OUTSIDER sur Polymarket : qui paie, et combien reste-t-il ?

MECANIQUE ECONOMIQUE TESTEE, et pourquoi elle n'est pas une n-ieme famille de
signaux. On ne predit rien : on achete un contrat a p et on encaisse 1 si
l'evenement se produit. La question est de CALIBRATION — la part des marches
resolus OUI dans un seau de prix egale-t-elle le prix de ce seau ?

  Qui paie ?            L'acheteur d'outsider, qui paie un billet de loterie
                        au-dessus de sa valeur.
  Quelle contrainte ?   Un gout documente pour les gains rares et eleves. Le
                        symetrique mecanique est que le FAVORI est sous-cote.
  Pourquoi accessible ? La capacite par marche se compte en centaines de
                        dollars : trop peu pour un acteur institutionnel, pas
                        pour 1 000 EUR.
  Quel risque ?         Binaire et non couvrable : on perd 100 % de la mise
                        avec probabilite 1 - w. C'est le risque qui est
                        remunere, et il est mesurable.

L'AUDIT PRECEDENT A FERME LA MOITIE OUTSIDER (-19,3 c/$ a 1-3 c) SANS TESTER
L'AUTRE. C'est la difference structurelle qui justifie de rouvrir la venue.

DEUX DEFAUTS STATISTIQUES QUE CE MODULE EVITE, PARCE QU'ILS ONT DEJA FAUSSE
MES PROPRES MESURES DANS CE PROJET :

  1. L'ECART-TYPE SOUS L'HYPOTHESE NULLE. Utiliser l'ecart-type de la
     proportion OBSERVEE donne 0 quand tous les marches d'un seau resolvent
     dans le meme sens, et un t infini. Le test correct prend la variance
     sous H0 : sqrt(p(1-p)/n), ou p est le prix.
  2. LA CORRELATION PAR EVENEMENT. « Quel candidat gagne » est un seul
     evenement decoupe en dix marches dont un seul resout OUI. Les compter
     comme dix observations independantes divise l'erreur-type par trois.
     Une observation = UN evenement.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from prism_v2.scans.nc_basis import benjamini_hochberg, norm_sf

#: Seaux de prix. Fins dans la zone favorite, ou l'ecart attendu est petit et
#: ou toute l'economie se joue.
BUCKETS = ((0.50, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.90),
           (0.90, 0.94), (0.94, 0.97), (0.97, 0.99), (0.99, 1.00))


def price_at(hist: Sequence[dict], closed_ts: float, hours_before: float
             ) -> Optional[Tuple[float, float]]:
    """(prix, heures reelles avant resolution) `hours_before` avant la fin.

    Ancre sur la RESOLUTION, jamais sur le dernier point de l'historique :
    celui-ci marque l'arret des echanges et peut le preceder de plusieurs mois.
    """
    target = closed_ts - hours_before * 3600.0
    cand = [x for x in hist if x["t"] <= target]
    if not cand:
        return None
    last = cand[-1]
    return float(last["p"]), (closed_ts - last["t"]) / 3600.0


def cluster_in_bucket(rows: Sequence[dict]) -> List[dict]:
    """Une observation par EVENEMENT, **a l'interieur d'un seau de prix deja
    constitue**.

    L'ordre compte, et le prendre a l'envers casse la mesure. Regrouper AVANT
    de repartir en seaux moyenne le prix d'un evenement entier : un groupe
    « quel candidat gagne » cote 0,90 / 0,05 / 0,02 / 0,02 / 0,01 ressort a
    0,20, et plus aucune observation n'atteint la zone favorite. C'est ce qui
    avait vide tous les seaux au-dessus de 0,75.

    On repartit donc d'abord par prix, puis on regroupe par evenement les
    marches TOMBES DANS CE SEAU. La correlation residuelle — un seul marche
    d'un groupe resout OUI — est ainsi traitee sans deplacer les prix.
    """
    groups: Dict[str, List[dict]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(r.get("event") or f"__solo_{i}", []).append(r)
    return [{"p": st.fmean(x["p"] for x in g),
             "win": st.fmean(x["win"] for x in g),
             "n_markets": len(g), "event": k}
            for k, g in groups.items()]


def bucket_test(rows: Sequence[dict]) -> Optional[dict]:
    """Test de calibration d'un seau, variance sous H0."""
    n = len(rows)
    if n < 8:
        return None
    p = st.fmean(r["p"] for r in rows)
    w = st.fmean(r["win"] for r in rows)
    if not (0.0 < p < 1.0):
        return None
    se = math.sqrt(p * (1.0 - p) / n)
    t = (w - p) / se if se > 0 else 0.0
    return {"n": n, "p": p, "w": w, "edge_c": (w - p) * 100.0, "t": t,
            "ev": (w / p - 1.0), "p_val": 2.0 * norm_sf(abs(t))}


def taker_fee(price: float, fee_schedule: Optional[dict]) -> float:
    """Frais taker par part, en dollars. Lu du marche, jamais suppose.

    Polymarket facture proportionnellement au MOINDRE des deux cotes : un
    contrat a 0,97 porte un frais assis sur 0,03, pas sur 0,97. La difference
    est d'un facteur trente sur un favori, donc decisive ici.
    """
    if not fee_schedule or not isinstance(fee_schedule, dict):
        return 0.0
    try:
        rate = float(fee_schedule.get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return rate * min(price, 1.0 - price)


def economics(bucket: dict, hours: float, half_spread_c: float,
              fee_rate: float) -> dict:
    """Economie complete d'un seau : EV net, duree, rendement par jour.

    Le prix d'achat est l'ASK, jamais le milieu : on paie le demi-spread.
    """
    p_exec = bucket["p"] + half_spread_c / 100.0
    fee = fee_rate * min(p_exec, 1.0 - p_exec)
    cost = p_exec + fee
    if cost <= 0 or cost >= 1.0:
        # Un favori dont le prix executable atteint 1,00 ne peut plus rien
        # rapporter : on le dit, on ne le masque pas derriere un NaN muet.
        return {**bucket, "p_exec": p_exec, "fee": fee,
                "ev_net": float("nan"), "days": max(hours / 24.0, 1e-6),
                "bps_day": float("nan")}
    ev_net = bucket["w"] / cost - 1.0
    days = max(hours / 24.0, 1e-6)
    return {**bucket, "p_exec": p_exec, "fee": fee, "ev_net": ev_net,
            "days": days, "bps_day": ev_net / days * 1e4}


def required_n(p: float, edge_fraction: float = 0.5, t_target: float = 2.0
               ) -> float:
    """Nombre d'evenements requis pour etablir un ecart, a un prix donne.

    C'EST LA CONTRAINTE QUI DECIDE OU CETTE MECANIQUE EST TESTABLE. Le gain
    maximal d'un favori achete a p vaut (1 - p) : il s'annule quand p tend
    vers 1. Le bruit, lui, decroit seulement en sqrt(p(1-p)). Supposons que
    l'ecart reel soit une fraction f du maximum, w - p = f*(1 - p). Alors

        n = t^2 * p(1-p) / (f(1-p))^2 = t^2 * p / (f^2 * (1-p))

    Le denominateur s'effondre quand p -> 1 : a 0,99 il faut seize fois plus
    d'observations qu'a 0,85 pour un ecart proportionnellement identique.
    **La mecanique devient donc invérifiable exactement la ou elle parait la
    plus sure.** Toute conclusion tiree d'un seau a n faible et w = 1,000
    observe est une illusion d'echantillon, pas un avantage.
    """
    if not (0.0 < p < 1.0) or edge_fraction <= 0:
        return float("inf")
    return t_target ** 2 * p / (edge_fraction ** 2 * (1.0 - p))


def kelly_fraction(w: float, p: float) -> float:
    """Kelly pour l'achat d'un contrat binaire a p qui paie 1.

    Mise f de la banque : gain (1/p - 1) avec probabilite w, perte totale
    sinon. f* = w - (1 - w) * p / (1 - p). Negatif => ne pas jouer.
    """
    if not (0.0 < p < 1.0):
        return 0.0
    return w - (1.0 - w) * p / (1.0 - p)


def main() -> int:
    path = os.environ.get("PRISM_PM_RESOLVED", "")
    if not path or not os.path.exists(path):
        print("PRISM_PM_RESOLVED doit pointer sur la collecte de marches resolus")
        return 2
    D = json.load(open(path))
    hs = float(os.environ.get("PRISM_PM_HALFSPREAD_C", "0.5"))
    fee_rate = float(os.environ.get("PRISM_PM_FEERATE", "0.0"))
    span = (max(r["closed_ts"] for r in D) - min(r["closed_ts"] for r in D)) / 86400
    print(f"{len(D)} marches resolus · {span:.0f} jours couverts · "
          f"{len({r.get('event') for r in D})} evenements")
    print(f"demi-spread applique {hs:.2f} c · taux de frais {fee_rate:.3f}\n")

    all_cells = []
    for hb in (2.0, 12.0, 48.0, 168.0):
        rows = []
        for r in D:
            got = price_at(r["hist"], r["closed_ts"], hb)
            if not got:
                continue
            p, age = got
            if not (0.0 < p < 1.0):
                continue
            rows.append({"p": p, "win": r["win"], "event": r.get("event")})
        if len(rows) < 60:
            continue
        print(f"=== {hb:.0f} h avant resolution · {len(rows)} marches ===")
        print(f"{'seau':>12s} {'n mar':>6s} {'n ev':>5s} {'prix':>7s} {'% OUI':>7s} "
              f"{'ecart c':>8s} {'t':>6s} {'EV net':>8s} {'bps/j':>8s} {'Kelly':>7s}")
        for a, b in BUCKETS:
            raw_g = [r for r in rows if a <= r["p"] < b]
            g = cluster_in_bucket(raw_g)
            bt = bucket_test(g)
            if not bt:
                continue
            ec = economics(bt, hb, hs, fee_rate)
            k = kelly_fraction(bt["w"], ec["p_exec"])
            all_cells.append({**ec, "hb": hb, "bucket": (a, b)})
            print(f"[{a:.2f},{b:.2f}) {len(raw_g):6d} {bt['n']:5d} {bt['p']:7.3f} "
                  f"{bt['w']:7.3f} {bt['edge_c']:8.2f} {bt['t']:6.2f} "
                  f"{ec['ev_net']*100:7.2f}% {ec['bps_day']:8.0f} {k*100:6.1f}%")
        print()
    print("Puissance requise — evenements necessaires pour etablir a t=2 un")
    print("ecart valant la moitie du gain maximal, par zone de prix :")
    print(f"  {'prix':>6s} {'gain max':>9s} {'n requis':>9s}")
    for pz in (0.75, 0.85, 0.90, 0.95, 0.97, 0.99):
        print(f"  {pz:6.2f} {(1-pz)/pz*100:8.1f}% {required_n(pz):9.0f}")
    print()
    if not all_cells:
        print("aucune cellule exploitable")
        return 1
    keep = benjamini_hochberg([c["p_val"] for c in all_cells], 0.10)
    surv = [c for c, k in zip(all_cells, keep) if k and c["edge_c"] > 0]
    print(f"cellules testees {len(all_cells)} · survivants BH a ecart POSITIF : "
          f"{len(surv)}")
    for c in surv:
        print(f"  [{c['bucket'][0]:.2f},{c['bucket'][1]:.2f}) a {c['hb']:.0f} h : "
              f"ecart {c['edge_c']:+.2f} c · t={c['t']:.2f} · n={c['n']} · "
              f"{c['bps_day']:.0f} bps/jour")
    return 0 if surv else 1


if __name__ == "__main__":
    sys.exit(main())
