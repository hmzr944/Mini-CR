"""Opportunity Discovery Engine — chercher activement, sur plusieurs familles.

CHANGEMENT DE PARADIGME PAR RAPPORT A L'ETAPE PRECEDENTE

    avant : "aucune opportunite M2 -> le systeme est bloque"
    apres : "aucune opportunite M2 -> le moteur explore immediatement
             les autres familles"

Le moteur n'est PAS le bot d'une strategie. C'est une machine qui cherche en
continu des etats de marche ou une erreur de pricing pourrait etre convertie
en capture nette. Les strategies sont des plugins.

SEPARATION NON NEGOCIABLE
    Le DETECTEUR trouve une anomalie et mesure son amplitude brute.
    Il ne decide JAMAIS qu'elle est rentable.
    Le CAPTURE ENGINE decide si elle est economiquement capturable.

Un detecteur qui retournerait "c'est rentable" serait un signal deguise —
exactement le paradigme abandonne.

PRIORITE ADAPTATIVE
Une famille qui ne produit aucune candidate perd en priorite. Une famille qui
en produit mais meurt systematiquement apres couts est CONSERVEE en memoire
d'echec sans recevoir de capital. Une famille qui survit regulierement monte
en priorite. La priorite pilote l'effort de RECHERCHE, jamais l'allocation de
capital : c'est le Capital Router qui alloue, sur l'economie seule.
"""
from __future__ import annotations

import abc
import enum
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .core_types import utc_now_iso
from .market_state import MarketState
from .opportunity import Candidate, DetectionStatus


class Family(str, enum.Enum):
    """Familles d'inefficiences explorees. Chacune a son detecteur, son
    replay causal, son economie, son modele d'execution et ses tests."""

    CROSS_MARKET = "CROSS_MARKET"                 # inverse / lineaire / spot
    CROSS_VENUE = "CROSS_VENUE"                   # OKX / autres venues
    FUNDING_BASIS = "FUNDING_BASIS"
    FORCED_FLOW = "FORCED_FLOW"                   # liquidations
    BOOK_IMBALANCE = "BOOK_IMBALANCE"
    DEPTH_WITHDRAWAL = "DEPTH_WITHDRAWAL"
    AGGRESSIVE_FLOW = "AGGRESSIVE_FLOW"
    SHORT_HORIZON_REVERSION = "SHORT_HORIZON_REVERSION"
    SPREAD_DISLOCATION = "SPREAD_DISLOCATION"


@dataclass
class DetectionOutcome:
    """Resultat d'un detecteur sur un etat de marche."""

    family: Family
    status: DetectionStatus
    candidates: List[Candidate] = field(default_factory=list)
    reason: str = ""
    missing: List[str] = field(default_factory=list)
    #: Nombre d'etats examines — sans lui, "0 candidate" est ininterpretable.
    states_examined: int = 0
    detect_duration_us: Optional[float] = None

    @classmethod
    def ok(cls, family: Family, candidates: List[Candidate],
           examined: int = 1) -> "DetectionOutcome":
        return cls(family, DetectionStatus.OK, candidates, states_examined=examined)

    @classmethod
    def nothing(cls, family: Family, reason: str, examined: int = 1) -> "DetectionOutcome":
        """Aucune anomalie — ce n'est PAS une donnee manquante."""
        return cls(family, DetectionStatus.OK, [], reason, states_examined=examined)

    @classmethod
    def insufficient(cls, family: Family, reason: str,
                     missing: Optional[List[str]] = None) -> "DetectionOutcome":
        return cls(family, DetectionStatus.INSUFFICIENT_DATA, [], reason, missing or [])

    def to_dict(self) -> Dict[str, Any]:
        return {"family": self.family.value, "status": self.status.value,
                "n_candidates": len(self.candidates), "reason": self.reason,
                "missing": self.missing, "states_examined": self.states_examined,
                "detect_duration_us": self.detect_duration_us}


class Detector(abc.ABC):
    """Contrat d'un detecteur de famille.

    Recoit un MarketState (et ses pairs), retourne des anomalies mesurees.
    Ne connait NI les couts, NI le risque, NI l'execution.
    """

    family: Family
    #: Champs de MarketState necessaires. Permet au moteur d'expliquer un
    #: INSUFFICIENT_DATA sans connaitre l'implementation.
    requires: tuple = ()

    @abc.abstractmethod
    def detect(self, state: MarketState) -> DetectionOutcome:
        ...

    def new_candidate_id(self) -> str:
        return f"{self.family.value.lower()}-{uuid.uuid4().hex[:12]}"

    def missing_requirements(self, state: MarketState) -> List[str]:
        missing: List[str] = []
        for r in self.requires:
            v = getattr(state, r, None)
            if v in (None, [], {}):
                missing.append(r)
        return missing


@dataclass
class FamilyStats:
    """Compteurs par famille. Aucun score : des comptes et des distributions."""

    family: Family
    states_examined: int = 0
    detections: int = 0
    candidates: int = 0
    insufficient_data: int = 0
    errors: int = 0
    gross_bps: List[float] = field(default_factory=list)
    verdicts: Counter = field(default_factory=Counter)
    last_reason: str = ""
    detect_us: List[float] = field(default_factory=list)

    @property
    def candidates_per_state(self) -> Optional[float]:
        if self.states_examined <= 0:
            return None
        return self.candidates / self.states_examined

    def to_dict(self) -> Dict[str, Any]:
        from .edge_health import describe
        return {"family": self.family.value,
                "states_examined": self.states_examined,
                "detections": self.detections, "candidates": self.candidates,
                "insufficient_data": self.insufficient_data, "errors": self.errors,
                "candidates_per_state": self.candidates_per_state,
                "gross_bps": describe(self.gross_bps),
                "verdicts": dict(self.verdicts.most_common()),
                "median_detect_us": (sorted(self.detect_us)[len(self.detect_us) // 2]
                                     if self.detect_us else None),
                "last_reason": self.last_reason[:160]}


#: Priorite initiale identique pour toutes les familles : le moteur ne
#: presuppose pas laquelle marche. Elle evolue par l'observation seule.
INITIAL_PRIORITY = 1.0
#: Bornes de priorite. Une famille n'est jamais eteinte definitivement : une
#: piste morte aujourd'hui peut revivre dans un autre regime.
MIN_PRIORITY, MAX_PRIORITY = 0.1, 4.0


class DiscoveryEngine:
    """Fait tourner tous les detecteurs sur tous les etats, en continu."""

    def __init__(self, detectors: Optional[Sequence[Detector]] = None):
        self._detectors: Dict[Family, Detector] = {}
        self.stats: Dict[Family, FamilyStats] = {}
        self.priority: Dict[Family, float] = {}
        for d in detectors or ():
            self.register(d)

    def register(self, detector: Detector) -> "DiscoveryEngine":
        if not isinstance(detector, Detector):
            raise TypeError(f"{detector!r} n'implemente pas Detector")
        if detector.family in self._detectors:
            raise ValueError(f"famille deja enregistree: {detector.family.value}")
        self._detectors[detector.family] = detector
        self.stats[detector.family] = FamilyStats(detector.family)
        self.priority[detector.family] = INITIAL_PRIORITY
        return self

    @property
    def families(self) -> List[str]:
        return sorted(f.value for f in self._detectors)

    def scan(self, states: Dict[str, MarketState]) -> List[DetectionOutcome]:
        """Passe toutes les familles sur tous les etats. Un detecteur qui
        echoue n'interrompt jamais les autres."""
        outcomes: List[DetectionOutcome] = []
        for family, det in self._detectors.items():
            st = self.stats[family]
            for state in states.values():
                st.states_examined += 1
                missing = det.missing_requirements(state)
                if missing:
                    st.insufficient_data += 1
                    st.last_reason = f"donnees manquantes: {missing}"
                    outcomes.append(DetectionOutcome.insufficient(
                        family, st.last_reason, missing))
                    continue
                t0 = time.perf_counter()
                try:
                    out = det.detect(state)
                except Exception as exc:
                    st.errors += 1
                    st.last_reason = f"{type(exc).__name__}: {exc}"
                    outcomes.append(DetectionOutcome(family, DetectionStatus.ERROR,
                                                     [], st.last_reason))
                    continue
                dur_us = (time.perf_counter() - t0) * 1e6
                out.detect_duration_us = dur_us
                st.detect_us.append(dur_us)
                st.detections += 1
                if out.status is DetectionStatus.INSUFFICIENT_DATA:
                    st.insufficient_data += 1
                st.candidates += len(out.candidates)
                for c in out.candidates:
                    st.gross_bps.append(c.gross_capture_bps)
                if out.reason:
                    st.last_reason = out.reason
                outcomes.append(out)
        return outcomes

    # ── priorite de RECHERCHE (jamais d'allocation de capital) ───────────
    def record_verdict(self, family: Family, verdict: str) -> None:
        self.stats[family].verdicts[verdict] += 1

    def update_priorities(self) -> Dict[str, float]:
        """Reevalue l'effort de recherche par famille.

        Regles, volontairement simples et lisibles :
          - produit des candidates qui survivent aux bornes  -> monte
          - produit des candidates toutes mortes apres couts -> descend
          - ne produit aucune candidate                      -> descend
          - manque de donnees                                -> NEUTRE :
            l'absence de mesure n'est pas une absence d'edge.
        """
        for family, st in self.stats.items():
            p = self.priority[family]
            survives = st.verdicts.get("SURVIVES_ALL_BOUNDS", 0)
            needs = st.verdicts.get("NEEDS_MEASUREMENT", 0)
            dead = st.verdicts.get("DEAD_EVEN_AT_BEST", 0) + st.verdicts.get("NO_RAW_EDGE", 0)
            if survives or needs:
                p *= 1.0 + 0.5 * (survives + 0.25 * needs) / max(1, survives + needs + dead)
            elif st.candidates == 0 and st.detections > 0:
                p *= 0.8
            elif dead:
                p *= 0.9
            self.priority[family] = max(MIN_PRIORITY, min(MAX_PRIORITY, p))
        return {f.value: round(v, 4) for f, v in
                sorted(self.priority.items(), key=lambda kv: -kv[1])}

    def report(self) -> Dict[str, Any]:
        return {
            "generated_at": utc_now_iso(),
            "families_registered": self.families,
            "priority": {f.value: round(p, 4) for f, p in
                         sorted(self.priority.items(), key=lambda kv: -kv[1])},
            "stats": {f.value: s.to_dict() for f, s in
                      sorted(self.stats.items(), key=lambda kv: kv[0].value)},
        }
