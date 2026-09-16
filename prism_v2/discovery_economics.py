"""Economie en mode DISCOVERY — raisonner sous incertitude sans la nier.

Probleme resolu : un cout UNKNOWN rendait toute evaluation UNRESOLVED, ce qui
bloquait la RECHERCHE en plus de l'execution. Mais chercher ne demande pas la
meme preuve qu'engager du capital.

METHODE : au lieu de remplacer un UNKNOWN par zero (interdit) ou d'abandonner
(sterile), on l'ENCADRE par deux bornes explicites, chacune justifiee :

    borne OPTIMISTE   : le cout inconnu vaut 0        -> net MAXIMAL concevable
    borne PESSIMISTE  : le cout inconnu vaut une borne superieure documentee,
                        derivee de donnees observees quand c'est possible
                                                      -> net MINIMAL concevable

Le resultat n'est JAMAIS un point : c'est un intervalle, et un verdict :

    DEAD_EVEN_AT_BEST   net optimiste <= 0
                        Meme en supposant tous les couts inconnus NULS, la
                        capture ne couvre pas les couts CONNUS. Conclusion
                        forte et definitive : inutile de mesurer davantage.

    SURVIVES_ALL_BOUNDS net pessimiste > 0
                        Survit meme au pire cas envisage. Candidate serieuse
                        a valider causalement. Ce n'est PAS une preuve de
                        rentabilite : les bornes restent des hypotheses.

    NEEDS_MEASUREMENT   l'intervalle contient zero
                        La reponse depend d'une grandeur non mesuree. Le
                        resultat NOMME laquelle et de combien elle doit etre
                        inferieure pour que la capture survive. C'est ce qui
                        transforme "je ne sais pas" en plan de mesure.

Aucun de ces verdicts n'autorise une execution. Seul EvaluationMode.EXECUTION
le fait, et il exige des couts OBSERVED.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Quality
from .costs import CostBreakdown, CostComponent
from .modes import EvaluationMode
from .opportunity import Candidate
from .orderbook import OrderBook


class DiscoveryVerdict(str, enum.Enum):
    DEAD_EVEN_AT_BEST = "DEAD_EVEN_AT_BEST"
    SURVIVES_ALL_BOUNDS = "SURVIVES_ALL_BOUNDS"
    NEEDS_MEASUREMENT = "NEEDS_MEASUREMENT"
    NO_RAW_EDGE = "NO_RAW_EDGE"

    @property
    def worth_pursuing(self) -> bool:
        return self in (DiscoveryVerdict.SURVIVES_ALL_BOUNDS,
                        DiscoveryVerdict.NEEDS_MEASUREMENT)


@dataclass(frozen=True)
class CostBound:
    """Borne superieure documentee d'un cout inconnu.

    `value_bps` n'est PAS une estimation : c'est un majorant. Le confondre
    avec une mesure serait exactement la faute que ce module evite.
    """

    name: str
    value_bps: float
    basis: str            # d'ou vient le majorant
    derived_from_observed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "upper_bound_bps": self.value_bps,
                "basis": self.basis,
                "derived_from_observed": self.derived_from_observed}


#: Majorant des frais, a defaut de lire le tier reel du compte. Bareme public
#: OKX niveau de base : 5 bps taker par jambe. C'est le tier LE PLUS CHER des
#: tiers standards, donc un majorant legitime pour un compte quelconque.
FEE_UPPER_BOUND_BPS_PER_LEG = 5.0

#: Majorant du slippage d'execution, EXPRIME EN MULTIPLES DU SPREAD OBSERVE.
#: Justification : au-dela d'un spread complet de degradation par jambe, un
#: ordre marche aurait consomme plusieurs niveaux, ce que l'impact mesure
#: deja separement. Ce majorant est donc derive d'une donnee observee.
SLIPPAGE_UPPER_BOUND_SPREADS_PER_LEG = 1.0

#: Majorant de la decroissance par latence, en multiples du spread observe.
#: Meme raisonnement : au-dela, le prix a bouge d'un spread entier pendant le
#: trajet de l'ordre, ce qui serait un evenement de volatilite, pas de latence.
LATENCY_UPPER_BOUND_SPREADS = 1.0

#: Majorant de l'adverse selection pour un MAKER, en multiples du spread.
#: Un maker rempli dans un marche qui continue perd typiquement le mouvement
#: qui l'a rempli. Un spread complet est un majorant conservateur.
ADVERSE_SELECTION_UPPER_BOUND_SPREADS = 1.0


def bound_for(name: str, book: Optional[OrderBook], legs: int = 2) -> CostBound:
    """Majorant documente d'une composante inconnue.

    Quand un carnet est disponible, les majorants sont DERIVES du spread
    observe — ils ne sortent pas de nulle part.
    """
    spread_bps = None
    if book is not None:
        try:
            spread_bps = book.spread_bps
        except Exception:
            spread_bps = None

    if name == "fees":
        return CostBound("fees", FEE_UPPER_BOUND_BPS_PER_LEG * legs,
                         f"bareme public OKX Lv1 taker {FEE_UPPER_BOUND_BPS_PER_LEG}"
                         f"bps x {legs} jambes (tier le plus cher des standards)",
                         False)
    if spread_bps is None:
        # Sans carnet, aucun majorant honnete n'est derivable.
        return CostBound(name, float("inf"),
                         "aucun carnet observe — majorant non derivable", False)
    if name == "slippage":
        return CostBound("slippage",
                         SLIPPAGE_UPPER_BOUND_SPREADS_PER_LEG * spread_bps * legs,
                         f"{SLIPPAGE_UPPER_BOUND_SPREADS_PER_LEG} spread observe "
                         f"({spread_bps:.4f}bps) x {legs} jambes", True)
    if name == "latency":
        return CostBound("latency", LATENCY_UPPER_BOUND_SPREADS * spread_bps,
                         f"{LATENCY_UPPER_BOUND_SPREADS} spread observe "
                         f"({spread_bps:.4f}bps)", True)
    if name == "adverse_selection":
        return CostBound("adverse_selection",
                         ADVERSE_SELECTION_UPPER_BOUND_SPREADS * spread_bps,
                         f"{ADVERSE_SELECTION_UPPER_BOUND_SPREADS} spread observe "
                         f"({spread_bps:.4f}bps)", True)
    if name == "funding":
        return CostBound("funding", 0.0,
                         "horizon court : aucun reglement de funding traverse", False)
    return CostBound(name, float("inf"), "majorant non defini", False)


@dataclass
class DiscoveryResult:
    verdict: DiscoveryVerdict
    gross_capture_bps: float
    known_cost_bps: float
    net_optimistic_bps: float          # tous les inconnus = 0
    net_pessimistic_bps: float         # tous les inconnus = leur majorant
    unknown_components: List[str]
    bounds: List[CostBound]
    #: Pour NEEDS_MEASUREMENT : combien le total des inconnus doit rester
    #: sous ce seuil pour que la capture survive. C'est le PLAN DE MESURE.
    unknown_budget_bps: Optional[float]
    dominant_known_cost: Optional[str]
    detail: str

    @property
    def is_conclusive(self) -> bool:
        return self.verdict in (DiscoveryVerdict.DEAD_EVEN_AT_BEST,
                                DiscoveryVerdict.NO_RAW_EDGE)

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict.value,
                "gross_capture_bps": self.gross_capture_bps,
                "known_cost_bps": self.known_cost_bps,
                "net_optimistic_bps": self.net_optimistic_bps,
                "net_pessimistic_bps": self.net_pessimistic_bps,
                "unknown_components": self.unknown_components,
                "bounds": [b.to_dict() for b in self.bounds],
                "unknown_budget_bps": self.unknown_budget_bps,
                "dominant_known_cost": self.dominant_known_cost,
                "detail": self.detail}


def discover(candidate: Candidate, costs: CostBreakdown,
             book: Optional[OrderBook] = None, legs: int = 2) -> DiscoveryResult:
    """Evalue une candidate en mode DISCOVERY, par encadrement.

    N'autorise aucune execution. Ne produit jamais un point estime a partir
    d'un inconnu. Sert a savoir OU chercher, pas a decider de trader.
    """
    gross = candidate.gross_capture_bps
    by_name = costs.by_name()
    known = sum(c.value_bps for c in costs.components() if c.value_bps is not None)
    unknown_names = [c.name for c in costs.components() if not c.is_known]

    known_only = [(c.name, c.value_bps) for c in costs.components()
                  if c.value_bps is not None and c.value_bps > 0]
    dominant = max(known_only, key=lambda p: p[1])[0] if known_only else None

    if gross <= 0:
        return DiscoveryResult(
            DiscoveryVerdict.NO_RAW_EDGE, gross, known, gross - known, gross - known,
            unknown_names, [], None, dominant,
            "aucune amplitude brute : rien a capturer avant meme les couts")

    bounds = [bound_for(n, book, legs) for n in unknown_names]
    bound_total = sum(b.value_bps for b in bounds)

    net_opt = gross - known                      # inconnus supposes nuls
    net_pess = gross - known - bound_total       # inconnus a leur majorant

    if net_opt <= 0:
        return DiscoveryResult(
            DiscoveryVerdict.DEAD_EVEN_AT_BEST, gross, known, net_opt, net_pess,
            unknown_names, bounds, None, dominant,
            f"meme avec tous les couts inconnus a ZERO, le net vaut "
            f"{net_opt:.4f} bps. Les couts CONNUS ({known:.4f} bps) suffisent a "
            f"detruire la capture. Mesurer davantage est inutile."
            + (f" Poste dominant: {dominant}." if dominant else ""))

    if net_pess > 0:
        return DiscoveryResult(
            DiscoveryVerdict.SURVIVES_ALL_BOUNDS, gross, known, net_opt, net_pess,
            unknown_names, bounds, None, dominant,
            f"survit meme au pire cas envisage ({net_pess:.4f} bps). "
            "A valider causalement — les bornes restent des hypotheses, "
            "ce n'est pas une preuve de rentabilite.")

    budget = gross - known
    return DiscoveryResult(
        DiscoveryVerdict.NEEDS_MEASUREMENT, gross, known, net_opt, net_pess,
        unknown_names, bounds, budget, dominant,
        f"la reponse depend de {unknown_names}. Pour survivre, leur total doit "
        f"rester sous {budget:.4f} bps (majorant actuel: {bound_total:.4f} bps). "
        "C'est la mesure a prioriser.")
