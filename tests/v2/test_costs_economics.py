"""Modele de cout typé + Expected Net Capture.

Coeur du garde-fou V2 : un cout UNKNOWN ne devient jamais zero.
"""
from __future__ import annotations

import unittest

from tests.v2.fixtures import BTC_INVERSE, simple_inverse_book, thin_inverse_book
from prism_v2.core_types import Direction, Provenance, Quality
from prism_v2.costs import (
    CostBreakdown, CostComponent, ExecutionStyle, LEGACY_ASSUMPTION_V33_BPS,
    build_breakdown, fees_assumed_public, fees_unknown, funding_not_applicable,
    funding_observed, funding_unknown, impact_from_book, latency_from_replay,
    latency_not_applicable, latency_unknown, legacy_v33_assumption,
    adverse_selection_not_applicable, slippage_observed, slippage_unknown,
    spread_from_book,
)
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.opportunity import Candidate

PROV = Provenance("OKX", "/test", "2026-09-16T00:00:00Z", "BTC-USD-SWAP")


def candidate(gross_bps: float, capacity: float | None = 1e9) -> Candidate:
    return Candidate(ts_utc="2026-09-16T00:00:00Z", instrument=BTC_INVERSE,
                     opportunity_type="TEST", direction=Direction.LONG,
                     gross_capture_bps=gross_bps, capacity_usd=capacity, provenance=PROV)


def known(name: str, v: float) -> CostComponent:
    return CostComponent(name, v, Quality.DERIVED, "test")


def breakdown(fees=1.0, spread=1.0, slippage=1.0, impact=1.0, funding=0.0,
              latency=0.0) -> CostBreakdown:
    """Toutes composantes connues. `latency` est desormais ESSENTIELLE en TAKER."""
    return CostBreakdown(known("fees", fees), known("spread", spread),
                         known("slippage", slippage), known("impact", impact),
                         known("funding", funding), latency=known("latency", latency),
                         adverse_selection=adverse_selection_not_applicable())


class TestCostComponentInvariants(unittest.TestCase):
    def test_unknown_cannot_carry_value(self):
        with self.assertRaises(ValueError):
            CostComponent("fees", 5.0, Quality.UNKNOWN, "s")

    def test_known_quality_requires_value(self):
        for q in (Quality.OBSERVED, Quality.DERIVED, Quality.ASSUMED):
            with self.subTest(q=q):
                with self.assertRaises(ValueError):
                    CostComponent("fees", None, q, "s")

    def test_negative_cost_refused(self):
        with self.assertRaises(ValueError):
            CostComponent("fees", -1.0, Quality.DERIVED, "s")

    def test_unknown_factory_is_consistent(self):
        c = CostComponent.unknown("fees", "non mesure")
        self.assertIsNone(c.value_bps)
        self.assertFalse(c.is_known)


class TestBreakdownTotals(unittest.TestCase):
    def test_total_sums_when_all_known(self):
        self.assertAlmostEqual(breakdown(1, 2, 3, 4, 5, latency=6).total_bps(), 21.0,
                               places=9)

    def test_total_is_none_when_essential_unknown(self):
        b = CostBreakdown(fees_unknown(), known("spread", 1), known("slippage", 1),
                          known("impact", 1), known("funding", 0),
                          latency=known("latency", 0),
                          adverse_selection=adverse_selection_not_applicable())
        self.assertIsNone(b.total_bps())
        self.assertEqual(b.unresolved_essentials(), ["fees"])

    def test_unknown_funding_alone_does_not_block(self):
        """funding n'est pas essentiel : il depend de l'horizon, declare par
        l'Opportunity. Il est neanmoins signale comme UNKNOWN."""
        b = CostBreakdown(known("fees", 1), known("spread", 1), known("slippage", 1),
                          known("impact", 1), funding_unknown(),
                          latency=known("latency", 0),
                          adverse_selection=adverse_selection_not_applicable())
        self.assertEqual(b.unresolved_essentials(), [])
        self.assertIn("funding", b.unknown_components())
        self.assertAlmostEqual(b.total_bps(), 4.0, places=9)

    def test_latency_is_essential_for_taker(self):
        """Ajout du Prompt 3 : arriver en retard est un COUT, pas un detail."""
        b = CostBreakdown(known("fees", 1), known("spread", 1), known("slippage", 1),
                          known("impact", 1), known("funding", 0),
                          latency=latency_unknown(),
                          adverse_selection=adverse_selection_not_applicable())
        self.assertIn("latency", b.unresolved_essentials())
        self.assertIsNone(b.total_bps())

    def test_adverse_selection_essential_only_for_maker(self):
        """Un maker peut etre rempli PARCE QUE le marche va contre lui.
        Supposer qu'il est meilleur parce que ses frais sont plus bas est
        une erreur ; le modele l'interdit structurellement."""
        common = dict(fees=known("fees", 1), spread=known("spread", 1),
                      slippage=known("slippage", 1), impact=known("impact", 1),
                      funding=known("funding", 0), latency=known("latency", 0))
        taker = CostBreakdown(**common, adverse_selection=CostComponent.unknown(
            "adverse_selection", "n/a"), style=ExecutionStyle.TAKER)
        maker = CostBreakdown(**common, adverse_selection=CostComponent.unknown(
            "adverse_selection", "n/a"), style=ExecutionStyle.MAKER)
        self.assertNotIn("adverse_selection", taker.unresolved_essentials())
        self.assertIn("adverse_selection", maker.unresolved_essentials())

    def test_weakest_quality_propagates(self):
        base = dict(spread=known("spread", 1), slippage=known("slippage", 1),
                    impact=known("impact", 1), funding=known("funding", 0),
                    latency=known("latency", 0),
                    adverse_selection=adverse_selection_not_applicable())
        self.assertIs(CostBreakdown(fees_assumed_public(), **base).weakest_quality(),
                      Quality.ASSUMED)
        self.assertIs(CostBreakdown(fees_unknown(), **base).weakest_quality(),
                      Quality.UNKNOWN)


class TestLegacyAssumption(unittest.TestCase):
    def test_legacy_28bps_is_assumed_and_never_default(self):
        c = legacy_v33_assumption()
        self.assertEqual(c.value_bps, LEGACY_ASSUMPTION_V33_BPS)
        self.assertIs(c.quality, Quality.ASSUMED)
        self.assertIn("backtest_v33.py", c.source)

    def test_default_breakdown_does_not_contain_28bps(self):
        b = build_breakdown(simple_inverse_book(), "ask", 500.0)
        for c in b.components():
            if c.value_bps is not None:
                self.assertNotAlmostEqual(c.value_bps, 28.0, places=6)


class TestCostsFromBook(unittest.TestCase):
    def test_spread_cost_derived_and_flags_exit_extrapolation(self):
        c = spread_from_book(simple_inverse_book(), "ask", legs=2)
        self.assertIs(c.quality, Quality.DERIVED)
        self.assertAlmostEqual(c.value_bps, 100.0, places=9)   # 50 bps x 2
        self.assertIn("extrapolee", c.note)

    def test_impact_unknown_when_depth_insufficient(self):
        c = impact_from_book(thin_inverse_book(), "ask", 10_000.0)
        self.assertIs(c.quality, Quality.UNKNOWN)
        self.assertIsNone(c.value_bps)
        self.assertIn("profondeur insuffisante", c.note)

    def test_strict_mode_leaves_fees_slippage_latency_unknown(self):
        b = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=True)
        self.assertEqual(sorted(b.unresolved_essentials()),
                         ["fees", "latency", "slippage"])

    def test_paper_slippage_zero_no_longer_exists(self):
        """Correction du Prompt 3 : un fill simule sur le carnet du meme
        instant ne MESURE pas le slippage d'execution, il le contourne.
        Aucune fabrique ne doit produire un slippage nul 'parce que PAPER'."""
        import prism_v2.costs as costs_mod
        self.assertFalse([n for n in dir(costs_mod) if "paper" in n.lower()
                          and "slippage" in n.lower()])
        b = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=False)
        self.assertIn("slippage", b.unresolved_essentials())

    def test_measured_components_resolve_the_breakdown(self):
        """Une economie ne devient resoluble que par la MESURE, pas par decret."""
        b = build_breakdown(simple_inverse_book(), "ask", 500.0, strict_fees=False)
        b.funding = funding_not_applicable("test")
        b.slippage = slippage_observed(1.5, "fills reels", n=42)
        b.latency = latency_from_replay(2.0, 100, "replay", n=17)
        self.assertEqual(b.unresolved_essentials(), [])
        self.assertIs(b.weakest_quality(), Quality.ASSUMED)   # frais restent ASSUMED

    def test_latency_from_replay_clamps_negative_decay(self):
        c = latency_from_replay(-3.0, 100, "replay", n=5)
        self.assertAlmostEqual(c.value_bps, 0.0, places=9)
        self.assertIn("ramene a 0", c.note)

    def test_funding_observed_computation(self):
        c = funding_observed(0.0001, 3, "test")
        self.assertAlmostEqual(c.value_bps, 3.0, places=9)   # 0.01% x 3 = 3 bps
        self.assertIs(c.quality, Quality.OBSERVED)


class TestExpectedNetCapture(unittest.TestCase):
    def test_accepted_when_gross_exceeds_costs(self):
        e = evaluate(candidate(20.0), breakdown(1, 1, 1, 1, 0))
        self.assertIs(e.status, CaptureStatus.ACCEPTED)
        self.assertAlmostEqual(e.expected_net_capture_bps, 16.0, places=9)
        self.assertTrue(e.is_actionable)

    def test_rejected_when_net_negative(self):
        e = evaluate(candidate(2.0), breakdown(1, 1, 1, 1, 0))
        self.assertIs(e.status, CaptureStatus.REJECTED)
        self.assertAlmostEqual(e.expected_net_capture_bps, -2.0, places=9)
        self.assertIn("<= 0", e.rejection_reason)

    def test_rejected_when_net_exactly_zero(self):
        """net == 0 est un rejet : une capture qui couvre juste ses couts
        n'est pas une capture."""
        e = evaluate(candidate(4.0), breakdown(1, 1, 1, 1, 0))
        self.assertIs(e.status, CaptureStatus.REJECTED)
        self.assertAlmostEqual(e.expected_net_capture_bps, 0.0, places=9)

    def test_unresolved_when_essential_unknown_even_if_gross_is_huge(self):
        """LE test central : un brut enorme ne doit PAS produire ACCEPTED
        si une composante essentielle est inconnue."""
        b = CostBreakdown(fees_unknown(), known("spread", 1), slippage_unknown(),
                          known("impact", 1), known("funding", 0),
                          latency=known("latency", 0),
                          adverse_selection=adverse_selection_not_applicable())
        e = evaluate(candidate(10_000.0), b)
        self.assertIs(e.status, CaptureStatus.UNRESOLVED)
        self.assertIsNone(e.expected_net_capture_bps)
        self.assertIsNone(e.total_cost_bps)
        self.assertEqual(sorted(e.unresolved_components), ["fees", "slippage"])
        self.assertFalse(e.is_actionable)

    def test_unknown_is_never_silently_zero(self):
        """Si UNKNOWN valait 0, le net serait 9998 et le statut ACCEPTED."""
        b = CostBreakdown(fees_unknown(), known("spread", 1), slippage_unknown(),
                          known("impact", 1), known("funding", 0),
                          latency=known("latency", 0),
                          adverse_selection=adverse_selection_not_applicable())
        e = evaluate(candidate(10_000.0), b)
        self.assertNotEqual(e.status, CaptureStatus.ACCEPTED)
        self.assertIsNone(e.expected_net_capture_bps)

    def test_capacity_shortfall_is_an_economic_rejection(self):
        e = evaluate(candidate(50.0, capacity=500.0), breakdown(1, 1, 1, 1, 0),
                     required_notional_usd=1000.0)
        self.assertIs(e.status, CaptureStatus.REJECTED)
        self.assertIn("capacite insuffisante", e.rejection_reason)

    def test_capacity_sufficient_passes(self):
        e = evaluate(candidate(50.0, capacity=5000.0), breakdown(1, 1, 1, 1, 0),
                     required_notional_usd=1000.0)
        self.assertIs(e.status, CaptureStatus.ACCEPTED)

    def test_weakest_quality_is_reported_on_accepted(self):
        b = CostBreakdown(fees_assumed_public(), known("spread", 1), known("slippage", 1),
                          known("impact", 1), known("funding", 0),
                          latency=known("latency", 0),
                          adverse_selection=adverse_selection_not_applicable())
        e = evaluate(candidate(100.0), b)
        self.assertIs(e.status, CaptureStatus.ACCEPTED)
        self.assertIs(e.weakest_quality, Quality.ASSUMED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
