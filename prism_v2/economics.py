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
    blocked_by: Optional[str] = None      # qualite ou risque, si applicable
    approved_notional_usd: Optional[float] = None

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
            "blocked_by": self.blocked_by,
            "approved_notional_usd": self.approved_notional_usd,
        }


def evaluate(candidate: Candidate, costs: CostBreakdown,
             required_notional_usd: Optional[float] = None,
             quality: Optional[Any] = None,
             risk_decision: Optional[Any] = None,
             mode: Optional[Any] = None) -> Evaluation:
    """Confronte une capture brute aux couts reels. Aucun seuil arbitraire.

    Le seul seuil est `net <= 0`, qui n'est pas un reglage mais une identite
    economique : une capture qui ne couvre pas ses couts n'existe pas.

    Ordre imperatif, non contournable :
        QUALITE DES DONNEES -> ECONOMIE -> CAPACITE -> RISQUE

    Une donnee inutilisable ne produit PAS un rejet economique : elle produit
    UNRESOLVED. Confondre "je ne peux pas mesurer" et "ce n'est pas rentable"
    est l'erreur qui fait abandonner une piste vivante ou poursuivre une piste
    morte.
    """
    unresolved = costs.unresolved_essentials()
    weakest = costs.weakest_quality()

    # ── -1. Information future : jamais executable, quel que soit le net ──
    # Une mesure ex-post (entree a l'extreme, sortie au meilleur point) est une
    # BORNE SUPERIEURE, pas une strategie. La laisser atteindre ACCEPTED ferait
    # passer un majorant irrealisable pour un edge — l'erreur exacte que ce
    # systeme existe pour empecher.
    meta = candidate.metadata or {}
    if meta.get("uses_future_information") or meta.get("upper_bound"):
        return Evaluation(
            status=CaptureStatus.UNRESOLVED,
            gross_capture_bps=candidate.gross_capture_bps,
            total_cost_bps=costs.total_bps(), expected_net_capture_bps=None,
            unresolved_components=["causality"] + unresolved,
            weakest_quality=weakest, rejection_reason=None,
            capacity_usd=candidate.capacity_usd,
            blocked_by="EX_POST_MEASUREMENT: borne superieure utilisant de "
                       "l'information future — mesure d'amplitude, non executable")

    # ── 0bis. Exigence de qualite propre au MODE ──────────────────────────
    # DISCOVERY tolere des bornes. CAPTURE_VALIDATION exige au moins DERIVED.
    # EXECUTION exige OBSERVED et refuse toute composante EXCLUE : on
    # n'engage pas de capital sur une friction qu'on a declaree absente.
    if mode is not None:
        from .costs import is_excluded
        from .modes import EvaluationMode, quality_satisfies
        if mode is EvaluationMode.EXECUTION:
            offenders = []
            names = costs.by_name()
            for n in costs.essential_names():
                comp = names.get(n)
                if comp is None:
                    continue
                if is_excluded(comp):
                    offenders.append(f"{n}(EXCLU)")
                elif not quality_satisfies(comp.quality, Quality.OBSERVED):
                    offenders.append(f"{n}({comp.quality.value})")
            if offenders:
                return Evaluation(
                    status=CaptureStatus.UNRESOLVED,
                    gross_capture_bps=candidate.gross_capture_bps,
                    total_cost_bps=costs.total_bps(),
                    expected_net_capture_bps=None,
                    unresolved_components=offenders, weakest_quality=weakest,
                    rejection_reason=None, capacity_usd=candidate.capacity_usd,
                    blocked_by=f"EXECUTION_MODE_REQUIRES_OBSERVED: {offenders}")

    # ── 0. Qualite des donnees : AVANT toute economie ─────────────────────
    if quality is not None and not getattr(quality, "is_usable", True):
        issues = [i.value for i in getattr(quality, "issues", [])]
        return Evaluation(
            status=CaptureStatus.UNRESOLVED,
            gross_capture_bps=candidate.gross_capture_bps,
            total_cost_bps=None, expected_net_capture_bps=None,
            unresolved_components=["data_quality"] + unresolved,
            weakest_quality=Quality.UNKNOWN, rejection_reason=None,
            capacity_usd=candidate.capacity_usd,
            blocked_by=f"DATA_QUALITY:{getattr(quality, 'verdict', '?')} {issues}")

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

    # ── Risque : dernier filtre avant l'execution, jamais contournable ────
    if risk_decision is not None and not getattr(risk_decision, "allowed", True):
        switches = [k.value for k in getattr(risk_decision, "triggered", [])]
        return Evaluation(
            status=CaptureStatus.REJECTED,
            gross_capture_bps=candidate.gross_capture_bps,
            total_cost_bps=total, expected_net_capture_bps=net,
            unresolved_components=[], weakest_quality=weakest,
            rejection_reason="risque: " + "; ".join(getattr(risk_decision, "reasons", [])),
            capacity_usd=candidate.capacity_usd,
            blocked_by="RISK:" + ",".join(switches), approved_notional_usd=0.0)

    return Evaluation(
        status=CaptureStatus.ACCEPTED,
        gross_capture_bps=candidate.gross_capture_bps,
        total_cost_bps=total, expected_net_capture_bps=net,
        unresolved_components=[], weakest_quality=weakest,
        rejection_reason=None, capacity_usd=candidate.capacity_usd,
        approved_notional_usd=(getattr(risk_decision, "approved_notional_usd", None)
                               if risk_decision is not None else None))
