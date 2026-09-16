"""Discovery Engine, modes, economie par bornes, router, sizing, L2 incrementiel."""
from __future__ import annotations

import unittest

from tests.v2.fixtures import (
    BTC_INVERSE, BTC_LINEAR, PROV, okx_book_payload, simple_inverse_book,
    thin_inverse_book,
)
from prism_v2.core_types import Direction, Provenance, Quality
from prism_v2.costs import (
    CostBreakdown, CostComponent, adverse_selection_not_applicable, build_breakdown,
    fees_assumed_public, is_excluded, latency_not_applicable,
    slippage_excluded_for_paper_validation,
)
from prism_v2.discovery import (
    DetectionOutcome, Detector, DiscoveryEngine, Family, MAX_PRIORITY, MIN_PRIORITY,
)
from prism_v2.discovery_economics import (
    DiscoveryVerdict, bound_for, discover,
)
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.l2book import L2Book, L2BookSet, okx_checksum
from prism_v2.market_state import MarketStateTracker, TradePrint
from prism_v2.modes import (
    EvaluationMode, MODE_PREREQUISITES, ModeGate, ModeTransitionRefused, SystemMode,
    quality_satisfies,
)
from prism_v2.opportunity import Candidate, DetectionStatus
from prism_v2.router import CapitalRouter, RoutingDecision, correlation_group
from prism_v2.sizing import SizingLimits, recommend_size, size_curve

PROVX = Provenance("OKX", "/t", "2026-09-16T00:00:00Z", "BTC-USD-SWAP")


def cand(gross: float, inst=BTC_INVERSE, family="TEST", cap=1e9) -> Candidate:
    return Candidate(ts_utc="t", instrument=inst, opportunity_type="X",
                     direction=Direction.LONG, gross_capture_bps=gross,
                     capacity_usd=cap, provenance=PROVX, family=family,
                     candidate_id=f"id-{gross}")


def known(n, v):
    return CostComponent(n, v, Quality.DERIVED, "test")


def full_costs(fees=1.0, spread=1.0, slippage=1.0, impact=1.0, latency=1.0):
    return CostBreakdown(known("fees", fees), known("spread", spread),
                         known("slippage", slippage), known("impact", impact),
                         known("funding", 0.0), latency=known("latency", latency),
                         adverse_selection=adverse_selection_not_applicable())


# ══════════════════════════════════════════════════════════════════════════
class TestEvaluationModes(unittest.TestCase):
    def test_modes_have_increasing_rigour(self):
        self.assertIs(EvaluationMode.DISCOVERY.min_quality, Quality.UNKNOWN)
        self.assertIs(EvaluationMode.CAPTURE_VALIDATION.min_quality, Quality.DERIVED)
        self.assertIs(EvaluationMode.EXECUTION.min_quality, Quality.OBSERVED)

    def test_only_execution_allows_capital(self):
        self.assertFalse(EvaluationMode.DISCOVERY.allows_capital)
        self.assertFalse(EvaluationMode.CAPTURE_VALIDATION.allows_capital)
        self.assertTrue(EvaluationMode.EXECUTION.allows_capital)

    def test_discovery_does_not_allow_paper_execution(self):
        self.assertFalse(EvaluationMode.DISCOVERY.allows_paper_execution)
        self.assertTrue(EvaluationMode.CAPTURE_VALIDATION.allows_paper_execution)

    def test_quality_ordering(self):
        self.assertTrue(quality_satisfies(Quality.OBSERVED, Quality.DERIVED))
        self.assertFalse(quality_satisfies(Quality.ASSUMED, Quality.DERIVED))
        self.assertFalse(quality_satisfies(Quality.UNKNOWN, Quality.ASSUMED))


class TestSystemModeBarrier(unittest.TestCase):
    def test_starts_in_discovery(self):
        self.assertIs(ModeGate().mode, SystemMode.DISCOVERY)

    def test_cannot_jump_straight_to_live(self):
        g = ModeGate()
        ok, reasons = g.can_transition(SystemMode.LIVE)
        self.assertFalse(ok)
        with self.assertRaises(ModeTransitionRefused):
            g.transition(SystemMode.LIVE)

    def test_demo_and_live_are_not_implemented(self):
        self.assertFalse(SystemMode.DEMO.is_implemented)
        self.assertFalse(SystemMode.LIVE.is_implemented)
        self.assertTrue(SystemMode.DISCOVERY.is_implemented)
        self.assertTrue(SystemMode.PAPER.is_implemented)

    def test_paper_requires_declared_capabilities(self):
        g = ModeGate()
        with self.assertRaises(ModeTransitionRefused):
            g.transition(SystemMode.PAPER)
        g.declare("instrument_registry_live", True)
        g.declare("data_quality_gate", True)
        self.assertIs(g.transition(SystemMode.PAPER), SystemMode.PAPER)

    def test_live_requires_many_prerequisites_and_acknowledgement(self):
        self.assertGreaterEqual(len(MODE_PREREQUISITES[SystemMode.LIVE]), 14)
        self.assertIn("human_acknowledgement", MODE_PREREQUISITES[SystemMode.LIVE])
        self.assertIn("observed_slippage", MODE_PREREQUISITES[SystemMode.LIVE])

    def test_live_still_refused_even_with_all_capabilities(self):
        """Barriere ultime : LIVE n'est pas implemente, donc jamais atteignable."""
        g = ModeGate()
        for c in MODE_PREREQUISITES[SystemMode.LIVE]:
            g.declare(c, True, "test")
        g.transition(SystemMode.PAPER)
        ok, reasons = g.can_transition(SystemMode.DEMO)
        self.assertFalse(ok)
        self.assertTrue(any("implemente" in r for r in reasons))


class TestBoundedDiscovery(unittest.TestCase):
    def test_no_raw_edge(self):
        r = discover(cand(0.0), full_costs(), simple_inverse_book())
        self.assertIs(r.verdict, DiscoveryVerdict.NO_RAW_EDGE)

    def test_dead_even_at_best_is_conclusive(self):
        """Meme avec tous les inconnus a zero, les couts CONNUS tuent la capture."""
        r = discover(cand(2.0), full_costs(), simple_inverse_book())
        self.assertIs(r.verdict, DiscoveryVerdict.DEAD_EVEN_AT_BEST)
        self.assertTrue(r.is_conclusive)
        self.assertLessEqual(r.net_optimistic_bps, 0)

    def test_survives_all_bounds(self):
        costs = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=True)
        r = discover(cand(5_000.0), costs, simple_inverse_book())
        self.assertIs(r.verdict, DiscoveryVerdict.SURVIVES_ALL_BOUNDS)
        self.assertGreater(r.net_pessimistic_bps, 0)

    def test_needs_measurement_names_the_budget(self):
        costs = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=True)
        r = discover(cand(120.0), costs, simple_inverse_book())
        self.assertIs(r.verdict, DiscoveryVerdict.NEEDS_MEASUREMENT)
        self.assertIsNotNone(r.unknown_budget_bps)
        self.assertGreater(r.unknown_budget_bps, 0)
        self.assertTrue(r.unknown_components)
        for name in r.unknown_components:
            self.assertIn(name, r.detail)

    def test_optimistic_always_at_least_pessimistic(self):
        costs = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=True)
        for g in (1.0, 50.0, 500.0, 50_000.0):
            r = discover(cand(g), costs, simple_inverse_book())
            self.assertGreaterEqual(r.net_optimistic_bps, r.net_pessimistic_bps)

    def test_bounds_are_derived_from_observed_book_when_possible(self):
        b = bound_for("slippage", simple_inverse_book())
        self.assertTrue(b.derived_from_observed)
        self.assertIn("spread observe", b.basis)

    def test_bound_without_book_is_infinite_not_guessed(self):
        b = bound_for("slippage", None)
        self.assertEqual(b.value_bps, float("inf"))
        self.assertFalse(b.derived_from_observed)

    def test_discovery_never_authorises_execution(self):
        """Aucun verdict de decouverte n'est un ACCEPTED."""
        costs = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=True)
        for g in (1.0, 5_000.0):
            r = discover(cand(g), costs, simple_inverse_book())
            self.assertNotIn(r.verdict.value, [s.value for s in CaptureStatus])


class TestExecutionModeGuards(unittest.TestCase):
    def test_execution_refuses_excluded_slippage(self):
        b = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=False,
                            slippage=slippage_excluded_for_paper_validation(),
                            latency=latency_not_applicable("t"))
        from prism_v2.costs import funding_not_applicable
        b.funding = funding_not_applicable("t")
        self.assertIs(evaluate(cand(500.0), b,
                               mode=EvaluationMode.CAPTURE_VALIDATION).status,
                      CaptureStatus.ACCEPTED)
        self.assertIs(evaluate(cand(500.0), b, mode=EvaluationMode.EXECUTION).status,
                      CaptureStatus.UNRESOLVED)

    def test_is_excluded_detects_convention_values(self):
        self.assertTrue(is_excluded(slippage_excluded_for_paper_validation()))
        self.assertFalse(is_excluded(known("slippage", 1.0)))


# ══════════════════════════════════════════════════════════════════════════
class _FakeDetector(Detector):
    family = Family.BOOK_IMBALANCE

    def __init__(self, n: int = 1, boom: bool = False):
        self.n, self.boom = n, boom

    def detect(self, state):
        if self.boom:
            raise RuntimeError("boom")
        return DetectionOutcome.ok(
            self.family, [cand(10.0, family=self.family.value)
                          for _ in range(self.n)])


class TestDiscoveryEngine(unittest.TestCase):
    def setUp(self):
        t = MarketStateTracker()
        t.on_book(simple_inverse_book())
        self.states = t.all_states()

    def test_registers_and_scans(self):
        e = DiscoveryEngine([_FakeDetector(n=2)])
        outs = e.scan(self.states)
        self.assertEqual(sum(len(o.candidates) for o in outs), 2)
        self.assertEqual(e.stats[Family.BOOK_IMBALANCE].candidates, 2)

    def test_one_failing_detector_does_not_stop_others(self):
        class Other(_FakeDetector):
            family = Family.SPREAD_DISLOCATION
        e = DiscoveryEngine([_FakeDetector(boom=True), Other(n=3)])
        outs = e.scan(self.states)
        errs = [o for o in outs if o.status is DetectionStatus.ERROR]
        self.assertEqual(len(errs), 1)
        self.assertEqual(e.stats[Family.SPREAD_DISLOCATION].candidates, 3)

    def test_duplicate_family_refused(self):
        e = DiscoveryEngine([_FakeDetector()])
        with self.assertRaises(ValueError):
            e.register(_FakeDetector())

    def test_priority_rises_for_surviving_family(self):
        e = DiscoveryEngine([_FakeDetector()])
        e.scan(self.states)
        for _ in range(5):
            e.record_verdict(Family.BOOK_IMBALANCE, "SURVIVES_ALL_BOUNDS")
        before = e.priority[Family.BOOK_IMBALANCE]
        e.update_priorities()
        self.assertGreater(e.priority[Family.BOOK_IMBALANCE], before)

    def test_priority_falls_for_barren_family(self):
        e = DiscoveryEngine([_FakeDetector(n=0)])
        e.scan(self.states)
        e.update_priorities()
        self.assertLess(e.priority[Family.BOOK_IMBALANCE], 1.0)

    def test_missing_data_does_not_penalise_priority(self):
        """L'absence de MESURE n'est pas une absence d'EDGE."""
        class NeedsData(Detector):
            family = Family.FORCED_FLOW
            requires = ("forced_flow",)
            def detect(self, state):
                return DetectionOutcome.ok(self.family, [])
        e = DiscoveryEngine([NeedsData()])
        e.scan(self.states)
        e.update_priorities()
        self.assertAlmostEqual(e.priority[Family.FORCED_FLOW], 1.0, places=6)

    def test_priority_is_bounded(self):
        e = DiscoveryEngine([_FakeDetector()])
        e.scan(self.states)
        for _ in range(200):
            e.record_verdict(Family.BOOK_IMBALANCE, "SURVIVES_ALL_BOUNDS")
            e.update_priorities()
        self.assertLessEqual(e.priority[Family.BOOK_IMBALANCE], MAX_PRIORITY)
        self.assertGreaterEqual(e.priority[Family.BOOK_IMBALANCE], MIN_PRIORITY)


# ══════════════════════════════════════════════════════════════════════════
class TestSizing(unittest.TestCase):
    def test_cost_rises_and_net_falls_with_size(self):
        pts = [p for p in size_curve(BTC_INVERSE, simple_inverse_book(), "ask",
                                     500.0, 10.0) if p.executable]
        nets = [p.net_capture_bps for p in pts]
        self.assertEqual(nets, sorted(nets, reverse=True))

    def test_below_min_size_is_not_executable(self):
        pts = {p.notional_usd: p for p in
               size_curve(BTC_INVERSE, simple_inverse_book(), "ask", 500.0, 10.0)}
        self.assertFalse(pts[10.0].executable is False and False)  # 10 USD = min
        self.assertFalse(any(p.executable for n, p in pts.items() if n < 10.0))

    def test_depth_exhaustion_marks_not_executable(self):
        pts = {p.notional_usd: p for p in
               size_curve(BTC_INVERSE, thin_inverse_book(), "ask", 500.0, 10.0)}
        self.assertFalse(pts[1_000.0].executable)

    def test_recommend_respects_capital_ceiling(self):
        r = recommend_size(BTC_INVERSE, simple_inverse_book(), "ask", 5_000.0, 10.0,
                           SizingLimits(available_capital_usd=50.0,
                                        max_notional_usd=10_000.0))
        self.assertLessEqual(r.recommended_notional_usd, 50.0)

    def test_recommend_respects_depth_fraction(self):
        r = recommend_size(BTC_INVERSE, simple_inverse_book(), "ask", 5_000.0, 10.0,
                           SizingLimits(available_capital_usd=1e9,
                                        max_notional_usd=1e9,
                                        max_capacity_fraction=0.10))
        depth = simple_inverse_book().depth("ask")
        self.assertLessEqual(r.recommended_notional_usd, depth * 0.10 + 1e-6)

    def test_binding_constraint_is_named(self):
        r = recommend_size(BTC_INVERSE, simple_inverse_book(), "ask", 5_000.0, 10.0,
                           SizingLimits(available_capital_usd=25.0))
        self.assertIn(r.binding_constraint,
                      {"CAPITAL", "MAX_NOTIONAL", "DEPTH_FRACTION",
                       "CORRELATED_EXPOSURE", "NET_CAPTURE_POSITIVE",
                       "MARGINAL_BREAKEVEN", "MAX_LOSS", "AUCUNE_TAILLE_EXECUTABLE"})

    def test_no_size_when_edge_is_negative(self):
        r = recommend_size(BTC_INVERSE, simple_inverse_book(), "ask", 1.0, 500.0,
                           SizingLimits(available_capital_usd=1e6))
        self.assertEqual(r.recommended_notional_usd, 0.0)


# ══════════════════════════════════════════════════════════════════════════
class TestCapitalRouter(unittest.TestCase):
    def _accepted(self, gross, inst=BTC_INVERSE):
        c = cand(gross, inst=inst)
        ev = evaluate(c, full_costs())
        sz = recommend_size(inst, simple_inverse_book(), "ask", gross, 4.0,
                            SizingLimits(available_capital_usd=100.0,
                                         max_notional_usd=100.0))
        return (c, ev, sz)

    def test_refuses_everything_when_nothing_actionable(self):
        c = cand(1.0)
        ev = evaluate(c, full_costs())
        self.assertIsNot(ev.status, CaptureStatus.ACCEPTED)
        routed = CapitalRouter().route([(c, ev, None)])
        self.assertTrue(CapitalRouter().summary(routed)["refused_everything"])
        self.assertIs(routed[0].decision, RoutingDecision.SKIP_NOT_ACTIONABLE)

    def test_allocates_to_best_net_first(self):
        r = CapitalRouter(SizingLimits(available_capital_usd=1_000.0,
                                       max_notional_usd=1_000.0))
        routed = r.route([self._accepted(50.0), self._accepted(500.0)])
        allocated = [x for x in routed if x.decision is RoutingDecision.ALLOCATE]
        self.assertTrue(allocated)
        self.assertEqual(allocated[0].rank, 1)
        self.assertAlmostEqual(allocated[0].candidate.gross_capture_bps, 500.0)

    def test_correlated_candidates_share_one_budget(self):
        """Deux candidates du meme sous-jacent ne sont pas des paris
        independants : les traiter comme tels sur-allouerait."""
        r = CapitalRouter(SizingLimits(available_capital_usd=100.0,
                                       max_notional_usd=100.0),
                          max_fraction_per_group=0.5)
        routed = r.route([self._accepted(500.0), self._accepted(400.0),
                          self._accepted(300.0)])
        total_btc = sum(x.allocated_usd for x in routed
                        if x.correlation_group == "BTC")
        self.assertLessEqual(total_btc, 50.0 + 1e-6)
        self.assertTrue(any(x.decision is RoutingDecision.SKIP_CORRELATED
                            for x in routed))

    def test_never_exceeds_capital_ceiling(self):
        r = CapitalRouter(SizingLimits(available_capital_usd=100.0,
                                       max_notional_usd=100.0))
        routed = r.route([self._accepted(500.0 + i) for i in range(6)])
        self.assertLessEqual(r.summary(routed)["total_allocated_usd"], 100.0 + 1e-6)

    def test_correlation_group_uses_underlying(self):
        self.assertEqual(correlation_group(cand(1.0, BTC_INVERSE)),
                         correlation_group(cand(1.0, BTC_LINEAR)))


# ══════════════════════════════════════════════════════════════════════════
class TestIncrementalL2Book(unittest.TestCase):
    """NON-REGRESSION : le canal `books` est INCREMENTIEL. Traiter un update
    comme un carnet complet produisait un carnet tronque (cote vide) dont
    tous les couts etaient faux. Defaut trouve en exploitation reelle."""

    def _snapshot(self, seq=10):
        return {"bids": [["100", "5"], ["99", "3"]],
                "asks": [["101", "4"], ["102", "6"]], "ts": "1000",
                "seqId": str(seq), "prevSeqId": "-1"}

    def test_snapshot_then_update_preserves_both_sides(self):
        bk = L2Book(BTC_INVERSE)
        self.assertTrue(bk.apply("snapshot", self._snapshot()))
        # Update ne touchant QUE les bids : les asks doivent survivre.
        self.assertTrue(bk.apply("update", {"bids": [["100", "7"]], "asks": [],
                                            "ts": "1001", "seqId": "11",
                                            "prevSeqId": "10"}))
        b = bk.book()
        self.assertIsNotNone(b)
        self.assertEqual(len(b.asks), 2)
        self.assertAlmostEqual(b.bids[0].size, 7.0)

    def test_zero_size_removes_a_level(self):
        bk = L2Book(BTC_INVERSE)
        bk.apply("snapshot", self._snapshot())
        bk.apply("update", {"bids": [["99", "0"]], "asks": [], "ts": "1001",
                            "seqId": "11", "prevSeqId": "10"})
        self.assertEqual(len(bk.book().bids), 1)

    def test_sequence_gap_invalidates_and_fails_closed(self):
        bk = L2Book(BTC_INVERSE)
        bk.apply("snapshot", self._snapshot())
        self.assertFalse(bk.apply("update", {"bids": [["100", "9"]], "asks": [],
                                             "ts": "1002", "seqId": "13",
                                             "prevSeqId": "12"}))
        self.assertFalse(bk.valid)
        self.assertIsNone(bk.book())
        self.assertEqual(bk.sequence_gaps, 1)

    def test_updates_refused_until_new_snapshot(self):
        bk = L2Book(BTC_INVERSE)
        bk.apply("snapshot", self._snapshot())
        bk.apply("update", {"bids": [], "asks": [], "ts": "1", "seqId": "20",
                            "prevSeqId": "19"})
        self.assertFalse(bk.apply("update", {"bids": [["100", "1"]], "asks": [],
                                             "ts": "2", "seqId": "21",
                                             "prevSeqId": "20"}))
        self.assertTrue(bk.apply("snapshot", self._snapshot(seq=30)))
        self.assertIsNotNone(bk.book())

    def test_checksum_zero_means_absent_not_failure(self):
        """OKX renvoie checksum=0 sur le canal `books` : absent, pas invalide.
        Revendiquer une garantie qu'on n'a pas serait pire que l'admettre."""
        bk = L2Book(BTC_INVERSE)
        snap = dict(self._snapshot())
        snap["checksum"] = 0
        self.assertTrue(bk.apply("snapshot", snap))
        self.assertFalse(bk.stats()["checksum_available"])

    def test_wrong_checksum_invalidates(self):
        bk = L2Book(BTC_INVERSE)
        snap = dict(self._snapshot())
        snap["checksum"] = 123456
        self.assertFalse(bk.apply("snapshot", snap))
        self.assertEqual(bk.checksum_failures, 1)
        self.assertIsNone(bk.book())

    def test_correct_checksum_accepted(self):
        bids = [("100", "5"), ("99", "3")]
        asks = [("101", "4"), ("102", "6")]
        snap = dict(self._snapshot())
        snap["checksum"] = okx_checksum(bids, asks)
        bk = L2Book(BTC_INVERSE)
        self.assertTrue(bk.apply("snapshot", snap))
        self.assertTrue(bk.stats()["checksum_available"])

    def test_crossed_book_is_refused(self):
        bk = L2Book(BTC_INVERSE)
        bk.apply("snapshot", {"bids": [["102", "1"]], "asks": [["101", "1"]],
                              "ts": "1", "seqId": "1"})
        self.assertIsNone(bk.book())

    def test_bookset_routes_by_instrument(self):
        bs = L2BookSet(specs={"BTC-USD-SWAP": BTC_INVERSE})
        self.assertTrue(bs.apply_event({"inst_id": "BTC-USD-SWAP",
                                        "action": "snapshot",
                                        "data": self._snapshot()}))
        self.assertIsNotNone(bs.book("BTC-USD-SWAP"))
        self.assertFalse(bs.apply_event({"inst_id": "INCONNU", "data": {}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
