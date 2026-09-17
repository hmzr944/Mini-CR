"""Polymarket — contrats binaires, rebate maker, markout.

Deux choses comptent ici plus que le reste :
  1. qu'un contrat binaire ne soit JAMAIS traite comme un perpetuel ;
  2. que le rebate soit LU sur le marche, jamais suppose.
"""
from __future__ import annotations

import inspect
import unittest

from prism_v2 import polymarket as pm
from prism_v2.polymarket import (
    MARKOUT_HORIZONS_S, MAX_STALENESS_S, BinaryMarket, MakerFill, PriceSeries,
    Side, Trade, measure_maker_fills,
)


def market(**over) -> BinaryMarket:
    base = dict(condition_id="0xabc", question="Q?", token_ids=("t1", "t2"),
                fees_enabled=True, fee_rate=0.05, rebate_rate=0.25,
                taker_only=True, fee_exponent=1.0, rewards_daily_rate=1000.0,
                rewards_min_size=200.0, rewards_max_spread=2.5,
                volume_24h=500_000.0, liquidity=300_000.0, spread=0.01)
    base.update(over)
    return BinaryMarket(**base)


class TestBinaryContractIsNotAPerpetual(unittest.TestCase):
    """Forcer un contrat binaire dans InstrumentSpec produirait des calculs
    faux sous une apparence correcte."""

    def test_binary_market_has_no_perpetual_mechanics(self):
        fields = set(BinaryMarket.__dataclass_fields__)
        for banned in ("ct_val", "ct_type", "ct_mult", "settle_ccy", "lever",
                       "tick_size", "lot_size"):
            self.assertNotIn(banned, fields)

    def test_binary_market_is_not_an_instrument_spec(self):
        from prism_v2.instruments import InstrumentSpec
        self.assertFalse(issubclass(BinaryMarket, InstrumentSpec))

    def test_polymarket_never_imports_perpetual_contract_math(self):
        src = inspect.getsource(pm)
        self.assertNotIn("from .contracts import", src)
        self.assertNotIn("from prism_v2.contracts import", src)


class TestRebateIsReadNotAssumed(unittest.TestCase):

    def test_rebate_uses_the_market_own_schedule(self):
        m = market(fee_rate=0.04, rebate_rate=0.25)
        self.assertAlmostEqual(m.maker_rebate_per_share(0.5),
                               0.25 * 0.04 * 0.25, places=12)

    def test_rebate_is_none_when_the_schedule_is_absent(self):
        for over in ({"fee_rate": None}, {"rebate_rate": None},
                     {"fees_enabled": False}):
            with self.subTest(**over):
                m = market(**over)
                self.assertFalse(m.rebate_is_observed)
                self.assertIsNone(m.maker_rebate_per_share(0.5))

    def test_rebate_peaks_at_even_odds_and_vanishes_at_the_extremes(self):
        """C'est la tension centrale : le maker est le MIEUX remunere la ou
        l'issue est la plus incertaine — donc la ou l'adverse selection est a
        priori la pire."""
        m = market()
        mid = m.maker_rebate_per_share(0.50)
        for p in (0.02, 0.10, 0.90, 0.98):
            self.assertLess(m.maker_rebate_per_share(p), mid)
        self.assertAlmostEqual(m.maker_rebate_per_share(0.001), 0.25 * 0.05 *
                               0.001 * 0.999, places=12)

    def test_price_outside_the_unit_interval_yields_zero_not_a_guess(self):
        m = market()
        for p in (0.0, 1.0, -0.5, 2.0):
            self.assertEqual(m.maker_rebate_per_share(p), 0.0)

    def test_maker_pays_fees_is_unknown_when_not_published(self):
        self.assertIsNone(market(taker_only=None).maker_pays_fees)
        self.assertFalse(market(taker_only=True).maker_pays_fees)
        self.assertTrue(market(taker_only=False).maker_pays_fees)


class TestMakerSideIsAlwaysOppositeTheTaker(unittest.TestCase):

    def test_side_inversion(self):
        self.assertIs(Side.BUY.maker_side, Side.SELL)
        self.assertIs(Side.SELL.maker_side, Side.BUY)

    def test_trade_exposes_the_maker_side(self):
        t = Trade(ts=1, price=0.4, size=10, taker_side=Side.SELL, asset="t1")
        self.assertIs(t.maker_side, Side.BUY)


class TestPriceSeriesRefusesStaleReferences(unittest.TestCase):
    """La mesure historique a echoue precisement ici : une reference vieille
    de 600 s donnait un ecart median de 5,5 fois le spread publie."""

    def setUp(self):
        self.s = PriceSeries([(1_000, 0.40), (1_600, 0.42), (2_200, 0.41)])

    def test_never_returns_a_future_price(self):
        self.assertEqual(self.s.at(1_599), 0.40)
        self.assertEqual(self.s.at(1_600), 0.42)

    def test_returns_none_before_the_first_point(self):
        self.assertIsNone(self.s.at(999))

    def test_refuses_a_reference_older_than_the_tolerance(self):
        self.assertIsNone(self.s.at(2_200 + MAX_STALENESS_S + 1))
        self.assertIsNotNone(self.s.at(2_200 + MAX_STALENESS_S - 1))

    def test_empty_series_yields_none(self):
        self.assertIsNone(PriceSeries([]).at(1_000))


class TestMakerFillAccounting(unittest.TestCase):

    def test_net_is_half_spread_plus_markout_plus_rebate_no_fee(self):
        f = MakerFill(ts=1, condition_id="c", maker_side=Side.BUY, price=0.40,
                      size=10, mid_at_fill=0.41, half_spread=0.01,
                      markout={600: -0.004}, rebate=0.003)
        self.assertAlmostEqual(f.net_per_share(600), 0.01 - 0.004 + 0.003)
        self.assertAlmostEqual(f.net_without_rebate(600), 0.01 - 0.004)

    def test_missing_term_yields_none_never_zero(self):
        f = MakerFill(ts=1, condition_id="c", maker_side=Side.BUY, price=0.4,
                      size=10, mid_at_fill=0.41, half_spread=0.01,
                      markout={600: -0.004}, rebate=None)
        self.assertIsNone(f.net_per_share(600))
        self.assertIsNotNone(f.net_without_rebate(600))

    def test_maker_buying_below_reference_earns_a_positive_half_spread(self):
        m = market()
        series = PriceSeries([(0, 0.50), (600, 0.50), (1_200, 0.50),
                              (21_600 + 600, 0.50), (43_200, 0.50)])
        trades = [Trade(ts=600, price=0.48, size=100, taker_side=Side.SELL,
                        asset="t1")]
        fills = measure_maker_fills(m, trades, series, horizons=(600,))
        self.assertEqual(len(fills), 1)
        self.assertIs(fills[0].maker_side, Side.BUY)
        self.assertAlmostEqual(fills[0].half_spread, 0.02, places=9)

    def test_maker_selling_above_reference_also_earns(self):
        m = market()
        series = PriceSeries([(0, 0.50), (600, 0.50), (1_200, 0.50),
                              (43_200, 0.50)])
        trades = [Trade(ts=600, price=0.52, size=100, taker_side=Side.BUY,
                        asset="t1")]
        fills = measure_maker_fills(m, trades, series, horizons=(600,))
        self.assertIs(fills[0].maker_side, Side.SELL)
        self.assertAlmostEqual(fills[0].half_spread, 0.02, places=9)

    def test_a_fill_whose_outcome_is_unobservable_is_dropped(self):
        """Sans issue observable, aucun chiffre n'est produit."""
        m = market()
        series = PriceSeries([(0, 0.5), (600, 0.5)])
        trades = [Trade(ts=600, price=0.48, size=10, taker_side=Side.SELL,
                        asset="t1")]
        self.assertEqual(measure_maker_fills(m, trades, series,
                                             horizons=(21_600,)), [])

    def test_horizons_are_bounded_by_the_data_resolution(self):
        """La serie publique a un pas de 600 s : mesurer en dessous
        comparerait un point a lui-meme."""
        self.assertGreaterEqual(min(MARKOUT_HORIZONS_S), 600)


if __name__ == "__main__":
    unittest.main()


class TestBookSeriesRefusesStaleAndBrokenBooks(unittest.TestCase):
    """La reference de carnet est ce qui distingue cette mesure du raccourci
    historique ecarte : elle doit etre fraiche, sinon elle mesure la derive."""

    def _recs(self):
        return [
            {"i": "t1", "ok": True, "recv": 1_000_000, "b": [[0.40, 100]],
             "a": [[0.42, 100]]},
            {"i": "t1", "ok": False, "recv": 1_010_000},          # illisible
            {"i": "t1", "ok": True, "recv": 1_020_000, "b": [[0.45, 100]],
             "a": [[0.44, 100]]},                                  # CROISE
            {"i": "t1", "ok": True, "recv": 1_030_000, "b": [[0.41, 100]],
             "a": [[0.43, 100]]},
            {"i": "t2", "ok": True, "recv": 1_000_000, "b": [[0.10, 100]],
             "a": [[0.12, 100]]},
        ]

    def test_invalid_and_crossed_books_never_enter_the_series(self):
        from prism_v2.poly_markout import BookSeries
        s = BookSeries.from_records("t1", self._recs())
        self.assertEqual(len(s), 2)          # 4 enregistrements, 2 exploitables
        self.assertEqual(s.ts, [1_000, 1_030])

    def test_series_is_isolated_per_token(self):
        from prism_v2.poly_markout import BookSeries
        self.assertEqual(len(BookSeries.from_records("t2", self._recs())), 1)

    def test_reference_older_than_the_tolerance_is_refused(self):
        from prism_v2.poly_markout import BookSeries, MAX_BOOK_AGE_S
        s = BookSeries.from_records("t1", self._recs())
        self.assertIsNotNone(s.at(1_030 + MAX_BOOK_AGE_S))
        self.assertIsNone(s.at(1_030 + MAX_BOOK_AGE_S + 1))

    def test_never_returns_a_future_book(self):
        from prism_v2.poly_markout import BookSeries
        s = BookSeries.from_records("t1", self._recs())
        self.assertIsNone(s.at(999))
        self.assertAlmostEqual(s.at(1_029)[0], 0.41, places=9)

    def test_horizons_exceed_the_collection_step(self):
        from prism_v2.poly_markout import HORIZONS_S
        self.assertGreaterEqual(min(HORIZONS_S), 60)
