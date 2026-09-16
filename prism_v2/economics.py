"""Expected Net Capture — le filtre economique du systeme.

    expected_net_capture = gross_capture
                           - fees - spread - impact - slippage - funding - autres

Trois etats possibles, et un seul chemin vers ACCEPTED :

  UNRESOLVED : une composante ESSENTIELLE est UNKNOWN. On ne la remplace
               jamais par zero. Rien ne peut etre conclu, ni dans un sens
               ni dans l'autre. C'est le garde-fou central de V2.
  REJECTED   : tous les couts sont connus et net <= 0.
  ACCEPTED   : tous les couts sont connus et net > 0.

ACCEPTED ne signifie PAS "rentable". Cela signifie : "sur cette observation,
apres ces couts-la, il reste quelque chose". La qualite la plus faible des
composantes est reportee pour que personne ne confonde ASSUMED et OBSERVED.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .core_types import Quality
from .costs import CostBreakdown
from .opportunity import Candidate


class CaptureStatus(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class Evaluation:
    status: CaptureStatus
    gross_capture_bps: float
    total_cost_bps: Optional[float]
    expected_net_capture_bps: Optional[float]
    unresolved_components: List[str]
    weakest_quality: Quality
    rejection_reason: Optional[str]
    capacity_usd: Optional[float]

    @property
    def is_actionable(self) -> bool:
        return self.status is CaptureStatus.ACCEPTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "gross_capture_bps": self.gross_capture_bps,
            "total_cost_bps": self.total_cost_bps,
            "expected_net_capture_bps": self.expected_net_capture_bps,
            "unresolved_components": self.unresolved_components,
            "weakest_quality": self.weakest_quality.value,
            "rejection_reason": self.rejection_reason,
            "capacity_usd": self.capacity_usd,
        }


def evaluate(candidate: Candidate, costs: CostBreakdown,
             required_notional_usd: Optional[float] = None) -> Evaluation:
    """Confronte une capture brute aux couts reels. Aucun seuil arbitraire.

    Le seul seuil est `net <= 0`, qui n'est pas un reglage mais une identite
    economique : une capture qui ne couvre pas ses couts n'existe pas.
    """
    unresolved = costs.unresolved_essentials()
    weakest = costs.weakest_quality()

    if unresolved:
        return Evaluation(
            status=CaptureStatus.UNRESOLVED,
            gross_capture_bps=candidate.gross_capture_bps,
            total_cost_bps=None, expected_net_capture_bps=None,
            unresolved_components=unresolved, weakest_quality=weakest,
            rejection_reason=None, capacity_usd=candidate.capacity_usd)

    total = costs.total_bps()
    assert total is not None  # garanti par l'absence d'UNKNOWN essentiel
    net = candidate.gross_capture_bps - total

    # Capacite insuffisante = rejet economique, pas une note de bas de page.
    if (required_notional_usd is not None and candidate.capacity_usd is not None
            and candidate.capacity_usd + 1e-9 < required_notional_usd):
        return Evaluation(
            status=CaptureStatus.REJECTED,
            gross_capture_bps=candidate.gross_capture_bps,
            total_cost_bps=total, expected_net_capture_bps=net,
            unresolved_components=[], weakest_quality=weakest,
            rejection_reason=(f"capacite insuffisante: ${candidate.capacity_usd:,.0f} "
                              f"disponible < ${required_notional_usd:,.0f} requis"),
            capacity_usd=candidate.capacity_usd)

    if net <= 0:
        return Evaluation(
            status=CaptureStatus.REJECTED,
            gross_capture_bps=candidate.gross_capture_bps,
            total_cost_bps=total, expected_net_capture_bps=net,
            unresolved_components=[], weakest_quality=weakest,
            rejection_reason=(f"net {net:.4f} bps <= 0 "
                              f"(brut {candidate.gross_capture_bps:.4f} - couts {total:.4f})"),
            capacity_usd=candidate.capacity_usd)

    return Evaluation(
        status=CaptureStatus.ACCEPTED,
        gross_capture_bps=candidate.gross_capture_bps,
        total_cost_bps=total, expected_net_capture_bps=net,
        unresolved_components=[], weakest_quality=weakest,
        rejection_reason=None, capacity_usd=candidate.capacity_usd)
