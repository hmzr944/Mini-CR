"""Pipeline de recherche — cable les 6 agents dans l'ordre, sans raccourci.

    OBSERVE -> PHENOMENE -> RELATION -> MECANISME -> FALSIFICATION
            -> ETUDE DE CAPTURE -> PAPER CAUSAL -> LEDGER -> PRIORITE

SPLIT DECOUVERTE / HOLDOUT
La fenetre est coupee en deux. Les phenomenes et relations sont cherches sur
la PREMIERE moitie ; la seconde ne sert qu'a verifier que l'effet tient. Une
relation trouvee et validee sur les memes donnees n'est pas une decouverte,
c'est une description.

AUCUN AGENT NE PEUT SAUTER UNE ETAPE. Le seul chemin vers une etude de
capture passe par `FalsificationAgent.apply()`, et le seul chemin vers une
execution passe par le noyau (economics/risk), hors de ce paquet.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..market_state import MarketState
from .agents import (
    CaptureResearchAgent, CaptureStudy, MarketObserverAgent, MechanismAgent,
    PhenomenonAgent, RelationAgent,
)
from .discovery_ledger import DiscoveryLedger
from .falsification import (
    FalsificationAgent, FalsificationContext, FalsificationResult, RejectionReason,
)
from .hypothesis import (
    Hypothesis, HypothesisRegistry, HypothesisStatus, Phenomenon, Relation,
)
from .observation import ObservationLog
from .orchestrator import ResearchOrchestrator


@dataclass
class PipelineTiming:
    """Observabilite : duree de chaque etage, en millisecondes."""

    observe_ms: float = 0.0
    phenomena_ms: float = 0.0
    relations_ms: float = 0.0
    mechanism_ms: float = 0.0
    falsification_ms: float = 0.0
    capture_ms: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {k: round(getattr(self, k), 3) for k in self.__dataclass_fields__}


@dataclass
class PipelineResult:
    observations: int
    phenomena: List[Phenomenon]
    relations_discovery: List[Relation]
    relations_holdout: List[Relation]
    hypotheses: List[Hypothesis]
    falsifications: Dict[str, FalsificationResult]
    survivors: List[Hypothesis]
    capture_studies: Dict[str, CaptureStudy]
    timing: PipelineTiming
    split_ts_ms: Optional[int]

    def funnel(self) -> Dict[str, Any]:
        rejected = len(self.hypotheses) - len(self.survivors)
        return {"observations": self.observations,
                "phenomena": len(self.phenomena),
                "relations_discovery": len(self.relations_discovery),
                "relations_holdout": len(self.relations_holdout),
                "hypotheses_proposed": len(self.hypotheses),
                "rejected_by_falsification": rejected,
                "survived_falsification": len(self.survivors),
                "capture_studies": len(self.capture_studies)}


class ResearchPipeline:
    """Orchestration sequentielle des agents. Aucune etape n'est optionnelle."""

    def __init__(self, registry: Optional[HypothesisRegistry] = None,
                 ledger: Optional[DiscoveryLedger] = None,
                 orchestrator: Optional[ResearchOrchestrator] = None,
                 sampling_step_ms: int = 500):
        self.registry = registry or HypothesisRegistry()
        self.ledger = ledger or DiscoveryLedger()
        self.orchestrator = orchestrator or ResearchOrchestrator()
        self.sampling_step_ms = sampling_step_ms
        self.observer = MarketObserverAgent()
        self.phenomenon_agent = PhenomenonAgent()
        self.relation_agent = RelationAgent()
        self.mechanism_agent = MechanismAgent()
        self.capture_agent = CaptureResearchAgent()
        #: Le red team est un attribut nomme : aucun chemin ne le contourne,
        #: et un test architectural verifie qu'il est toujours appele.
        self.falsifier = FalsificationAgent(self.registry.accounting)

    # ── etage 1 : observation ─────────────────────────────────────────────
    def observe(self, states: Dict[str, MarketState]) -> None:
        t0 = time.perf_counter()
        self.observer.observe(states)
        self._observe_ms = getattr(self, "_observe_ms", 0.0) + \
            (time.perf_counter() - t0) * 1000

    @property
    def log(self) -> ObservationLog:
        return self.observer.log

    # ── etages 2 a 6 ──────────────────────────────────────────────────────
    def analyse(self, mids_by_inst: Dict[str, Dict[int, float]],
                states_now: Dict[str, MarketState],
                fee_bps: Optional[float], fee_quality: str,
                notional_usd: float, transport_delay_ms: Optional[float],
                typical_spreads: Dict[str, float]) -> PipelineResult:
        timing = PipelineTiming(observe_ms=getattr(self, "_observe_ms", 0.0))
        log = self.observer.log
        window = log.window_ms()
        split_ts = None
        if window:
            split_ts = window[0] + (window[1] - window[0]) // 2

        # Etage 2 : phenomenes, cherches sur la MOITIE DECOUVERTE seulement.
        t0 = time.perf_counter()
        discovery_log = self._slice_log(log, None, split_ts)
        holdout_log = self._slice_log(log, split_ts, None)
        phenomena = self.phenomenon_agent.discover(discovery_log)
        timing.phenomena_ms = (time.perf_counter() - t0) * 1000
        for p in phenomena:
            self.orchestrator.record_phenomena(p.feature, 1)
        self.orchestrator_observations(log)

        # Etage 3 : relations, mesurees sur decouverte puis sur holdout.
        t0 = time.perf_counter()
        rel_disc = self.relation_agent.measure(
            discovery_log, phenomena, mids_by_inst, self.registry.accounting,
            sample="DISCOVERY")
        rel_hold = self.relation_agent.measure(
            holdout_log, phenomena, mids_by_inst, self.registry.accounting,
            sample="HOLDOUT")
        timing.relations_ms = (time.perf_counter() - t0) * 1000
        for r in rel_disc:
            self.orchestrator.record_relation(r.feature, r.excess_bps)
        holdout_by_phen = {(r.phenomenon_id, r.horizon_ms): r for r in rel_hold}
        phen_by_id = {p.phenomenon_id: p for p in phenomena}

        # Etage 4 : mecanisme economique propose.
        t0 = time.perf_counter()
        hypotheses: List[Hypothesis] = []
        for r in rel_disc:
            phen = phen_by_id.get(r.phenomenon_id)
            if phen is None:
                continue
            h = self.registry.register(self.mechanism_agent.propose(r, phen))
            hypotheses.append(h)
        timing.mechanism_ms = (time.perf_counter() - t0) * 1000

        # Etage 5 : FALSIFICATION — passage obligatoire.
        t0 = time.perf_counter()
        falsifications: Dict[str, FalsificationResult] = {}
        survivors: List[Hypothesis] = []
        for h in hypotheses:
            state = states_now.get(h.relation.inst_id)
            ctx = FalsificationContext(
                sampling_step_ms=self.sampling_step_ms,
                typical_spread_bps=typical_spreads.get(h.relation.inst_id),
                available_depth_usd=(self._depth(state) if state else None),
                required_notional_usd=notional_usd,
                transport_delay_ms=transport_delay_ms,
                holdout_relation=holdout_by_phen.get(
                    (h.relation.phenomenon_id, h.relation.horizon_ms)),
                costs_applied=False,
                quote_currencies=self._quotes(state),
                book_age_ms=None)
            evaluated, res = self.falsifier.apply(h, ctx)
            self.registry.supersede(h, evaluated)
            falsifications[evaluated.hypothesis_id] = res
            self.orchestrator.record_hypothesis(
                h.relation.feature, evaluated, res.reasons)
            lineage = self.registry.lineage.get(h.hypothesis_id, [h.hypothesis_id])
            self.ledger.record(
                evaluated, res, lineage=lineage,
                multiple_testing=self.registry.accounting.to_dict(),
                source_observations=log.total,
                economic_status="NOT_EVALUATED",
                failure_reason=(res.reasons[0].value if res.reasons else None))
            if res.verdict.survived:
                survivors.append(evaluated)
        timing.falsification_ms = (time.perf_counter() - t0) * 1000

        # Etage 6 : etude de capture, SEULEMENT pour les survivants.
        t0 = time.perf_counter()
        studies: Dict[str, CaptureStudy] = {}
        for h in survivors:
            studies[h.hypothesis_id] = self.capture_agent.study(
                h, states_now.get(h.relation.inst_id), fee_bps, fee_quality,
                notional_usd, transport_delay_ms)
        timing.capture_ms = (time.perf_counter() - t0) * 1000

        return PipelineResult(
            observations=log.total, phenomena=phenomena,
            relations_discovery=rel_disc, relations_holdout=rel_hold,
            hypotheses=hypotheses, falsifications=falsifications,
            survivors=survivors, capture_studies=studies, timing=timing,
            split_ts_ms=split_ts)

    # ── utilitaires ───────────────────────────────────────────────────────
    def orchestrator_observations(self, log: ObservationLog) -> None:
        for feature, cov in log.feature_coverage().items():
            self.orchestrator.record_observations(feature, int(cov["n"]))

    @staticmethod
    def _slice_log(log: ObservationLog, lo: Optional[int],
                   hi: Optional[int]) -> ObservationLog:
        out = ObservationLog()
        for inst_id in log.instruments():
            for o in log.series(inst_id):
                if lo is not None and o.ts_ms < lo:
                    continue
                if hi is not None and o.ts_ms >= hi:
                    continue
                out.add(o)
        return out

    @staticmethod
    def _depth(state: Optional[MarketState]) -> Optional[float]:
        if state is None:
            return None
        try:
            return min(state.depth_usd("bid"), state.depth_usd("ask"))
        except Exception:
            return None

    @staticmethod
    def _quotes(state: Optional[MarketState]) -> Tuple[str, ...]:
        if state is None:
            return ()
        spec = state.instrument
        return (spec.quote or spec.settle_ccy or "",)
