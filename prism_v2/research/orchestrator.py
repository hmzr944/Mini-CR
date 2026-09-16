"""RESEARCH ORCHESTRATOR — repartit le BUDGET DE RECHERCHE, jamais du capital.

INVARIANT ABSOLU : PRIORITY != CAPITAL.
L'orchestrateur peut decider qu'une piste merite plus d'investigation. Il ne
peut PAS decider qu'elle merite de l'argent. L'allocation reste au Capital
Router, sur l'economie seule. Un test architectural verifie que cette classe
n'expose aucun moyen d'allouer du capital.

PAS DE SCORE MAGIQUE UNIQUE. Les metriques restent SEPAREES et lisibles :
agreger « volume d'observations », « stabilite » et « capture potentielle »
en un seul nombre detruirait precisement l'information qui permet de decider
quoi mesurer ensuite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .falsification import RejectionReason
from .hypothesis import Hypothesis, HypothesisStatus

#: Bornes de priorite de recherche. Une piste n'est jamais eteinte
#: definitivement : morte dans ce regime, elle peut revivre dans un autre.
MIN_PRIORITY, MAX_PRIORITY = 0.1, 4.0


@dataclass
class ResearchBudget:
    """Metriques SEPAREES d'une piste de recherche. Jamais agregees."""

    key: str
    observations: int = 0
    phenomena: int = 0
    relations_tested: int = 0
    hypotheses_proposed: int = 0
    survived_falsification: int = 0
    rejected: int = 0
    economically_dead: int = 0
    economically_open: int = 0
    #: Raisons de rejet — dit QUELLE contrainte tue la piste.
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    #: Meilleur exces observe, en bps. Grandeur brute, avant frictions.
    best_excess_bps: Optional[float] = None
    #: Part des hypotheses dont l'effet garde son signe hors echantillon.
    out_of_sample_consistency: Optional[float] = None
    data_quality_ok: Optional[bool] = None
    priority: float = 1.0

    @property
    def survival_rate(self) -> Optional[float]:
        total = self.survived_falsification + self.rejected
        return (self.survived_falsification / total) if total else None

    @property
    def dominant_rejection(self) -> Optional[str]:
        if not self.rejection_reasons:
            return None
        return max(self.rejection_reasons.items(), key=lambda kv: kv[1])[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "priority": round(self.priority, 4),
            "observations": self.observations, "phenomena": self.phenomena,
            "relations_tested": self.relations_tested,
            "hypotheses_proposed": self.hypotheses_proposed,
            "survived_falsification": self.survived_falsification,
            "rejected": self.rejected,
            "economically_dead": self.economically_dead,
            "economically_open": self.economically_open,
            "survival_rate": self.survival_rate,
            "dominant_rejection": self.dominant_rejection,
            "rejection_reasons": dict(sorted(self.rejection_reasons.items(),
                                             key=lambda kv: -kv[1])),
            "best_excess_bps": self.best_excess_bps,
            "out_of_sample_consistency": self.out_of_sample_consistency,
            "data_quality_ok": self.data_quality_ok,
        }


class ResearchOrchestrator:
    """Distribue l'effort de recherche entre pistes, sur des metriques observees."""

    name = "RESEARCH_ORCHESTRATOR"

    def __init__(self) -> None:
        self.budgets: Dict[str, ResearchBudget] = {}

    def budget(self, key: str) -> ResearchBudget:
        if key not in self.budgets:
            self.budgets[key] = ResearchBudget(key=key)
        return self.budgets[key]

    # ── enregistrement ────────────────────────────────────────────────────
    def record_observations(self, key: str, n: int) -> None:
        self.budget(key).observations += n

    def record_phenomena(self, key: str, n: int) -> None:
        self.budget(key).phenomena += n

    def record_relation(self, key: str, excess_bps: Optional[float]) -> None:
        b = self.budget(key)
        b.relations_tested += 1
        if excess_bps is not None:
            mag = abs(excess_bps)
            if b.best_excess_bps is None or mag > b.best_excess_bps:
                b.best_excess_bps = mag

    def record_hypothesis(self, key: str, h: Hypothesis,
                          reasons: Sequence[RejectionReason] = ()) -> None:
        b = self.budget(key)
        b.hypotheses_proposed += 1
        if h.status is HypothesisStatus.SURVIVED_FALSIFICATION:
            b.survived_falsification += 1
        elif h.status is HypothesisStatus.REJECTED:
            b.rejected += 1
            for r in reasons:
                b.rejection_reasons[r.value] = b.rejection_reasons.get(r.value, 0) + 1
        elif h.status is HypothesisStatus.ECONOMICALLY_DEAD:
            b.economically_dead += 1
        elif h.status is HypothesisStatus.ECONOMICALLY_OPEN:
            b.economically_open += 1

    # ── priorite de RECHERCHE ─────────────────────────────────────────────
    def reprioritise(self) -> Dict[str, float]:
        """Reevalue l'effort de recherche. Regles lisibles, jamais un score.

          - des hypotheses survivent           -> monte
          - tout est rejete pour CAUSE ECONOMIQUE ou STATISTIQUE -> descend
          - tout est rejete faute de DONNEES   -> NEUTRE : l'absence de mesure
            n'est pas l'absence d'edge, et baisser la priorite ici reviendrait
            a abandonner une piste qu'on n'a jamais pu tester
          - rien n'a ete teste                 -> neutre
        """
        data_reasons = {RejectionReason.INSUFFICIENT_SAMPLE.value,
                        RejectionReason.CORRELATED_OBSERVATIONS.value,
                        RejectionReason.STALE_QUOTES.value}
        for b in self.budgets.values():
            total = b.survived_falsification + b.rejected
            if total == 0:
                continue
            if b.survived_falsification:
                b.priority *= 1.0 + 0.5 * (b.survived_falsification / total)
            else:
                dominant = b.dominant_rejection
                if dominant in data_reasons:
                    pass        # neutre : on n'a pas pu mesurer
                else:
                    b.priority *= 0.85
            b.priority = max(MIN_PRIORITY, min(MAX_PRIORITY, b.priority))
        return self.ranking()

    def ranking(self) -> Dict[str, float]:
        return {k: round(b.priority, 4) for k, b in
                sorted(self.budgets.items(), key=lambda kv: -kv[1].priority)}

    def next_research_targets(self, n: int = 5) -> List[Dict[str, Any]]:
        """Pistes meritant davantage d'investigation, avec le POURQUOI.

        Une piste bloquee faute de donnees est une piste a INSTRUMENTER, pas
        une piste morte : la distinction est portee explicitement.
        """
        rows: List[Dict[str, Any]] = []
        for b in sorted(self.budgets.values(), key=lambda x: -x.priority):
            dominant = b.dominant_rejection
            if b.survived_falsification:
                why = f"{b.survived_falsification} hypothese(s) ont survecu"
                action = "valider economiquement puis en PAPER causal"
            elif dominant in ("INSUFFICIENT_SAMPLE", "CORRELATED_OBSERVATIONS"):
                why = f"bloquee par {dominant} : jamais reellement testee"
                action = "collecter plus longtemps (N effectif insuffisant)"
            elif dominant == "EFFECT_BELOW_SPREAD":
                why = "effet reel mais plus petit que le spread"
                action = "chercher des instruments a spread plus serre, ou abandonner"
            elif dominant == "MULTIPLE_TESTING":
                why = "ne survit pas a la correction pour tests multiples"
                action = "reduire l'espace de recherche ou collecter plus"
            elif dominant:
                why = f"rejetee majoritairement pour {dominant}"
                action = "piste economiquement fermee en l'etat"
            else:
                why = "aucune hypothese testee"
                action = "collecter des donnees"
            rows.append({"key": b.key, "priority": round(b.priority, 4),
                         "why": why, "recommended_action": action,
                         "best_excess_bps": b.best_excess_bps,
                         "survival_rate": b.survival_rate})
        return rows[:n]

    def report(self) -> Dict[str, Any]:
        return {"ranking": self.ranking(),
                "budgets": {k: b.to_dict() for k, b in sorted(self.budgets.items())},
                "next_targets": self.next_research_targets(),
                "invariant": "PRIORITY != CAPITAL — cet orchestrateur ne peut "
                             "pas allouer de capital, seulement de l'effort"}
