"""La simulation de remplissage respecte-t-elle la file et la causalite ?"""
import unittest

from prism_v2.subsidy.tape import (RestingQuote, Trade, filled_notional,
                                   fills_per_day, simulate)


def q(**kw):
    base = dict(price=0.40, size=100.0, is_bid=True, queue_ahead=0.0,
                placed_ts=0.0)
    base.update(kw)
    return RestingQuote(**base)


class TestCausalite(unittest.TestCase):

    def test_un_echange_anterieur_ne_remplit_jamais(self):
        r = simulate(q(placed_ts=100.0),
                     [Trade(50.0, 0.40, 50, taker_is_buy=False)])
        self.assertEqual(r.filled, 0.0)

    def test_un_echange_posterieur_remplit(self):
        r = simulate(q(placed_ts=100.0),
                     [Trade(150.0, 0.40, 50, taker_is_buy=False)])
        self.assertEqual(r.filled, 50.0)


class TestFile(unittest.TestCase):

    def test_la_file_devant_se_sert_d_abord(self):
        """200 parts devant : un echange de 150 ne m'atteint pas."""
        r = simulate(q(queue_ahead=200.0),
                     [Trade(10.0, 0.40, 150, taker_is_buy=False)])
        self.assertEqual(r.filled, 0.0)

    def test_le_reliquat_me_sert_apres_la_file(self):
        r = simulate(q(queue_ahead=200.0),
                     [Trade(10.0, 0.40, 250, taker_is_buy=False)])
        self.assertEqual(r.filled, 50.0)

    def test_la_file_ne_se_reconstitue_pas_entre_echanges(self):
        r = simulate(q(queue_ahead=100.0),
                     [Trade(10.0, 0.40, 60, taker_is_buy=False),
                      Trade(20.0, 0.40, 60, taker_is_buy=False)])
        self.assertEqual(r.filled, 20.0)

    def test_on_ne_depasse_jamais_sa_taille(self):
        r = simulate(q(size=100.0),
                     [Trade(10.0, 0.40, 10_000, taker_is_buy=False)])
        self.assertEqual(r.filled, 100.0)


class TestSens(unittest.TestCase):

    def test_un_preneur_vendeur_frappe_mon_bid(self):
        r = simulate(q(is_bid=True, price=0.40),
                     [Trade(10.0, 0.39, 50, taker_is_buy=False)])
        self.assertEqual(r.filled, 50.0)

    def test_un_preneur_acheteur_ne_frappe_pas_mon_bid(self):
        r = simulate(q(is_bid=True, price=0.40),
                     [Trade(10.0, 0.39, 50, taker_is_buy=True)])
        self.assertEqual(r.filled, 0.0)

    def test_un_echange_sous_mon_ask_ne_me_sert_pas(self):
        r = simulate(q(is_bid=False, price=0.60),
                     [Trade(10.0, 0.55, 50, taker_is_buy=True)])
        self.assertEqual(r.filled, 0.0)


class TestExtrapolation(unittest.TestCase):

    def test_une_fenetre_courte_ne_produit_pas_de_taux(self):
        """Extrapoler depuis dix minutes donnerait un nombre d'apparence mesuree."""
        r = simulate(q(), [Trade(10.0, 0.40, 50, taker_is_buy=False)])
        self.assertIsNone(fills_per_day(r, 600.0))
        self.assertIsNotNone(fills_per_day(r, 7_200.0))

    def test_le_taux_est_bien_un_par_jour(self):
        r = simulate(q(size=1000.0),
                     [Trade(10.0, 0.40, 10, taker_is_buy=False),
                      Trade(20.0, 0.40, 10, taker_is_buy=False)])
        self.assertAlmostEqual(fills_per_day(r, 86_400.0), 2.0, places=9)

    def test_le_notionnel_rempli_est_en_dollars(self):
        r = simulate(q(), [Trade(10.0, 0.40, 50, taker_is_buy=False)])
        self.assertAlmostEqual(filled_notional(r), 20.0, places=9)


if __name__ == "__main__":
    unittest.main()
