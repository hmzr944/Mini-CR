"""Modele de cout typé. Aucune hypothese heritee n'est vraie par defaut.

Chaque composante porte sa qualite :
    OBSERVED : mesuree sur une donnee de marche reelle horodatee
    DERIVED  : calculee deterministiquement depuis de l'OBSERVED
    ASSUMED  : posee par hypothese documentee, NON verifiee sur ce compte
    UNKNOWN  : non mesuree -> ne devient JAMAIS zero

Le forfait v33 de 28 bps n'apparait que comme LEGACY_ASSUMPTION, jamais
comme defaut, et il est exclu de tout calcul sauf demande explicite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Quality
from .instruments import Instrument
from .orderbook import OrderBook

#: Composantes exigees pour qu'une economie soit resolue.
#: `funding` n'en fait pas partie : il ne s'applique qu'aux detentions
#: chevauchant un reglement, et l'Opportunity doit le declarer explicitement.
ESSENTIAL_COMPONENTS = ("fees", "spread", "impact", "slippage")


@dataclass(frozen=True)
class CostComponent:
    """Une composante de cout, en bps du notionnel, avec sa provenance.

    Invariant : quality=UNKNOWN <=> value_bps is None. Impossible de porter
    une valeur numerique sans revendiquer d'ou elle vient.
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
    """Somme typee des couts d'un aller-retour, en bps du notionnel."""

    fees: CostComponent
    spread: CostComponent
    slippage: CostComponent
    impact: CostComponent
    funding: CostComponent
    other: List[CostComponent] = field(default_factory=list)

    def components(self) -> List[CostComponent]:
        return [self.fees, self.spread, self.slippage, self.impact, self.funding] + list(self.other)

    def unknown_components(self) -> List[str]:
        return [c.name for c in self.components() if not c.is_known]

    def unresolved_essentials(self) -> List[str]:
        """UNKNOWN parmi les composantes sans lesquelles aucune conclusion
        economique n'est possible."""
        by_name = {c.name: c for c in self.components()}
        return [n for n in ESSENTIAL_COMPONENTS
                if n in by_name and not by_name[n].is_known]

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
        return {
            "components": [c.to_dict() for c in self.components()],
            "total_bps": self.total_bps(),
            "unknown": self.unknown_components(),
            "unresolved_essentials": self.unresolved_essentials(),
            "weakest_quality": self.weakest_quality().value,
        }


# ── Hypothese heritee : citee, jamais appliquee par defaut ────────────────────
LEGACY_ASSUMPTION_V33_BPS = 28.0
LEGACY_ASSUMPTION_NOTE = (
    "v33: COMMISSION 0.001*2 (20bps) + SLIPPAGE 0.0005 (5bps) + EXIT_SLIPPAGE "
    "0.0003 (3bps) = 28bps forfaitaires. Jamais confronte a un fill reel. "
    "Fourni pour comparaison historique uniquement."
)


def legacy_v33_assumption() -> CostComponent:
    """Le forfait v33, explicitement etiquete ASSUMED. A n'utiliser que pour
    comparer V2 a l'heritage, jamais comme reference de verite."""
    return CostComponent(
        name="legacy_v33_roundtrip", value_bps=LEGACY_ASSUMPTION_V33_BPS,
        quality=Quality.ASSUMED, source="backtest_v33.py:57-59",
        note=LEGACY_ASSUMPTION_NOTE,
    )


# ── Frais ────────────────────────────────────────────────────────────────────
#: Bareme public OKX niveau de base, perpetuels. NON verifie sur le compte :
#: /api/v5/account/trade-fee exige une authentification que V2 n'a pas.
OKX_PUBLIC_TAKER_BPS = 5.0
OKX_PUBLIC_MAKER_BPS = 2.0
_FEE_SOURCE = "OKX bareme public perpetuels, niveau de base (non authentifie)"
_FEE_NOTE = ("ASSUMED : le tier reel du compte n'est pas verifiable sans cle API. "
             "Passer a OBSERVED exige /api/v5/account/trade-fee.")


def fees_unknown() -> CostComponent:
    """Posture stricte : tant que le tier du compte n'est pas lu, les frais
    sont UNKNOWN et l'economie reste UNRESOLVED."""
    return CostComponent.unknown(
        "fees", source="non mesure",
        note="exige /api/v5/account/trade-fee (authentifie) — absent de V2")


def fees_assumed_public(taker_legs: int = 2, maker_legs: int = 0) -> CostComponent:
    """Frais d'aller-retour sous hypothese de bareme public, clairement ASSUMED."""
    if taker_legs < 0 or maker_legs < 0:
        raise ValueError("nombre de jambes negatif")
    value = taker_legs * OKX_PUBLIC_TAKER_BPS + maker_legs * OKX_PUBLIC_MAKER_BPS
    return CostComponent(
        name="fees", value_bps=value, quality=Quality.ASSUMED, source=_FEE_SOURCE,
        note=f"{_FEE_NOTE} (taker_legs={taker_legs}, maker_legs={maker_legs})")


# ── Couts derives du carnet observe ──────────────────────────────────────────
def spread_from_book(book: OrderBook, side: str, legs: int = 2) -> CostComponent:
    """Cout de traversee du spread (mid -> touch), derive du carnet observe.

    Instrument-correct : passe par book.crossing_cost_bps, dont le
    denominateur est le prix d'execution (cf orderbook.py). La jambe de
    sortie est EXTRAPOLEE depuis le carnet d'entree — c'est note
    explicitement, car le spread de sortie n'est pas observe.
    """
    per_leg = book.crossing_cost_bps(side)
    note = f"{per_leg:.4f}bps/jambe x {legs}; seqId={book.seq_id}"
    if legs > 1:
        note += (" — ATTENTION: la jambe de sortie est extrapolee depuis le "
                 "carnet d'entree, le spread de sortie n'est pas observe")
    return CostComponent(name="spread", value_bps=per_leg * legs,
                         quality=Quality.DERIVED,
                         source=f"{book.provenance.endpoint} @ {book.ts_utc}",
                         note=note)


def impact_from_book(book: OrderBook, side: str, notional_usd: float,
                     legs: int = 2) -> CostComponent:
    """Impact de marche au-dela du touch, derive du carnet observe.

    Retourne UNKNOWN si le carnet ne peut pas absorber le notionnel : on ne
    postule pas de liquidite au-dela de ce qui est affiche.
    """
    walk = book.walk(side, notional_usd)
    if walk.vwap is None or walk.exhausted:
        return CostComponent.unknown(
            "impact", source=f"{book.provenance.endpoint} @ {book.ts_utc}",
            note=(f"profondeur insuffisante pour ${notional_usd:,.0f} "
                  f"(rempli {walk.fill_ratio:.1%}) — impact non mesurable"))
    per_leg = book.slippage_vs_touch_bps(side, notional_usd) or 0.0
    return CostComponent(
        name="impact", value_bps=per_leg * legs, quality=Quality.DERIVED,
        source=f"{book.provenance.endpoint} @ {book.ts_utc}",
        note=f"{per_leg:.4f}bps/jambe sur {walk.levels_consumed} niveaux x {legs}")


def slippage_unknown() -> CostComponent:
    """Slippage d'execution = ecart entre le prix decide et le prix obtenu.

    Distinct de l'impact (lui, lisible dans le carnet) : il recouvre la derive
    entre la decision et l'arrivee de l'ordre. Il ne peut etre mesure que par
    des fills reels horodates — V2 n'en a aucun. Reste UNKNOWN.
    """
    return CostComponent.unknown(
        "slippage", source="non mesure",
        note="exige des fills reels horodates (expected_px vs actual_fill_px). "
             "Aucun trade reel n'a jamais eu lieu dans ce projet.")


def slippage_modelled_zero_for_paper() -> CostComponent:
    """En PAPER, le fill est simule sur le carnet observe : par construction
    il n'y a pas de derive decision->arrivee. On l'ecrit 0 avec la qualite
    DERIVED et la note qui dit pourquoi ce 0 ne vaut pas pour le reel."""
    return CostComponent(
        name="slippage", value_bps=0.0, quality=Quality.DERIVED,
        source="simulation PAPER sur carnet observe",
        note="0 par construction en PAPER (fill calcule sur le carnet du meme "
             "instant). NE PAS transposer au reel : la latence n'est pas modelisee.")


def funding_not_applicable(reason: str = "horizon intra-periode de funding") -> CostComponent:
    return CostComponent(
        name="funding", value_bps=0.0, quality=Quality.DERIVED,
        source="non applicable", note=reason)


def funding_unknown() -> CostComponent:
    return CostComponent.unknown(
        "funding", source="non mesure",
        note="exige l'horizon de detention et le funding du contrat EXECUTE "
             "(-USD-SWAP inverse), pas celui du contrat lineaire.")


def funding_observed(rate: float, periods_held: float, source: str) -> CostComponent:
    """Cout de funding observe. `rate` est le taux par periode (ex 0.0001)."""
    return CostComponent(
        name="funding", value_bps=abs(rate) * periods_held * 10_000.0,
        quality=Quality.OBSERVED, source=source,
        note=f"taux={rate:.6f}/periode x {periods_held} periodes")


def build_breakdown(book: OrderBook, side: str, notional_usd: float,
                    *, strict_fees: bool = True, paper_slippage: bool = False,
                    funding: Optional[CostComponent] = None,
                    legs: int = 2) -> CostBreakdown:
    """Assemble un CostBreakdown depuis un carnet reel.

    strict_fees=True   -> frais UNKNOWN -> economie UNRESOLVED (posture par defaut)
    strict_fees=False  -> frais ASSUMED (bareme public), clairement etiquetes
    """
    return CostBreakdown(
        fees=fees_unknown() if strict_fees else fees_assumed_public(taker_legs=legs),
        spread=spread_from_book(book, side, legs=legs),
        impact=impact_from_book(book, side, notional_usd, legs=legs),
        slippage=slippage_modelled_zero_for_paper() if paper_slippage else slippage_unknown(),
        funding=funding or funding_unknown(),
    )
