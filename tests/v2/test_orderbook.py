"""Carnet : parsing, spread, VWAP, profondeur, impact."""
from __future__ import annotations

import unittest

from tests.v2.fixtures import (
    BTC_INVERSE, BTC_LINEAR, PROV, okx_book_payload, simple_inverse_book, thin_inverse_book,
)
from prism_v2.orderbook import EmptyBook, OrderBook, cost_bps


class TestParsingAndNotional(unittest.TestCase):
    def test_levels_converted_to_usd_via_spec_not_price(self):
        """ctVal=100 USD : 10 contrats = 1000 USD, quel que soit le prix."""
        b = simple_inverse_book()
        self.assertAlmostEqual(b.asks[0].notional_usd, 1000.0, places=9)
        self.assertAlmostEqual(b.asks[1].notional_usd, 2000.0, places=9)
        self.assertAlmostEqual(b.bids[0].notional_usd, 1000.0, places=9)

    def test_same_payload_gives_different_notional_for_linear(self):
        """Le MEME payload lu comme lineaire donne un notionnel tout autre."""
        payload = okx_book_payload([["99.0", "10"]], [["100.0", "10"]])
        inv = OrderBook.from_okx(BTC_INVERSE, payload, PROV)
        lin = OrderBook.from_okx(BTC_LINEAR, payload, PROV)
        self.assertAlmostEqual(inv.asks[0].notional_usd, 1000.0, places=9)   # 10*100
        self.assertAlmostEqual(lin.asks[0].notional_usd, 10.0, places=9)     # 10*0.01*100
        self.assertNotAlmostEqual(inv.asks[0].notional_usd, lin.asks[0].notional_usd)

    def test_levels_are_sorted_correctly(self):
        b = simple_inverse_book()
        self.assertEqual([l.price for l in b.asks], [100.0, 101.0, 102.0])
        self.assertEqual([l.price for l in b.bids], [99.0, 98.0])

    def test_empty_book_raises(self):
        with self.assertRaises(EmptyBook):
            OrderBook.from_okx(BTC_INVERSE, [], PROV)
        with self.assertRaises(EmptyBook):
            OrderBook.from_okx(BTC_INVERSE, okx_book_payload([], [["100", "1"]]), PROV).mid

    def test_non_positive_levels_are_dropped(self):
        b = OrderBook.from_okx(BTC_INVERSE,
                               okx_book_payload([["99", "10"], ["0", "5"], ["98", "0"]],
                                                [["100", "10"]]), PROV)
        self.assertEqual(len(b.bids), 1)


class TestSpread(unittest.TestCase):
    def test_spread_and_mid(self):
        b = simple_inverse_book()
        self.assertAlmostEqual(b.best_bid, 99.0)
        self.assertAlmostEqual(b.best_ask, 100.0)
        self.assertAlmostEqual(b.mid, 99.5)
        self.assertAlmostEqual(b.spread, 1.0)

    def test_spread_bps_is_market_descriptor_at_mid(self):
        """1.0 / 99.5 * 10000 = 100.5025 bps"""
        self.assertAlmostEqual(simple_inverse_book().spread_bps, 100.50251256281406, places=9)

    def test_crossing_cost_uses_execution_price_denominator(self):
        """Achat: mid 99.5 -> ask 100.0. Cout = 0.5/100.0 = 50 bps
        (et NON 0.5/99.5 = 50.2513 bps, qui serait la convention lineaire)."""
        b = simple_inverse_book()
        self.assertAlmostEqual(b.crossing_cost_bps("ask"), 50.0, places=9)
        self.assertAlmostEqual(b.crossing_cost_bps("bid"), 0.5 / 99.0 * 10_000, places=9)
        self.assertNotAlmostEqual(b.crossing_cost_bps("ask"), b.spread_bps / 2, places=6)

    def test_cost_bps_is_symmetric_in_definition(self):
        self.assertAlmostEqual(cost_bps(101.0, 100.0, "ask"), 1 / 101 * 10_000, places=9)
        self.assertAlmostEqual(cost_bps(99.0, 100.0, "bid"), 1 / 99 * 10_000, places=9)
        self.assertGreater(cost_bps(101.0, 100.0, "ask"), 0)
        self.assertGreater(cost_bps(99.0, 100.0, "bid"), 0)


class TestDepthAndVWAP(unittest.TestCase):
    def test_depth_totals(self):
        b = simple_inverse_book()
        self.assertAlmostEqual(b.ask_depth(), 6000.0, places=9)   # 1000+2000+3000
        self.assertAlmostEqual(b.bid_depth(), 3000.0, places=9)   # 1000+2000
        self.assertAlmostEqual(b.ask_depth(max_levels=1), 1000.0, places=9)

    def test_vwap_within_first_level(self):
        """500 USD sur un niveau de 1000 USD @100 -> VWAP exactement 100."""
        self.assertAlmostEqual(simple_inverse_book().vwap_for_notional("ask", 500.0),
                               100.0, places=9)

    def test_vwap_across_two_levels(self):
        """3000 USD = 1000@100 + 2000@101 -> (1000*100 + 2000*101)/3000 = 100.6667"""
        b = simple_inverse_book()
        self.assertAlmostEqual(b.vwap_for_notional("ask", 3000.0),
                               (1000 * 100 + 2000 * 101) / 3000, places=9)

    def test_walk_reports_levels_and_exhaustion(self):
        b = simple_inverse_book()
        w = b.walk("ask", 3000.0)
        self.assertEqual(w.levels_consumed, 2)
        self.assertFalse(w.exhausted)
        self.assertAlmostEqual(w.fill_ratio, 1.0)

    def test_walk_exhausts_and_does_not_invent_liquidity(self):
        """Demande 10 000 USD sur un carnet de 6 000 USD."""
        w = simple_inverse_book().walk("ask", 10_000.0)
        self.assertTrue(w.exhausted)
        self.assertTrue(w.is_partial)
        self.assertAlmostEqual(w.filled_notional, 6000.0, places=9)
        self.assertAlmostEqual(w.fill_ratio, 0.6, places=9)

    def test_thin_book_partial_fill(self):
        w = thin_inverse_book().walk("ask", 1000.0)
        self.assertTrue(w.exhausted)
        self.assertAlmostEqual(w.filled_notional, 100.0, places=9)

    def test_impact_increases_with_size(self):
        b = simple_inverse_book()
        small = b.market_impact_bps("ask", 500.0)
        large = b.market_impact_bps("ask", 5000.0)
        self.assertGreater(large, small)

    def test_slippage_vs_touch_is_zero_at_touch(self):
        b = simple_inverse_book()
        self.assertAlmostEqual(b.slippage_vs_touch_bps("ask", 500.0), 0.0, places=9)
        self.assertGreater(b.slippage_vs_touch_bps("ask", 3000.0), 0.0)

    def test_invalid_arguments_rejected(self):
        b = simple_inverse_book()
        for bad in (0.0, -100.0):
            with self.assertRaises(ValueError):
                b.walk("ask", bad)
        with self.assertRaises(ValueError):
            b.walk("middle", 100.0)

    def test_no_signal_functions_exist(self):
        """Garde anti-derive : le carnet ne doit exposer aucun predicteur."""
        forbidden = ("signal", "predict", "direction", "imbalance_signal", "score")
        for name in dir(OrderBook):
            if name.startswith("_"):
                continue
            self.assertNotIn(name.lower(), forbidden, f"OrderBook.{name} ressemble a un signal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
