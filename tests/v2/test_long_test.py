"""Outils statistiques du test long.

La correction pour tests multiples est ce qui separe « j'ai trouve un signal »
de « j'ai teste quatre choses et garde la meilleure ». Sans elle, tester
quatre signaux et retenir le meilleur donne environ une chance sur cinq de
retenir du bruit.
"""
from __future__ import annotations

import random
import unittest

from prism_v2.long_test import (
    BARS_PER_DAY, BARS_PER_YEAR, COST_BPS, benjamini_hochberg,
    sharpe_from_bars, t_test_one_sided,
)


class TestStatistique(unittest.TestCase):

    def test_une_moyenne_nulle_ne_produit_aucune_significativite(self):
        random.seed(1)
        t, p = t_test_one_sided([random.gauss(0, 1) for _ in range(3000)])
        self.assertGreater(p, 0.01)

    def test_une_moyenne_positive_est_detectee(self):
        random.seed(2)
        t, p = t_test_one_sided([random.gauss(0.05, 1) for _ in range(3000)])
        self.assertLess(p, 0.01)
        self.assertGreater(t, 0)

    def test_le_test_est_unilateral(self):
        """Une moyenne negative ne doit jamais ressortir significative."""
        random.seed(3)
        t, p = t_test_one_sided([random.gauss(-0.05, 1) for _ in range(3000)])
        self.assertGreater(p, 0.9)

    def test_echantillon_trop_petit_refuse_de_conclure(self):
        t, p = t_test_one_sided([0.1] * 10)
        self.assertEqual(p, 1.0)

    def test_variance_nulle_ne_fabrique_pas_un_t_infini(self):
        t, p = t_test_one_sided([0.01] * 100)
        self.assertEqual(p, 1.0)


class TestBenjaminiHochberg(unittest.TestCase):

    def test_seuls_les_p_assez_petits_survivent(self):
        s = benjamini_hochberg({"a": 0.001, "b": 0.20, "c": 0.60, "d": 0.99},
                               q=0.10)
        self.assertTrue(s["a"])
        self.assertFalse(s["b"])
        self.assertFalse(s["d"])

    def test_aucun_survivant_quand_tout_est_du_bruit(self):
        s = benjamini_hochberg({k: 0.5 for k in "abcd"}, q=0.10)
        self.assertFalse(any(s.values()))

    def test_bh_est_plus_severe_qu_un_seuil_naif(self):
        """p=0,04 passerait un seuil naif a 5 % ; BH le refuse ici."""
        p = {"a": 0.04, "b": 0.5, "c": 0.6, "d": 0.7}
        self.assertFalse(benjamini_hochberg(p, q=0.10)["a"])

    def test_la_procedure_est_par_paliers_pas_par_seuil_fixe(self):
        """Un p moyen survit s'il est accompagne : c'est le propre de BH."""
        p = {"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.9}
        s = benjamini_hochberg(p, q=0.10)
        self.assertTrue(s["a"])
        self.assertTrue(s["b"])
        self.assertFalse(s["d"])

    def test_un_q_plus_permissif_laisse_passer_davantage(self):
        p = {"a": 0.03, "b": 0.5, "c": 0.6, "d": 0.7}
        self.assertFalse(benjamini_hochberg(p, q=0.05)["a"])
        self.assertTrue(benjamini_hochberg(p, q=0.50)["a"])


class TestParametresGeles(unittest.TestCase):

    def test_le_cout_est_celui_mesure(self):
        """4,5 bps de frais + 2,04 bps de demi-spread p75 reel."""
        self.assertAlmostEqual(COST_BPS, 6.54, places=2)

    def test_les_barres_sont_de_quatre_heures(self):
        self.assertEqual(BARS_PER_DAY, 6)
        self.assertEqual(BARS_PER_YEAR, 6 * 365)

    def test_le_sharpe_est_annualise_sur_la_cadence_des_barres(self):
        import math
        v = [0.001] * 100 + [-0.001] * 100
        s = sharpe_from_bars(v)
        self.assertIsNotNone(s)
        self.assertAlmostEqual(abs(s), 0.0, places=6)

    def test_une_serie_constante_ne_donne_pas_de_sharpe(self):
        self.assertIsNone(sharpe_from_bars([0.001] * 50))


if __name__ == "__main__":
    unittest.main()
