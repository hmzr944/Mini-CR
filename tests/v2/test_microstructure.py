"""Qualite des donnees, replay, latence, absence de look-ahead."""
from __future__ import annotations

import unittest

from tests.v2.fixtures import BTC_INVERSE, BTC_LINEAR, PROV, okx_book_payload, simple_inverse_book
from prism_v2.core_types import Direction
from prism_v2.orderbook import Level, OrderBook, book_from_snapshot
from prism_v2.quality import (
    QualityIssue, QualityPolicy, QualityVerdict, SequenceTracker, assess_book,
)
from prism_v2.replay import (
    DEFAULT_LATENCY_GRID_MS, EventTimeline, LookAheadViolation, MeasurementMode,
    ReplayCursor, causal_capture, latency_decay_curve,
)

T0 = 1_789_000_000_000


def ws_event(ts_ms: int, bid: float, ask: float, bid_sz: float = 10.0,
             ask_sz: float = 10.0, seq: int = 1) -> dict:
    return {"channel": "books5", "inst_id": "BTC-USD-SWAP", "exchange_ts_ms": ts_ms,
            "local_recv_ts_ms": ts_ms + 90, "seq_id": seq,
            "data": {"bids": [[str(bid), str(bid_sz), "0", "1"]],
                     "asks": [[str(ask), str(ask_sz), "0", "1"]],
                     "ts": str(ts_ms), "seqId": seq, "instId": "BTC-USD-SWAP"}}


class TestDataQuality(unittest.TestCase):
    def test_fresh_book_is_ok(self):
        b = simple_inverse_book()
        r = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms + 100)
        self.assertIs(r.verdict, QualityVerdict.OK)
        self.assertTrue(r.is_usable)

    def test_stale_book_is_unusable(self):
        b = simple_inverse_book()
        r = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms + 60_000)
        self.assertIs(r.verdict, QualityVerdict.UNUSABLE)
        self.assertIn(QualityIssue.STALE_BOOK, r.issues)
        self.assertFalse(r.is_usable)

    def test_instrument_mismatch_is_unusable(self):
        b = simple_inverse_book()
        r = assess_book(b, BTC_LINEAR, now_ms=b.ts_ms + 10)
        self.assertIs(r.verdict, QualityVerdict.UNUSABLE)
        self.assertIn(QualityIssue.INSTRUMENT_MISMATCH, r.issues)

    def test_crossed_book_is_unusable(self):
        b = OrderBook.from_okx(BTC_INVERSE,
                               okx_book_payload([["101", "10"]], [["100", "10"]]), PROV)
        r = assess_book(b, BTC_INVERSE, now_ms=(b.ts_ms or 0) + 10)
        self.assertIn(QualityIssue.CROSSED_BOOK, r.issues)
        self.assertFalse(r.is_usable)

    def test_incomplete_book_is_unusable(self):
        b = OrderBook.from_okx(BTC_INVERSE, okx_book_payload([], [["100", "10"]]), PROV)
        r = assess_book(b, BTC_INVERSE, now_ms=(b.ts_ms or 0) + 10)
        self.assertIn(QualityIssue.INCOMPLETE_BOOK, r.issues)

    def test_clock_anomaly_detected(self):
        b = simple_inverse_book()
        r = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms - 60_000)
        self.assertIn(QualityIssue.CLOCK_ANOMALY, r.issues)

    def test_abnormal_spread_is_degraded_not_blocking(self):
        """Un spread enorme est peut-etre l'evenement etudie : on le signale,
        on ne le bloque pas."""
        b = OrderBook.from_okx(BTC_INVERSE,
                               okx_book_payload([["50", "10"]], [["100", "10"]]), PROV)
        r = assess_book(b, BTC_INVERSE, now_ms=(b.ts_ms or 0) + 10)
        self.assertIn(QualityIssue.ABNORMAL_SPREAD, r.issues)
        self.assertIs(r.verdict, QualityVerdict.DEGRADED)
        self.assertTrue(r.is_usable)

    def test_strict_policy_turns_degraded_into_unusable(self):
        b = OrderBook.from_okx(BTC_INVERSE,
                               okx_book_payload([["50", "10"]], [["100", "10"]]), PROV)
        r = assess_book(b, BTC_INVERSE, now_ms=(b.ts_ms or 0) + 10,
                        policy=QualityPolicy(allow_degraded=False))
        self.assertIs(r.verdict, QualityVerdict.UNUSABLE)

    def test_sequence_tracker_flags_duplicates_and_regressions(self):
        t = SequenceTracker()
        self.assertEqual(t.observe("books5:BTC", 100), [])
        self.assertEqual(t.observe("books5:BTC", 101), [])
        self.assertIn(QualityIssue.DUPLICATE_EVENT, t.observe("books5:BTC", 101))
        self.assertIn(QualityIssue.SEQUENCE_GAP, t.observe("books5:BTC", 50))
        self.assertEqual(t.duplicates, 1)
        self.assertEqual(t.regressions, 1)


class TestReplayHasNoLookAhead(unittest.TestCase):
    def setUp(self):
        self.events = [ws_event(T0 + i * 100, 100.0 + i, 101.0 + i, seq=i)
                       for i in range(20)]
        self.tl = EventTimeline.from_events(BTC_INVERSE, self.events)

    def test_timeline_built(self):
        self.assertEqual(len(self.tl), 20)
        self.assertEqual(self.tl.first_ts_ms, T0)
        self.assertEqual(self.tl.last_ts_ms, T0 + 1900)

    def test_book_at_never_returns_a_future_book(self):
        """Invariant structurel : quel que soit l'instant demande, le carnet
        rendu a toujours un horodatage <= a cet instant."""
        for offset in range(0, 2000, 37):
            book = self.tl.book_at(T0 + offset)
            if book is not None:
                self.assertLessEqual(book.ts_ms, T0 + offset)

    def test_book_at_before_first_event_is_none(self):
        self.assertIsNone(self.tl.book_at(T0 - 1))

    def test_cursor_refuses_to_go_backwards(self):
        cur = ReplayCursor(self.tl, T0 + 500)
        cur.advance_to(T0 + 600)
        with self.assertRaises(LookAheadViolation):
            cur.advance_to(T0 + 100)

    def test_cursor_refuses_to_read_the_future(self):
        cur = ReplayCursor(self.tl, T0 + 500)
        self.assertIsNotNone(cur.book_at_or_before(T0 + 400))
        with self.assertRaises(LookAheadViolation):
            cur.book_at_or_before(T0 + 900)

    def test_measurement_modes_are_distinct(self):
        self.assertEqual({m.value for m in MeasurementMode},
                         {"BAR_BACKTEST", "EVENT_REPLAY", "LIVE_PAPER"})


class TestLatencyDecay(unittest.TestCase):
    def setUp(self):
        # Le carnet se degrade avec le temps : ask s'eloigne, profondeur fond.
        self.events = []
        for i in range(60):
            ts = T0 + i * 100
            self.events.append(ws_event(ts, 100.0, 101.0 + i * 0.5,
                                        bid_sz=10, ask_sz=max(1.0, 10 - i * 0.1),
                                        seq=i))
        self.tl = EventTimeline.from_events(BTC_INVERSE, self.events)

    def test_cost_increases_with_latency(self):
        pts = latency_decay_curve(self.tl, T0, Direction.LONG, 500.0,
                                  grid_ms=(0, 500, 1000, 2000))
        costs = [p.cost_bps for p in pts if p.cost_bps is not None]
        self.assertGreater(len(costs), 2)
        self.assertEqual(costs, sorted(costs), "le cout devrait croitre avec le retard")

    def test_decay_is_zero_at_delta_zero(self):
        pts = latency_decay_curve(self.tl, T0, Direction.LONG, 500.0, grid_ms=(0,))
        self.assertAlmostEqual(pts[0].decay_bps, 0.0, places=9)

    def test_depth_survival_ratio_measured_not_assumed(self):
        pts = latency_decay_curve(self.tl, T0, Direction.LONG, 500.0,
                                  grid_ms=(0, 2000))
        self.assertAlmostEqual(pts[0].depth_survival_ratio, 1.0, places=9)
        self.assertLess(pts[1].depth_survival_ratio, 1.0)

    def test_out_of_window_is_unknown_not_extrapolated(self):
        pts = latency_decay_curve(self.tl, T0, Direction.LONG, 500.0,
                                  grid_ms=(0, 999_999))
        far = pts[-1]
        self.assertFalse(far.available)
        self.assertIsNone(far.cost_bps)
        self.assertIsNone(far.decay_bps)
        self.assertIn("UNKNOWN", far.note)

    def test_default_grid_spans_sub_ms_to_seconds(self):
        self.assertEqual(DEFAULT_LATENCY_GRID_MS[0], 0)
        self.assertGreaterEqual(DEFAULT_LATENCY_GRID_MS[-1], 5_000)


class TestCausalCapture(unittest.TestCase):
    def setUp(self):
        self.events = [ws_event(T0 + i * 100, 100.0 + i * 0.1, 101.0 + i * 0.1, seq=i)
                       for i in range(60)]
        self.tl = EventTimeline.from_events(BTC_INVERSE, self.events)

    def test_resolves_within_window(self):
        cap = causal_capture(self.tl, T0, Direction.LONG, 500.0,
                             latency_ms=200, hold_ms=1000)
        self.assertTrue(cap.resolved, cap.reason)
        self.assertEqual(cap.decision_ts_ms, T0 + 200)
        self.assertEqual(cap.exit_ts_ms, T0 + 1200)

    def test_unresolved_outside_window_never_extrapolated(self):
        cap = causal_capture(self.tl, T0, Direction.LONG, 500.0,
                             latency_ms=200, hold_ms=999_999)
        self.assertFalse(cap.resolved)
        self.assertIsNone(cap.gross_bps)

    def test_gross_uses_inverse_formula_not_linear(self):
        """NON-REGRESSION (defaut trouve en revue adversariale) : le brut
        etait calcule en (Px-Pe)/Pe, la convention LINEAIRE."""
        from prism_v2.contracts import contracts_for_usd_notional, pnl
        cap = causal_capture(self.tl, T0, Direction.LONG, 500.0,
                             latency_ms=200, hold_ms=1000)
        entry = self.tl.book_at(T0 + 200)
        exit_b = self.tl.book_at(T0 + 1200)
        n = contracts_for_usd_notional(BTC_INVERSE, 500.0, entry.mid)
        expected = pnl(BTC_INVERSE, Direction.LONG, n, entry.mid, exit_b.mid).return_bps_usd
        self.assertAlmostEqual(cap.gross_bps, expected, places=9)

    def test_entry_price_is_the_book_at_arrival_not_at_event(self):
        """Le prix d'entree doit venir du carnet a T0+latence, jamais de T0."""
        cap = causal_capture(self.tl, T0, Direction.LONG, 500.0,
                             latency_ms=1000, hold_ms=1000)
        at_event = self.tl.book_at(T0)
        at_arrival = self.tl.book_at(T0 + 1000)
        self.assertNotAlmostEqual(at_event.best_ask, at_arrival.best_ask, places=6)
        self.assertAlmostEqual(cap.entry_px, at_arrival.best_ask, places=6)


class TestSnapshotReplayGuards(unittest.TestCase):
    def test_snapshot_replay_roundtrip(self):
        b = simple_inverse_book()
        back = book_from_snapshot(b.snapshot_dict(), BTC_INVERSE)
        self.assertAlmostEqual(back.mid, b.mid, places=9)
        self.assertEqual(back.ts_ms, b.ts_ms)

    def test_snapshot_refuses_instrument_substitution(self):
        with self.assertRaises(ValueError):
            book_from_snapshot(simple_inverse_book().snapshot_dict(), BTC_LINEAR)

    def test_snapshot_refuses_divergent_contract_metadata(self):
        """NON-REGRESSION : un ctVal modifie entre collecte et replay rendait
        tous les couts faux en silence."""
        snap = simple_inverse_book().snapshot_dict()
        snap["bids"] = [[p, s, n * 10] for p, s, n in snap["bids"]]  # notionnel falsifie
        with self.assertRaises(ValueError) as c:
            book_from_snapshot(snap, BTC_INVERSE)
        self.assertIn("Metadonnees de contrat divergentes", str(c.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestResolutionLimit(unittest.TestCase):
    """NON-REGRESSION : un delta sous la cadence de publication renvoyait 0,
    un ARTEFACT presente comme une mesure."""

    def setUp(self):
        # Carnets publies toutes les 500ms.
        self.events = [ws_event(T0 + i * 500, 100.0, 101.0 + i * 0.5, seq=i)
                       for i in range(40)]
        self.tl = EventTimeline.from_events(BTC_INVERSE, self.events)

    def test_update_interval_measured(self):
        self.assertAlmostEqual(self.tl.update_interval_ms(), 500.0, places=6)

    def test_sub_resolution_deltas_are_unknown_not_zero(self):
        pts = {p.delta_ms: p for p in
               latency_decay_curve(self.tl, T0 + 2_000, Direction.LONG, 500.0,
                                   grid_ms=(0, 10, 50, 100, 250, 1_000))}
        for d in (10, 50, 100, 250):
            with self.subTest(delta=d):
                self.assertIsNone(pts[d].decay_bps, f"delta {d}ms devrait etre UNKNOWN")
                self.assertFalse(pts[d].available)
                self.assertIn("SOUS-RESOLU", pts[d].note)

    def test_resolvable_delta_is_measured(self):
        pts = {p.delta_ms: p for p in
               latency_decay_curve(self.tl, T0 + 2_000, Direction.LONG, 500.0,
                                   grid_ms=(0, 1_000))}
        self.assertIsNotNone(pts[1_000].decay_bps)
        self.assertTrue(self.tl.is_resolvable(1_000))
        self.assertFalse(self.tl.is_resolvable(100))

    def test_delta_zero_always_resolvable(self):
        pts = latency_decay_curve(self.tl, T0 + 2_000, Direction.LONG, 500.0,
                                  grid_ms=(0,))
        self.assertIsNotNone(pts[0].decay_bps)
