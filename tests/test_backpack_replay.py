"""Le rejeu contre carnet enregistre refuse-t-il d'inventer un carnet ?

Ce module de test garde les deux interdits du rejeu, et ils ont le meme mode
d'echec : produire un markout NUL — c'est-a-dire l'absence d'adverse
selection, le resultat le plus flatteur possible — a partir d'un defaut de
donnee plutot que d'une mesure.

  - reconduire un instantane perime : le mid ne bouge pas, donc pas de derive ;
  - interpoler entre deux instantanes : la derive devient lisse et petite.

Les deux rendent INCONNU a la place.
"""
import json
import tempfile
import unittest
from pathlib import Path

from prism_v2.backpack.collector import DEFAULT_POLL_S
from prism_v2.backpack.replay import (MIN_FILLS_FOR_MEDIAN, BookHistory, Snap,
                                      load_books, replay)
from prism_v2.subsidy.tape import Trade


def snap(ts, bid, ask, bid_sz=100.0, ask_sz=100.0):
    return Snap(ts, bid, ask, bid_sz, ask_sz)


class TestLectureParTemps(unittest.TestCase):

    def setUp(self):
        self.h = BookHistory([snap(0.0, 99.0, 101.0), snap(5.0, 99.5, 101.5),
                              snap(10.0, 100.0, 102.0)], poll_s=5.0)

    def test_avant_rend_le_dernier_strictement_anterieur(self):
        self.assertEqual(self.h.before(7.0).ts, 5.0)

    def test_avant_exclut_l_instantane_du_meme_horodatage(self):
        """Un carnet pris a l'instant de l'echange peut deja porter son effet."""
        self.assertEqual(self.h.before(5.0).ts, 0.0)

    def test_un_instantane_trop_VIEUX_rend_INCONNU(self):
        """Reconduire le dernier carnet connu fabriquerait un markout nul."""
        self.assertIsNone(self.h.before(100.0))

    def test_a_partir_de_rend_le_premier_a_ou_apres(self):
        self.assertEqual(self.h.at_or_after(6.0).ts, 10.0)
        self.assertEqual(self.h.at_or_after(10.0).ts, 10.0)

    def test_un_horizon_au_dela_de_la_collecte_rend_INCONNU(self):
        self.assertIsNone(self.h.at_or_after(1_000.0))

    def test_aucune_interpolation_n_est_produite(self):
        """La valeur lue est celle d'un instantane REEL, jamais une moyenne."""
        s = self.h.at_or_after(6.0)
        self.assertIn(s.mid, {101.0, 100.5, 100.0})
        self.assertEqual(s.mid, 101.0)

    def test_avant_le_premier_instantane_il_n_y_a_rien(self):
        self.assertIsNone(self.h.before(-1.0))


class TestChargement(unittest.TestCase):

    def test_une_ligne_tronquee_est_sautee_pas_reparee(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.jsonl"
            good = {"ts": 1.0, "sym": "X", "bid": 99.0, "ask": 101.0,
                    "bid_sz": 5.0, "ask_sz": 5.0}
            p.write_text(json.dumps(good) + "\n" + '{"ts": 2.0, "sym": "X"')
            books = load_books(p)
            self.assertEqual(len(books["X"]), 1)

    def test_les_symboles_sont_separes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.jsonl"
            rows = [{"ts": 1.0, "sym": s, "bid": 99.0, "ask": 101.0,
                     "bid_sz": 5.0, "ask_sz": 5.0} for s in ("A", "B")]
            p.write_text("\n".join(json.dumps(r) for r in rows))
            self.assertEqual(sorted(load_books(p)), ["A", "B"])


class TestRejeu(unittest.TestCase):

    def _books(self, n=80, drift=0.0):
        return BookHistory([snap(i * 5.0, 99.0 + i * drift, 101.0 + i * drift)
                            for i in range(n)], poll_s=5.0)

    def _tape(self, n=80):
        """Un echange par cycle, alternant les deux cotes."""
        out = []
        for i in range(n):
            ts = i * 5.0 + 2.5
            if i % 2:
                out.append(Trade(ts, 101.0, 1.0, taker_is_buy=True))
            else:
                out.append(Trade(ts, 99.0, 1.0, taker_is_buy=False))
        return out

    def test_un_echange_hors_fenetre_de_collecte_est_exclu(self):
        """Fenetre = [0 s, 20 s] (5 instantanes a 5 s). La bande porte des
        echanges a 2,5 / 7,5 / 12,5 / 17,5 / 22,5 s : le dernier tombe APRES
        le dernier carnet enregistre, et le lointain n'a jamais de carnet.
        Les deux sont exclus — les mesurer supposerait un carnet."""
        books = self._books(n=5)
        tape = self._tape(n=5) + [Trade(1e9, 99.0, 1.0, taker_is_buy=False)]
        self.assertEqual(books.window(), (0.0, 20.0))
        self.assertEqual(replay("X", books, tape).trades_in_window, 4)

    def test_sans_carnet_tout_est_inconnu_et_rien_ne_plante(self):
        r = replay("X", BookHistory([]), self._tape())
        self.assertEqual(r.snapshots, 0)
        self.assertIsNone(r.median_half_spread_bps())
        self.assertIsNone(r.breakeven_fee_bps(60.0))

    def test_un_marche_immobile_encaisse_le_demi_spread_sans_markout(self):
        r = replay("X", self._books(), self._tape(), horizons_s=(60.0,))
        self.assertAlmostEqual(r.median_markout_bps(60.0), 0.0, places=9)
        self.assertGreater(r.median_half_spread_bps(), 0.0)
        self.assertGreater(r.breakeven_fee_bps(60.0), 0.0)

    def test_sous_le_seuil_de_mediane_le_resultat_reste_INCONNU(self):
        peu = MIN_FILLS_FOR_MEDIAN - 1
        r = replay("X", self._books(n=peu), self._tape(n=peu),
                   horizons_s=(60.0,))
        self.assertIsNone(r.median_half_spread_bps())
        self.assertIsNone(r.breakeven_fee_bps(60.0))

    def test_un_marche_qui_derive_produit_un_markout_non_nul(self):
        r = replay("X", self._books(drift=0.05), self._tape(),
                   horizons_s=(60.0,))
        self.assertNotAlmostEqual(r.median_markout_bps(60.0), 0.0, places=6)

    def test_la_file_reelle_retarde_les_fills(self):
        """Une file profonde doit produire MOINS de fills qu'une file vide.

        C'est la difference entre ce rejeu et `passive.py`, qui donnait au
        maker la premiere place et donc tous les fills.
        """
        profonde = BookHistory([snap(i * 5.0, 99.0, 101.0, bid_sz=1e9)
                                for i in range(80)], poll_s=5.0)
        vide = BookHistory([snap(i * 5.0, 99.0, 101.0, bid_sz=0.0)
                            for i in range(80)], poll_s=5.0)
        r_prof = replay("X", profonde, self._tape())
        r_vide = replay("X", vide, self._tape())
        self.assertEqual(r_prof.queue_aware_fills, 0)
        self.assertIsNotNone(r_vide.queue_aware_fills)

    def test_la_cadence_de_sondage_est_prise_du_collecteur(self):
        """Les deux modules doivent s'accorder : une cadence divergente
        rendrait tous les instantanes perimes, donc tout INCONNU."""
        self.assertEqual(DEFAULT_POLL_S, 5.0)



class TestCouvertureDeBande(unittest.TestCase):
    """La garde doit alarmer sur une bande ECHAPPEE, et seulement sur elle."""

    def _t(self, ts):
        return Trade(ts, 100.0, 1.0, taker_is_buy=True)

    def test_une_bande_echappee_par_la_GAUCHE_est_detectee(self):
        """Le defaut reel : /trades plafonne a 1 000 echanges, les plus
        anciens sortent, et le rejeu lit « 0 echange » comme un marche mort."""
        from prism_v2.backpack.replay import tape_covers_window
        self.assertFalse(tape_covers_window([self._t(50.0)], (0.0, 100.0)))

    def test_une_bande_finissant_AVANT_le_dernier_carnet_reste_valide(self):
        """Un marche calme n'a simplement pas traite pendant quelques
        secondes. Alarmer ici rendait NON sur six marches sains."""
        from prism_v2.backpack.replay import tape_covers_window
        self.assertTrue(tape_covers_window([self._t(-10.0), self._t(90.0)],
                                           (0.0, 100.0)))

    def test_une_bande_vide_n_est_jamais_une_couverture(self):
        from prism_v2.backpack.replay import tape_covers_window
        self.assertFalse(tape_covers_window([], (0.0, 100.0)))

if __name__ == "__main__":
    unittest.main(verbosity=2)
