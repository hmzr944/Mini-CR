"""Courbe cout/capacite. On OBSERVE la courbe, on ne cherche pas d'optimum.

Le prompt d'origine est explicite : pas de "notionnel optimal". Chercher un
optimum sur un carnet instantane serait une optimisation de parametre sur un
echantillon de taille 1 — exactement l'erreur que V2 doit eviter.

Toutes les grandeurs USD passent par InstrumentSpec, donc la courbe est juste
pour un inverse comme pour un lineaire.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .contracts import build_order
from .instruments import InstrumentSpec
from .orderbook import OrderBook

#: Echelle de sondage par defaut, en USD. Choisie pour couvrir l'ordre de
#: grandeur d'un petit capital jusqu'a l'epuisement typique d'un carnet ;
#: ce n'est PAS un parametre optimise, seulement une grille d'observation.
DEFAULT_NOTIONALS_USD = (10.0, 50.0, 100.0, 500.0, 1_000.0, 5_000.0,
                         10_000.0, 50_000.0, 100_000.0, 500_000.0, 1_000_000.0)


@dataclass(frozen=True)
class CapacityPoint:
    notional_usd: float
    exec_vwap: Optional[float]
    mid: float
    impact_bps: Optional[float]          # cout total depuis le mid (spread+impact)
    slippage_vs_touch_bps: Optional[float]
    available_depth_usd: float
    filled_notional_usd: float
    fill_ratio: float
    levels_consumed: int
    exhausted: bool
    order_executable: bool               # respecte lotSz/minSz ?
    order_contracts: float
    order_reason: str
    total_estimated_cost_bps: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def capacity_curve(book: OrderBook, side: str,
                   notionals_usd: Sequence[float] = DEFAULT_NOTIONALS_USD,
                   round_trip_legs: int = 2) -> List[CapacityPoint]:
    """Observe le cout de traversee pour une serie de notionnels.

    `total_estimated_cost_bps` couvre uniquement spread+impact (x legs). Les
    frais et le slippage d'execution ne sont PAS inclus ici : ils relevent du
    CostModel, qui sait dire lesquels sont UNKNOWN.
    """
    spec: InstrumentSpec = book.instrument
    mid = book.mid
    depth = book.depth(side)
    points: List[CapacityPoint] = []

    for notional in sorted(notionals_usd):
        walk = book.walk(side, notional)
        impact = book.market_impact_bps(side, notional)
        slip = book.slippage_vs_touch_bps(side, notional)
        # Contrainte d'ordre reelle, au prix d'execution estime.
        px = walk.vwap or mid
        order = build_order(spec, notional, px)
        total = None if (impact is None or walk.exhausted) else impact * round_trip_legs
        points.append(CapacityPoint(
            notional_usd=notional, exec_vwap=walk.vwap, mid=mid,
            impact_bps=impact, slippage_vs_touch_bps=slip,
            available_depth_usd=depth, filled_notional_usd=walk.filled_notional,
            fill_ratio=walk.fill_ratio, levels_consumed=walk.levels_consumed,
            exhausted=walk.exhausted, order_executable=order.is_executable,
            order_contracts=order.contracts, order_reason=order.reason,
            total_estimated_cost_bps=total))
    return points


def max_notional_without_exhaustion(points: Sequence[CapacityPoint]) -> Optional[float]:
    """Plus grand notionnel SONDE que le carnet absorbe entierement.

    Ce n'est pas un optimum ni une recommandation de taille : c'est la borne
    d'observation de la grille utilisee.
    """
    ok = [p.notional_usd for p in points if not p.exhausted and p.order_executable]
    return max(ok) if ok else None
