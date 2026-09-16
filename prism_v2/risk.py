"""Risk — coupe-circuits. Minimum viable, FAIL CLOSED, entierement teste.

Pas de Kelly. Pas de taille indexee sur un score. Le sizing depend de bornes
explicites et de la capacite observee, rien d'autre.

Le RiskGate se place APRES l'economie et AVANT l'execution. Aucune
opportunite ne peut l'eviter.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contracts import build_order
from .instruments import InstrumentSpec
from .quality import QualityReport, QualityVerdict


class KillSwitch(str, enum.Enum):
    STALE_DATA = "STALE_DATA"
    REGISTRY_MISMATCH = "REGISTRY_MISMATCH"
    ABNORMAL_SPREAD = "ABNORMAL_SPREAD"
    EXECUTION_MISMATCH = "EXECUTION_MISMATCH"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    CLOCK_ANOMALY = "CLOCK_ANOMALY"
    MAX_NOTIONAL = "MAX_NOTIONAL"
    MAX_CONCURRENT_POSITIONS = "MAX_CONCURRENT_POSITIONS"
    MAX_LOSS_PER_OPPORTUNITY = "MAX_LOSS_PER_OPPORTUNITY"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    ORDER_NOT_EXECUTABLE = "ORDER_NOT_EXECUTABLE"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass(frozen=True)
class RiskLimits:
    """Bornes EXPLICITES et configurables. Documentees, jamais devinees.

    Ce ne sont pas des parametres a optimiser : les bouger ne cree pas d'edge,
    cela ne fait que deplacer le point de rupture accepte.
    """

    max_notional_usd: float = 1_000.0
    max_concurrent_positions: int = 3
    max_loss_per_opportunity_usd: float = 50.0
    max_daily_loss_usd: float = 150.0
    #: Part maximale de la profondeur affichee qu'un ordre peut consommer.
    #: La profondeur observee a T0 n'est PAS garantie a T0+latence : consommer
    #: une fraction seulement laisse une marge a son evaporation.
    max_capacity_fraction: float = 0.10


@dataclass
class RiskState:
    open_positions: int = 0
    realized_pnl_today_usd: float = 0.0
    emergency_stop: bool = False
    stop_reason: str = ""

    def trip_emergency(self, reason: str) -> None:
        self.emergency_stop = True
        self.stop_reason = reason


@dataclass
class RiskDecision:
    allowed: bool
    approved_notional_usd: float
    triggered: List[KillSwitch] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"allowed": self.allowed,
                "approved_notional_usd": self.approved_notional_usd,
                "triggered": [k.value for k in self.triggered],
                "reasons": self.reasons}


class RiskGate:
    """Porte de risque. Refuse par defaut des qu'un doute existe."""

    def __init__(self, limits: RiskLimits = RiskLimits(), state: Optional[RiskState] = None):
        self.limits = limits
        self.state = state or RiskState()

    def evaluate(self, spec: InstrumentSpec, requested_notional_usd: float,
                 *, quality: Optional[QualityReport] = None,
                 capacity_usd: Optional[float] = None,
                 reference_price: Optional[float] = None,
                 registry_spec: Optional[InstrumentSpec] = None,
                 expected_loss_usd: Optional[float] = None) -> RiskDecision:
        triggered: List[KillSwitch] = []
        reasons: List[str] = []
        notional = float(requested_notional_usd)

        if self.state.emergency_stop:
            return RiskDecision(False, 0.0, [KillSwitch.EMERGENCY_STOP],
                                [f"arret d'urgence actif: {self.state.stop_reason}"])

        # 1. Qualite des donnees — bloquant avant toute autre consideration.
        if quality is not None and not quality.is_usable:
            issues = {i.value for i in quality.issues}
            if "STALE_BOOK" in issues:
                triggered.append(KillSwitch.STALE_DATA)
            if "SEQUENCE_GAP" in issues:
                triggered.append(KillSwitch.SEQUENCE_GAP)
            if "CLOCK_ANOMALY" in issues:
                triggered.append(KillSwitch.CLOCK_ANOMALY)
            if "INSTRUMENT_MISMATCH" in issues:
                triggered.append(KillSwitch.REGISTRY_MISMATCH)
            if not triggered:
                triggered.append(KillSwitch.STALE_DATA)
            reasons.append(f"qualite des donnees {quality.verdict.value}: "
                           f"{sorted(issues)}")
        if quality is not None and "ABNORMAL_SPREAD" in {i.value for i in quality.issues}:
            triggered.append(KillSwitch.ABNORMAL_SPREAD)
            reasons.append("spread anormal")

        # 2. Coherence avec le Registry — jamais de correction silencieuse.
        if registry_spec is not None:
            for field_name in ("inst_id", "inst_type", "ct_type", "ct_val",
                               "ct_mult", "settle_ccy", "lot_size", "min_size"):
                if getattr(spec, field_name) != getattr(registry_spec, field_name):
                    triggered.append(KillSwitch.REGISTRY_MISMATCH)
                    reasons.append(f"divergence Registry sur {field_name}: "
                                   f"{getattr(spec, field_name)!r} != "
                                   f"{getattr(registry_spec, field_name)!r}")
                    break

        # 3. Bornes de portefeuille.
        if self.state.open_positions >= self.limits.max_concurrent_positions:
            triggered.append(KillSwitch.MAX_CONCURRENT_POSITIONS)
            reasons.append(f"{self.state.open_positions} positions ouvertes >= "
                           f"{self.limits.max_concurrent_positions}")
        if self.state.realized_pnl_today_usd <= -abs(self.limits.max_daily_loss_usd):
            triggered.append(KillSwitch.MAX_DAILY_LOSS)
            reasons.append(f"perte du jour {self.state.realized_pnl_today_usd:.2f} USD "
                           f"<= -{self.limits.max_daily_loss_usd}")
        if (expected_loss_usd is not None
                and expected_loss_usd > self.limits.max_loss_per_opportunity_usd):
            triggered.append(KillSwitch.MAX_LOSS_PER_OPPORTUNITY)
            reasons.append(f"perte attendue {expected_loss_usd:.2f} USD > "
                           f"{self.limits.max_loss_per_opportunity_usd}")

        # 4. Plafonnement du notionnel (reduction, pas refus).
        if notional > self.limits.max_notional_usd:
            reasons.append(f"notionnel ramene de {notional:.2f} a "
                           f"{self.limits.max_notional_usd:.2f} (plafond)")
            notional = self.limits.max_notional_usd
        if capacity_usd is not None:
            cap = capacity_usd * self.limits.max_capacity_fraction
            if notional > cap:
                reasons.append(f"notionnel ramene a {cap:.2f} "
                               f"({self.limits.max_capacity_fraction:.0%} de la "
                               f"profondeur affichee ${capacity_usd:,.0f})")
                notional = cap
            if cap <= 0:
                triggered.append(KillSwitch.CAPACITY_EXCEEDED)
                reasons.append("capacite nulle")

        # 5. L'ordre est-il seulement soumettable ?
        if reference_price is not None and notional > 0 and not triggered:
            order = build_order(spec, notional, reference_price)
            if not order.is_executable:
                triggered.append(KillSwitch.ORDER_NOT_EXECUTABLE)
                reasons.append(order.reason)
            else:
                notional = order.usd_notional

        allowed = not triggered and notional > 0
        if not allowed and notional > 0 and triggered:
            notional = 0.0
        return RiskDecision(allowed, notional, triggered, reasons)

    def on_reconciliation_failure(self, detail: str) -> None:
        self.state.trip_emergency(f"echec de reconciliation: {detail}")

    def on_execution_mismatch(self, detail: str) -> None:
        self.state.trip_emergency(f"divergence d'execution: {detail}")
