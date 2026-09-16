"""Couche de recherche : agents, falsification, provenance, tests multiples."""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from tests.v2.fixtures import BTC_INVERSE, simple_inverse_book
from prism_v2.core_types import Provenance, Quality
from prism_v2.fees import (
    AssumedFeeProvider, FeeBook, FeeSchedule, FeeTierSource,
    NoCredentialsFeeProvider, RealisedFeeProvider,
)
from prism_v2.market_state import MarketState, MarketStateTracker
from prism_v2.research.agents import (
    DEFAULT_HORIZONS_MS, MarketObserverAgent, MechanismAgent, PhenomenonAgent,
    RelationAgent,
)
from prism_v2.research.discovery_ledger import DiscoveryLedger
from prism_v2.research.falsification import (
    FalsificationAgent, FalsificationContext, FalsificationVerdict,
    MIN_EFFECTIVE_N, RejectionReason,
)
from prism_v2.research.hypothesis import (
    EconomicMechanism, Hypothesis, HypothesisRegistry, HypothesisStatus,
    MultipleTestingAccount, Phenomenon, Relation,
)
from prism_v2.research.observation import FEATURE_SPACE, ObservationLog, observe_state
from prism_v2.research.observation import FEATURE_SPACE as _FS
from prism_v2.research.open_discovery import DiscoveryClass, audit
from prism_v2.research.orchestrator import ResearchOrchestrator
from prism_v2.research.pipeline import ResearchPipeline

PROV = Provenance("OKX", "/t", "t", "BTC-USD-SWAP")


def rel(n=1000, horizon=30_000, p=1e-9, mean=50.0, base=0.0, sample="DISCOVERY"):
    return Relation(relation_id=f"r-{n}-{horizon}-{mean}", phenomenon_id="p1",
                    feature="spread_bps", condition="high", inst_id="BTC-USD-SWAP",
                    horizon_ms=horizon, n=n, mean_forward_bps=mean,
                    median_forward_bps=mean, std_forward_bps=1.0,
                    baseline_mean_bps=base, n_baseline=1000, p_value=p,
                    sample=sample)


def phen():
    return Phenomenon(phenomenon_id="p1", feature="spread_bps", condition="high",
                      threshold=1.0, inst_id="BTC-USD-SWAP", venue="OKX",
                      n_occurrences=100, n_observations=1000, window_ms=(0, 100_000))


def hyp(r=None):
    return Hypothesis.propose(r or rel(), phen(), EconomicMechanism.FORCED_FLOW, "r")


def permissive_account():
    a = MultipleTestingAccount()
    for _ in range(3):
        a.record(1e-9)
    return a


# ══════════════════════════════════════════════════════════════════════════
class TestObservationIsNotATrade(unittest.TestCase):
    def test_observation_carries_no_direction(self):
        t = MarketStateTracker()
        t.on_book(simple_inverse_book())
        obs = observe_state(t.state("BTC-USD-SWAP"))
        for banned in ("direction", "side", "signal", "buy", "sell", "action"):
            self.assertNotIn(banned, obs.values)
            self.assertNotIn(banned, obs.to_dict())

    def test_missing_features_are_absent_not_zero(self):
        t = MarketStateTracker()
        t.on_book(simple_inverse_book())
        obs = observe_state(t.state("BTC-USD-SWAP"))
        self.assertTrue(obs.missing)
        for m in obs.missing:
            self.assertNotIn(m, obs.values)
            self.assertIsNone(obs.get(m))

    def test_observation_has_provenance(self):
        t = MarketStateTracker()
        t.on_book(simple_inverse_book())
        obs = observe_state(t.state("BTC-USD-SWAP"))
        self.assertEqual(obs.provenance.inst_id, "BTC-USD-SWAP")
        self.assertIn("inst_type", obs.provenance.extra)

    def test_feature_space_has_no_technical_indicator(self):
        for f in FEATURE_SPACE:
            for banned in ("rsi", "macd", "adx", "ema", "bollinger", "stoch"):
                self.assertNotIn(banned, f.lower())


class TestPhenomenonThresholdsAreMeasured(unittest.TestCase):
    def _log(self, values):
        from prism_v2.research.observation import Observation
        log = ObservationLog()
        for i, v in enumerate(values):
            log.add(Observation(ts_ms=1000 * i, inst_id="X", venue="OKX",
                                values={"spread_bps": v}, provenance=PROV))
        return log

    def test_threshold_comes_from_the_sample_not_a_constant(self):
        a = PhenomenonAgent(min_occurrences=10).discover(self._log(range(100)))
        b = PhenomenonAgent(min_occurrences=10).discover(
            self._log([v * 1000 for v in range(100)]))
        ta = {p.condition: p.threshold for p in a if p.feature == "spread_bps"}
        tb = {p.condition: p.threshold for p in b if p.feature == "spread_bps"}
        self.assertTrue(ta and tb)
        self.assertNotAlmostEqual(ta["high"], tb["high"], places=3)

    def test_constant_feature_yields_no_phenomenon(self):
        found = PhenomenonAgent(min_occurrences=5).discover(self._log([7.0] * 100))
        self.assertEqual([p for p in found if p.feature == "spread_bps"], [])


class TestRelationMeasurementIsCausal(unittest.TestCase):
    def test_horizon_must_be_covered_within_tolerance(self):
        """NON-REGRESSION : on retenait la derniere observation <= cible sans
        controler sa distance, mesurant un mouvement de 20 s en l'appelant 30 s."""
        from prism_v2.research.observation import Observation
        series = [Observation(ts_ms=1000 * i, inst_id="X", venue="OKX",
                              values={}, provenance=PROV) for i in range(21)]
        mids = {1000 * i: 100.0 + i for i in range(21)}
        self.assertIsNone(RelationAgent._forward_move_bps(series, mids, 0, 30_000))
        self.assertIsNotNone(RelationAgent._forward_move_bps(series, mids, 0, 20_000))

    def test_missing_mid_does_not_erase_a_valid_value(self):
        """NON-REGRESSION : un trou tardif remettait le resultat a None."""
        from prism_v2.research.observation import Observation
        series = [Observation(ts_ms=1000 * i, inst_id="X", venue="OKX",
                              values={}, provenance=PROV) for i in range(21)]
        mids = {1000 * i: 100.0 + i for i in range(21)}
        del mids[20_000]
        self.assertIsNotNone(RelationAgent._forward_move_bps(series, mids, 0, 20_000))

    def test_welch_accounts_for_baseline_variance(self):
        """NON-REGRESSION : le test traitait la reference comme une constante
        connue, sous-estimait l'erreur standard, et produisait trop de
        'decouvertes'."""
        cond = [1.0 + (i % 5) * 0.2 for i in range(60)]
        noisy = [(i % 11) * 1.5 - 7.0 for i in range(600)]
        quiet = [0.0] * 600
        p_noisy = RelationAgent._welch_p(cond, noisy)
        p_quiet = RelationAgent._welch_p(cond, quiet)
        self.assertIsNotNone(p_noisy)
        self.assertGreater(p_noisy, p_quiet,
                           "une reference bruitee doit donner une p-value PLUS "
                           "grande : sa variance compte")

    def test_forward_outcome_never_precedes_the_condition(self):
        from prism_v2.research.observation import Observation
        series = [Observation(ts_ms=1000 * i, inst_id="X", venue="OKX",
                              values={}, provenance=PROV) for i in range(40)]
        mids = {1000 * i: 100.0 for i in range(40)}
        for idx in range(30):
            v = RelationAgent._forward_move_bps(series, mids, idx, 5_000)
            if v is not None:
                self.assertAlmostEqual(v, 0.0, places=9)


class TestMultipleTesting(unittest.TestCase):
    def test_counts_every_test_even_without_pvalue(self):
        a = MultipleTestingAccount()
        a.record(0.01)
        a.record(None)
        self.assertEqual(a.n_tests, 2)
        self.assertEqual(a.n_with_pvalue, 1)

    def test_bonferroni_tightens_with_more_tests(self):
        a = MultipleTestingAccount()
        for _ in range(100):
            a.record(0.5)
        self.assertAlmostEqual(a.bonferroni_threshold(), 0.0005, places=9)

    def test_nothing_survives_when_all_pvalues_are_large(self):
        a = MultipleTestingAccount()
        for _ in range(50):
            a.record(0.9)
        self.assertIsNone(a.benjamini_hochberg_threshold())
        self.assertFalse(a.survives_correction(0.9))

    def test_none_pvalue_never_survives(self):
        self.assertFalse(permissive_account().survives_correction(None))


class TestHypothesisImmutability(unittest.TestCase):
    def test_silent_mutation_is_refused(self):
        reg = HypothesisRegistry()
        h = reg.register(hyp())
        tampered = dataclasses.replace(h, mechanism_rationale="CHANGE APRES COUP")
        with self.assertRaises(ValueError):
            reg.register(tampered)

    def test_revision_creates_a_new_version_referencing_the_old(self):
        reg = HypothesisRegistry()
        h = reg.register(hyp())
        v2 = h.with_status(HypothesisStatus.REJECTED, reasons=["X"])
        reg.supersede(h, v2)
        self.assertEqual(v2.supersedes, h.hypothesis_id)
        self.assertEqual(v2.version, 2)
        self.assertNotEqual(v2.hypothesis_id, h.hypothesis_id)
        self.assertIn(h.hypothesis_id, reg.versions)   # l'ancienne survit

    def test_orphan_version_is_refused(self):
        reg = HypothesisRegistry()
        h = reg.register(hyp())
        with self.assertRaises(ValueError):
            reg.supersede(h, hyp(rel(mean=99.0)))

    def test_latest_returns_only_head_of_each_lineage(self):
        reg = HypothesisRegistry()
        h = reg.register(hyp())
        reg.supersede(h, h.with_status(HypothesisStatus.REJECTED))
        self.assertEqual(len(reg.latest()), 1)
        self.assertEqual(len(reg.versions), 2)

    def test_funnel_reports_everything_not_only_survivors(self):
        reg = HypothesisRegistry()
        for i in range(3):
            h = reg.register(hyp(rel(mean=float(i))))
            reg.supersede(h, h.with_status(HypothesisStatus.REJECTED))
        f = reg.funnel()
        self.assertEqual(f["hypotheses_rejected"], 3)
        self.assertIn("multiple_testing", f)


class TestFalsificationCannotBeBypassed(unittest.TestCase):
    def setUp(self):
        self.agent = FalsificationAgent(permissive_account())

    def test_effective_n_shrinks_with_overlapping_windows(self):
        r = rel(n=60, horizon=30_000)
        self.assertAlmostEqual(
            self.agent.effective_sample_size(r, 500), 1.0, places=6)
        self.assertAlmostEqual(
            self.agent.effective_sample_size(r, 30_000), 60.0, places=6)

    def test_correlated_observations_rejects(self):
        res = self.agent.attack(hyp(rel(n=60, horizon=30_000)),
                                FalsificationContext(sampling_step_ms=500))
        self.assertIs(res.verdict, FalsificationVerdict.REJECTED)
        self.assertIn(RejectionReason.CORRELATED_OBSERVATIONS, res.reasons)

    def test_effect_below_spread_rejects(self):
        r = rel(n=5000, horizon=30_000, mean=0.5)
        res = self.agent.attack(hyp(r), FalsificationContext(
            sampling_step_ms=30_000, typical_spread_bps=2.0))
        self.assertIn(RejectionReason.EFFECT_BELOW_SPREAD, res.reasons)

    def test_no_excess_over_baseline_rejects(self):
        r = rel(n=5000, horizon=30_000, mean=10.0, base=10.0)
        res = self.agent.attack(hyp(r), FalsificationContext(sampling_step_ms=30_000))
        self.assertIn(RejectionReason.NO_EXCESS_OVER_BASELINE, res.reasons)

    def test_holdout_sign_flip_rejects(self):
        r = rel(n=5000, horizon=30_000, mean=50.0)
        ho = rel(n=5000, horizon=30_000, mean=-50.0, sample="HOLDOUT")
        res = self.agent.attack(hyp(r), FalsificationContext(
            sampling_step_ms=30_000, typical_spread_bps=1.0, holdout_relation=ho))
        self.assertIn(RejectionReason.UNSTABLE_OUT_OF_SAMPLE, res.reasons)

    def test_holdout_collapse_rejects(self):
        r = rel(n=5000, horizon=30_000, mean=50.0)
        ho = rel(n=5000, horizon=30_000, mean=1.0, sample="HOLDOUT")
        res = self.agent.attack(hyp(r), FalsificationContext(
            sampling_step_ms=30_000, typical_spread_bps=1.0, holdout_relation=ho))
        self.assertIn(RejectionReason.UNSTABLE_OUT_OF_SAMPLE, res.reasons)

    def test_latency_illusion_rejects(self):
        r = rel(n=5000, horizon=1_000, mean=50.0)
        res = self.agent.attack(hyp(r), FalsificationContext(
            sampling_step_ms=1_000, typical_spread_bps=1.0,
            transport_delay_ms=2_000))
        self.assertIn(RejectionReason.LATENCY_ILLUSION, res.reasons)

    def test_capacity_illusion_rejects(self):
        res = self.agent.attack(hyp(rel(n=5000, horizon=30_000)),
                                FalsificationContext(
                                    sampling_step_ms=30_000, typical_spread_bps=1.0,
                                    available_depth_usd=100.0,
                                    required_notional_usd=10_000.0))
        self.assertIn(RejectionReason.CAPACITY_ILLUSION, res.reasons)

    def test_denomination_mismatch_rejects(self):
        res = self.agent.attack(hyp(rel(n=5000, horizon=30_000)),
                                FalsificationContext(
                                    sampling_step_ms=30_000, typical_spread_bps=1.0,
                                    quote_currencies=("USD", "AED")))
        self.assertIn(RejectionReason.DENOMINATION_MISMATCH, res.reasons)

    def test_stale_quotes_reject(self):
        res = self.agent.attack(hyp(rel(n=5000, horizon=30_000)),
                                FalsificationContext(
                                    sampling_step_ms=30_000, typical_spread_bps=1.0,
                                    book_age_ms=60_000))
        self.assertIn(RejectionReason.STALE_QUOTES, res.reasons)

    def test_regime_dependence_rejects(self):
        res = self.agent.attack(hyp(rel(n=5000, horizon=30_000)),
                                FalsificationContext(
                                    sampling_step_ms=30_000, typical_spread_bps=1.0,
                                    early_half_mean_bps=100.0,
                                    late_half_mean_bps=1.0))
        self.assertIn(RejectionReason.REGIME_DEPENDENCE, res.reasons)

    def test_data_leakage_on_forward_looking_feature(self):
        r = dataclasses.replace(rel(n=5000, horizon=30_000), feature="forward_return")
        h = Hypothesis.propose(r, phen(), EconomicMechanism.FORCED_FLOW, "r")
        res = self.agent.attack(h, FalsificationContext(
            sampling_step_ms=30_000, typical_spread_bps=1.0))
        self.assertIn(RejectionReason.DATA_LEAKAGE, res.reasons)

    def test_a_clean_hypothesis_can_survive(self):
        """Le red team doit pouvoir laisser passer, sinon il ne teste rien."""
        r = rel(n=5000, horizon=30_000, mean=500.0)
        ho = rel(n=5000, horizon=30_000, mean=480.0, sample="HOLDOUT")
        res = self.agent.attack(hyp(r), FalsificationContext(
            sampling_step_ms=30_000, typical_spread_bps=1.0, holdout_relation=ho,
            available_depth_usd=1e9, required_notional_usd=1_000.0,
            transport_delay_ms=100.0, early_half_mean_bps=250.0,
            late_half_mean_bps=250.0, quote_currencies=("USD",)))
        self.assertIs(res.verdict, FalsificationVerdict.SURVIVED, res.details)

    def test_missing_control_is_reported_not_counted_as_success(self):
        res = self.agent.attack(hyp(rel(n=5000, horizon=30_000)),
                                FalsificationContext(sampling_step_ms=30_000))
        self.assertTrue(any("NON EXERCE" in d for d in res.details))

    def test_apply_produces_a_new_version_with_the_verdict(self):
        h = hyp(rel(n=60, horizon=30_000))
        ev, res = self.agent.apply(h, FalsificationContext(sampling_step_ms=500))
        self.assertIs(ev.status, HypothesisStatus.REJECTED)
        self.assertEqual(ev.supersedes, h.hypothesis_id)
        self.assertTrue(ev.rejection_reasons)


class TestOrchestratorCannotAllocateCapital(unittest.TestCase):
    def test_no_capital_method_exists(self):
        methods = [m for m in dir(ResearchOrchestrator) if not m.startswith("_")]
        for m in methods:
            for banned in ("capital", "allocate", "notional", "size", "fund"):
                self.assertNotIn(banned, m.lower())

    def test_priority_is_bounded(self):
        o = ResearchOrchestrator()
        h = hyp().with_status(HypothesisStatus.SURVIVED_FALSIFICATION)
        for _ in range(200):
            o.record_hypothesis("f", h)
            o.reprioritise()
        self.assertLessEqual(o.budgets["f"].priority, 4.0)

    def test_data_blocked_lead_keeps_neutral_priority(self):
        """L'absence de MESURE n'est pas l'absence d'EDGE."""
        o = ResearchOrchestrator()
        h = hyp().with_status(HypothesisStatus.REJECTED)
        for _ in range(5):
            o.record_hypothesis("f", h, [RejectionReason.CORRELATED_OBSERVATIONS])
        o.reprioritise()
        self.assertAlmostEqual(o.budgets["f"].priority, 1.0, places=6)

    def test_economically_rejected_lead_loses_priority(self):
        o = ResearchOrchestrator()
        h = hyp().with_status(HypothesisStatus.REJECTED)
        for _ in range(5):
            o.record_hypothesis("f", h, [RejectionReason.EFFECT_BELOW_SPREAD])
        o.reprioritise()
        self.assertLess(o.budgets["f"].priority, 1.0)

    def test_next_targets_distinguish_untested_from_dead(self):
        o = ResearchOrchestrator()
        h = hyp().with_status(HypothesisStatus.REJECTED)
        for _ in range(5):
            o.record_hypothesis("blocked", h, [RejectionReason.CORRELATED_OBSERVATIONS])
            o.record_hypothesis("dead", h, [RejectionReason.EFFECT_BELOW_SPREAD])
        rows = {r["key"]: r for r in o.next_research_targets(10)}
        self.assertIn("jamais reellement testee", rows["blocked"]["why"])
        self.assertIn("collecter", rows["blocked"]["recommended_action"])
        self.assertIn("spread", rows["dead"]["why"])


class TestDiscoveryLedgerImmutability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = DiscoveryLedger(Path(self._tmp.name) / "d.jsonl")
        self.agent = FalsificationAgent(permissive_account())

    def tearDown(self):
        self._tmp.cleanup()

    def _record(self, h=None):
        h = h or hyp()
        _, res = self.agent.apply(h, FalsificationContext(sampling_step_ms=500))
        return self.ledger.record(h, res, lineage=[h.hypothesis_id],
                                  multiple_testing={}, source_observations=10)

    def test_rejections_are_recorded_too(self):
        rec = self._record()
        self.assertEqual(rec.falsification_status, "REJECTED")
        self.assertEqual(len(self.ledger.read_all()), 1)

    def test_double_record_without_supersedes_is_refused(self):
        self._record()
        with self.assertRaises(ValueError):
            self._record()

    def test_every_required_field_present(self):
        from prism_v2.research.discovery_ledger import REQUIRED_FIELDS
        rec = self._record().to_dict()
        for f in REQUIRED_FIELDS:
            self.assertIn(f, rec)

    def test_lineage_and_features_are_persisted(self):
        rec = self._record()
        self.assertTrue(rec.research_lineage)
        self.assertEqual(rec.features_used, ["spread_bps"])


class TestFeeQualityLevels(unittest.TestCase):
    def test_no_credentials_gives_unknown(self):
        s = NoCredentialsFeeProvider().schedule_for(BTC_INVERSE)
        self.assertIs(s.quality, Quality.UNKNOWN)
        self.assertIsNone(s.round_trip_bps())
        self.assertFalse(s.authorises_execution)

    def test_assumed_never_authorises_execution(self):
        s = AssumedFeeProvider().schedule_for(BTC_INVERSE)
        self.assertIs(s.quality, Quality.ASSUMED)
        self.assertAlmostEqual(s.round_trip_bps(), 10.0, places=9)
        self.assertFalse(s.authorises_execution)

    def test_derived_from_real_fills_never_authorises_execution(self):
        p = RealisedFeeProvider(min_samples=3)
        for _ in range(4):
            p.observe_fill("BTC-USD-SWAP", 0.5, 1_000.0, False)
        s = p.schedule_for(BTC_INVERSE)
        self.assertIs(s.quality, Quality.DERIVED)
        self.assertFalse(s.authorises_execution)

    def test_only_observed_authorises_execution(self):
        s = FeeSchedule(inst_id="BTC-USD-SWAP", maker_bps=1.0, taker_bps=3.0,
                        quality=Quality.OBSERVED, source=FeeTierSource.ACCOUNT_API,
                        note="compte")
        self.assertTrue(s.authorises_execution)

    def test_feebook_prefers_best_quality(self):
        p = RealisedFeeProvider(min_samples=1)
        p.observe_fill("BTC-USD-SWAP", 0.3, 1_000.0, False)
        fb = FeeBook().add(AssumedFeeProvider()).add(p).add(NoCredentialsFeeProvider())
        self.assertIs(fb.best_for(BTC_INVERSE).quality, Quality.DERIVED)

    def test_unknown_schedule_cannot_carry_a_value(self):
        with self.assertRaises(ValueError):
            FeeSchedule(inst_id="X", maker_bps=1.0, taker_bps=1.0,
                        quality=Quality.UNKNOWN, source=FeeTierSource.NONE, note="")

    def test_status_names_the_blocker(self):
        st = FeeBook().add(NoCredentialsFeeProvider()).status([BTC_INVERSE])
        self.assertFalse(st["execution_authorised"])
        self.assertIn("trade-fee", st["blocker"])

    def test_no_credential_is_ever_accepted_as_argument(self):
        """Le module ne doit exposer aucun point d'entree acceptant une cle."""
        import inspect
        import prism_v2.fees as fees_mod
        for name, obj in inspect.getmembers(fees_mod, inspect.isclass):
            if obj.__module__ != fees_mod.__name__:
                continue
            try:
                params = inspect.signature(obj.__init__).parameters
            except (TypeError, ValueError):
                continue
            for p in params:
                for banned in ("key", "secret", "passphrase", "token", "cred"):
                    self.assertNotIn(banned, p.lower(),
                                     f"{name}.__init__ accepte '{p}'")


class TestOpenDiscoveryHonesty(unittest.TestCase):
    """Le verdict d'ouverture doit etre MESURE, jamais ecrit en dur.

    La version precedente retournait STRUCTURED comme une constante : elle ne
    pouvait ni se tromper, ni changer quand le systeme changeait.
    """

    @classmethod
    def setUpClass(cls):
        cls.a = audit(len(_FS), 4, 2, 18, composition_arity=1,
                      realised_size=1678)

    def test_verdict_is_bounded_not_open(self):
        self.assertIs(self.a.discovery_class, DiscoveryClass.BOUNDED)
        self.assertIsNot(self.a.discovery_class, DiscoveryClass.OPEN)

    def test_verdict_follows_from_the_measured_space(self):
        """BOUNDED decoule du fait que l'espace est enumerable, pas d'une
        constante : rendre l'espace non enumerable changerait le verdict."""
        self.assertTrue(self.a.space.is_enumerable_by_hand)
        self.assertEqual(self.a.space.enumerable_size, len(_FS) * 4 * 2 * 18)

    def test_composition_enlarges_the_space(self):
        a1 = audit(15, 4, 2, 18, composition_arity=1, run_empirical_tests=False)
        a2 = audit(15, 4, 2, 18, composition_arity=2, run_empirical_tests=False)
        self.assertGreater(a2.space.enumerable_size, a1.space.enumerable_size)

    def test_engine_is_not_blind(self):
        """Sans cette mesure, « 0 survivant » est ininterpretable."""
        self.assertFalse(self.a.engine_is_blind,
                         f"le moteur rate un effet plante: {self.a.sensitivity}")

    def test_engine_does_not_hallucinate(self):
        self.assertFalse(self.a.engine_hallucinates,
                         f"le moteur trouve un effet dans du bruit: "
                         f"{self.a.specificity}")

    def test_unverified_audit_is_undetermined_not_favourable(self):
        a = audit(15, 4, 2, 18, run_empirical_tests=False)
        self.assertIs(a.discovery_class, DiscoveryClass.UNDETERMINED)
        self.assertIsNone(a.engine_is_blind)

    def test_all_strategy_inputs_are_withheld(self):
        self.assertTrue(all(self.a.inputs_withheld.values()))

    def test_limitations_name_the_bounded_space(self):
        self.assertTrue(any("enumerable" in l for l in self.a.limitations))
        self.assertTrue(any("composition" in l.lower() for l in self.a.limitations))

    def test_research_layer_does_not_import_detectors(self):
        """Preuve mecanique qu'aucune famille codee n'intervient."""
        import ast
        from pathlib import Path as P
        root = P(__file__).resolve().parents[2] / "prism_v2" / "research"
        for f in sorted(root.rglob("*.py")):
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("detectors", node.module, f.name)
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertNotIn("detectors", a.name, f.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
