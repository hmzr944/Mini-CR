"""Crible de mecanismes — chercher dans l'espace des causes.

L'audit a montre que le cout d'une erreur n'etait pas la strategie mais
d'avoir choisi un evenement sans verifier son sens causal. Ce module inverse
la demarche ; ses tests protegent les deux endroits ou il pourrait mentir :
l'agregation entre instruments, et l'unite des tailles de carnet.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prism_v2.mechanism_screen import (
    CANDIDATES, ROUND_TRIP_COST_BPS, Event, Tape, ev_book_imbalance,
    ev_large_trade, load_tape, measure_event, screen_all,
)

S = 1000


def make_tape(n=200, drift=0.0, inst="X"):
    polls = [i * 6 * S for i in range(n)]
    mids = [100.0 * (1 + drift * i) for i in range(n)]
    return Tape(inst, polls, mids, [1.0] * n, [1e5] * n, [1e5] * n, {})


class TestTape(unittest.TestCase):

    def test_le_mid_hors_couverture_est_none(self):
        t = make_tape()
        self.assertIsNone(t.mid_at(-1))
        self.assertIsNone(t.mid_at(t.poll_ms[-1] + 10 * S))
        self.assertIsNotNone(t.mid_at(t.poll_ms[5]))

    def test_les_tailles_sont_converties_par_ctval(self):
        """Les tailles OKX sont en CONTRATS. Supposer ctVal=1 surestimait la
        profondeur BTC d'un facteur 100 — defaut reel de l'audit."""
        rec = {"inst": "BTC-USDT-SWAP", "poll_ms": 0, "book_ts": 0,
               "bids": [[100.0, 10.0]], "asks": [[101.0, 10.0]], "trades": []}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            p.write_text("\n".join(json.dumps({**rec, "poll_ms": i * 6 * S})
                                   for i in range(60)))
            brut = load_tape(p, ct_val=1.0)
            vrai = load_tape(p, ct_val=0.01)
        self.assertAlmostEqual(brut.bid_depth_usd[0],
                               100.0 * vrai.bid_depth_usd[0], places=6)

    def test_un_fichier_trop_court_ne_produit_pas_de_tape(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            p.write_text(json.dumps({"inst": "X", "poll_ms": 0, "book_ts": 0,
                                     "bids": [[1.0, 1.0]], "asks": [[2.0, 1.0]],
                                     "trades": []}))
            self.assertIsNone(load_tape(p))

    def test_un_carnet_croise_est_ecarte(self):
        rec = {"inst": "X", "poll_ms": 0, "book_ts": 0,
               "bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]], "trades": []}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            p.write_text("\n".join(json.dumps({**rec, "poll_ms": i * 6 * S})
                                   for i in range(60)))
            self.assertIsNone(load_tape(p))


class TestMesure(unittest.TestCase):

    def test_le_sens_annonce_oriente_la_mesure(self):
        """Un evenement qui annonce une baisse dans un marche qui baisse doit
        ressortir POSITIF : la mesure est orientee, pas signee."""
        t = make_tape(drift=-0.0001)
        evs = [Event(t.poll_ms[i], -1) for i in range(20, 60)]
        pre, post = measure_event(t, evs, 30, 30)
        self.assertTrue(all(x > 0 for x in pre))
        self.assertTrue(all(x > 0 for x in post))

    def test_les_evenements_hors_couverture_sont_ecartes(self):
        t = make_tape(n=60)
        evs = [Event(t.poll_ms[0], 1), Event(t.poll_ms[-1], 1)]
        pre, post = measure_event(t, evs, 60, 60)
        self.assertEqual(len(pre), 0)

    def test_les_valeurs_rendues_sont_brutes(self):
        """Agreger par instrument puis dupliquer la moyenne ecrasait la
        dispersion et faussait tout t-stat ulterieur — defaut reel."""
        t = make_tape(drift=0.0002)
        evs = [Event(t.poll_ms[i], 1) for i in range(20, 60)]
        pre, _ = measure_event(t, evs, 30, 30)
        self.assertEqual(len(pre), 40)


class TestCrible(unittest.TestCase):

    def test_les_observations_sont_mises_en_commun_entre_instruments(self):
        """Exiger dix evenements PAR instrument eliminait silencieusement
        tous les evenements de carnet, rares chacun mais nombreux au total.
        """
        tapes = [make_tape(drift=0.0001, inst=f"I{k}") for k in range(6)]
        for t in tapes:
            for i in range(30, 50):
                t.trades[t.poll_ms[i]] = (1e6, 0.0, 5)
            for i in range(60):
                t.trades.setdefault(t.poll_ms[i], (10.0, 10.0, 1))
        res = screen_all(tapes, 30, 30)
        n = res["gros_trade_agressif"]["n"]
        self.assertGreater(n, 10)

    def test_le_seuil_economique_rend_symptome_un_effet_trop_petit(self):
        """Un evenement parfaitement causal mais a 2 bps n'est pas
        exploitable a 11 bps de cout."""
        tapes = [make_tape(drift=0.0000005, inst=f"I{k}") for k in range(6)]
        for t in tapes:
            for i in range(20, 60):
                t.trades[t.poll_ms[i]] = (1e6, 0.0, 5)
            for i in range(60):
                t.trades.setdefault(t.poll_ms[i], (10.0, 10.0, 1))
        libre = screen_all(tapes, 30, 30, min_post_bps=0.0)
        cher = screen_all(tapes, 30, 30, min_post_bps=ROUND_TRIP_COST_BPS)
        self.assertFalse(cher["gros_trade_agressif"]["candidat"])
        self.assertIsNotNone(libre["gros_trade_agressif"]["n"])

    def test_le_cout_est_celui_mesure(self):
        self.assertAlmostEqual(ROUND_TRIP_COST_BPS, 11.0)

    def test_les_evenements_candidats_sont_declares(self):
        self.assertGreaterEqual(len(CANDIDATES), 5)
        for name, fn in CANDIDATES.items():
            self.assertTrue(callable(fn), name)

    def test_un_univers_vide_ne_fabrique_aucun_resultat(self):
        for r in screen_all([], 30, 30).values():
            self.assertEqual(r["statut"], "ECHANTILLON_INSUFFISANT")


if __name__ == "__main__":
    unittest.main()
