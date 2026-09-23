"""La subvention : la formule est-elle transcrite, et les inconnues tenues ?

Ce module est le seul du depot dont le revenu ne depend d'aucune prediction.
Il est donc aussi le seul ou une erreur de formule se lit directement en
euros. Ces tests figent la formule publiee et, surtout, les DEUX fautes que
j'ai commises en la construisant dans cette meme session :

  1. remplacer une liquidite qualifiante NULLE par 1 dollar, ce qui donnait
     une part de 99 % et un rendement annonce de 112,88 %/jour ;
  2. modeliser la part au prorata PLAT, en ignorant que le score decroit en
     carre de la distance au mid.

Les deux allaient dans le sens favorable. Les deux sont desormais des tests.
"""
import unittest

from prism_v2.subsidy.economics import (TAKER_FEE_RATES, UNKNOWNS, Fill,
                                        MarketEconomics, portfolio_gross,
                                        taker_fee_usd)
from prism_v2.subsidy.scoring import (MIDPOINT_BAND, QualifyingOrder,
                                      daily_reward, epoch_share, order_score,
                                      two_sided_score)


class TestFormule(unittest.TestCase):
    """S(v, s) = ((v - s)/v)^2 * taille, transcrite et non approchee."""

    def test_le_score_est_nul_a_la_bordure(self):
        """A s = v le score vaut EXACTEMENT zero, pas un residu."""
        self.assertEqual(
            order_score(QualifyingOrder(4.5, 1000, True), 4.5, 0), 0.0)
        self.assertEqual(
            order_score(QualifyingOrder(9.0, 1000, True), 4.5, 0), 0.0)

    def test_la_decroissance_est_quadratique_et_non_lineaire(self):
        """A mi-bande le score vaut le QUART, pas la moitie.

        C'est tout l'ecart entre le prorata plat — qui a produit 9,44 %/jour —
        et la formule publiee. Un modele lineaire surevalue d'un facteur 2 a
        mi-bande, et bien plus pres du bord.
        """
        plein = order_score(QualifyingOrder(0.0, 100, True), 4.0, 0)
        moitie = order_score(QualifyingOrder(2.0, 100, True), 4.0, 0)
        self.assertAlmostEqual(moitie / plein, 0.25, places=9)

    def test_sous_la_taille_minimale_le_score_est_nul(self):
        self.assertEqual(order_score(QualifyingOrder(1.0, 19, True), 4.5, 20),
                         0.0)
        self.assertGreater(order_score(QualifyingOrder(1.0, 20, True), 4.5, 20),
                           0.0)

    def test_hors_de_la_bande_de_mid_un_seul_cote_ne_paie_rien(self):
        """Regle documentee : hors [0,10 ; 0,90] la double cotation est due."""
        bid = QualifyingOrder(1.0, 150, True)
        lo, hi = MIDPOINT_BAND
        self.assertEqual(two_sided_score([bid], lo - 0.01, 4.5, 20), 0.0)
        self.assertEqual(two_sided_score([bid], hi + 0.01, 4.5, 20), 0.0)
        self.assertGreater(two_sided_score([bid], 0.5, 4.5, 20), 0.0)

    def test_dans_la_bande_un_seul_cote_est_divise_par_trois(self):
        bid = QualifyingOrder(1.0, 150, True)
        seul = two_sided_score([bid], 0.5, 4.5, 20)
        plein = order_score(bid, 4.5, 20)
        self.assertAlmostEqual(seul, plein / 3.0, places=9)

    def test_le_cote_faible_commande(self):
        """Deux cotes desequilibres : le fort ne rattrape pas le faible."""
        gros = QualifyingOrder(1.0, 1000, True)
        petit = QualifyingOrder(1.0, 10, False)
        q = two_sided_score([gros, petit], 0.5, 4.5, 0)
        self.assertLess(q, order_score(gros, 4.5, 0))


class TestInconnues(unittest.TestCase):
    """Le defaut a 112,88 %/jour, fige pour qu'il ne revienne pas."""

    def test_concurrence_inconnue_ne_vaut_pas_cent_pour_cent(self):
        """LA FAUTE : `others_q = None` doit rendre None, jamais 1,0."""
        self.assertIsNone(epoch_share(100.0, None))
        self.assertIsNone(daily_reward(106.0, epoch_share(100.0, None)))

    def test_concurrence_mesuree_nulle_est_autre_chose(self):
        """Mesuree a zero, la part vaut bien 1,0 — mais il faut l'avoir mesuree."""
        self.assertEqual(epoch_share(100.0, 0.0), 1.0)

    def test_une_part_inconnue_contamine_le_portefeuille(self):
        """Sommer en sautant l'inconnu produirait un total d'apparence complete."""
        connu = MarketEconomics("a", 100.0, 0.5, 500.0)
        inconnu = MarketEconomics("b", 100.0, None, 500.0)
        self.assertIsNotNone(portfolio_gross([connu]))
        self.assertIsNone(portfolio_gross([connu, inconnu]))

    def test_le_net_est_none_tant_que_le_flux_n_est_pas_mesure(self):
        m = MarketEconomics("a", 106.0, 0.448, 375.0)
        self.assertIsNotNone(m.gross_usd_per_day())
        self.assertIsNone(m.net_usd_per_day())
        self.assertEqual(m.status(), "EDGE BRUT OBSERVE")

    def test_les_six_inconnues_sont_nommees_dans_le_code(self):
        """Un rapport se perime, un import non."""
        for k in ("part_reelle", "pool_effectivement_verse", "flux_subi",
                  "disponibilite", "taux_de_frais_taker", "acces"):
            self.assertIn(k, UNKNOWNS)
            self.assertGreater(len(UNKNOWNS[k]), 60)

    def test_la_disponibilite_multiplie_et_ne_se_prorate_pas(self):
        plein = daily_reward(100.0, 0.5, 1.0)
        moitie = daily_reward(100.0, 0.5, 0.5)
        self.assertAlmostEqual(moitie, plein / 2.0, places=9)
        with self.assertRaises(ValueError):
            daily_reward(100.0, 0.5, 1.5)


class TestNeutralisation(unittest.TestCase):
    """YES + NO = 1 : le cout d'un remplissage se LIT, il ne s'estime pas."""

    def test_le_frais_taker_est_compte_sur_la_jambe_de_protection(self):
        """Oublier ce frais rend gratuite la seule operation qui protege."""
        geo = Fill(0.30, 0.68, 100, taker_rate=0.0)
        pol = Fill(0.30, 0.68, 100, taker_rate=0.04)
        self.assertLess(geo.neutralisation_cost_usd(),
                        pol.neutralisation_cost_usd())

    def test_un_taux_inconnu_ne_vaut_pas_zero(self):
        self.assertIsNone(Fill(0.30, 0.68, 100).neutralisation_cost_usd())

    def test_sans_complement_cote_on_ne_peut_pas_neutraliser(self):
        self.assertIsNone(
            Fill(0.30, None, 100, taker_rate=0.0).neutralisation_cost_usd())

    def test_le_frais_est_maximal_au_milieu(self):
        """frais = parts * taux * p * (1-p) : symetrique, pic a 0,50."""
        milieu = taker_fee_usd(0.50, 100, 0.05)
        bord = taker_fee_usd(0.05, 100, 0.05)
        self.assertGreater(milieu, bord)
        self.assertAlmostEqual(taker_fee_usd(0.30, 100, 0.05),
                               taker_fee_usd(0.70, 100, 0.05), places=9)

    def test_le_geopolitique_est_le_seul_taux_nul(self):
        self.assertEqual(TAKER_FEE_RATES["geopolitical"], 0.0)
        for k, v in TAKER_FEE_RATES.items():
            if k != "geopolitical":
                self.assertGreater(v, 0.0)


if __name__ == "__main__":
    unittest.main()
