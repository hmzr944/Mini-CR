"""Gardes de la porte de capital.

Un objectif en EUROS n'est pas un objectif tant que le capital n'est pas dit :
20 EUR/jour vaut 200 bps/jour sur 1 000 EUR et 1 bps/jour sur 200 000. Ces
tests verrouillent la conversion, la decomposition par rotation, et le refus
de comparer un brut taker a un cout maker.
"""
import math
import unittest

from prism_v2.capital_gate import (BEST_MEASURED_GROSS_BPS,
                                   BEST_MEASURED_TRADES_PER_DAY,
                                   MEASURED_RATES, MeasuredRate,
                                   best_demonstrated, cost_ceiling_bps,
                                   gap_factor, net_edge_bps,
                                   rate_from_turnover, required_capital,
                                   required_edge_bps, required_rate_bps_day,
                                   verdict)


class TestConversion(unittest.TestCase):
    def test_vingt_euros_par_jour_sur_mille(self):
        self.assertAlmostEqual(required_rate_bps_day(20.0, 1000.0), 200.0)

    def test_le_meme_objectif_est_trivial_a_grand_capital(self):
        self.assertAlmostEqual(required_rate_bps_day(20.0, 200_000.0), 1.0)

    def test_aller_retour_de_la_conversion(self):
        cap = required_capital(20.0, 1.1)
        self.assertAlmostEqual(required_rate_bps_day(20.0, cap), 1.1, places=9)

    def test_un_taux_nul_exige_un_capital_infini(self):
        self.assertEqual(required_capital(20.0, 0.0), float("inf"))
        self.assertEqual(required_rate_bps_day(20.0, 0.0), float("inf"))


class TestTauxMesures(unittest.TestCase):
    def test_aucun_taux_sans_source(self):
        with self.assertRaises(ValueError):
            MeasuredRate("invente", 500.0, "")

    def test_toutes_les_entrees_ont_une_source(self):
        for r in MEASURED_RATES.values():
            self.assertTrue(r.source)

    def test_le_meilleur_demontre_exclut_les_non_significatifs(self):
        best = best_demonstrated()
        self.assertIsNotNone(best)
        self.assertTrue(best.demonstrated)
        # les recompenses Polymarket (4 bps) sont plus grandes mais NON
        # demontrees : elles ne doivent pas etre retenues
        self.assertLess(best.bps_per_day, 4.0)

    def test_le_verdict_est_negatif_a_mille_euros(self):
        v = verdict(20.0, 1000.0)
        self.assertFalse(v["reachable"])
        self.assertGreater(v["gap_factor"], 100.0)

    def test_le_verdict_devient_positif_au_capital_requis(self):
        best = best_demonstrated()
        cap = required_capital(20.0, best.bps_per_day)
        self.assertTrue(verdict(20.0, cap * 1.01)["reachable"])


class TestRotation(unittest.TestCase):
    def test_le_rendement_est_le_produit(self):
        self.assertAlmostEqual(rate_from_turnover(10.0, 20.0), 200.0)

    def test_l_edge_requis_tombe_avec_la_frequence(self):
        a = required_edge_bps(20.0, 1000.0, 10.0)
        b = required_edge_bps(20.0, 1000.0, 100.0)
        self.assertAlmostEqual(a, 20.0)
        self.assertAlmostEqual(b, 2.0)
        self.assertGreater(a, b)

    def test_sans_rotation_aucun_edge_ne_suffit(self):
        self.assertEqual(required_edge_bps(20.0, 1000.0, 0.0), float("inf"))

    def test_le_plafond_de_cout_est_la_forme_utile(self):
        """Le brut et la frequence sont mesures ; le cout est un parametre
        d'acces. Le plafond dit quel tarif rendrait la mecanique viable."""
        c = cost_ceiling_bps(BEST_MEASURED_GROSS_BPS, 20.0, 1000.0,
                             BEST_MEASURED_TRADES_PER_DAY)
        self.assertGreater(c, 0.0)
        self.assertLess(c, 3.0)    # sous le moins cher des maker accessibles

    def test_le_brut_mesure_depasse_la_cible_mais_pas_le_net(self):
        brut = rate_from_turnover(BEST_MEASURED_GROSS_BPS,
                                  BEST_MEASURED_TRADES_PER_DAY)
        self.assertGreater(brut, required_rate_bps_day(20.0, 1000.0))
        # ... et pourtant le net au tarif le moins cher reste negatif
        net = (BEST_MEASURED_GROSS_BPS - 3.0) * BEST_MEASURED_TRADES_PER_DAY
        self.assertLess(net, 0.0)


class TestMelangeDesStyles(unittest.TestCase):
    """Soustraire un cout maker d'un brut mesure en taker fait apparaitre un
    mecanisme a portee de main alors que le brut n'a jamais ete mesure sous
    remplissage passif."""

    def test_meme_style_autorise(self):
        self.assertAlmostEqual(net_edge_bps(2.63, 9.0, "taker", "taker"), -6.37)

    def test_melange_refuse(self):
        with self.assertRaises(ValueError):
            net_edge_bps(2.63, 3.0, "taker", "maker")


if __name__ == "__main__":
    unittest.main()
