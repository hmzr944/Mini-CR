"""Reconciliation : attendu vs realise simule, avec imputation de l'ecart.

C'est la brique qui transforme une execution en INFORMATION. Sans elle, le
systeme ne peut pas apprendre de son exploitation — seulement du mouvement
des prix, ce qui est exactement le paradigme abandonne.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .economics import CaptureStatus, Evaluation
from .execution import PaperFill, PaperRoundTrip
from .opportunity import Candidate


class DiscrepancyCause(str, enum.Enum):
    AS_EXPECTED = "AS_EXPECTED"
    PARTIAL_FILL_DEPTH = "PARTIAL_FILL_DEPTH"
    QUANTIZATION_RESIDUAL = "QUANTIZATION_RESIDUAL"
    IMPACT_WORSE_THAN_MODELLED = "IMPACT_WORSE_THAN_MODELLED"
    IMPACT_BETTER_THAN_MODELLED = "IMPACT_BETTER_THAN_MODELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    NOT_EXECUTED_UNRESOLVED = "NOT_EXECUTED_UNRESOLVED"
    NOT_EXECUTED_REJECTED = "NOT_EXECUTED_REJECTED"


@dataclass(frozen=True)
class Reconciliation:
    expected_price: Optional[float]
    expected_cost_bps: Optional[float]
    expected_net_bps: Optional[float]
    actual_fill_price: Optional[float]
    actual_cost_bps: Optional[float]
    actual_realized_bps: Optional[float]
    price_diff_bps: Optional[float]
    net_diff_bps: Optional[float]
    cause: DiscrepancyCause
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["cause"] = self.cause.value
        return d


#: Tolerance de comparaison, en bps. Ce n'est pas un seuil de decision :
#: uniquement le bruit numerique en-deca duquel "attendu" et "realise" sont
#: la meme grandeur.
NUMERIC_TOLERANCE_BPS = 1e-6


def reconcile(candidate: Candidate, evaluation: Evaluation,
              fill: Optional[PaperFill] = None,
              round_trip: Optional[PaperRoundTrip] = None) -> Reconciliation:
    """Compare ce qui etait attendu a ce qui a ete simule, et impute l'ecart."""
    expected_cost = evaluation.total_cost_bps
    expected_net = evaluation.expected_net_capture_bps

    if fill is None:
        cause = (DiscrepancyCause.NOT_EXECUTED_UNRESOLVED
                 if evaluation.status is CaptureStatus.UNRESOLVED
                 else DiscrepancyCause.NOT_EXECUTED_REJECTED)
        detail = (evaluation.rejection_reason
                  or f"non execute: composantes non resolues {evaluation.unresolved_components}")
        return Reconciliation(None, expected_cost, expected_net, None, None, None,
                              None, None, cause, detail)

    if fill.is_rejected:
        return Reconciliation(fill.reference_price, expected_cost, expected_net,
                              None, None, None, None, None,
                              DiscrepancyCause.ORDER_REJECTED, fill.reject_reason)

    expected_price = fill.reference_price          # le mid : reference de decision
    actual_price = fill.exec_price
    price_diff = fill.slippage_vs_mid_bps
    actual_cost = (fill.slippage_vs_mid_bps or 0.0) + fill.fee_bps
    actual_realized = round_trip.realized_bps if round_trip else None
    net_diff = (actual_realized - expected_net
                if (actual_realized is not None and expected_net is not None) else None)

    residual = float(fill.metadata.get("quantization_residual_usd", 0.0) or 0.0)
    if fill.metadata.get("book_exhausted"):
        cause = DiscrepancyCause.PARTIAL_FILL_DEPTH
        detail = (f"carnet epuise: rempli ${fill.filled_notional_usd:,.2f} "
                  f"sur ${fill.submitted_notional_usd:,.2f} demandes")
    elif residual > 1e-9:
        cause = DiscrepancyCause.QUANTIZATION_RESIDUAL
        detail = (f"residu de quantification ${residual:,.2f} "
                  f"(lot_size={candidate.instrument.lot_size:g})")
    elif expected_cost is None:
        cause = DiscrepancyCause.AS_EXPECTED
        detail = "aucun cout attendu resolu : comparaison impossible, fill nominal"
    elif actual_cost > expected_cost + NUMERIC_TOLERANCE_BPS:
        cause = DiscrepancyCause.IMPACT_WORSE_THAN_MODELLED
        detail = f"cout realise {actual_cost:.4f} bps > attendu {expected_cost:.4f} bps"
    elif actual_cost < expected_cost - NUMERIC_TOLERANCE_BPS:
        cause = DiscrepancyCause.IMPACT_BETTER_THAN_MODELLED
        detail = f"cout realise {actual_cost:.4f} bps < attendu {expected_cost:.4f} bps"
    else:
        cause = DiscrepancyCause.AS_EXPECTED
        detail = f"cout realise conforme ({actual_cost:.4f} bps)"

    return Reconciliation(expected_price, expected_cost, expected_net,
                          actual_price, actual_cost, actual_realized,
                          price_diff, net_diff, cause, detail)
