"""Gardes du module pm_favlong — chacune verrouille une erreur deja commise.

Aucune ne verifie qu'un edge existe. Toutes verifient que la mesure ne peut
pas en fabriquer un.
"""
import math
import unittest

from prism_v2.scans.pm_favlong import (bucket_test, cluster_in_bucket,
                                       economics, kelly_fraction, price_at,
                                       required_n, taker_fee)


class TestAncrage(unittest.TestCase):
    """La v1 datait les prix par rapport au DERNIER POINT de l'historique, qui
    marque l'arret des echanges. L'ecart avec la resolution reelle allait de
    2 593 a 11 935 heures : « six heures avant » pouvait etre des mois avant."""

    def test_ancre_sur_la_resolution_pas_sur_la_fin_de_l_historique(self):
        # l'historique s'arrete 100 h avant la resolution
        closed = 1_000_000.0
        hist = [{"t": closed - 200 * 3600 + i * 3600, "p": 0.5 + i * 0.001}
                for i in range(100)]
        got = price_at(hist, closed, 2.0)
        self.assertIsNotNone(got)
        _p, age = got
        # l'age REEL doit etre signale, pas ramene a 2 h
        self.assertGreater(age, 99.0)

    def test_rien_avant_le_debut(self):
        hist = [{"t": 500.0, "p": 0.5}]
        self.assertIsNone(price_at(hist, 1000.0, 10.0))


class TestClustering(unittest.TestCase):
    """Regrouper par evenement AVANT de repartir en seaux moyenne le prix d'un
    groupe entier : 0,90/0,05/0,02 ressort a 0,32 et la zone favorite se vide.
    C'est ce qui avait rendu tous les seaux au-dessus de 0,75 inexistants."""

    def test_le_regroupement_ne_deplace_pas_les_prix_du_seau(self):
        rows = [{"p": 0.97, "win": 1.0, "event": "E"},
                {"p": 0.98, "win": 1.0, "event": "E"}]
        g = cluster_in_bucket(rows)
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["p"], 0.975)   # reste dans la zone favorite

    def test_un_groupe_correle_compte_pour_une_observation(self):
        rows = [{"p": 0.6, "win": 1.0, "event": "course"}] + \
               [{"p": 0.6, "win": 0.0, "event": "course"} for _ in range(9)]
        g = cluster_in_bucket(rows)
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["win"], 0.1)

    def test_les_marches_isoles_restent_distincts(self):
        rows = [{"p": 0.6, "win": 1.0, "event": None} for _ in range(5)]
        self.assertEqual(len(cluster_in_bucket(rows)), 5)


class TestStatistique(unittest.TestCase):
    """L'ecart-type de la proportion OBSERVEE vaut 0 quand tout un seau resout
    dans le meme sens, et le t part a l'infini — la v1 affichait t = 38 000.
    Le test correct prend la variance sous H0."""

    def test_un_seau_unanime_ne_donne_pas_un_t_infini(self):
        rows = [{"p": 0.98, "win": 1.0} for _ in range(16)]
        bt = bucket_test(rows)
        self.assertTrue(math.isfinite(bt["t"]))
        self.assertLess(abs(bt["t"]), 2.0)      # 16/16 a 0,98 n'est pas rare

    def test_un_seau_unanime_devient_significatif_avec_assez_d_observations(self):
        petit = bucket_test([{"p": 0.98, "win": 1.0} for _ in range(16)])
        grand = bucket_test([{"p": 0.98, "win": 1.0} for _ in range(2000)])
        self.assertGreater(grand["t"], petit["t"])
        self.assertGreater(grand["t"], 2.0)

    def test_echantillon_trop_petit_refuse(self):
        self.assertIsNone(bucket_test([{"p": 0.9, "win": 1.0}] * 3))


class TestPuissance(unittest.TestCase):
    """Le gain maximal d'un favori vaut (1 - p) et s'annule quand p -> 1, mais
    le bruit ne decroit qu'en sqrt(p(1-p)). La mecanique devient donc
    inverifiable exactement la ou elle parait la plus sure."""

    def test_l_exigence_explose_vers_un(self):
        self.assertLess(required_n(0.85), required_n(0.95))
        self.assertLess(required_n(0.95), required_n(0.99))
        self.assertGreater(required_n(0.99) / required_n(0.85), 10.0)

    def test_bornes(self):
        self.assertEqual(required_n(1.0), float("inf"))
        self.assertEqual(required_n(0.9, 0.0), float("inf"))


class TestCouts(unittest.TestCase):
    """Le frais Polymarket porte sur min(p, 1-p), pas sur le prix. La
    difference est d'un facteur trente entre un favori et un contrat a
    mi-prix, et c'est elle qui decide du verdict."""

    def test_l_assiette_est_le_moindre_des_deux_cotes(self):
        fs = {"rate": 0.05, "takerOnly": True, "exponent": 1}
        self.assertAlmostEqual(taker_fee(0.98, fs), 0.05 * 0.02)
        self.assertAlmostEqual(taker_fee(0.50, fs), 0.05 * 0.50)
        self.assertAlmostEqual(taker_fee(0.02, fs), 0.05 * 0.02)

    def test_le_frais_est_maximal_a_mi_prix(self):
        fs = {"rate": 0.05}
        self.assertGreater(taker_fee(0.5, fs), taker_fee(0.9, fs))
        self.assertGreater(taker_fee(0.5, fs), taker_fee(0.1, fs))

    def test_frais_absent_vaut_zero_et_non_une_valeur_supposee(self):
        self.assertEqual(taker_fee(0.9, None), 0.0)
        self.assertEqual(taker_fee(0.9, {}), 0.0)

    def test_l_achat_se_fait_a_l_ask(self):
        b = {"n": 100, "p": 0.95, "w": 0.96, "edge_c": 1.0, "t": 1.0,
             "ev": 0.0, "p_val": 0.3}
        sans = economics(b, 48.0, 0.0, 0.0)
        avec = economics(b, 48.0, 0.5, 0.05)
        self.assertGreater(avec["p_exec"], sans["p_exec"])
        self.assertLess(avec["ev_net"], sans["ev_net"])

    def test_un_prix_executable_a_un_ne_rapporte_rien(self):
        b = {"n": 100, "p": 0.999, "w": 1.0, "edge_c": 0.1, "t": 1.0,
             "ev": 0.0, "p_val": 0.3}
        ec = economics(b, 48.0, 5.0, 0.0)
        self.assertIn("p_exec", ec)          # jamais de KeyError silencieux
        self.assertTrue(math.isnan(ec["ev_net"]))


class TestKelly(unittest.TestCase):
    def test_pas_d_avantage_pas_de_mise(self):
        self.assertLessEqual(kelly_fraction(0.95, 0.95), 1e-9)
        self.assertLess(kelly_fraction(0.90, 0.95), 0.0)

    def test_avantage_reel_donne_une_mise_positive(self):
        self.assertGreater(kelly_fraction(0.99, 0.95), 0.0)


if __name__ == "__main__":
    unittest.main()
