"""Sizing borne + courbe taille -> capture nette.

Pas de Kelly. Pas d'optimiseur opaque. Pas de taille indexee sur un score de
conviction. La taille est bornee par des contraintes explicites, et le point
ou l'augmenter DETRUIT l'edge est MESURE sur le carnet, pas suppose.

Grandeur centrale : la capture nette MARGINALE. Le notionnel total peut
encore etre profitable alors que le dernier dollar ajoute perd deja de
l'argent — c'est ce point de bascule qui interesse, pas un optimum global.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .contracts import build_order
from .instruments import InstrumentSpec
from .orderbook import EmptyBook, OrderBook

#: Grille de sondage par defaut, en USD. Points d'OBSERVATION, pas un reglage.
DEFAULT_SIZE_GRID_USD = (10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1_000.0,
                         2_500.0, 5_000.0, 10_000.0, 25_000.0, 50_000.0,
                         100_000.0, 250_000.0, 500_000.0, 1_000_000.0)


@dataclass(frozen=True)
class SizePoint:
    notional_usd: float
    executable: bool
    reason: str
    round_trip_cost_bps: Optional[float]
    net_capture_bps: Optional[float]        # par unite de notionnel
    net_capture_usd: Optional[float]        # total
    marginal_net_usd: Optional[float]       # gain du dernier increment
    contracts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class SizingLimits:
    """Bornes explicites. Les bouger ne cree aucun edge : cela ne fait que
    deplacer le point de rupture accepte."""

    available_capital_usd: float = 100.0
    max_leverage: float = 1.0
    max_notional_usd: float = 1_000.0
    max_loss_usd: float = 50.0
    #: Fraction maximale de la profondeur AFFICHEE consommable. La profondeur
    #: observee a T0 n'est pas garantie a T0+latence.
    max_capacity_fraction: float = 0.10
    #: Notionnel deja engage sur des candidates correlees.
    correlated_exposure_usd: float = 0.0
    #: Plafond d'exposition correlee, toutes candidates confondues.
    max_correlated_exposure_usd: float = 1_000.0

    @property
    def capital_ceiling_usd(self) -> float:
        return self.available_capital_usd * max(1.0, self.max_leverage)


@dataclass
class SizingResult:
    curve: List[SizePoint]
    recommended_notional_usd: float
    binding_constraint: str
    max_profitable_notional_usd: Optional[float]
    marginal_breakeven_usd: Optional[float]
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"curve": [p.to_dict() for p in self.curve],
                "recommended_notional_usd": self.recommended_notional_usd,
                "binding_constraint": self.binding_constraint,
                "max_profitable_notional_usd": self.max_profitable_notional_usd,
                "marginal_breakeven_usd": self.marginal_breakeven_usd,
                "detail": self.detail}


def size_curve(spec: InstrumentSpec, book: OrderBook, side: str,
               gross_capture_bps: float, fixed_cost_bps: float,
               sizes: Sequence[float] = DEFAULT_SIZE_GRID_USD,
               legs: int = 2) -> List[SizePoint]:
    """Capture nette en fonction de la taille, sur le carnet OBSERVE.

    `fixed_cost_bps` regroupe les couts independants de la taille (frais,
    spread). L'impact, lui, croit avec la taille : c'est lui qui finit par
    detruire l'edge, et il est mesure en marchant le carnet.
    """
    points: List[SizePoint] = []
    prev_net_usd: Optional[float] = None
    prev_notional = 0.0
    for notional in sorted(sizes):
        order = build_order(spec, notional, book.mid)
        if not order.is_executable:
            points.append(SizePoint(notional, False, order.reason, None, None,
                                    None, None, 0.0))
            continue
        try:
            impact = book.market_impact_bps(side, notional)
            walk = book.walk(side, notional)
        except (EmptyBook, ValueError) as exc:
            points.append(SizePoint(notional, False, str(exc), None, None,
                                    None, None, order.contracts))
            continue
        if impact is None or walk.exhausted:
            points.append(SizePoint(
                notional, False,
                f"profondeur insuffisante (rempli {walk.fill_ratio:.0%})",
                None, None, None, None, order.contracts))
            continue
        total_cost = fixed_cost_bps + impact * legs
        net_bps = gross_capture_bps - total_cost
        net_usd = net_bps / 10_000.0 * notional
        marginal = (None if prev_net_usd is None
                    else net_usd - prev_net_usd)
        points.append(SizePoint(notional, True, "", total_cost, net_bps, net_usd,
                                marginal, order.contracts))
        prev_net_usd, prev_notional = net_usd, notional
    return points


def recommend_size(spec: InstrumentSpec, book: OrderBook, side: str,
                   gross_capture_bps: float, fixed_cost_bps: float,
                   limits: SizingLimits,
                   sizes: Sequence[float] = DEFAULT_SIZE_GRID_USD,
                   legs: int = 2) -> SizingResult:
    """Taille retenue = le plus petit des plafonds, jamais un optimum cherche.

    On ne choisit PAS la taille qui maximise le PnL simule : ce serait une
    optimisation sur un echantillon de taille 1. On prend la plus grande
    taille de la grille qui reste sous TOUTES les bornes ET dont la capture
    marginale est encore positive.
    """
    curve = size_curve(spec, book, side, gross_capture_bps, fixed_cost_bps,
                       sizes, legs)
    usable = [p for p in curve if p.executable and p.net_capture_usd is not None]
    if not usable:
        return SizingResult(curve, 0.0, "AUCUNE_TAILLE_EXECUTABLE", None, None,
                            "aucune taille de la grille n'est soumettable sur ce carnet")

    profitable = [p for p in usable if (p.net_capture_bps or 0) > 0]
    max_profitable = max((p.notional_usd for p in profitable), default=None)
    # Point ou le dernier increment cesse de rapporter.
    marginal_be = None
    for p in usable:
        if p.marginal_net_usd is not None and p.marginal_net_usd <= 0:
            marginal_be = p.notional_usd
            break

    try:
        depth = book.depth(side)
    except (EmptyBook, ValueError):
        depth = 0.0
    ceilings = {
        "CAPITAL": limits.capital_ceiling_usd,
        "MAX_NOTIONAL": limits.max_notional_usd,
        "DEPTH_FRACTION": depth * limits.max_capacity_fraction,
        "CORRELATED_EXPOSURE": max(0.0, limits.max_correlated_exposure_usd
                                   - limits.correlated_exposure_usd),
    }
    if max_profitable is not None:
        ceilings["NET_CAPTURE_POSITIVE"] = max_profitable
    if marginal_be is not None:
        ceilings["MARGINAL_BREAKEVEN"] = marginal_be
    if gross_capture_bps > 0:
        # Taille au-dela de laquelle une perte totale depasserait le plafond.
        ceilings["MAX_LOSS"] = limits.max_loss_usd / (gross_capture_bps / 10_000.0)

    binding = min(ceilings, key=lambda k: ceilings[k])
    cap = max(0.0, ceilings[binding])
    eligible = [p.notional_usd for p in usable
                if p.notional_usd <= cap + 1e-9 and (p.net_capture_bps or 0) > 0]
    chosen = max(eligible) if eligible else 0.0
    return SizingResult(
        curve, chosen, binding, max_profitable, marginal_be,
        f"plafond contraignant: {binding} = ${cap:,.2f} ; "
        f"taille retenue ${chosen:,.2f}"
        + ("" if chosen > 0 else " — aucune taille positive sous les bornes"))
