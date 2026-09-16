"""Standard de preuve — les 14 conditions, et le verdict qui en decoule.

Aucune condition n'est optionnelle. Aucune ne peut etre supposee satisfaite
par defaut : une condition dont la donnee manque est NON SATISFAITE, et le
verdict devient INSUFFICIENT_DATA plutot que VALIDATED.

Le verdict n'est jamais « rentable ». Il nomme PRECISEMENT ou l'hypothese
meurt, ce qui est la seule information reutilisable.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class Verdict(str, enum.Enum):
    VALIDATED_NET_EDGE = "VALIDATED_NET_EDGE"
    NO_VALIDATED_EDGE = "NO_VALIDATED_EDGE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    EDGE_NOT_EXECUTABLE = "EDGE_NOT_EXECUTABLE"
    EDGE_NOT_ROBUST = "EDGE_NOT_ROBUST"
    EDGE_DECAYED = "EDGE_DECAYED"


class Condition(str, enum.Enum):
    CAUSALITY = "causalite : aucune information posterieure a T0 dans la decision"
    COSTS = "couts OBSERVED, ou bornes superieures documentees"
    EXECUTION_REALISM = "execution simulee sur carnets d'entree et de sortie distincts"
    NET_POSITIVE = "PnL net positif apres tous les couts"
    OUT_OF_SAMPLE = "positif sur le FINAL_HOLDOUT"
    TEMPORAL_STABILITY = "stable dans le temps, pas concentre sur un regime"
    CAPACITY = "capacite non nulle a une taille exploitable"
    ENOUGH_TRADES = "nombre de trades suffisant"
    RISK_MEASURED = "risque mesure"
    DRAWDOWN_MEASURED = "drawdown mesure"
    MULTIPLE_TESTING = "survit a la correction pour essais multiples"
    NO_LEAKAGE = "aucune fuite temporelle"
    REALISTIC_FILL = "aucune hypothese de fill irrealiste"
    NO_CRITICAL_UNKNOWN = "aucune dependance a un UNKNOWN critique"


#: Nombre minimal d'observations independantes. En dessous, aucune statistique
#: n'est defendable, quel que soit le resultat apparent.
MIN_TRADES = 30


@dataclass
class ProofStandard:
    """Evalue une hypothese contre les 14 conditions. Aucune n'est facultative."""

    satisfied: Dict[Condition, bool] = field(default_factory=dict)
    evidence: Dict[Condition, str] = field(default_factory=dict)

    def assess(self, condition: Condition, ok: Optional[bool],
               evidence: str = "") -> None:
        """`ok=None` signifie NON MESURE, donc NON SATISFAITE."""
        self.satisfied[condition] = bool(ok)
        self.evidence[condition] = evidence or (
            "non mesure" if ok is None else "")

    @property
    def unmet(self) -> List[Condition]:
        return [c for c in Condition if not self.satisfied.get(c, False)]

    @property
    def complete(self) -> bool:
        return not self.unmet

    def verdict(self) -> Verdict:
        """Le verdict nomme la PREMIERE cause de mort, par ordre de gravite."""
        unmet = set(self.unmet)
        if not unmet:
            return Verdict.VALIDATED_NET_EDGE
        # 1. Sait-on seulement mesurer ? Le manque de donnee prime sur tout.
        if ({Condition.ENOUGH_TRADES, Condition.RISK_MEASURED,
             Condition.DRAWDOWN_MEASURED} & unmet
                and Condition.NET_POSITIVE not in unmet):
            return Verdict.INSUFFICIENT_DATA
        if {Condition.ENOUGH_TRADES} & unmet:
            return Verdict.INSUFFICIENT_DATA
        # 2. Fuite ou fill irrealiste : le resultat n'existe pas.
        if {Condition.CAUSALITY, Condition.NO_LEAKAGE,
            Condition.REALISTIC_FILL} & unmet:
            return Verdict.NO_VALIDATED_EDGE
        # 3. Existe brut mais pas executable.
        if ({Condition.EXECUTION_REALISM, Condition.CAPACITY,
             Condition.COSTS, Condition.NO_CRITICAL_UNKNOWN} & unmet
                and Condition.NET_POSITIVE not in unmet):
            return Verdict.EDGE_NOT_EXECUTABLE
        # 4. Existe en echantillon mais pas hors echantillon.
        if Condition.OUT_OF_SAMPLE in unmet and Condition.NET_POSITIVE not in unmet:
            return Verdict.EDGE_NOT_ROBUST
        # 5. Existe mais s'eteint.
        if Condition.TEMPORAL_STABILITY in unmet and Condition.NET_POSITIVE not in unmet:
            return Verdict.EDGE_DECAYED
        # 6. Survit a tout sauf a la comptabilite des essais.
        if Condition.MULTIPLE_TESTING in unmet and Condition.NET_POSITIVE not in unmet:
            return Verdict.EDGE_NOT_ROBUST
        return Verdict.NO_VALIDATED_EDGE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict().value,
            "n_conditions": len(Condition),
            "n_satisfied": sum(1 for c in Condition
                               if self.satisfied.get(c, False)),
            "conditions": [
                {"condition": c.value,
                 "satisfied": self.satisfied.get(c, False),
                 "evidence": self.evidence.get(c, "non evalue")}
                for c in Condition],
            "unmet": [c.value for c in self.unmet],
        }
