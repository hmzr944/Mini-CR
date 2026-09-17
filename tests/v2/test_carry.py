"""Carry inverse/lineaire — neutralite, appariement, couts, selection.

Le point le plus important de ce module n'est pas qu'il trouve un rendement :
c'est qu'il refuse de le surestimer. Les tests portent donc d'abord sur les
facons dont ce chiffre pourrait etre gonfle.
"""
from __future__ import annotations

import unittest

from tests.v2.fixtures import BTC_INVERSE, BTC_LINEAR
from prism_v2 import contracts
from prism_v2.carry import (
    DISCOVERY_FRACTION, FEE_BPS_PER_LEG, LEG_CROSSINGS, PERIODS_PER_YEAR,
    PairSeries, analyse_pair, round_trip_cost,
)
from prism_v2.core_types import Direction


class TestHedgeIsExactNotApproximate(unittest.TestCase):
    """Toute l'experience repose sur la neutralite de la paire. Si elle n'est
    pas exacte, le differentiel de funding est noye dans du risque de prix."""

    def _pair_pnl(self, p0: float, p1: float, notional: float = 10_000.0):
        sz_i = contracts.contracts_for_usd_notional(BTC_INVERSE, notional, p0)
        sz_l = contracts.contracts_for_usd_notional(BTC_LINEAR, notional, p0)
        a = contracts.pnl(BTC_INVERSE, Direction.SHORT, sz_i, p0, p1)
        b = contracts.pnl(BTC_LINEAR, Direction.LONG, sz_l, p0, p1)
        return a.pnl_usd_at_exit + b.pnl_usd_at_exit

    def test_net_pnl_is_zero_across_extreme_moves(self):
        for p1 in (5_000.0, 25_000.0, 50_000.0, 100_000.0, 500_000.0):
            with self.subTest(exit_price=p1):
                self.assertAlmostEqual(self._pair_pnl(50_000.0, p1), 0.0, places=6)

    def test_hedge_holds_for_any_notional(self):
        for n in (100.0, 10_000.0, 1_000_000.0):
            with self.subTest(notional=n):
                self.assertAlmostEqual(self._pair_pnl(50_000.0, 65_000.0, n),
                                       0.0, places=4)

    def test_the_two_legs_settle_in_different_currencies(self):
        """C'est la raison pour laquelle la marge ne se nette pas."""
        a = contracts.pnl(BTC_INVERSE, Direction.SHORT, 100, 50_000.0, 55_000.0)
        b = contracts.pnl(BTC_LINEAR, Direction.LONG, 20, 50_000.0, 55_000.0)
        self.assertNotEqual(a.settle_ccy, b.settle_ccy)
        self.assertEqual(a.settle_ccy, "BTC")
        self.assertEqual(b.settle_ccy, "USDT")


class TestCostsAreCompleteAndRefuseToGuess(unittest.TestCase):

    def test_round_trip_counts_four_crossings(self):
        c = round_trip_cost(spread_inv=2.0, spread_lin=1.0, fee_bps_per_leg=5.0)
        # 2 traversees par jambe x demi-spread, + 4 lots de frais
        self.assertAlmostEqual(c, (2.0 / 2) * 2 + (1.0 / 2) * 2 + 4 * 5.0)
        self.assertEqual(LEG_CROSSINGS, 4)

    def test_missing_spread_yields_unknown_not_zero(self):
        self.assertIsNone(round_trip_cost(None, 1.0))
        self.assertIsNone(round_trip_cost(2.0, None))

    def test_fee_is_the_public_upper_bound(self):
        self.assertEqual(FEE_BPS_PER_LEG, 5.0)


class TestPairingIsOnSettlementTimeNotIndex(unittest.TestCase):
    """Apparier par position dans la liste melangerait des periodes des qu'un
    instrument a un releve manquant — ce qui est le cas de HYPE."""

    def test_missing_period_does_not_shift_the_alignment(self):
        import json
        import tempfile
        from pathlib import Path
        from prism_v2.carry import load_pairs
        inv, lin = BTC_INVERSE.to_dict(), BTC_LINEAR.to_dict()

        def hist(times, rate):
            return [{"fundingTime": str(t), "fundingRate": str(rate),
                     "realizedRate": str(rate)} for t in times]

        times = [1_000 + i * 28_800_000 for i in range(40)]
        payload = {
            "pairs": [[inv["inst_id"], lin["inst_id"]]],
            "instruments": {
                inv["inst_id"]: {"spec": inv, "history": hist(times, 0.0002)},
                # une periode MANQUANTE au milieu
                lin["inst_id"]: {"spec": lin,
                                 "history": hist(times[:10] + times[11:], 0.0001)},
            }}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            pairs, _ = load_pairs(p)
        self.assertEqual(len(pairs), 1)
        ps = pairs[0]
        self.assertEqual(len(ps), 39)          # l'intersection, pas 40
        for d in ps.diff_bps:                  # 2 bps - 1 bps partout
            self.assertAlmostEqual(d, 1.0, places=9)


class TestSelectionDoesNotUseTheHoldout(unittest.TestCase):

    def _series(self, disc_rate: float, hold_rate: float, n: int = 100):
        ps = PairSeries(base="X", inverse_id="X-USD-SWAP", linear_id="X-USDT-SWAP")
        for i in range(n):
            ps.times.append(1_000 + i * 28_800_000)
            d = disc_rate if i < n * DISCOVERY_FRACTION else hold_rate
            ps.inverse_bps.append(d)
            ps.linear_bps.append(0.0)
            ps.diff_bps.append(d)
        return ps

    def test_discovery_and_holdout_are_measured_separately(self):
        r = analyse_pair(self._series(1.0, 0.25),
                         {"X-USD-SWAP": 2.0, "X-USDT-SWAP": 1.0})
        self.assertAlmostEqual(r.discovery["mean"], 1.0, places=9)
        self.assertAlmostEqual(r.holdout["mean"], 0.25, places=9)
        self.assertTrue(r.sign_agrees)

    def test_yield_is_taken_from_the_holdout_never_from_discovery(self):
        """Utiliser la decouverte surestimerait par construction."""
        r = analyse_pair(self._series(10.0, 0.25),
                         {"X-USD-SWAP": 2.0, "X-USDT-SWAP": 1.0})
        expected = 0.25 * PERIODS_PER_YEAR - r.round_trip_cost_bps
        self.assertAlmostEqual(r.net_bps_per_year, expected, places=6)

    def test_sign_flip_between_halves_is_detected(self):
        r = analyse_pair(self._series(1.0, -1.0),
                         {"X-USD-SWAP": 2.0, "X-USDT-SWAP": 1.0})
        self.assertFalse(r.sign_agrees)

    def test_chronological_order_is_enforced(self):
        """L'API rend les periodes du plus RECENT au plus ancien. Sans remise
        en ordre, « premiere moitie » designerait le futur."""
        ps = self._series(1.0, 0.25)
        ps.times.reverse()                      # ordre inverse, comme l'API
        r = analyse_pair(ps, {"X-USD-SWAP": 2.0, "X-USDT-SWAP": 1.0})
        # La decouverte doit rester la moitie CHRONOLOGIQUEMENT ancienne.
        self.assertAlmostEqual(r.discovery["mean"], 0.25, places=9)
        self.assertAlmostEqual(r.holdout["mean"], 1.0, places=9)

    def test_breakeven_requires_a_positive_holdout(self):
        r = analyse_pair(self._series(1.0, 0.0),
                         {"X-USD-SWAP": 2.0, "X-USDT-SWAP": 1.0})
        self.assertIsNone(r.breakeven_periods)


if __name__ == "__main__":
    unittest.main()
