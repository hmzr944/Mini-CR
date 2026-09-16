"""Capital Router — arbitrer entre candidates simultanees, ou ne rien faire.

Classement ECONOMIQUE explicite, aucun score arbitraire. Chaque critere est
une grandeur mesuree, et le router doit pouvoir REFUSER de deployer du
capital quand aucune candidate ne le justifie : ne rien faire est une
decision legitime, souvent la meilleure.

Piege explicitement evite : plusieurs candidates issues du meme mouvement de
marche ne sont PAS des paris independants. Les traiter comme tels conduirait
a sur-allouer sur une seule idee. Les candidates correlees partagent donc un
budget commun.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .economics import CaptureStatus, Evaluation
from .opportunity import Candidate
from .sizing import SizingLimits, SizingResult


class RoutingDecision(str, enum.Enum):
    ALLOCATE = "ALLOCATE"
    SKIP_NOT_ACTIONABLE = "SKIP_NOT_ACTIONABLE"
    SKIP_CORRELATED = "SKIP_CORRELATED"
    SKIP_NO_CAPITAL = "SKIP_NO_CAPITAL"
    SKIP_WORSE_ALTERNATIVE = "SKIP_WORSE_ALTERNATIVE"


@dataclass
class RoutedCandidate:
    candidate: Candidate
    evaluation: Evaluation
    sizing: Optional[SizingResult]
    allocated_usd: float
    decision: RoutingDecision
    rank: int
    reason: str
    correlation_group: str

    def to_dict(self) -> Dict[str, Any]:
        return {"candidate_id": self.candidate.candidate_id,
                "family": self.candidate.family,
                "inst_id": self.candidate.instrument.inst_id,
                "expected_net_capture_bps": self.evaluation.expected_net_capture_bps,
                "capacity_usd": self.candidate.capacity_usd,
                "allocated_usd": self.allocated_usd,
                "decision": self.decision.value, "rank": self.rank,
                "reason": self.reason, "correlation_group": self.correlation_group}


def correlation_group(candidate: Candidate) -> str:
    """Deux candidates du meme sous-jacent sont correlees, quelles que soient
    leur famille et leur venue : elles parient sur le meme mouvement."""
    return candidate.instrument.base or candidate.instrument.inst_id


@dataclass
class CapitalRouter:
    """Repartit un capital entre candidates simultanees, ou refuse."""

    limits: SizingLimits = field(default_factory=SizingLimits)
    #: Part maximale du capital allouable a un meme sous-jacent.
    max_fraction_per_group: float = 0.5

    def route(self, scored: Sequence[tuple]) -> List[RoutedCandidate]:
        """`scored` : sequence de (Candidate, Evaluation, SizingResult|None).

        Classement par capture nette attendue, puis par capacite. Aucun
        critere n'est pondere par un coefficient invente : l'ordre est
        lexicographique et explicite.
        """
        actionable: List[tuple] = []
        out: List[RoutedCandidate] = []

        for cand, ev, sz in scored:
            if ev.status is not CaptureStatus.ACCEPTED:
                out.append(RoutedCandidate(
                    cand, ev, sz, 0.0, RoutingDecision.SKIP_NOT_ACTIONABLE, -1,
                    f"statut {ev.status.value}"
                    + (f": {ev.rejection_reason}" if ev.rejection_reason else ""),
                    correlation_group(cand)))
                continue
            actionable.append((cand, ev, sz))

        # Tri economique : capture nette d'abord, capacite ensuite (a capture
        # egale, on prefere ce qui absorbe plus), horizon court ensuite (le
        # capital se recycle plus vite).
        actionable.sort(key=lambda t: (
            -(t[1].expected_net_capture_bps or 0.0),
            -(t[0].capacity_usd or 0.0),
            t[0].expected_horizon_ms if t[0].expected_horizon_ms is not None else 10 ** 12,
        ))

        remaining = self.limits.capital_ceiling_usd
        group_used: Dict[str, float] = {}
        group_cap = self.limits.capital_ceiling_usd * self.max_fraction_per_group

        for rank, (cand, ev, sz) in enumerate(actionable, start=1):
            grp = correlation_group(cand)
            used = group_used.get(grp, 0.0)
            if remaining <= 0:
                out.append(RoutedCandidate(cand, ev, sz, 0.0,
                                           RoutingDecision.SKIP_NO_CAPITAL, rank,
                                           "capital epuise", grp))
                continue
            if used >= group_cap:
                out.append(RoutedCandidate(
                    cand, ev, sz, 0.0, RoutingDecision.SKIP_CORRELATED, rank,
                    f"exposition sur {grp} deja a ${used:,.2f} "
                    f"(plafond ${group_cap:,.2f}) — candidates correlees, "
                    "pas des paris independants", grp))
                continue

            want = sz.recommended_notional_usd if sz else 0.0
            allow = min(want, remaining, group_cap - used)
            if allow <= 0:
                out.append(RoutedCandidate(
                    cand, ev, sz, 0.0, RoutingDecision.SKIP_NO_CAPITAL, rank,
                    f"taille recommandee ${want:,.2f} incompatible avec les "
                    f"bornes restantes", grp))
                continue

            remaining -= allow
            group_used[grp] = used + allow
            out.append(RoutedCandidate(
                cand, ev, sz, allow, RoutingDecision.ALLOCATE, rank,
                f"rang {rank} sur {len(actionable)} candidates actionnables ; "
                f"net {ev.expected_net_capture_bps:.4f} bps", grp))

        return out

    def summary(self, routed: Sequence[RoutedCandidate]) -> Dict[str, Any]:
        allocated = [r for r in routed if r.decision is RoutingDecision.ALLOCATE]
        total = sum(r.allocated_usd for r in allocated)
        by_decision: Dict[str, int] = {}
        for r in routed:
            by_decision[r.decision.value] = by_decision.get(r.decision.value, 0) + 1
        return {
            "n_candidates": len(routed),
            "n_allocated": len(allocated),
            "total_allocated_usd": total,
            "capital_ceiling_usd": self.limits.capital_ceiling_usd,
            "capital_utilisation": (total / self.limits.capital_ceiling_usd
                                    if self.limits.capital_ceiling_usd else 0.0),
            "by_decision": dict(sorted(by_decision.items())),
            "by_group": {g: round(sum(r.allocated_usd for r in allocated
                                      if r.correlation_group == g), 2)
                         for g in sorted({r.correlation_group for r in allocated})},
            "refused_everything": not allocated,
        }
