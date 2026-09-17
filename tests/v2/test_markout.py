"""Markout des fills passifs — et surtout, le controle qui le valide.

Le resultat de ce module (« coter passivement perd ») n'a de valeur que si la
mesure elle-meme ne derive pas. Le controle non conditionnel est donc teste
plus severement que la mesure principale.
"""
from __future__ import annotations

import unittest

from prism_v2.markout import (
    Fill, MAKER_FEE_BPS, MARKOUT_HORIZONS_MS, SIDE_ASK, SIDE_BID,
    conditional_analysis, simulate_passive_fills, unconditional_markout,
)


def snap(ts, bid, ask, size=100.0, trades=None, inst="X-USD-SWAP"):
    r = {"i": inst, "ok": True, "ts": ts,
         "b": [[bid, size], [bid - 1, size]],
         "a": [[ask, size], [ask + 1, size]]}
    if trades:
        r["t"] = trades
    return r


def trade(n=1, buy_usd=0.0, sell_usd=0.0, fp=100.0, lp=100.0):
    return {"n": n, "bu": buy_usd, "su": sell_usd, "fp": fp, "lp": lp}


class TestFillAccountingIsExact(unittest.TestCase):

    def test_net_is_half_spread_plus_markout_minus_fee(self):
        f = Fill(ts_ms=1, inst_id="X", side=SIDE_BID, quote_px=99.0,
                 mid_at_fill=100.0, half_spread_bps=10.0,
                 markout_bps={1_000: -4.0})
        self.assertAlmostEqual(f.net_bps(1_000, fee_bps=2.0), 10.0 - 4.0 - 2.0)

    def test_missing_horizon_yields_none_not_zero(self):
        f = Fill(ts_ms=1, inst_id="X", side=SIDE_BID, quote_px=99.0,
                 mid_at_fill=100.0, half_spread_bps=10.0, markout_bps={})
        self.assertIsNone(f.net_bps(1_000))

    def test_maker_fee_is_the_public_tier(self):
        self.assertEqual(MAKER_FEE_BPS, 2.0)


class TestFillDetectionIsCausal(unittest.TestCase):

    def test_a_quote_is_never_filled_by_a_trade_already_past(self):
        """Un ordre poste a l'instant t ne peut pas etre execute par un trade
        survenu avant t. Le fill est cherche dans la tranche SUIVANTE."""
        recs = [snap(0, 99.0, 101.0, trades=trade(n=5, sell_usd=1000, fp=99.0, lp=99.0)),
                snap(1_000, 99.0, 101.0),
                snap(2_000, 99.0, 101.0)]
        fills = simulate_passive_fills(recs, "X-USD-SWAP", SIDE_BID,
                                       horizons=(1_000,))
        # Le seul trade est dans la tranche 0, donc ANTERIEUR a toute cotation
        # dont le fill serait cherche apres : aucun fill.
        self.assertEqual(fills, [])

    def test_bid_is_filled_only_when_a_sell_reaches_it(self):
        base = [snap(i * 1_000, 99.0, 101.0) for i in range(60)]
        # vente a 99 dans la tranche 1 -> le bid a 99 est frappe
        base[1] = snap(1_000, 99.0, 101.0,
                       trades=trade(n=3, sell_usd=500, fp=99.0, lp=99.0))
        fills = simulate_passive_fills(base, "X-USD-SWAP", SIDE_BID,
                                       horizons=(1_000, 5_000))
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].ts_ms, 1_000)

    def test_bid_is_not_filled_when_the_price_runs_away(self):
        """C'est l'asymetrie fondamentale d'un ordre passif : il n'est pas
        execute quand le marche s'eloigne de lui."""
        base = [snap(i * 1_000, 99.0, 101.0) for i in range(60)]
        base[1] = snap(1_000, 99.0, 101.0,
                       trades=trade(n=3, sell_usd=500, fp=105.0, lp=106.0))
        fills = simulate_passive_fills(base, "X-USD-SWAP", SIDE_BID,
                                       horizons=(1_000,))
        self.assertEqual(fills, [])

    def test_buy_volume_does_not_fill_a_bid(self):
        base = [snap(i * 1_000, 99.0, 101.0) for i in range(60)]
        base[1] = snap(1_000, 99.0, 101.0,
                       trades=trade(n=3, buy_usd=500, fp=99.0, lp=99.0))
        self.assertEqual(
            simulate_passive_fills(base, "X-USD-SWAP", SIDE_BID,
                                   horizons=(1_000,)), [])

    def test_half_spread_is_positive_on_both_sides(self):
        for side, tr in ((SIDE_BID, trade(n=1, sell_usd=500, fp=99.0, lp=99.0)),
                         (SIDE_ASK, trade(n=1, buy_usd=500, fp=101.0, lp=101.0))):
            base = [snap(i * 1_000, 99.0, 101.0) for i in range(60)]
            base[1] = snap(1_000, 99.0, 101.0, trades=tr)
            fills = simulate_passive_fills(base, "X-USD-SWAP", side,
                                           horizons=(1_000,))
            self.assertTrue(fills, side)
            self.assertGreater(fills[0].half_spread_bps, 0, side)


class TestControlValidatesTheMeasurement(unittest.TestCase):
    """Sans ce controle, un markout negatif pourrait n'etre qu'une derive."""

    def test_flat_market_gives_zero_unconditional_markout(self):
        recs = [snap(i * 1_000, 99.0, 101.0) for i in range(200)]
        ctrl = unconditional_markout(recs, "X-USD-SWAP", SIDE_BID,
                                     horizons=(1_000,), step_ms=5_000)
        self.assertGreater(len(ctrl), 10)
        for f in ctrl:
            self.assertAlmostEqual(f.markout_bps[1_000], 0.0, places=6)

    def test_control_needs_no_trade_to_exist(self):
        """Le controle poste le meme ordre sans exiger qu'on le frappe :
        c'est precisement ce qui isole l'effet d'AVOIR ETE CHOISI."""
        recs = [snap(i * 1_000, 99.0, 101.0) for i in range(100)]
        self.assertTrue(unconditional_markout(recs, "X-USD-SWAP", SIDE_BID,
                                              horizons=(1_000,), step_ms=5_000))
        self.assertEqual(simulate_passive_fills(recs, "X-USD-SWAP", SIDE_BID,
                                                horizons=(1_000,)), [])

    def test_control_detects_a_genuine_drift(self):
        """Si le marche derive vraiment, le controle DOIT le voir — sinon il
        ne pourrait pas disqualifier une mesure biaisee."""
        recs = [snap(i * 1_000, 99.0 + i * 0.01, 101.0 + i * 0.01)
                for i in range(200)]
        ctrl = unconditional_markout(recs, "X-USD-SWAP", SIDE_BID,
                                     horizons=(10_000,), step_ms=5_000)
        means = [f.markout_bps[10_000] for f in ctrl]
        self.assertGreater(sum(means) / len(means), 1.0)


class TestConditionalAnalysisUsesOnlyDecisionTimeFeatures(unittest.TestCase):

    def _fills(self, n=120):
        out = []
        for i in range(n):
            out.append(Fill(ts_ms=i, inst_id="X", side=SIDE_BID, quote_px=99.0,
                            mid_at_fill=100.0, half_spread_bps=1.0,
                            markout_bps={5_000: -2.0 if i % 2 else 0.5},
                            spread_bps=1.0 + i * 0.01,
                            depth_imbalance=(i % 7) / 7.0,
                            trade_intensity=i % 5,
                            aggressor_ratio=(i % 3) / 3.0))
        return out

    def test_small_sample_refuses_to_conclude(self):
        r = conditional_analysis(self._fills(5), 5_000)
        self.assertIn("note", r)
        self.assertNotIn("by_spread", r)

    def test_every_feature_is_observable_before_the_fill(self):
        import inspect
        from prism_v2 import markout
        src = inspect.getsource(markout.conditional_analysis)
        # Aucune tranche ne doit etre construite sur le markout lui-meme.
        self.assertNotIn("f.markout_bps", src.split("def terciles")[1])

    def test_buckets_partition_the_sample(self):
        fills = self._fills()
        r = conditional_analysis(fills, 5_000)
        total = sum(r["by_spread"][k]["n"] for k in ("bas", "moyen", "haut"))
        self.assertEqual(total, len(fills))


if __name__ == "__main__":
    unittest.main()
