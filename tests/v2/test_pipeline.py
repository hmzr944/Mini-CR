"""Capacite, Opportunity, execution PAPER, reconciliation, ledger."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.v2.fixtures import BTC_INVERSE, simple_inverse_book, thin_inverse_book
from prism_v2.capacity import capacity_curve, max_notional_without_exhaustion
from prism_v2.core_types import Direction, ExecutionMode, Provenance, Quality
from prism_v2.costs import CostBreakdown, CostComponent, fees_unknown
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.execution import PaperExecutor
from prism_v2.ledger import CaptureLedger, SecretInLedger
from prism_v2.opportunities.dislocation import LiquidationDislocationOpportunity
from prism_v2.opportunity import (
    Candidate, DetectionResult, DetectionStatus, MarketContext, Opportunity,
    OpportunityRegistry,
)
from prism_v2.reconciliation import DiscrepancyCause, reconcile

PROV = Provenance("OKX", "/test", "2026-09-16T00:00:00Z", "BTC-USD-SWAP")


def known(name, v):
    return CostComponent(name, v, Quality.DERIVED, "test")


def cheap_costs():
    return CostBreakdown(known("fees", 1), known("spread", 1), known("slippage", 0),
                         known("impact", 1), known("funding", 0))


def candidate(gross=500.0, capacity=1e9):
    return Candidate(ts_utc="2026-09-16T00:00:00Z", instrument=BTC_INVERSE,
                     opportunity_type="TEST", direction=Direction.LONG,
                     gross_capture_bps=gross, capacity_usd=capacity, provenance=PROV)


class TestCapacity(unittest.TestCase):
    def test_curve_covers_all_probed_notionals(self):
        pts = capacity_curve(simple_inverse_book(), "ask", [100.0, 1000.0, 3000.0])
        self.assertEqual([p.notional_usd for p in pts], [100.0, 1000.0, 3000.0])

    def test_cost_is_monotone_non_decreasing_in_size(self):
        pts = capacity_curve(simple_inverse_book(), "ask", [100.0, 1000.0, 3000.0, 6000.0])
        impacts = [p.impact_bps for p in pts if p.impact_bps is not None]
        self.assertEqual(impacts, sorted(impacts))

    def test_exhaustion_flagged_beyond_depth(self):
        pts = capacity_curve(simple_inverse_book(), "ask", [6000.0, 10_000.0])
        self.assertFalse(pts[0].exhausted)
        self.assertTrue(pts[1].exhausted)
        self.assertIsNone(pts[1].total_estimated_cost_bps)

    def test_min_size_constraint_reported(self):
        """min_size 0.1 contrat = 10 USD. Un sondage a 5 USD est non executable."""
        pts = capacity_curve(simple_inverse_book(), "ask", [5.0, 100.0])
        self.assertFalse(pts[0].order_executable)
        self.assertTrue(pts[1].order_executable)

    def test_max_probed_without_exhaustion(self):
        pts = capacity_curve(simple_inverse_book(), "ask", [100.0, 6000.0, 10_000.0])
        self.assertAlmostEqual(max_notional_without_exhaustion(pts), 6000.0, places=9)

    def test_no_optimum_function_exists(self):
        import prism_v2.capacity as cap
        for name in dir(cap):
            self.assertNotIn("optimal", name.lower())
            self.assertNotIn("best_", name.lower())


class TestOpportunityInterface(unittest.TestCase):
    def test_candidate_requires_instrument_spec(self):
        with self.assertRaises(TypeError):
            Candidate(ts_utc="t", instrument="BTC-USD-SWAP", opportunity_type="X",
                      direction=Direction.LONG, gross_capture_bps=1.0,
                      capacity_usd=None, provenance=PROV)

    def test_confidence_requires_objective_definition(self):
        with self.assertRaises(ValueError):
            Candidate(ts_utc="t", instrument=BTC_INVERSE, opportunity_type="X",
                      direction=Direction.LONG, gross_capture_bps=1.0,
                      capacity_usd=None, provenance=PROV, confidence=0.9)

    def test_confidence_with_definition_accepted(self):
        c = Candidate(ts_utc="t", instrument=BTC_INVERSE, opportunity_type="X",
                      direction=Direction.LONG, gross_capture_bps=1.0, capacity_usd=None,
                      provenance=PROV, confidence=0.9,
                      confidence_definition="frequence empirique sur N=1000 observations")
        self.assertAlmostEqual(c.confidence, 0.9)

    def test_dislocation_returns_insufficient_data_not_a_signal(self):
        opp = LiquidationDislocationOpportunity()
        r = opp.detect(MarketContext(instrument=BTC_INVERSE))
        self.assertIs(r.status, DetectionStatus.INSUFFICIENT_DATA)
        self.assertEqual(r.candidates, [])
        self.assertTrue(r.reason)

    def test_dislocation_insufficient_when_candles_missing(self):
        ctx = MarketContext(instrument=BTC_INVERSE,
                            liquidations=[{"details": [{"bkPx": "100", "sz": "1",
                                                        "side": "sell", "ts": "1789549475551"}]}],
                            candles=[])
        r = LiquidationDislocationOpportunity().detect(ctx)
        self.assertIs(r.status, DetectionStatus.INSUFFICIENT_DATA)

    def test_dislocation_uses_no_technical_indicators(self):
        """Inspecte les IDENTIFIANTS du code (AST), pas la prose.

        Une docstring qui dit "pas de RSI" est legitime ; une variable nommee
        rsi ne l'est pas.
        """
        from tests.v2.helpers import banned_identifiers, code_identifiers
        src = (Path(__file__).resolve().parents[2]
               / LiquidationDislocationOpportunity.__module__.replace(".", "/")) \
            .with_suffix(".py")
        found = banned_identifiers(code_identifiers(src))
        self.assertEqual(found, [], f"indicateurs techniques dans {src.name}: {found}")

    def test_registry_reports_missing_requirements_per_opportunity(self):
        reg = OpportunityRegistry().register(LiquidationDislocationOpportunity())
        out = reg.detect_all(MarketContext(instrument=BTC_INVERSE))
        r = out[LiquidationDislocationOpportunity.name]
        self.assertIs(r.status, DetectionStatus.INSUFFICIENT_DATA)
        self.assertIn("liquidations", r.missing)

    def test_failing_opportunity_does_not_kill_the_core(self):
        class Exploding(Opportunity):
            name = "EXPLODING"
            def detect(self, ctx):
                raise RuntimeError("boom")

        out = OpportunityRegistry().register(Exploding()).detect_all(
            MarketContext(instrument=BTC_INVERSE))
        self.assertIs(out["EXPLODING"].status, DetectionStatus.ERROR)
        self.assertIn("boom", out["EXPLODING"].reason)

    def test_duplicate_registration_refused(self):
        reg = OpportunityRegistry().register(LiquidationDislocationOpportunity())
        with self.assertRaises(ValueError):
            reg.register(LiquidationDislocationOpportunity())


class TestPaperExecution(unittest.TestCase):
    def setUp(self):
        self.ex = PaperExecutor()

    def test_mode_is_always_paper(self):
        self.assertIs(PaperExecutor.mode, ExecutionMode.PAPER)
        f = self.ex.submit(BTC_INVERSE, Direction.LONG, simple_inverse_book(), 500.0)
        self.assertEqual(f.mode, "PAPER")

    def test_no_real_executor_class_exists(self):
        import prism_v2.execution as ex
        self.assertEqual([n for n in dir(ex) if "Real" in n or "Live" in n], [])
        self.assertEqual([m.value for m in ExecutionMode], ["PAPER"])

    def test_fill_price_comes_from_the_observed_book(self):
        f = self.ex.submit(BTC_INVERSE, Direction.LONG, simple_inverse_book(), 500.0)
        self.assertFalse(f.is_rejected)
        self.assertAlmostEqual(f.exec_price, 100.0, places=9)
        self.assertAlmostEqual(f.reference_price, 99.5, places=9)

    def test_partial_fill_when_depth_insufficient(self):
        f = self.ex.submit(BTC_INVERSE, Direction.LONG, thin_inverse_book(), 10_000.0)
        self.assertTrue(f.is_partial)
        self.assertLess(f.filled_notional_usd, 10_000.0)
        self.assertTrue(f.metadata["book_exhausted"])

    def test_order_below_min_size_rejected(self):
        f = self.ex.submit(BTC_INVERSE, Direction.LONG, simple_inverse_book(), 5.0)
        self.assertTrue(f.is_rejected)
        self.assertIn("min_size", f.reject_reason)

    def test_fees_are_denominated_in_settle_currency(self):
        f = self.ex.submit(BTC_INVERSE, Direction.LONG, simple_inverse_book(), 1000.0)
        self.assertEqual(f.settle_ccy, "BTC")
        self.assertGreater(f.fee_settle_ccy, 0)
        self.assertAlmostEqual(f.fee_bps, 5.0, places=6)   # taker Lv1

    def test_round_trip_flat_market_loses_exactly_costs(self):
        """Marche plat : entree a l'ask, sortie au bid -> perte = spread + frais."""
        b = simple_inverse_book()
        rt = self.ex.round_trip(BTC_INVERSE, Direction.LONG, b, b, 1000.0)
        self.assertIsNotNone(rt)
        self.assertLess(rt.realized_bps, 0)
        self.assertAlmostEqual(rt.total_fee_bps, 10.0, places=4)

    def test_round_trip_uses_inverse_pnl_formula(self):
        """Verifie que le PnL passe par la formule en 1/prix, pas lineaire."""
        from prism_v2.contracts import pnl
        b = simple_inverse_book()
        rt = self.ex.round_trip(BTC_INVERSE, Direction.LONG, b, b, 1000.0)
        expected = pnl(BTC_INVERSE, Direction.LONG, rt.entry.contracts,
                       rt.entry.exec_price, rt.exit.exec_price)
        self.assertAlmostEqual(rt.gross_bps, expected.return_bps_usd, places=9)


class TestReconciliation(unittest.TestCase):
    def test_not_executed_when_unresolved(self):
        b = CostBreakdown(fees_unknown(), known("spread", 1), known("slippage", 1),
                          known("impact", 1), known("funding", 0))
        ev = evaluate(candidate(), b)
        r = reconcile(candidate(), ev, None)
        self.assertIs(r.cause, DiscrepancyCause.NOT_EXECUTED_UNRESOLVED)
        self.assertIsNone(r.actual_fill_price)

    def test_not_executed_when_rejected(self):
        ev = evaluate(candidate(gross=1.0), cheap_costs())
        self.assertIs(ev.status, CaptureStatus.REJECTED)
        r = reconcile(candidate(gross=1.0), ev, None)
        self.assertIs(r.cause, DiscrepancyCause.NOT_EXECUTED_REJECTED)

    def test_partial_fill_cause_attributed_to_depth(self):
        ev = evaluate(candidate(), cheap_costs())
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      thin_inverse_book(), 10_000.0)
        r = reconcile(candidate(), ev, fill)
        self.assertIs(r.cause, DiscrepancyCause.PARTIAL_FILL_DEPTH)

    def test_order_rejected_cause(self):
        ev = evaluate(candidate(), cheap_costs())
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG, simple_inverse_book(), 5.0)
        r = reconcile(candidate(), ev, fill)
        self.assertIs(r.cause, DiscrepancyCause.ORDER_REJECTED)

    def test_cost_discrepancy_detected(self):
        """Cout attendu volontairement sous-estime -> ecart impute."""
        ev = evaluate(candidate(), cheap_costs())       # 3 bps attendus
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      simple_inverse_book(), 1000.0)
        r = reconcile(candidate(), ev, fill)
        self.assertIs(r.cause, DiscrepancyCause.IMPACT_WORSE_THAN_MODELLED)
        self.assertGreater(r.actual_cost_bps, r.expected_cost_bps)


class TestLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = CaptureLedger(Path(self._tmp.name) / "captures.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_rejected_candidate_is_still_recorded(self):
        c = candidate(gross=1.0)
        ev = evaluate(c, cheap_costs())
        self.assertIs(ev.status, CaptureStatus.REJECTED)
        self.ledger.record(c, ev, costs=cheap_costs())
        rows = self.ledger.read_all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "REJECTED")
        self.assertIsNotNone(rows[0]["rejection_reason"])

    def test_unresolved_writes_null_costs_not_zero(self):
        b = CostBreakdown(fees_unknown(), known("spread", 1), known("slippage", 1),
                          known("impact", 1), known("funding", 0))
        c = candidate()
        ev = evaluate(c, b)
        self.ledger.record(c, ev, costs=b)
        row = self.ledger.read_all()[0]
        self.assertIsNone(row["fees_bps"])           # null, PAS 0
        self.assertEqual(row["cost_quality"]["fees"], "UNKNOWN")
        self.assertEqual(row["status"], "UNRESOLVED")
        self.assertIn("fees", row["unresolved_components"])

    def test_all_required_fields_present(self):
        from prism_v2.ledger import REQUIRED_FIELDS
        c = candidate()
        self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())
        row = self.ledger.read_all()[0]
        for f in REQUIRED_FIELDS:
            self.assertIn(f, row)

    def test_instrument_mechanics_recorded_for_audit(self):
        c = candidate()
        self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())
        row = self.ledger.read_all()[0]
        self.assertEqual(row["inst_type"], "SWAP_INVERSE")
        self.assertEqual(row["ct_type"], "inverse")
        self.assertEqual(row["ct_val"], 100.0)
        self.assertEqual(row["settle_ccy"], "BTC")

    def test_execution_mode_is_always_paper(self):
        c = candidate()
        self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())
        self.assertEqual(self.ledger.read_all()[0]["execution_mode"], "PAPER")

    def test_append_only_accumulates(self):
        for g in (1.0, 500.0, 2.0):
            c = candidate(gross=g)
            self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())
        self.assertEqual(len(self.ledger), 3)
        s = self.ledger.summary()
        self.assertEqual(s["n_records"], 3)
        self.assertEqual(s["by_status"]["REJECTED"], 2)
        self.assertEqual(s["by_status"]["ACCEPTED"], 1)

    def test_secrets_are_refused(self):
        c = candidate()
        c.metadata["okx_api_key"] = "abc123"
        with self.assertRaises(SecretInLedger):
            self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())

    def test_nested_secret_refused(self):
        c = candidate()
        c.metadata["creds"] = {"passphrase": "x"}
        with self.assertRaises(SecretInLedger):
            self.ledger.record(c, evaluate(c, cheap_costs()), costs=cheap_costs())


if __name__ == "__main__":
    unittest.main(verbosity=2)
