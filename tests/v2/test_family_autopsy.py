"""Gardes contre les detecteurs degeneres.

Ces tests existent parce que DEUX familles sur neuf ne pouvaient
STRUCTURELLEMENT jamais emettre un candidat, et que personne ne pouvait le
savoir : leur silence etait indiscernable d'un vrai resultat negatif.

Le depot avait diagnostique ce mode d'echec (commit 5ef49cb) et ecrit une garde
pour lui -- `sanity.check_firing_rate` -- qui n'etait appelee NULLE PART.
"""
from __future__ import annotations

import random
import unittest

from tests.v2.fixtures import BTC_INVERSE, PROV, okx_book_payload
from prism_v2.detectors.microstructure import (
    BookImbalanceDetector, DepthWithdrawalDetector)
from prism_v2.family_audit import FamilyFunnel, run_funnel
from prism_v2.discovery import Family
from prism_v2.market_state import MarketState
from prism_v2.orderbook import OrderBook

T0 = 1_789_000_000_000


def book(bid, ask, bsz, asz, ts=T0):
    return OrderBook.from_okx(BTC_INVERSE, okx_book_payload(
        [[f"{bid:.8f}", f"{bsz:.6f}"]], [[f"{ask:.8f}", f"{asz:.6f}"]],
        ts=str(ts)), PROV)


class TestMicropriceIsBoundedByHalfSpread(unittest.TestCase):
    """La cause racine de BOOK_IMBALANCE, isolee de tout le reste."""

    def test_deviation_never_exceeds_half_the_spread(self):
        rng = random.Random(11)
        worst = 0.0
        for _ in range(3000):
            px = rng.uniform(1.0, 100_000.0)
            ask = px * (1 + 10 ** rng.uniform(-4, 1) / 100)
            s = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                            book=book(px, ask, 10 ** rng.uniform(-3, 4),
                                      10 ** rng.uniform(-3, 4)))
            dev = s.microprice_deviation_bps
            if dev is None or s.spread_bps <= 0:
                continue
            worst = max(worst, abs(dev) / s.spread_bps)
        self.assertLessEqual(worst, 0.5 + 1e-9,
                             "le microprix vit dans [bid, ask] : |dev| <= spread/2")

    def test_a_gate_requiring_more_than_the_full_spread_is_unsatisfiable(self):
        """Documente pourquoi l'ancienne porte ne pouvait jamais etre franchie."""
        s = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                        book=book(100.0, 101.0, 1e6, 1e-6))   # desequilibre extreme
        dev = abs(s.microprice_deviation_bps)
        self.assertGreater(dev, 0.0)
        self.assertLessEqual(dev, s.spread_bps / 2 + 1e-9)
        self.assertLess(dev, s.spread_bps)        # l'ancienne porte: jamais franchie

    def test_the_corrected_detector_can_actually_fire(self):
        s = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                        book=book(100.0, 101.0, 1e6, 1e-6))
        self.assertTrue(BookImbalanceDetector().detect(s).candidates)

    def test_the_corrected_detector_still_rejects_a_balanced_book(self):
        s = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                        book=book(100.0, 101.0, 10.0, 10.0))
        self.assertFalse(BookImbalanceDetector().detect(s).candidates)


class TestDepthWithdrawalUsesRealPastSpreads(unittest.TestCase):

    def _state(self, cur_spread_bps, past_spreads, shrink=0.1):
        px = 100.0
        ask = px * (1 + cur_spread_bps / 1e4)
        d0 = 1e6
        return MarketState(
            instrument=BTC_INVERSE, ts_ms=T0 + 10_000,
            book=book(px, ask, 1.0 * shrink, 1.0 * shrink, T0 + 10_000),
            depth_history=[(T0 + i * 1000, d0, d0) for i in range(6)] +
                          [(T0 + 9_000, d0 * shrink, d0 * shrink)],
            # dans la fenetre de 5 s qui precede ts_ms, sinon hors lookback
            spread_history=[(T0 + 5_000 + i * 800, sp)
                            for i, sp in enumerate(past_spreads)])

    def test_missing_spread_history_reports_insufficient_not_nothing(self):
        """Le defaut le plus grave : une donnee absente deguisee en negatif."""
        s = self._state(50.0, [])
        out = DepthWithdrawalDetector().detect(s)
        self.assertEqual(out.status.value, "INSUFFICIENT_DATA")
        self.assertIn("spread_history", out.missing)

    def test_a_real_widening_is_detected(self):
        s = self._state(50.0, [5.0, 5.0, 6.0, 5.0, 5.0])
        cands = DepthWithdrawalDetector().detect(s).candidates
        self.assertTrue(cands, "un elargissement reel doit etre detecte")
        # le raw edge est l'exces du spread COURANT sur le plus serre du passe
        self.assertAlmostEqual(cands[0].gross_capture_bps,
                               s.spread_bps - 5.0, places=6)

    def test_no_widening_yields_nothing(self):
        s = self._state(5.0, [5.0, 5.0, 5.0])
        self.assertFalse(DepthWithdrawalDetector().detect(s).candidates)

    def test_the_current_spread_is_excluded_from_its_own_reference(self):
        """La regression exacte : se comparer a soi-meme rend excess = 0."""
        s = self._state(50.0, [5.0, 5.0])
        past = DepthWithdrawalDetector()._past_spreads(s)
        self.assertNotIn(s.spread_bps, past)
        self.assertEqual(sorted(past), [5.0, 5.0])


class TestFunnelFlagsDeadDetectors(unittest.TestCase):

    def test_a_detector_that_never_fires_is_flagged(self):
        f = FamilyFunnel(Family.BOOK_IMBALANCE, states_examined=1000,
                         insufficient_data=0, detector_nothing=1000, candidates=0)
        self.assertTrue(f.is_presumed_dead())
        self.assertEqual(f.verdict().status, "ALERTE")

    def test_a_small_sample_is_not_enough_to_declare_death(self):
        f = FamilyFunnel(Family.BOOK_IMBALANCE, states_examined=10,
                         detector_nothing=10, candidates=0)
        self.assertFalse(f.is_presumed_dead())

    def test_a_detector_that_always_fires_is_also_flagged(self):
        """Un seuil qui ne filtre rien est aussi un defaut qu'un seuil qui tue tout."""
        f = FamilyFunnel(Family.SPREAD_DISLOCATION, states_examined=1000,
                         detector_nothing=0, candidates=1000)
        self.assertEqual(f.verdict().status, "ALERTE")
        self.assertIn("filtre rien", f.verdict().detail)

    def test_funnel_separates_missing_data_from_threshold_rejection(self):
        """C'est la distinction qui manquait : « pas de donnee » n'est pas « rien »."""
        states = [MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                              book=book(100.0, 100.5, 10.0, 10.0))
                  for _ in range(5)]
        f = run_funnel([DepthWithdrawalDetector()], states)["DEPTH_WITHDRAWAL"]
        self.assertEqual(f.insufficient_data, 5)
        self.assertEqual(f.detector_nothing, 0)
        self.assertEqual(f.candidates, 0)


if __name__ == "__main__":
    unittest.main()
