"""Modes du systeme et modes d'evaluation economique.

DEBLOCAGE CENTRAL DE CETTE ETAPE
--------------------------------
Jusqu'ici, un cout UNKNOWN rendait toute evaluation UNRESOLVED, ce qui
bloquait aussi la RECHERCHE. Or chercher et executer ne demandent pas le
meme niveau de preuve :

    DISCOVERY           : "cette situation POURRAIT-ELLE etre rentable ?"
                          Les UNKNOWN sont remplaces par des BORNES EXPLICITES
                          (optimiste / pessimiste), jamais par zero, et le
                          resultat est un INTERVALLE, jamais un point.
                          N'autorise JAMAIS une execution.

    CAPTURE_VALIDATION  : "le simulateur causal confirme-t-il la capture ?"
                          Exige que les couts soient DERIVED ou mieux.
                          Autorise l'execution PAPER.

    EXECUTION           : "peut-on engager du capital ?"
                          Exige OBSERVED sur frais et slippage.
                          Seul mode autorisant DEMO/LIVE.

Ainsi le moteur continue d'explorer toutes les familles meme quand une
donnee manquante interdit encore d'executer. C'est la difference entre
"aucune opportunite M2 -> systeme bloque" et "aucune opportunite M2 -> le
moteur explore immediatement les autres familles".

BARRIERE D'EXECUTION
--------------------
DISCOVERY -> PAPER -> DEMO -> LIVE, transitions explicites et irreversibles
par accident. LIVE exige une liste de pre-requis tous satisfaits ET un
acquittement humain explicite. Aucun chemin de code ne peut y arriver seul.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Quality


class EvaluationMode(str, enum.Enum):
    DISCOVERY = "DISCOVERY"
    CAPTURE_VALIDATION = "CAPTURE_VALIDATION"
    EXECUTION = "EXECUTION"

    @property
    def allows_paper_execution(self) -> bool:
        return self in (EvaluationMode.CAPTURE_VALIDATION, EvaluationMode.EXECUTION)

    @property
    def allows_capital(self) -> bool:
        return self is EvaluationMode.EXECUTION

    @property
    def min_quality(self) -> Quality:
        """Qualite minimale exigee sur les composantes essentielles."""
        return {
            EvaluationMode.DISCOVERY: Quality.UNKNOWN,       # bornes admises
            EvaluationMode.CAPTURE_VALIDATION: Quality.DERIVED,
            EvaluationMode.EXECUTION: Quality.OBSERVED,
        }[self]


#: Ordre de rigueur croissante. Sert a comparer deux qualites.
_QUALITY_ORDER = [Quality.OBSERVED, Quality.DERIVED, Quality.ASSUMED, Quality.UNKNOWN]


def quality_satisfies(actual: Quality, required: Quality) -> bool:
    """`actual` est-elle au moins aussi solide que `required` ?"""
    return _QUALITY_ORDER.index(actual) <= _QUALITY_ORDER.index(required)


class SystemMode(str, enum.Enum):
    """Mode operationnel du terminal. Barriere a franchir explicitement."""

    DISCOVERY = "DISCOVERY"   # observe et mesure, n'execute rien
    PAPER = "PAPER"           # execution simulee sur carnet observe
    DEMO = "DEMO"             # compte demo exchange — NON IMPLEMENTE
    LIVE = "LIVE"             # capital reel — NON IMPLEMENTE

    @property
    def is_implemented(self) -> bool:
        return self in (SystemMode.DISCOVERY, SystemMode.PAPER)

    @property
    def touches_real_capital(self) -> bool:
        return self is SystemMode.LIVE


#: Transitions autorisees. On ne saute jamais une marche.
MODE_TRANSITIONS: Dict[SystemMode, frozenset] = {
    SystemMode.DISCOVERY: frozenset({SystemMode.PAPER}),
    SystemMode.PAPER: frozenset({SystemMode.DISCOVERY, SystemMode.DEMO}),
    SystemMode.DEMO: frozenset({SystemMode.PAPER, SystemMode.LIVE}),
    SystemMode.LIVE: frozenset({SystemMode.DEMO}),
}

#: Pre-requis a satisfaire AVANT d'entrer dans un mode. Chacun est une
#: capacite verifiable, pas une intention.
MODE_PREREQUISITES: Dict[SystemMode, tuple] = {
    SystemMode.DISCOVERY: (),
    SystemMode.PAPER: ("instrument_registry_live", "data_quality_gate"),
    SystemMode.DEMO: ("instrument_registry_live", "data_quality_gate",
                      "observed_fees", "account_state_channel", "order_state_channel",
                      "execution_reconciliation", "api_permissions_checked",
                      "max_notional_limit", "daily_loss_limit", "kill_switch",
                      "stale_book_guard", "latency_guard", "partial_fill_guard"),
    SystemMode.LIVE: ("instrument_registry_live", "data_quality_gate",
                      "observed_fees", "observed_slippage", "account_state_channel",
                      "order_state_channel", "execution_reconciliation",
                      "api_permissions_checked", "max_notional_limit",
                      "daily_loss_limit", "kill_switch", "stale_book_guard",
                      "latency_guard", "partial_fill_guard",
                      "demo_validated", "human_acknowledgement"),
}


class ModeTransitionRefused(RuntimeError):
    """Transition interdite, ou pre-requis non satisfaits."""


@dataclass
class ModeGate:
    """Porte de mode. Refuse par defaut, exige des capacites verifiees.

    DEMO et LIVE ne sont pas implementes dans V2 : la porte les refuse
    explicitement plutot que de laisser croire qu'ils existent.
    """

    mode: SystemMode = SystemMode.DISCOVERY
    capabilities: Dict[str, bool] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def declare(self, capability: str, available: bool, evidence: str = "") -> None:
        """Declare une capacite comme disponible, avec sa preuve."""
        self.capabilities[capability] = bool(available)
        self.history.append({"capability": capability, "available": bool(available),
                             "evidence": evidence})

    def missing_for(self, target: SystemMode) -> List[str]:
        return [c for c in MODE_PREREQUISITES[target] if not self.capabilities.get(c)]

    def can_transition(self, target: SystemMode) -> tuple[bool, List[str]]:
        reasons: List[str] = []
        if target not in MODE_TRANSITIONS[self.mode]:
            reasons.append(f"transition {self.mode.value} -> {target.value} interdite")
        if not target.is_implemented:
            reasons.append(f"{target.value} n'est pas implemente dans V2 "
                           "(aucun executeur reel n'existe)")
        missing = self.missing_for(target)
        if missing:
            reasons.append(f"pre-requis manquants: {missing}")
        return (not reasons), reasons

    def transition(self, target: SystemMode, acknowledgement: str = "") -> SystemMode:
        ok, reasons = self.can_transition(target)
        if not ok:
            raise ModeTransitionRefused("; ".join(reasons))
        if target.touches_real_capital and acknowledgement != "I_ACCEPT_REAL_CAPITAL_RISK":
            raise ModeTransitionRefused(
                "LIVE exige un acquittement humain explicite et litteral")
        self.history.append({"from": self.mode.value, "to": target.value,
                             "acknowledgement": bool(acknowledgement)})
        self.mode = target
        return self.mode

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode.value,
                "capabilities": dict(sorted(self.capabilities.items())),
                "missing_for_demo": self.missing_for(SystemMode.DEMO),
                "missing_for_live": self.missing_for(SystemMode.LIVE)}
