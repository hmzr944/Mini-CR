"""Modele de cout typé. Aucune hypothese heritee n'est vraie par defaut.

Chaque composante porte sa qualite :
    OBSERVED : mesure directe sur une donnee de marche reelle et horodatee
    DERIVED  : calculee deterministiquement depuis de l'OBSERVED
    ASSUMED  : posee par hypothese documentee, NON verifiee sur ce compte
    UNKNOWN  : non mesuree -> ne devient JAMAIS zero

DECISION STRUCTURANTE (corrigee apres le Prompt 2) : le slippage d'execution
n'est PAS zero en PAPER. La friction du carnet est deja comptee dans `impact`.
Le `slippage` designe ce qui reste — position dans la file, derive entre la
decision et l'arrivee de l'ordre, remplissage partiel non anticipe — et il ne
peut etre mesure que par des fills reels. Il reste donc UNKNOWN tant qu'aucun
fill reel n'existe. Consequence assumee : en posture stricte, toute evaluation
est UNRESOLVED. C'est la verite, pas un defaut.

Le forfait v33 de 28 bps n'existe que comme LEGACY_ASSUMPTION, jamais comme
defaut, et il est exclu de tout calcul sauf demande explicite.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Quality
from .instruments import InstrumentSpec
from .orderbook import OrderBook


class ExecutionStyle(str, enum.Enum):
    """Le style d'execution change la structure de cout, pas seulement son niveau."""

    TAKER = "TAKER"       # traverse le spread, fill quasi certain
    MAKER = "MAKER"       # poste, frais moindres, mais file d'attente ET adverse selection
    PASSIVE = "PASSIVE"   # observe sans engager de capital

    @property
    def crosses_spread(self) -> bool:
        return self is ExecutionStyle.TAKER


#: Composantes exigees pour qu'une economie soit resolue, PAR STYLE.
#: `adverse_selection` n'est essentielle qu'en MAKER : c'est precisement la
#: ou un fill peut etre mauvais *parce qu'il a eu lieu*. Supposer qu'un maker
#: est meilleur parce que ses frais sont plus bas est une erreur classique.
ESSENTIAL_BY_STYLE: Dict[ExecutionStyle, tuple] = {
    ExecutionStyle.TAKER: ("fees", "spread", "impact", "slippage", "latency"),
    ExecutionStyle.MAKER: ("fees", "impact", "slippage", "latency", "adverse_selection"),
    ExecutionStyle.PASSIVE: (),
}

#: Defaut historique conserve pour compatibilite des appels sans style.
ESSENTIAL_COMPONENTS = ESSENTIAL_BY_STYLE[ExecutionStyle.TAKER]


@dataclass(frozen=True)
class CostComponent:
    """Une composante de cout, en bps du notionnel USD d'entree, avec provenance.

    Invariant : quality=UNKNOWN <=> value_bps is None. Impossible de porter une
    valeur numerique sans revendiquer d'ou elle vient.
    """

    name: str
    value_bps: Optional[float]
    quality: Quality
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.quality is Quality.UNKNOWN and self.value_bps is not None:
            raise ValueError(f"{self.name}: UNKNOWN ne peut pas porter une valeur")
        if self.quality is not Quality.UNKNOWN and self.value_bps is None:
            raise ValueError(f"{self.name}: {self.quality.value} exige une valeur")
        if self.value_bps is not None and self.value_bps < 0:
            raise ValueError(f"{self.name}: cout negatif ({self.value_bps}) — "
                             "un rebate doit etre modelise explicitement, pas par un signe")

    @property
    def is_known(self) -> bool:
        return self.quality is not Quality.UNKNOWN

    @classmethod
    def unknown(cls, name: str, source: str, note: str = "") -> "CostComponent":
        return cls(name=name, value_bps=None, quality=Quality.UNKNOWN,
                   source=source, note=note)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "value_bps": self.value_bps,
                "quality": self.quality.value, "source": self.source, "note": self.note}


@dataclass
class CostBreakdown:
    """Somme typee des couts d'un aller-retour, en bps du notionnel d'entree."""

    fees: CostComponent
    spread: CostComponent
    slippage: CostComponent
    impact: CostComponent
    funding: CostComponent
    latency: CostComponent = field(
        default_factory=lambda: CostComponent.unknown(
            "latency", "non mesure",
            "exige un replay d'evenements horodates (prism_v2/replay.py)"))
    adverse_selection: CostComponent = field(
        default_factory=lambda: CostComponent.unknown(
            "adverse_selection", "non mesure",
            "exige des fills maker reels et leur derive post-fill"))
    style: ExecutionStyle = ExecutionStyle.TAKER
    other: List[CostComponent] = field(default_factory=list)

    def components(self) -> List[CostComponent]:
        return [self.fees, self.spread, self.slippage, self.impact, self.funding,
                self.latency, self.adverse_selection] + list(self.other)

    def by_name(self) -> Dict[str, CostComponent]:
        return {c.name: c for c in self.components()}

    def essential_names(self) -> tuple:
        return ESSENTIAL_BY_STYLE[self.style]

    def unknown_components(self) -> List[str]:
        return [c.name for c in self.components() if not c.is_known]

    def unresolved_essentials(self) -> List[str]:
        """UNKNOWN parmi les composantes sans lesquelles aucune conclusion
        economique n'est possible, pour le style d'execution retenu."""
        names = self.by_name()
        return [n for n in self.essential_names()
                if n in names and not names[n].is_known]

    def weakest_quality(self) -> Quality:
        order = [Quality.OBSERVED, Quality.DERIVED, Quality.ASSUMED, Quality.UNKNOWN]
        return max((c.quality for c in self.components()), key=order.index)

    def total_bps(self) -> Optional[float]:
        """Somme des couts. None si une composante ESSENTIELLE est UNKNOWN.

        On ne somme jamais en ignorant un trou : c'est le piege exact qui
        transforme un edge brut en faux PnL.
        """
        if self.unresolved_essentials():
            return None
        return sum(c.value_bps for c in self.components() if c.value_bps is not None)

    def to_dict(self) -> Dict[str, Any]:
        return {"style": self.style.value,
                "components": [c.to_dict() for c in self.components()],
                "total_bps": self.total_bps(),
                "unknown": self.unknown_components(),
                "unresolved_essentials": self.unresolved_essentials(),
                "weakest_quality": self.weakest_quality().value}


# ── Hypothese heritee : citee, jamais appliquee par defaut ────────────────────
LEGACY_ASSUMPTION_V33_BPS = 28.0
LEGACY_ASSUMPTION_NOTE = (
    "v33: COMMISSION 0.001*2 (20bps) + SLIPPAGE 0.0005 (5bps) + EXIT_SLIPPAGE "
    "0.0003 (3bps) = 28bps forfaitaires. Jamais confronte a un fill reel. "
    "Fourni pour comparaison historique uniquement.")


def legacy_v33_assumption() -> CostComponent:
    return CostComponent(name="legacy_v33_roundtrip", value_bps=LEGACY_ASSUMPTION_V33_BPS,
                         quality=Quality.ASSUMED, source="backtest_v33.py:57-59",
                         note=LEGACY_ASSUMPTION_NOTE)


# ── Frais ────────────────────────────────────────────────────────────────────
OKX_PUBLIC_TAKER_BPS = 5.0
OKX_PUBLIC_MAKER_BPS = 2.0
_FEE_SOURCE = "OKX bareme public perpetuels, niveau de base (non authentifie)"
_FEE_NOTE = ("ASSUMED : le tier reel du compte n'est pas verifiable sans cle API. "
             "Passer a OBSERVED exige /api/v5/account/trade-fee.")


def fees_unknown() -> CostComponent:
    return CostComponent.unknown(
        "fees", source="non mesure",
        note="exige /api/v5/account/trade-fee (authentifie) — absent de V2")


def fees_assumed_public(taker_legs: int = 2, maker_legs: int = 0) -> CostComponent:
    if taker_legs < 0 or maker_legs < 0:
        raise ValueError("nombre de jambes negatif")
    value = taker_legs * OKX_PUBLIC_TAKER_BPS + maker_legs * OKX_PUBLIC_MAKER_BPS
    return CostComponent(name="fees", value_bps=value, quality=Quality.ASSUMED,
                         source=_FEE_SOURCE,
                         note=f"{_FEE_NOTE} (taker_legs={taker_legs}, maker_legs={maker_legs})")


# ── Couts derives du carnet observe ──────────────────────────────────────────
def spread_from_book(book: OrderBook, side: str, legs: int = 2) -> CostComponent:
    """Cout de traversee du spread (mid -> touch), derive du carnet observe."""
    per_leg = book.crossing_cost_bps(side)
    note = f"{per_leg:.4f}bps/jambe x {legs}; seqId={book.seq_id}"
    if legs > 1:
        note += (" — ATTENTION: la jambe de sortie est extrapolee depuis le "
                 "carnet d'entree, le spread de sortie n'est pas observe")
    return CostComponent(name="spread", value_bps=per_leg * legs, quality=Quality.DERIVED,
                         source=f"{book.provenance.endpoint} @ {book.ts_utc}", note=note)


def spread_not_applicable_maker() -> CostComponent:
    """Un maker ne traverse pas le spread — il le gagne, s'il est rempli.

    Le cout correspondant se deplace vers adverse_selection et slippage
    (non-fill, file d'attente), qui restent UNKNOWN sans fills reels.
    """
    return CostComponent(name="spread", value_bps=0.0, quality=Quality.DERIVED,
                         source="style MAKER",
                         note="le maker ne traverse pas le spread ; le risque se "
                              "reporte sur adverse_selection et slippage")


def impact_from_book(book: OrderBook, side: str, notional_usd: float,
                     legs: int = 2) -> CostComponent:
    """Impact de marche au-dela du touch, derive du carnet observe.

    UNKNOWN si le carnet ne peut pas absorber le notionnel : on ne postule
    jamais de liquidite au-dela de ce qui est affiche.
    """
    walk = book.walk(side, notional_usd)
    if walk.vwap is None or walk.exhausted:
        return CostComponent.unknown(
            "impact", source=f"{book.provenance.endpoint} @ {book.ts_utc}",
            note=(f"profondeur insuffisante pour ${notional_usd:,.0f} "
                  f"(rempli {walk.fill_ratio:.1%}) — impact non mesurable"))
    per_leg = book.slippage_vs_touch_bps(side, notional_usd) or 0.0
    return CostComponent(name="impact", value_bps=per_leg * legs, quality=Quality.DERIVED,
                         source=f"{book.provenance.endpoint} @ {book.ts_utc}",
                         note=f"{per_leg:.4f}bps/jambe sur {walk.levels_consumed} niveaux x {legs}")


def slippage_unknown() -> CostComponent:
    """Slippage d'execution : ce qui reste APRES l'impact de carnet.

    File d'attente, derive entre decision et arrivee, remplissage partiel non
    anticipe. Mesurable uniquement sur fills reels horodates. Aucun trade reel
    n'a jamais eu lieu dans ce projet : reste UNKNOWN.

    N'EXISTE PLUS : une variante "zero en PAPER". Un fill simule sur le carnet
    du meme instant ne mesure pas cette friction, il la contourne.
    """
    return CostComponent.unknown(
        "slippage", source="non mesure",
        note="exige des fills reels horodates (expected_px vs actual_fill_px). "
             "La simulation PAPER ne la mesure pas : elle l'ignore.")


def slippage_excluded_for_paper_validation() -> CostComponent:
    """EXCLUSION EXPLICITE du slippage, reservee a CAPTURE_VALIDATION.

    Ce n'est PAS une mesure et ce n'est PAS "slippage = 0". C'est la
    declaration qu'une simulation PAPER sur le carnet observe ne contient pas
    cette friction, et que tout resultat qui en decoule est donc une BORNE
    INFERIEURE DU COUT (donc une borne SUPERIEURE de la capture).

    Interdit en EvaluationMode.EXECUTION : `evaluate(mode=EXECUTION)` refuse
    toute composante portant `excluded=True`.
    """
    return CostComponent(
        name="slippage", value_bps=0.0, quality=Quality.ASSUMED,
        source="EXCLU de la simulation PAPER",
        note="EXCLUSION, pas une mesure : la simulation PAPER calcule le fill "
             "sur le carnet du meme instant et ne contient donc ni file "
             "d'attente, ni derive decision->arrivee. Resultat = BORNE "
             "INFERIEURE du cout reel. Interdit en mode EXECUTION.")


def is_excluded(component: CostComponent) -> bool:
    """Une composante 'exclue' porte une valeur de convention, pas une mesure."""
    return component.source.startswith("EXCLU")


def slippage_observed(value_bps: float, source: str, n: int) -> CostComponent:
    if n <= 0:
        raise ValueError("n doit etre > 0 pour une mesure OBSERVED")
    return CostComponent("slippage", abs(value_bps), Quality.OBSERVED, source,
                         note=f"mesure sur N={n} fills reels")


def latency_unknown() -> CostComponent:
    return CostComponent.unknown(
        "latency", source="non mesure",
        note="exige un replay d'evenements horodates (prism_v2/replay.py)")


def latency_from_replay(decay_bps: float, delta_ms: int, source: str,
                        n: int) -> CostComponent:
    """Decroissance par latence MESUREE sur des evenements collectes.

    `decay_bps` est l'augmentation du cout de traversee entre T0 et T0+delta.
    Une valeur negative (le carnet s'ameliore) est ramenee a 0 : on ne compte
    pas un cout negatif comme un gain.
    """
    if n <= 0:
        raise ValueError("n doit etre > 0 pour une mesure OBSERVED")
    return CostComponent("latency", max(0.0, decay_bps), Quality.OBSERVED, source,
                         note=f"decroissance mesuree a delta={delta_ms}ms sur N={n} "
                              f"evenements" + (" (brut negatif ramene a 0)"
                                               if decay_bps < 0 else ""))


def latency_not_applicable(reason: str) -> CostComponent:
    return CostComponent("latency", 0.0, Quality.DERIVED, "non applicable", note=reason)


def adverse_selection_unknown() -> CostComponent:
    return CostComponent.unknown(
        "adverse_selection", source="non mesure",
        note="exige des fills maker reels et leur derive post-fill. "
             "Ne JAMAIS supposer qu'un maker est meilleur parce que ses frais "
             "sont plus bas.")


def adverse_selection_not_applicable(reason: str = "style TAKER : fill immediat") -> CostComponent:
    return CostComponent("adverse_selection", 0.0, Quality.DERIVED, "non applicable",
                         note=reason)


def funding_not_applicable(reason: str = "horizon intra-periode de funding") -> CostComponent:
    return CostComponent("funding", 0.0, Quality.DERIVED, "non applicable", note=reason)


def funding_unknown() -> CostComponent:
    return CostComponent.unknown(
        "funding", source="non mesure",
        note="exige l'horizon de detention et le funding du contrat EXECUTE "
             "(-USD-SWAP inverse), pas celui du contrat lineaire.")


def funding_observed(rate: float, periods_held: float, source: str) -> CostComponent:
    return CostComponent(name="funding", value_bps=abs(rate) * periods_held * 10_000.0,
                         quality=Quality.OBSERVED, source=source,
                         note=f"taux={rate:.6f}/periode x {periods_held} periodes")


def build_breakdown(book: OrderBook, side: str, notional_usd: float,
                    *, style: ExecutionStyle = ExecutionStyle.TAKER,
                    strict_fees: bool = True,
                    latency: Optional[CostComponent] = None,
                    funding: Optional[CostComponent] = None,
                    slippage: Optional[CostComponent] = None,
                    legs: int = 2) -> CostBreakdown:
    """Assemble un CostBreakdown depuis un carnet reel.

    Posture par defaut STRICTE : frais, slippage et latence UNKNOWN. Une
    evaluation sera donc UNRESOLVED tant que ces grandeurs ne sont pas
    mesurees. C'est voulu.
    """
    if style is ExecutionStyle.MAKER:
        spread = spread_not_applicable_maker()
        fees = fees_unknown() if strict_fees else fees_assumed_public(
            taker_legs=0, maker_legs=legs)
        adverse = adverse_selection_unknown()
    else:
        spread = spread_from_book(book, side, legs=legs)
        fees = fees_unknown() if strict_fees else fees_assumed_public(taker_legs=legs)
        adverse = adverse_selection_not_applicable()
    return CostBreakdown(
        fees=fees, spread=spread, impact=impact_from_book(book, side, notional_usd, legs=legs),
        slippage=slippage or slippage_unknown(),
        funding=funding or funding_unknown(),
        latency=latency or latency_unknown(), adverse_selection=adverse, style=style)
