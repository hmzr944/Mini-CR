#!/usr/bin/env python3
"""CROSS-VENUE — la derniere famille dont la forme pouvait convenir.

Acheter YES sur une venue et NO sur l'autre, quand la somme des deux prix
est inferieure a 1, garantit 1 $ a la resolution. Aucune prevision, aucun
risque directionnel : c'est la seule famille rencontree dont le gain ne
depend d'aucun modele.

DEUX ERREURS DE LECTURE A EVITER, ET ELLES VONT EN SENS CONTRAIRE.

1. LA FENETRE N'EST PAS LA DUREE DE DETENTION. La litterature decrit des
   fenetres d'entree de 2 a 7 secondes. C'est le temps pour SAISIR l'ecart,
   pas le temps pendant lequel le capital travaille. Les deux jambes sont
   financees jusqu'a la RESOLUTION de l'evenement. Le capital est donc
   immobilise des heures (sport) a des mois (politique). Lire « 3 % en 5
   secondes » est un contresens d'un facteur 10 000.

2. LE CAPITAL EST LA SOMME DES DEUX JAMBES. Un arbitrage a 0,45 + 0,52
   immobilise 0,97 $ pour recevoir 1,00 $. Rapporter le gain de 3 c a la
   seule jambe la moins chere doublerait le rendement affiche.

LES DEUX VENUES TAXENT LA MEME CHOSE, ET AU MEME ENDROIT.

    Polymarket : fee = C x rate x p x (1-p)      rate 0,04 a 0,07 selon
                                                 la categorie, 0 en
                                                 geopolitique
    Kalshi     : fee = ceil(0,07 x C x p x (1-p))   taker
                 fee = ceil(0,0175 x C x p x (1-p)) maker

Un arbitrage se fait a p sur une venue et a environ 1-p sur l'autre. Or
p(1-p) est SYMETRIQUE : les deux jambes paient le meme facteur. Les baremes
ne se compensent pas, ils s'additionnent, et ils sont maximaux a 50/50 —
c'est-a-dire exactement la ou les marches jumeaux sont les plus liquides et
ou les ecarts inter-venues sont les plus frequents.

Ce module ne mesure rien : il calcule ce que les baremes publies imposent.
C'est suffisant pour savoir si la famille merite une collecte.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.capital_efficiency import Objective

# Baremes taker publies. Source : docs.polymarket.com/trading/fees et
# kalshi.com/docs/kalshi-fee-schedule.pdf (juillet 2026).
POLYMARKET_TAKER_RATE: Dict[str, float] = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "tech": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "geopolitics": 0.0,
}
KALSHI_TAKER_RATE = 0.07
KALSHI_MAKER_RATE = 0.0175
POLYMARKET_MAKER_RATE = 0.0   # Polymarket ne facture pas le maker


@dataclass(frozen=True)
class Leg:
    """Une jambe de l'arbitrage."""
    venue: str
    price: float
    rate: float

    def fee_per_share(self) -> float:
        """Frais en dollars par part. Meme forme sur les deux venues."""
        return self.rate * self.price * (1.0 - self.price)


@dataclass(frozen=True)
class Arb:
    """Un arbitrage a deux jambes, tenu jusqu'a resolution."""
    leg_a: Leg
    leg_b: Leg
    days_to_resolution: float

    def capital_per_share(self) -> float:
        """Les DEUX jambes sont financees. C'est le denominateur honnete."""
        return self.leg_a.price + self.leg_b.price

    def gross_per_share(self) -> float:
        """1 $ recu a la resolution, moins ce qu'on a paye."""
        return 1.0 - self.capital_per_share()

    def fees_per_share(self) -> float:
        return self.leg_a.fee_per_share() + self.leg_b.fee_per_share()

    def net_per_share(self) -> float:
        return self.gross_per_share() - self.fees_per_share()

    def net_bps_on_capital(self) -> float:
        return self.net_per_share() / self.capital_per_share() * 10_000.0

    def bps_per_day(self) -> float:
        """LE chiffre. Le capital dort jusqu'a la resolution, pas 5 secondes."""
        if self.days_to_resolution <= 0:
            raise ValueError("days_to_resolution doit etre > 0")
        return self.net_bps_on_capital() / self.days_to_resolution

    def is_profitable(self) -> bool:
        return self.net_per_share() > 0.0


def symmetric_arb(price_a: float, gross_edge: float, category: str,
                  days: float, kalshi_maker: bool = False,
                  poly_maker: bool = False) -> Arb:
    """Construit l'arbitrage le plus favorable pour un ecart brut donne.

    `gross_edge` est l'ecart en dollars par part : la somme des deux prix
    vaut 1 - gross_edge. On place la jambe Polymarket a `price_a` et la
    jambe Kalshi au complement, ce qui est la configuration reelle : les
    deux venues cotent le meme evenement en sens oppose.
    """
    if not 0.0 < price_a < 1.0:
        raise ValueError("price_a hors (0,1)")
    price_b = 1.0 - gross_edge - price_a
    if not 0.0 < price_b < 1.0:
        raise ValueError("l'ecart demande rend la seconde jambe impossible")
    if category not in POLYMARKET_TAKER_RATE:
        raise ValueError(f"categorie inconnue : {category}")
    poly_rate = (POLYMARKET_MAKER_RATE if poly_maker
                 else POLYMARKET_TAKER_RATE[category])
    kalshi_rate = KALSHI_MAKER_RATE if kalshi_maker else KALSHI_TAKER_RATE
    return Arb(Leg("polymarket", price_a, poly_rate),
               Leg("kalshi", price_b, kalshi_rate), days)


def breakeven_edge(price_a: float, category: str,
                   kalshi_maker: bool = False,
                   poly_maker: bool = False) -> float:
    """Ecart brut, en dollars par part, ou l'arbitrage cesse de perdre.

    Resolu par bissection plutot qu'analytiquement : les frais dependent
    des deux prix, qui dependent eux-memes de l'ecart. Une forme fermee
    existerait, mais elle serait fausse le jour ou un bareme change de
    forme, et silencieusement.
    """
    lo, hi = 0.0, min(0.5, 1.0 - price_a - 1e-6)
    if symmetric_arb(price_a, hi, category, 1.0,
                     kalshi_maker, poly_maker).net_per_share() <= 0.0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        arb = symmetric_arb(price_a, mid, category, 1.0,
                            kalshi_maker, poly_maker)
        if arb.net_per_share() > 0.0:
            hi = mid
        else:
            lo = mid
    return hi


def required_edge_for_objective(price_a: float, category: str, days: float,
                                objective: Objective,
                                kalshi_maker: bool = False,
                                poly_maker: bool = False) -> float:
    """Ecart brut necessaire pour ATTEINDRE l'objectif, pas pour survivre.

    Renvoie NaN si aucun ecart admissible n'y parvient — ce qui est une
    reponse, pas une erreur.
    """
    target = objective.required_bps_per_day()
    lo, hi = 0.0, min(0.5, 1.0 - price_a - 1e-6)
    if symmetric_arb(price_a, hi, category, days,
                     kalshi_maker, poly_maker).bps_per_day() < target:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        arb = symmetric_arb(price_a, mid, category, days,
                            kalshi_maker, poly_maker)
        if arb.bps_per_day() >= target:
            hi = mid
        else:
            lo = mid
    return hi


# Ecarts bruts rapportes par la litterature commerciale, en dollars par
# part. 🟡 NON VERIFIE PAR MOI : aucun de ces chiffres n'est une mesure de
# ce depot. Ils servent de borne haute genereuse, pas de preuve.
REPORTED_EDGE_LOW = 0.015
REPORTED_EDGE_HIGH = 0.045


def scan(objective: Objective) -> dict:
    """Balaye les configurations plausibles contre les deux seuils."""
    rows = []
    scenarios = [
        ("sports, resolution en 1 jour", "sports", 1.0, False),
        ("sports, resolution en 1 jour, maker sur Kalshi", "sports", 1.0, True),
        ("politique, resolution en 30 jours", "politics", 30.0, False),
        ("politique, resolution en 180 jours", "politics", 180.0, False),
        ("geopolitique (0 % cote Polymarket), 30 jours",
         "geopolitics", 30.0, False),
    ]
    for label, cat, days, k_maker in scenarios:
        be = breakeven_edge(0.50, cat, kalshi_maker=k_maker)
        need = required_edge_for_objective(0.50, cat, days, objective,
                                           kalshi_maker=k_maker)
        high = symmetric_arb(0.50, REPORTED_EDGE_HIGH, cat, days,
                             kalshi_maker=k_maker)
        rows.append({
            "scenario": label,
            "ecart_seuil_rentabilite_c": round(be * 100.0, 3),
            "ecart_requis_objectif_c": (None if math.isnan(need)
                                        else round(need * 100.0, 3)),
            "atteignable_dans_ecarts_rapportes":
                (not math.isnan(need)) and need <= REPORTED_EDGE_HIGH,
            "au_meilleur_ecart_rapporte": {
                "ecart_brut_c": REPORTED_EDGE_HIGH * 100.0,
                "frais_c": round(high.fees_per_share() * 100.0, 3),
                "net_c": round(high.net_per_share() * 100.0, 3),
                "net_bps_capital": round(high.net_bps_on_capital(), 1),
                "bps_par_jour": round(high.bps_per_day(), 2),
                "rentable": high.is_profitable(),
            },
        })
    return {
        "seuil_bps_par_jour": round(objective.required_bps_per_day(), 2),
        "ecarts_rapportes_c": [REPORTED_EDGE_LOW * 100.0,
                               REPORTED_EDGE_HIGH * 100.0],
        "avertissement_ecarts": "non verifies par ce depot (litterature "
                                "commerciale) — bornes hautes genereuses",
        "scenarios": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--multiple", type=float, default=10.0)
    ap.add_argument("--jours", type=float, default=365.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    obj = Objective(1000.0, args.multiple, args.jours)
    rep = scan(obj)
    if args.json:
        print(json.dumps(rep, indent=1, ensure_ascii=False))
        return 0

    print(f"SEUIL {rep['seuil_bps_par_jour']:.2f} bps/jour  |  ecarts bruts "
          f"rapportes {rep['ecarts_rapportes_c'][0]:.1f} a "
          f"{rep['ecarts_rapportes_c'][1]:.1f} c/part (non verifies)\n")
    print(f"{'scenario':<46}{'seuil rent.':>12}{'requis obj.':>13}"
          f"{'net a 4,5c':>12}")
    print("-" * 83)
    for r in rep["scenarios"]:
        need = ("impossible" if r["ecart_requis_objectif_c"] is None
                else f"{r['ecart_requis_objectif_c']:.2f} c")
        print(f"{r['scenario'][:45]:<46}"
              f"{r['ecart_seuil_rentabilite_c']:>10.2f} c"
              f"{need:>13}"
              f"{r['au_meilleur_ecart_rapporte']['bps_par_jour']:>9.1f} b/j")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
