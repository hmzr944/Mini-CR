"""Kill switches, Failure Memory, Edge Health, et les gardes adversariaux."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.v2.fixtures import BTC_INVERSE, BTC_LINEAR, simple_inverse_book, thin_inverse_book
from tests.v2.test_pipeline import candidate, cheap_costs, known
from prism_v2.core_types import Direction, Provenance, Quality
from prism_v2.costs import CostBreakdown, adverse_selection_not_applicable, fees_unknown, latency_unknown
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.edge_health import MIN_N_FOR_ANY_CLAIM, EdgeHealth, describe
from prism_v2.execution import OrderState, PaperExecutor
from prism_v2.failure_memory import FailureMemory, FailureReason, classify
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.ledger import CaptureLedger, LedgerInconsistency
from prism_v2.opportunity import Candidate
from prism_v2.quality import QualityPolicy, assess_book
from prism_v2.reconciliation import reconcile
from prism_v2.risk import KillSwitch, RiskGate, RiskLimits, RiskState


class TestKillSwitches(unittest.TestCase):
    def setUp(self):
        self.gate = RiskGate(RiskLimits(max_notional_usd=1_000.0,
                                        max_concurrent_positions=2,
                                        max_daily_loss_usd=100.0,
                                        max_loss_per_opportunity_usd=50.0))

    def test_allows_a_sane_order(self):
        d = self.gate.evaluate(BTC_INVERSE, 500.0, capacity_usd=1_000_000.0,
                               reference_price=75_000.0)
        self.assertTrue(d.allowed, d.reasons)
        self.assertGreater(d.approved_notional_usd, 0)

    def test_stale_data_blocks(self):
        b = simple_inverse_book()
        q = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms + 60_000)
        d = self.gate.evaluate(BTC_INVERSE, 500.0, quality=q, reference_price=75_000.0)
        self.assertFalse(d.allowed)
        self.assertIn(KillSwitch.STALE_DATA, d.triggered)

    def test_registry_mismatch_blocks_and_never_corrects_silently(self):
        tampered = InstrumentSpec(**{**BTC_INVERSE.to_dict(),
                                     "inst_type": InstrumentType.SWAP_INVERSE,
                                     "ct_val": 10.0})
        d = self.gate.evaluate(tampered, 500.0, registry_spec=BTC_INVERSE,
                               reference_price=75_000.0)
        self.assertFalse(d.allowed)
        self.assertIn(KillSwitch.REGISTRY_MISMATCH, d.triggered)
        self.assertTrue(any("ct_val" in r for r in d.reasons))

    def test_max_concurrent_positions(self):
        self.gate.state.open_positions = 2
        d = self.gate.evaluate(BTC_INVERSE, 500.0, reference_price=75_000.0)
        self.assertIn(KillSwitch.MAX_CONCURRENT_POSITIONS, d.triggered)

    def test_max_daily_loss(self):
        self.gate.state.realized_pnl_today_usd = -100.0
        d = self.gate.evaluate(BTC_INVERSE, 500.0, reference_price=75_000.0)
        self.assertIn(KillSwitch.MAX_DAILY_LOSS, d.triggered)

    def test_max_loss_per_opportunity(self):
        d = self.gate.evaluate(BTC_INVERSE, 500.0, reference_price=75_000.0,
                               expected_loss_usd=200.0)
        self.assertIn(KillSwitch.MAX_LOSS_PER_OPPORTUNITY, d.triggered)

    def test_notional_capped_not_refused(self):
        d = self.gate.evaluate(BTC_INVERSE, 10_000.0, capacity_usd=10_000_000.0,
                               reference_price=75_000.0)
        self.assertTrue(d.allowed)
        self.assertLessEqual(d.approved_notional_usd, 1_000.0)

    def test_capacity_fraction_limits_size(self):
        """La profondeur affichee a T0 n'est pas garantie a T0+latence :
        on n'en consomme qu'une fraction."""
        d = self.gate.evaluate(BTC_INVERSE, 1_000.0, capacity_usd=2_000.0,
                               reference_price=75_000.0)
        self.assertLessEqual(d.approved_notional_usd, 200.0)   # 10% de 2000

    def test_order_below_min_size_blocked(self):
        d = self.gate.evaluate(BTC_INVERSE, 1_000.0, capacity_usd=50.0,
                               reference_price=75_000.0)
        self.assertFalse(d.allowed)
        self.assertIn(KillSwitch.ORDER_NOT_EXECUTABLE, d.triggered)

    def test_emergency_stop_blocks_everything(self):
        self.gate.state.trip_emergency("test")
        d = self.gate.evaluate(BTC_INVERSE, 10.0, reference_price=75_000.0)
        self.assertFalse(d.allowed)
        self.assertEqual(d.triggered, [KillSwitch.EMERGENCY_STOP])

    def test_reconciliation_failure_trips_emergency(self):
        self.gate.on_reconciliation_failure("divergence")
        self.assertTrue(self.gate.state.emergency_stop)

    def test_execution_mismatch_trips_emergency(self):
        self.gate.on_execution_mismatch("etat ambigu")
        self.assertTrue(self.gate.state.emergency_stop)

    def test_risk_block_reaches_economics(self):
        """Le risque est un filtre NON CONTOURNABLE, apres l'economie."""
        self.gate.state.open_positions = 99
        d = self.gate.evaluate(BTC_INVERSE, 500.0, reference_price=75_000.0)
        ev = evaluate(candidate(gross=500.0), cheap_costs(), risk_decision=d)
        self.assertIs(ev.status, CaptureStatus.REJECTED)
        self.assertTrue(ev.blocked_by.startswith("RISK:"))


class TestDataQualityGatesEconomics(unittest.TestCase):
    def test_unusable_data_gives_unresolved_not_rejected(self):
        """"Je ne peux pas mesurer" n'est pas "ce n'est pas rentable"."""
        b = simple_inverse_book()
        q = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms + 60_000)
        ev = evaluate(candidate(gross=500.0), cheap_costs(), quality=q)
        self.assertIs(ev.status, CaptureStatus.UNRESOLVED)
        self.assertIn("data_quality", ev.unresolved_components)
        self.assertIsNone(ev.expected_net_capture_bps)

    def test_good_quality_lets_economics_decide(self):
        b = simple_inverse_book()
        q = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms + 10)
        ev = evaluate(candidate(gross=500.0), cheap_costs(), quality=q)
        self.assertIs(ev.status, CaptureStatus.ACCEPTED)


class TestExPostCannotBeExecuted(unittest.TestCase):
    """NON-REGRESSION : une borne superieure ex-post atteignait ACCEPTED."""

    def _ex_post(self, gross: float) -> Candidate:
        return Candidate(ts_utc="t", instrument=BTC_INVERSE, opportunity_type="M2",
                         direction=Direction.LONG, gross_capture_bps=gross,
                         capacity_usd=1e9, provenance=Provenance("OKX", "/x", "t"),
                         metadata={"uses_future_information": True, "upper_bound": True})

    def test_future_information_never_accepted(self):
        ev = evaluate(self._ex_post(10_000.0), cheap_costs())
        self.assertIs(ev.status, CaptureStatus.UNRESOLVED)
        self.assertIn("causality", ev.unresolved_components)
        self.assertIn("EX_POST", ev.blocked_by)

    def test_upper_bound_alone_is_enough_to_block(self):
        c = self._ex_post(10_000.0)
        c.metadata.pop("uses_future_information")
        self.assertIs(evaluate(c, cheap_costs()).status, CaptureStatus.UNRESOLVED)

    def test_causal_candidate_is_not_blocked(self):
        self.assertIs(evaluate(candidate(gross=500.0), cheap_costs()).status,
                      CaptureStatus.ACCEPTED)


class TestUnclosedExposure(unittest.TestCase):
    """NON-REGRESSION : l'exposition non debouclee disparaissait en silence."""

    def test_asymmetric_fill_reports_residual(self):
        ex = PaperExecutor()
        rt = ex.round_trip(BTC_INVERSE, Direction.LONG,
                           simple_inverse_book(), thin_inverse_book(), 1_000.0)
        self.assertIsNotNone(rt)
        self.assertFalse(rt.fully_closed)
        self.assertGreater(rt.unclosed_contracts, 0)
        self.assertGreater(rt.unclosed_notional_usd, 0)

    def test_symmetric_fill_is_fully_closed(self):
        b = simple_inverse_book()
        rt = PaperExecutor().round_trip(BTC_INVERSE, Direction.LONG, b, b, 1_000.0)
        self.assertTrue(rt.fully_closed)
        self.assertAlmostEqual(rt.unclosed_contracts, 0.0, places=12)


class TestLedgerConsistency(unittest.TestCase):
    """NON-REGRESSION : le ledger pouvait affirmer un fill non etabli."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = CaptureLedger(Path(self._tmp.name) / "c.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_executed_without_fill_state_is_refused(self):
        c = candidate()
        ev = evaluate(c, cheap_costs())
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      simple_inverse_book(), 1_000.0)
        object.__setattr__(fill, "state", OrderState.CREATED.value)
        with self.assertRaises(LedgerInconsistency):
            self.ledger.record(c, ev, costs=cheap_costs(), fill=fill)

    def test_consistent_execution_is_accepted(self):
        c = candidate()
        ev = evaluate(c, cheap_costs())
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      simple_inverse_book(), 1_000.0)
        row = self.ledger.record(c, ev, costs=cheap_costs(), fill=fill)
        self.assertEqual(row["order_state"], "FILLED")
        self.assertTrue(row["executed"])

    def test_order_lifecycle_is_persisted(self):
        c = candidate()
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      simple_inverse_book(), 1_000.0)
        row = self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs(),
                                 fill=fill)
        self.assertIsNotNone(row["order_lifecycle"])
        self.assertEqual([t["to"] for t in row["order_lifecycle"]["transitions"]],
                         ["SUBMITTED", "FILLED"])

    def test_failure_reason_recorded_on_every_non_accepted(self):
        c = candidate(gross=0.5)
        row = self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())
        self.assertEqual(row["status"], "REJECTED")
        self.assertIsNotNone(row["failure_reason"])

    def test_measurement_mode_is_recorded(self):
        c = candidate()
        row = self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs(),
                                 measurement_mode="EVENT_REPLAY")
        self.assertEqual(row["measurement_mode"], "EVENT_REPLAY")


class TestFailureMemory(unittest.TestCase):
    def test_cost_too_high(self):
        self.assertIs(classify({"status": "REJECTED", "gross_capture_bps": 5.0,
                                "rejection_reason": "net -2 bps <= 0"}),
                      FailureReason.COST_TOO_HIGH)

    def test_no_edge_when_gross_is_non_positive(self):
        self.assertIs(classify({"status": "REJECTED", "gross_capture_bps": 0.0,
                                "rejection_reason": "net <= 0"}),
                      FailureReason.NO_EDGE)

    def test_capacity_too_low(self):
        self.assertIs(classify({"status": "REJECTED", "gross_capture_bps": 50.0,
                                "rejection_reason": "capacite insuffisante"}),
                      FailureReason.CAPACITY_TOO_LOW)

    def test_insufficient_data_when_unresolved(self):
        self.assertIs(classify({"status": "UNRESOLVED"}), FailureReason.INSUFFICIENT_DATA)

    def test_stale_book_wins_over_economics(self):
        """Une economie calculee sur donnee douteuse ne veut rien dire."""
        self.assertIs(classify({"status": "REJECTED", "gross_capture_bps": 5.0,
                                "data_quality": {"verdict": "UNUSABLE",
                                                 "issues": ["STALE_BOOK"]}}),
                      FailureReason.STALE_BOOK)

    def test_liquidity_withdrawal_from_partial_fill(self):
        self.assertIs(classify({"status": "ACCEPTED",
                                "reconciliation": {"cause": "PARTIAL_FILL_DEPTH"}}),
                      FailureReason.LIQUIDITY_WITHDRAWAL)

    def test_risk_blocked(self):
        self.assertIs(classify({"status": "REJECTED", "risk_blocked": True}),
                      FailureReason.RISK_BLOCKED)

    def test_data_vs_economics_verdict(self):
        fm = FailureMemory.from_records([
            {"status": "UNRESOLVED"}, {"status": "UNRESOLVED"},
            {"status": "REJECTED", "gross_capture_bps": 5.0,
             "rejection_reason": "net <= 0"}])
        d = fm.data_vs_economics()
        self.assertEqual(d["data_problems"], 2)
        self.assertEqual(d["economic_rejections"], 1)
        self.assertIn("collecte insuffisante", d["verdict"])

    def test_aggregation_by_instrument_and_opportunity(self):
        fm = FailureMemory.from_records([
            {"status": "UNRESOLVED", "inst_id": "BTC-USD-SWAP", "opportunity_type": "M2"},
            {"status": "UNRESOLVED", "inst_id": "ETH-USD-SWAP", "opportunity_type": "M2"}])
        self.assertEqual(fm.total, 2)
        self.assertIn("BTC-USD-SWAP", fm.by_instrument)
        self.assertEqual(fm.dominant(), "INSUFFICIENT_DATA")


class TestEdgeHealth(unittest.TestCase):
    def test_describe_reports_n_first(self):
        d = describe([1.0, 2.0, 3.0])
        self.assertEqual(d["n"], 3)
        self.assertAlmostEqual(d["median"], 2.0)
        self.assertAlmostEqual(d["positive_share"], 1.0)

    def test_describe_on_empty(self):
        self.assertEqual(describe([])["n"], 0)

    def test_insufficient_evidence_below_min_n(self):
        eh = EdgeHealth.from_records([{"status": "REJECTED"}] * 3)
        ev = eh.report()["evidence"]
        self.assertFalse(ev["sufficient_for_claim"])
        self.assertIn("PREUVE INSUFFISANTE", ev["statement"])

    def test_sufficient_evidence_above_min_n(self):
        eh = EdgeHealth.from_records([{"status": "REJECTED"}] * MIN_N_FOR_ANY_CLAIM)
        self.assertTrue(eh.report()["evidence"]["sufficient_for_claim"])

    def test_no_magic_confidence_score(self):
        keys = set(EdgeHealth.from_records([{"status": "REJECTED"}]).report())
        for banned in ("confidence", "score", "rating", "grade"):
            self.assertNotIn(banned, keys)

    def test_expected_vs_realized_tracked(self):
        eh = EdgeHealth.from_records([
            {"status": "ACCEPTED", "expected_net_capture_bps": 10.0, "realized_bps": 4.0},
            {"status": "ACCEPTED", "expected_net_capture_bps": 8.0, "realized_bps": 3.0}])
        evr = eh.report()["expected_vs_realized"]
        self.assertEqual(evr["n"], 2)
        self.assertLess(evr["diff_bps"]["median"], 0)   # realise en dessous de l'attendu


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestControlIsNotAnOpportunity(unittest.TestCase):
    """Un CONTROLE mesure un instrument ; il ne decouvre pas une opportunite.

    Les confondre gonflerait artificiellement le compte d'opportunites et
    ferait passer une mesure de cout pour un edge.
    """

    def _control(self) -> Candidate:
        return Candidate(
            ts_utc="t", instrument=BTC_INVERSE,
            opportunity_type="EXECUTION_CONTROL_PAPER", direction=Direction.LONG,
            gross_capture_bps=0.0, capacity_usd=1e9,
            provenance=Provenance("OKX", "/market/books", "t"),
            metadata={"is_control": True, "not_an_opportunity": True,
                      "is_cost_lower_bound": True})

    def test_control_has_zero_gross_by_construction(self):
        self.assertEqual(self._control().gross_capture_bps, 0.0)

    def test_control_can_never_be_accepted(self):
        """gross=0 moins des couts positifs est toujours <= 0."""
        ev = evaluate(self._control(), cheap_costs())
        self.assertIsNot(ev.status, CaptureStatus.ACCEPTED)

    def test_control_is_labelled_as_lower_bound(self):
        m = self._control().metadata
        self.assertTrue(m["is_cost_lower_bound"])
        self.assertTrue(m["not_an_opportunity"])

    def test_edge_health_can_isolate_real_opportunities(self):
        eh = EdgeHealth.from_records([
            {"status": "REJECTED", "opportunity_type": "EXECUTION_CONTROL_PAPER"},
            {"status": "REJECTED", "opportunity_type": "M2_LIQUIDATION_DISLOCATION"}])
        self.assertEqual(eh.for_opportunity("M2_LIQUIDATION_DISLOCATION")
                         .counts()["total"], 1)


class TestNoFabricatedPerformance(unittest.TestCase):
    """Gardes contre la fabrication de performance (mandat section 32)."""

    def test_realized_pnl_requires_an_actual_fill(self):
        from prism_v2.ledger import LedgerInconsistency, _assert_execution_consistent
        with self.assertRaises(LedgerInconsistency):
            _assert_execution_consistent({"executed": False, "order_state": None,
                                          "realized_pnl_usd": 42.0})

    def test_losing_trades_are_recorded_like_winning_ones(self):
        """Aucun filtrage des mauvais resultats : le ledger est append-only."""
        with tempfile.TemporaryDirectory() as d:
            ledger = CaptureLedger(Path(d) / "c.jsonl")
            for g in (0.1, 900.0, 0.2):
                c = candidate(gross=g)
                ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())
            rows = ledger.read_all()
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(1 for r in rows if r["status"] == "REJECTED"), 2)

    def test_min_n_threshold_cannot_be_trivially_low(self):
        from prism_v2.edge_health import MIN_N_FOR_ANY_CLAIM
        self.assertGreaterEqual(MIN_N_FOR_ANY_CLAIM, 30)

    def test_paper_results_are_never_labelled_real(self):
        from prism_v2.core_types import ExecutionMode
        self.assertEqual([m.value for m in ExecutionMode], ["PAPER"])
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      simple_inverse_book(), 1_000.0)
        self.assertEqual(fill.mode, "PAPER")
