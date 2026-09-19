"""PORTE DE CAPITAL : quel capital un objectif en EUROS PAR JOUR exige-t-il ?

POURQUOI CE MODULE EXISTE. Le projet a change deux fois d'objectif — « x5 en
60 jours », puis « 20 EUR/jour » — et les deux ont ete compares a des
rendements exprimes en bps/jour de CAPITAL. La conversion n'avait jamais ete
ecrite. Or elle decide tout : 20 EUR/jour sur 1 000 EUR vaut 200 bps/jour,
tandis que les memes 20 EUR/jour sur 200 000 EUR valent 1 bps/jour. Le premier
chiffre est hors de portee de tout ce que ce projet a mesure ; le second est
un livret d'epargne.

Un objectif en euros n'est donc pas un objectif tant que le capital n'est pas
dit. Ce module l'impose, dans les deux sens :

    capital_requis(euros_par_jour, taux_bps_jour)
    taux_requis(euros_par_jour, capital)

CE QU'IL NE FAIT PAS. Il ne juge pas si un taux est atteignable — il le
confronte. Les taux passes en argument doivent etre MESURES, jamais esperes ;
`MEASURED_RATES` ne contient que des valeurs relevees dans ce depot, chacune
avec sa source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

BPS = 1e4


@dataclass(frozen=True)
class MeasuredRate:
    """Un rendement MESURE, avec sa provenance et son incertitude."""

    name: str
    bps_per_day: float
    source: str
    note: str = ""
    demonstrated: bool = True   # False = borne ou point non significatif

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("un taux sans source n'est pas une mesure")


#: Tout ce que ce depot a MESURE comme positif, en bps/jour de capital.
#: Rien d'espere, rien d'extrapole. Les entrees non demontrees sont marquees.
MEASURED_RATES: Dict[str, MeasuredRate] = {
    "pret_usdt_okx": MeasuredRate(
        "Pret USDT/USDC OKX", 0.96,
        "api/v5/finance/savings/lending-rate-summary", "3,50 %/an"),
    "pret_sol_okx": MeasuredRate(
        "Pret SOL OKX", 1.10, "api/v5/finance/savings/lending-rate-summary",
        "4,00 %/an"),
    "hlp_hyperliquid": MeasuredRate(
        "Vault HLP Hyperliquid", 1.08, "info/vaultDetails apr",
        "3,93 %/an ; tenue de marche du protocole, non garantie"),
    "polymarket_rewards_median": MeasuredRate(
        "Recompenses Polymarket, marche median", 4.0,
        "prism_v2/scans/pm_rewards.py",
        "fourchette 3 a 6 bps/jour hors choix ; SELECTIONNER les marches les "
        "mieux payes donne -234 a -3 039 bps/jour", demonstrated=False),
    "prediction_de_prix": MeasuredRate(
        "Toute prediction de prix testee", 0.0,
        "nc_gate.py, pm_favlong.py, et l'audit anterieur",
        "aucun candidat n'a survecu a Benjamini-Hochberg", demonstrated=False),
}


def required_capital(eur_per_day: float, bps_per_day: float) -> float:
    """Capital necessaire pour tirer `eur_per_day` d'un rendement donne."""
    if bps_per_day <= 0:
        return float("inf")
    return eur_per_day / (bps_per_day / BPS)


def required_rate_bps_day(eur_per_day: float, capital_eur: float) -> float:
    """Rendement necessaire, en bps/jour, pour un capital donne."""
    if capital_eur <= 0:
        return float("inf")
    return eur_per_day / capital_eur * BPS


def gap_factor(eur_per_day: float, capital_eur: float,
               bps_per_day: float) -> float:
    """De combien le meilleur taux mesure est-il trop petit ? (1 = atteint)"""
    need = required_rate_bps_day(eur_per_day, capital_eur)
    if bps_per_day <= 0:
        return float("inf")
    return need / bps_per_day


def best_demonstrated() -> Optional[MeasuredRate]:
    """Le meilleur taux REELLEMENT demontre, hors bornes et non-significatifs."""
    ok = [r for r in MEASURED_RATES.values() if r.demonstrated and r.bps_per_day > 0]
    return max(ok, key=lambda r: r.bps_per_day) if ok else None


def verdict(eur_per_day: float, capital_eur: float) -> dict:
    """Confronte un objectif en euros au meilleur rendement demontre."""
    need = required_rate_bps_day(eur_per_day, capital_eur)
    best = best_demonstrated()
    return {
        "eur_per_day": eur_per_day,
        "capital_eur": capital_eur,
        "required_bps_per_day": need,
        "best_demonstrated": best.name if best else None,
        "best_bps_per_day": best.bps_per_day if best else 0.0,
        "gap_factor": gap_factor(eur_per_day, capital_eur,
                                 best.bps_per_day if best else 0.0),
        "capital_needed_at_best": (
            required_capital(eur_per_day, best.bps_per_day) if best
            else float("inf")),
        "reachable": bool(best and best.bps_per_day >= need),
    }


# ── Decomposition par rotation ───────────────────────────────────────────────
# Un petit capital n'a qu'un seul levier contre un objectif en euros : la
# VITESSE. Le rendement quotidien se decompose en
#
#     bps/jour = edge_net_par_aller-retour (bps) x rotations par jour
#
# 200 bps/jour — ce que 20 EUR/jour exige de 1 000 EUR — s'atteint donc
# indifferemment par 20 allers-retours a 10 bps ou par 100 a 2 bps. C'est la
# seule facon de poser la question qui laisse une chance a 1 000 EUR, et elle
# rend la contrainte testable : il ne s'agit plus de trouver « un edge » mais
# un COUPLE (edge net, frequence).


def rate_from_turnover(edge_net_bps: float, trades_per_day: float) -> float:
    """Rendement quotidien sur capital, si tout le capital tourne a chaque fois."""
    return edge_net_bps * trades_per_day


def required_edge_bps(eur_per_day: float, capital_eur: float,
                      trades_per_day: float) -> float:
    """Edge NET par aller-retour requis, a frequence donnee."""
    if trades_per_day <= 0:
        return float("inf")
    return required_rate_bps_day(eur_per_day, capital_eur) / trades_per_day


def cost_ceiling_bps(gross_edge_bps: float, eur_per_day: float,
                     capital_eur: float, trades_per_day: float) -> float:
    """Cout d'aller-retour MAXIMAL tolerable, connaissant le brut mesure.

    C'est la forme la plus utile de la contrainte : le brut et la frequence
    sont MESURES, le cout est un parametre d'acces. Le nombre rendu dit
    exactement quel tarif rendrait la mecanique viable — et se compare
    directement aux baremes lus (OKX 5,0/2,0 ; Hyperliquid 4,5/1,5).
    """
    return gross_edge_bps - required_edge_bps(eur_per_day, capital_eur,
                                              trades_per_day)


#: Le couple (brut, frequence) le plus favorable jamais MESURE dans ce depot.
#: Perpetuels d'actions tokenisees OKX, barres de 5 min, seuil 2 sigma, tenue
#: 6 barres, execution decalee d'une barre, 38 instruments, 76 jours.
#: `prism_v2/scans/nc_gate.py` le recalcule.
BEST_MEASURED_GROSS_BPS = 2.63
BEST_MEASURED_TRADES_PER_DAY = 141.0


#: PIEGE A NE PAS TENDRE, ecrit ici parce que le rapprochement est tentant.
#: Le brut de 2,63 bps a ete mesure avec une execution TAKER — entree et
#: sortie a l'ouverture d'une barre, donc en traversant. Le rapprocher du cout
#: maker (3,00 bps chez Hyperliquid) suggere qu'il ne manque qu'un facteur 1,15.
#: C'est faux : passer en maker ne change pas seulement le tarif, il change la
#: REGLE DE REMPLISSAGE. Un ordre passif n'est execute que lorsque le prix
#: vient le chercher, c'est-a-dire lorsqu'il bouge contre lui — le brut
#: mesure en traversant ne survit pas au changement. Les deux nombres ne sont
#: pas comparables, et `taker_gross_vs_maker_cost_is_invalid` le rappelle a
#: tout appelant qui tenterait la soustraction.
TAKER_GROSS_NOT_COMPARABLE_TO_MAKER_COST = (
    "Le brut mesure en TAKER ne peut pas etre compare a un cout MAKER : "
    "l'execution passive change la regle de remplissage, pas seulement le "
    "tarif. Mesurer le brut sous remplissage passif avant toute comparaison."
)


def net_edge_bps(gross_bps: float, round_trip_cost_bps: float,
                 gross_measured_with: str, cost_style: str) -> float:
    """Edge net, avec refus explicite de melanger les styles d'execution.

    Soustraire un cout maker d'un brut mesure en taker est la faute que ce
    module existe pour empecher : elle fait apparaitre un mecanisme a portee
    de main alors que le brut n'a jamais ete mesure dans ces conditions.
    """
    if gross_measured_with != cost_style:
        raise ValueError(TAKER_GROSS_NOT_COMPARABLE_TO_MAKER_COST)
    return gross_bps - round_trip_cost_bps
