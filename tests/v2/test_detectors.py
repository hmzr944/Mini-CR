"""Detecteurs par famille : ce qu'ils mesurent, et ce qu'ils refusent d'affirmer."""
from __future__ import annotations

import unittest

from tests.v2.fixtures import BTC_INVERSE, BTC_LINEAR, PROV, okx_book_payload
from prism_v2.core_types import Direction, Quality
from prism_v2.detectors.cross_market import (
    USD_EQUIVALENT_QUOTES, CrossMarketDetector, comparable,
)
from prism_v2.detectors.cross_venue import CrossVenueDetector
from prism_v2.detectors.forced_flow import ForcedFlowDetector
from prism_v2.detectors.funding_basis import FundingBasisDetector
from prism_v2.detectors.microstructure import (
    AggressiveFlowDetector, BookImbalanceDetector, ShortHorizonReversionDetector,
    SpreadDislocationDetector,
)
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.market_state import (
    ForcedFlowEvent, MarketState, MarketStateTracker, TradePrint,
)
from prism_v2.opportunity import DetectionStatus
from prism_v2.orderbook import OrderBook
from prism_v2.venues import VenueLevel, VenueQuote

T0 = 1_789_000_000_000

BTC_SPOT_EUR = InstrumentSpec(
    inst_id="BTC-EUR", exchange="OKX", inst_type=InstrumentType.SPOT, ct_type="",
    base="BTC", quote="EUR", settle_ccy="EUR", ct_val=1.0, ct_val_ccy="BTC",
    ct_mult=1.0, tick_size=0.1, lot_size=1e-8, min_size=1e-5, state="live",
    fetched_at="t")


#: Taille par defaut des carnets de test. Elle doit PORTER le notionnel de
#: sonde des detecteurs (1 000 USD) : depuis que `vwap_for_notional` refuse de
#: donner un prix pour une taille que le carnet n'absorbe pas, un carnet plus
#: mince ferait echouer la detection pour cause de profondeur, pas de mesure.
#: Pour un lineaire a 110 USD, 100 contrats ne valent que 110 USD.
DEFAULT_TEST_SIZE = 2_000.0


def book(spec, bid, ask, bid_sz=DEFAULT_TEST_SIZE, ask_sz=DEFAULT_TEST_SIZE,
         ts=T0, seq=1) -> OrderBook:
    return OrderBook.from_okx(
        spec, okx_book_payload([[str(bid), str(bid_sz)]], [[str(ask), str(ask_sz)]],
                               ts=str(ts)), PROV)


def state_for(spec, bid, ask, ts=T0, **kw) -> MarketState:
    return MarketState(instrument=spec, ts_ms=ts, book=book(spec, bid, ask, ts=ts), **kw)


class TestCrossMarketCurrencyGuard(unittest.TestCase):
    """NON-REGRESSION : le detecteur comparait BTC-USD-SWAP a BTC-AED et
    annoncait 26 741 bps. C'etait le taux de change USD/AED, pas un edge."""

    def test_usd_equivalents_are_comparable(self):
        ok, _ = comparable(BTC_INVERSE, BTC_LINEAR)
        self.assertTrue(ok)
        self.assertEqual(USD_EQUIVALENT_QUOTES, frozenset({"USD", "USDT", "USDC"}))

    def test_foreign_currency_is_refused(self):
        ok, why = comparable(BTC_INVERSE, BTC_SPOT_EUR)
        self.assertFalse(ok)
        self.assertIn("EUR", why)

    def test_different_underlyings_refused(self):
        ada = InstrumentSpec(**{**BTC_INVERSE.to_dict(),
                                "inst_type": InstrumentType.SWAP_INVERSE,
                                "base": "ADA", "inst_id": "ADA-USD-SWAP"})
        ok, why = comparable(BTC_INVERSE, ada)
        self.assertFalse(ok)
        self.assertIn("sous-jacents", why)

    def test_detector_refuses_fx_peer_entirely(self):
        st = state_for(BTC_INVERSE, 100.0, 100.1)
        st.peers = {"BTC-EUR": state_for(BTC_SPOT_EUR, 90.0, 90.1)}
        out = CrossMarketDetector(min_history=1).detect(st)
        self.assertIs(out.status, DetectionStatus.INSUFFICIENT_DATA)
        self.assertEqual(out.candidates, [])
        self.assertIn("taux de change", out.reason)


class TestCrossMarketMeasuresDislocationNotLevel(unittest.TestCase):
    """NON-REGRESSION : un basis PERMANENT (prime de portage, peg USD/USDT)
    n'est pas capturable. Seul son ECART a son niveau habituel l'est."""

    def _feed_stable_basis(self, det, n=30):
        """Basis constant : aucune dislocation ne doit etre emise."""
        outs = []
        for i in range(n):
            st = state_for(BTC_INVERSE, 100.0, 100.01, ts=T0 + i * 1000)
            st.peers = {"BTC-USDT-SWAP": state_for(BTC_LINEAR, 110.0, 110.01,
                                                   ts=T0 + i * 1000)}
            outs.append(det.detect(st))
        return outs

    def test_constant_basis_emits_nothing(self):
        det = CrossMarketDetector(min_history=20)
        outs = self._feed_stable_basis(det, n=30)
        self.assertEqual(sum(len(o.candidates) for o in outs), 0)

    def test_sudden_dislocation_is_emitted(self):
        det = CrossMarketDetector(min_history=20)
        self._feed_stable_basis(det, n=30)
        # Le pair decroche brutalement de son niveau habituel.
        st = state_for(BTC_INVERSE, 100.0, 100.01, ts=T0 + 99_000)
        st.peers = {"BTC-USDT-SWAP": state_for(BTC_LINEAR, 130.0, 130.01,
                                               ts=T0 + 99_000)}
        out = det.detect(st)
        self.assertTrue(out.candidates)
        c = out.candidates[0]
        self.assertTrue(c.metadata["measures_dislocation_not_level"])
        self.assertIn("dislocation_bps", c.metadata)
        # L'edge retenu ne depasse jamais l'ecart reellement executable.
        self.assertLessEqual(c.gross_capture_bps,
                             c.metadata["executable_edge_bps"] + 1e-9)

    def test_candidate_carries_both_legs_with_their_specs(self):
        det = CrossMarketDetector(min_history=20)
        self._feed_stable_basis(det, n=30)
        st = state_for(BTC_INVERSE, 100.0, 100.01, ts=T0 + 99_000)
        st.peers = {"BTC-USDT-SWAP": state_for(BTC_LINEAR, 130.0, 130.01,
                                               ts=T0 + 99_000)}
        c = det.detect(st).candidates[0]
        self.assertEqual(len(c.legs), 2)
        for leg in c.legs:
            self.assertIn("ct_type", leg)
            self.assertIn("settle_ccy", leg)
            self.assertIn("inst_type", leg)
        self.assertTrue(c.metadata["leg_risk"])

    def test_settlement_mismatch_is_flagged(self):
        det = CrossMarketDetector(min_history=20)
        self._feed_stable_basis(det, n=30)
        st = state_for(BTC_INVERSE, 100.0, 100.01, ts=T0 + 99_000)
        st.peers = {"BTC-USDT-SWAP": state_for(BTC_LINEAR, 130.0, 130.01,
                                               ts=T0 + 99_000)}
        c = det.detect(st).candidates[0]
        self.assertFalse(c.metadata["same_settlement_currency"])
        self.assertIn("PAS un arbitrage sans risque",
                      c.metadata["settlement_warning"])
        self.assertIn("exposition residuelle", c.metadata["settlement_warning"])


class TestCrossVenue(unittest.TestCase):
    def _quote(self, venue, bid, ask, fee=5.0, delay=100):
        return VenueQuote(
            venue=venue, symbol="BTC", ts_ms=T0, local_recv_ts_ms=T0 + delay,
            bids=[VenueLevel(bid, 1_000_000.0)], asks=[VenueLevel(ask, 1_000_000.0)],
            taker_fee_bps=fee, fee_source="test", settle_ccy="USD")

    def test_needs_two_venues(self):
        st = state_for(BTC_INVERSE, 100.0, 100.1)
        st.venue_quotes = {"OKX": self._quote("OKX", 100.0, 100.1)}
        out = CrossVenueDetector().detect(st)
        self.assertIs(out.status, DetectionStatus.INSUFFICIENT_DATA)

    def test_spread_below_fee_floor_emits_nothing(self):
        """Le defaut des scanners publics : annoncer un ecart que les frais
        des deux jambes effacent."""
        st = state_for(BTC_INVERSE, 100.0, 100.1)
        st.venue_quotes = {"OKX": self._quote("OKX", 100.0, 100.05),
                           "HL": self._quote("HL", 100.06, 100.11)}
        out = CrossVenueDetector().detect(st)
        self.assertEqual(out.candidates, [])
        self.assertIn("frais", out.reason)

    def test_real_gap_above_fees_is_emitted(self):
        st = state_for(BTC_INVERSE, 100.0, 100.1)
        st.venue_quotes = {"OKX": self._quote("OKX", 100.0, 100.05, fee=1.0),
                           "HL": self._quote("HL", 105.0, 105.1, fee=1.0)}
        out = CrossVenueDetector().detect(st)
        self.assertTrue(out.candidates)
        c = out.candidates[0]
        self.assertTrue(c.metadata["prices_are_fillable_vwap"])
        self.assertTrue(c.metadata["not_a_displayed_spread"])
        self.assertIn("pre-positionne", c.metadata["capital_constraint"])

    def test_stale_quotes_are_refused(self):
        st = state_for(BTC_INVERSE, 100.0, 100.1)
        st.venue_quotes = {"OKX": self._quote("OKX", 100.0, 100.05, fee=1.0),
                           "HL": self._quote("HL", 105.0, 105.1, fee=1.0,
                                             delay=99_999)}
        out = CrossVenueDetector().detect(st)
        self.assertIs(out.status, DetectionStatus.INSUFFICIENT_DATA)
        self.assertIn("anciennes", out.reason)

    def test_insufficient_depth_gives_no_candidate(self):
        st = state_for(BTC_INVERSE, 100.0, 100.1)
        thin = VenueQuote(venue="HL", symbol="BTC", ts_ms=T0,
                          local_recv_ts_ms=T0 + 50,
                          bids=[VenueLevel(105.0, 1.0)], asks=[VenueLevel(105.1, 1.0)],
                          taker_fee_bps=1.0, fee_source="t", settle_ccy="USD")
        st.venue_quotes = {"OKX": self._quote("OKX", 100.0, 100.05, fee=1.0),
                           "HL": thin}
        self.assertEqual(CrossVenueDetector().detect(st).candidates, [])


class TestFundingBasis(unittest.TestCase):
    def test_missing_funding_is_insufficient_not_zero(self):
        out = FundingBasisDetector().detect(state_for(BTC_INVERSE, 100.0, 100.1))
        self.assertIs(out.status, DetectionStatus.INSUFFICIENT_DATA)

    def test_tiny_funding_below_spread_emits_nothing(self):
        st = state_for(BTC_INVERSE, 100.0, 100.5, funding_rate=0.0000001)
        self.assertEqual(FundingBasisDetector().detect(st).candidates, [])

    def test_positive_funding_means_short_is_paid(self):
        st = state_for(BTC_INVERSE, 100.0, 100.001, funding_rate=0.001)
        c = FundingBasisDetector().detect(st).candidates[0]
        self.assertIs(c.direction, Direction.SHORT)
        self.assertAlmostEqual(c.gross_capture_bps, 10.0, places=6)

    def test_negative_funding_means_long_is_paid(self):
        st = state_for(BTC_INVERSE, 100.0, 100.001, funding_rate=-0.001)
        self.assertIs(FundingBasisDetector().detect(st).candidates[0].direction,
                      Direction.LONG)

    def test_candidate_names_its_own_instrument_no_mixing(self):
        st = state_for(BTC_INVERSE, 100.0, 100.001, funding_rate=0.001)
        c = FundingBasisDetector().detect(st).candidates[0]
        self.assertEqual(c.metadata["funding_instrument"], "BTC-USD-SWAP")
        self.assertIn("aucun melange", c.metadata["no_instrument_mixing"])

    def test_directional_risk_is_declared(self):
        st = state_for(BTC_INVERSE, 100.0, 100.001, funding_rate=0.001)
        c = FundingBasisDetector().detect(st).candidates[0]
        self.assertIn("DIRECTIONNELLE", c.metadata["unhedged_directional_risk"])


class TestForcedFlowIsCausal(unittest.TestCase):
    def _state_with_liq(self, exec_bid=90.0, exec_ask=90.1, ref_mid=100.0):
        hist = [(T0 - 60_000 + i * 1000, ref_mid) for i in range(40)]
        return MarketState(
            instrument=BTC_INVERSE, ts_ms=T0,
            book=book(BTC_INVERSE, exec_bid, exec_ask),
            forced_flow=[ForcedFlowEvent(T0 - 2_000, 89.0, 100.0, 10_000.0, "sell")],
            mid_history=hist)

    def test_no_liquidation_no_candidate(self):
        out = ForcedFlowDetector().detect(state_for(BTC_INVERSE, 100.0, 100.1))
        self.assertEqual(out.candidates, [])
        self.assertIn("aucune liquidation", out.reason)

    def test_liquidation_without_history_is_insufficient(self):
        st = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                         book=book(BTC_INVERSE, 90.0, 90.1),
                         forced_flow=[ForcedFlowEvent(T0 - 1000, 89.0, 1.0,
                                                      100.0, "sell")])
        out = ForcedFlowDetector().detect(st)
        self.assertIs(out.status, DetectionStatus.INSUFFICIENT_DATA)
        self.assertIn("mid_history", out.missing)

    def test_forced_sell_gives_long_direction_never_a_rule(self):
        c = ForcedFlowDetector().detect(self._state_with_liq()).candidates[0]
        self.assertIs(c.direction, Direction.LONG)
        self.assertFalse(c.metadata["uses_future_information"])
        self.assertTrue(c.metadata["causal"])
        self.assertIn("hypothese", c.metadata["reversion_is_a_hypothesis"])

    def test_displacement_is_measured_from_past_reference_only(self):
        c = ForcedFlowDetector().detect(self._state_with_liq()).candidates[0]
        self.assertAlmostEqual(c.metadata["reference_mid"], 100.0, places=6)
        self.assertGreater(c.metadata["displacement_bps"], 0)
        self.assertEqual(c.causal_reference_ts_ms, T0)

    def test_no_displacement_emits_nothing(self):
        st = self._state_with_liq(exec_bid=100.0, exec_ask=100.01, ref_mid=100.0)
        self.assertEqual(ForcedFlowDetector().detect(st).candidates, [])


class TestMicrostructureHonesty(unittest.TestCase):
    """Ces familles mesurent un etat ; elles n'affirment aucune reversion."""

    def _rich_state(self):
        hist = [(T0 - 30_000 + i * 500, 100.0 + (i % 7) * 0.01) for i in range(60)]
        return MarketState(
            instrument=BTC_INVERSE, ts_ms=T0, book=book(BTC_INVERSE, 100.0, 101.0),
            mid_history=hist,
            trades=[TradePrint(T0 - 1000, 100.5, 10, 1000.0, True),
                    TradePrint(T0 - 500, 100.5, 5, 500.0, True)],
            depth_history=[(T0 - 10_000, 1e6, 1e6), (T0 - 1000, 1e6, 1e6)])

    def test_all_microstructure_candidates_declare_untested_hypothesis(self):
        st = self._rich_state()
        for det in (BookImbalanceDetector(), AggressiveFlowDetector(),
                    ShortHorizonReversionDetector(), SpreadDislocationDetector()):
            out = det.detect(st)
            for c in out.candidates:
                with self.subTest(family=det.family.value):
                    self.assertTrue(c.metadata["hypothesis_untested"])
                    self.assertIn("non validee", c.metadata["feature_is_not_alpha"])
                    self.assertTrue(c.metadata["no_technical_indicators"])

    def test_imbalance_below_spread_emits_nothing(self):
        st = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                         book=book(BTC_INVERSE, 100.0, 100.001,
                                   bid_sz=100.0, ask_sz=100.0))
        self.assertEqual(BookImbalanceDetector().detect(st).candidates, [])

    def test_spread_below_realized_vol_is_not_an_excess(self):
        """Un spread large en marche agite est le PRIX du risque."""
        hist = [(T0 - 30_000 + i * 500, 100.0 * (1 + (i % 2) * 0.05))
                for i in range(60)]
        st = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                         book=book(BTC_INVERSE, 100.0, 100.01), mid_history=hist)
        out = SpreadDislocationDetector().detect(st)
        self.assertEqual(out.candidates, [])
        self.assertIn("paie le risque", out.reason)

    def test_maker_families_declare_maker_requirement(self):
        st = self._rich_state()
        out = SpreadDislocationDetector().detect(st)
        if out.candidates:
            c = out.candidates[0]
            self.assertEqual(c.required_execution, "MAKER")
            self.assertIn("APPORTANT", c.metadata["maker_required"])
            self.assertIn("continue contre lui",
                          c.metadata["adverse_selection_warning"])

    def test_reversion_needs_enough_points(self):
        st = MarketState(instrument=BTC_INVERSE, ts_ms=T0,
                         book=book(BTC_INVERSE, 100.0, 100.01),
                         mid_history=[(T0 - 1000, 100.0)])
        out = ShortHorizonReversionDetector().detect(st)
        self.assertIs(out.status, DetectionStatus.INSUFFICIENT_DATA)

    def test_no_detector_returns_a_profitability_claim(self):
        """Aucun detecteur ne porte de champ affirmant la rentabilite."""
        st = self._rich_state()
        for det in (BookImbalanceDetector(), AggressiveFlowDetector(),
                    ShortHorizonReversionDetector(), SpreadDislocationDetector()):
            for c in det.detect(st).candidates:
                self.assertIsNone(c.confidence)
                for banned in ("profitable", "expected_pnl", "win_rate", "edge_score"):
                    self.assertNotIn(banned, c.metadata)


if __name__ == "__main__":
    unittest.main(verbosity=2)
