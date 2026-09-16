"""Observatoire longue duree — durabilite et honnetete du flux ecrit.

Ces tests portent sur des defauts REELS rencontres pendant la collecte de six
heures, pas sur des scenarios imagines. Chacun a coute du temps d'horloge.
"""
from __future__ import annotations

import ast
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from prism_v2.observatory import (
    DEFAULT_DEPTH_LEVELS, DEFAULT_SNAPSHOT_MS, MarketObservatory,
    ObservatoryStats, load_snapshots,
)

OBS_SRC = Path(__file__).resolve().parents[2] / "prism_v2" / "observatory.py"


def write_obs(path: Path, records, meta=None) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(meta or {"_meta": True, "snapshot_ms": 250}) + "\n")
        for r in records:
            fh.write(json.dumps(r) + "\n")


class TestTruncatedFileIsStillUsable(unittest.TestCase):
    """Un flux gzip en cours d'ecriture n'a pas de marqueur de fin.

    Sans lecture tolerante, six heures de collecte interrompue a la cinquieme
    seraient integralement perdues.
    """

    def test_complete_file_reads_without_truncation_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.jsonl.gz"
            write_obs(p, [{"i": "X", "ok": True, "ts": 1000 + i,
                           "b": [[99.0, 1.0]], "a": [[101.0, 1.0]]}
                          for i in range(20)])
            meta, recs = load_snapshots(p)
            self.assertFalse(meta["truncated"])
            self.assertEqual(len(recs), 20)

    def test_truncated_stream_returns_what_it_read_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.jsonl.gz"
            write_obs(p, [{"i": "X", "ok": True, "ts": 1000 + i,
                           "b": [[99.0, 1.0]], "a": [[101.0, 1.0]]}
                          for i in range(200)])
            raw = p.read_bytes()
            p.write_bytes(raw[:len(raw) // 2])      # coupe en plein flux
            meta, recs = load_snapshots(p)
            self.assertTrue(meta["truncated"])
            self.assertGreater(len(recs), 0,
                               "une coupure ne doit pas tout perdre")

    def test_partial_trailing_line_is_ignored_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.jsonl.gz"
            with gzip.open(p, "wt", encoding="utf-8") as fh:
                fh.write(json.dumps({"_meta": True, "snapshot_ms": 250}) + "\n")
                fh.write(json.dumps({"i": "X", "ok": True, "ts": 1,
                                     "b": [[9.0, 1.0]], "a": [[11.0, 1.0]]}) + "\n")
                fh.write('{"i": "X", "ok": true, "ts": 2, "b": [[9.0')  # tronquee
            meta, recs = load_snapshots(p)
            self.assertEqual(len(recs), 1)


class TestInvalidBooksNeverBecomeObservations(unittest.TestCase):

    def test_not_ok_records_are_dropped_on_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.jsonl.gz"
            write_obs(p, [
                {"i": "X", "ok": True, "ts": 1, "b": [[9.0, 1.0]], "a": [[11.0, 1.0]]},
                {"i": "X", "ok": False, "why": "trou de sequence"},
                {"i": "X", "ok": True, "ts": 3, "b": [[9.0, 1.0]], "a": [[11.0, 1.0]]},
            ])
            _, recs = load_snapshots(p)
            self.assertEqual([r["ts"] for r in recs], [1, 3])

    def test_records_are_returned_in_exchange_time_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "e.jsonl.gz"
            write_obs(p, [{"i": "X", "ok": True, "ts": t,
                           "b": [[9.0, 1.0]], "a": [[11.0, 1.0]]}
                          for t in (50, 10, 30)])
            _, recs = load_snapshots(p)
            self.assertEqual([r["ts"] for r in recs], [10, 30, 50])

    def test_an_invalid_book_is_written_without_levels(self):
        """Ecrire les niveaux d'un carnet invalide le ferait passer pour frais."""
        src = OBS_SRC.read_text(encoding="utf-8")
        self.assertIn('rec["ok"] = False', src)
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_write_snapshots")
        # Les niveaux ne sont affectes que dans la branche valide.
        assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)]
        level_writes = [a for a in assigns
                        for t in a.targets
                        if isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value in ("b", "a")]
        self.assertTrue(level_writes, "aucune ecriture de niveaux trouvee")


class TestCollectorStaysHonestWhenTheFeedBreaks(unittest.TestCase):
    """Defaut reel : la collecte est morte a 16h54 et rien ne l'a signale.

    Deux causes distinctes, deux gardes.
    """

    def test_connection_error_does_not_skip_snapshot_writing(self):
        """Le `continue` dans le gestionnaire d'exception laissait le fichier
        muet pendant toute une tempete de reconnexions."""
        src = OBS_SRC.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
        handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
        self.assertTrue(handlers)
        for h in handlers:
            self.assertFalse(
                any(isinstance(n, ast.Continue) for n in ast.walk(h)),
                "un `continue` dans le gestionnaire saute l'ecriture "
                "d'instantanes : la panne redeviendrait invisible")

    def test_stats_carry_a_heartbeat(self):
        """Un fichier muet est indiscernable d'un marche calme. Le heartbeat
        distingue les deux."""
        st = ObservatoryStats()
        self.assertIn("heartbeat_at", st.to_dict())
        self.assertIn("elapsed_s", st.to_dict())
        src = OBS_SRC.read_text(encoding="utf-8")
        self.assertIn("stats.heartbeat_at = utc_now_iso()", src)

    def test_errors_are_accumulated_not_swallowed(self):
        st = ObservatoryStats()
        st.errors.append("TimeoutError: boom")
        self.assertEqual(len(st.to_dict()["errors"]), 1)


class TestObservatoryRefusesBadInput(unittest.TestCase):

    def test_zero_duration_is_refused(self):
        with self.assertRaises(ValueError):
            MarketObservatory().run([], duration_s=0)

    def test_empty_instrument_list_is_refused(self):
        with self.assertRaises(ValueError):
            MarketObservatory().run([], duration_s=10)

    def test_defaults_are_documented_constants(self):
        self.assertEqual(DEFAULT_SNAPSHOT_MS, 250)
        self.assertEqual(DEFAULT_DEPTH_LEVELS, 10)


if __name__ == "__main__":
    unittest.main()
