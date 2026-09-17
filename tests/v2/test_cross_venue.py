"""Arbitrage inter-venues — et les trois facons de le faire paraitre bon.

C'est la premiere famille du depot qui ne s'effondre pas de plusieurs ordres
de grandeur. Elle merite donc des tests plus mefiants que les autres, orientes
vers les erreurs qui la flatteraient :

  1. compter le capital d'une seule jambe au lieu des deux ;
  2. confondre la fenetre d'entree avec la duree de detention ;
  3. oublier que les deux venues taxent au meme endroit, a 50/50.
"""
from __future__ import annotations

import math
import unittest

from prism_v2.capital_efficiency import Objective
from prism_v2.cross_venue import (
    KALSHI_MAKER_RATE, KALSHI_TAKER_RATE, POLYMARKET_MAKER_RATE,
    POLYMARKET_TAKER_RATE, REPORTED_EDGE_HIGH, REPORTED_EDGE_LOW, Arb, Leg,
    breakeven_edge, required_edge_for_objective, scan, symmetric_arb,
)

OBJ = Objective(1000.0, 10.0, 365.0)


class TestBaremes(unittest.TestCase):

    def test_les_deux_venues_ont_la_meme_forme_en_p_1_moins_p(self):
        poly = Leg("polymarket", 0.5, 0.05)
        kalshi = Leg("kalshi", 0.5, KALSHI_TAKER_RATE)
        self.assertAlmostEqual(poly.fee_per_share(), 0.05 * 0.25)
        self.assertAlmostEqual(kalshi.fee_per_share(), 0.07 * 0.25)

    def test_les_frais_sont_maximaux_a_50_50(self):
        """La propriete decisive : le cout culmine la ou l'arbitrage vit."""
        f = [Leg("k", p, 0.07).fee_per_share()
             for p in (0.10, 0.30, 0.50, 0.70, 0.90)]
        self.assertEqual(max(f), f[2])
        self.assertAlmostEqual(f[1], f[3], places=12)

    def test_les_deux_jambes_paient_le_meme_facteur_ils_s_additionnent(self):
        """A p et 1-p, p(1-p) est identique : aucune compensation."""
        a, b = Leg("poly", 0.42, 0.05), Leg("kalshi", 0.58, 0.07)
        self.assertAlmostEqual(a.fee_per_share() / 0.05,
                               b.fee_per_share() / 0.07, places=12)

    def test_geopolitique_est_le_seul_taux_nul(self):
        zero = [k for k, v in POLYMARKET_TAKER_RATE.items() if v == 0.0]
        self.assertEqual(zero, ["geopolitics"])

    def test_maker_est_moins_cher_que_taker_sur_les_deux_venues(self):
        self.assertLess(POLYMARKET_MAKER_RATE, min(
            v for v in POLYMARKET_TAKER_RATE.values() if v > 0))
        self.assertLess(KALSHI_MAKER_RATE, KALSHI_TAKER_RATE)


class TestDenominateur(unittest.TestCase):

    def test_le_capital_est_la_somme_des_deux_jambes(self):
        arb = Arb(Leg("poly", 0.45, 0.05), Leg("kalshi", 0.52, 0.07), 1.0)
        self.assertAlmostEqual(arb.capital_per_share(), 0.97)
        self.assertAlmostEqual(arb.gross_per_share(), 0.03)

    def test_compter_une_seule_jambe_doublerait_le_rendement(self):
        """Le piege exact que le module doit empecher."""
        arb = Arb(Leg("poly", 0.50, 0.0), Leg("kalshi", 0.47, 0.0), 1.0)
        honnete = arb.net_bps_on_capital()
        flatteur = arb.net_per_share() / arb.leg_a.price * 10_000.0
        self.assertAlmostEqual(honnete, 0.03 / 0.97 * 10_000.0, places=6)
        self.assertGreater(flatteur, honnete * 1.9)


class TestDureeDeDetention(unittest.TestCase):

    def test_la_duree_divise_le_rendement_quotidien(self):
        """Meme arbitrage, deux horizons : c'est tout l'enjeu du critere."""
        court = symmetric_arb(0.50, 0.045, "sports", 1.0)
        long = symmetric_arb(0.50, 0.045, "sports", 180.0)
        self.assertAlmostEqual(court.net_bps_on_capital(),
                               long.net_bps_on_capital(), places=9)
        self.assertAlmostEqual(court.bps_per_day(),
                               long.bps_per_day() * 180.0, places=6)

    def test_lire_la_fenetre_comme_la_detention_gonfle_de_10000x(self):
        """Une fenetre d'entree de 5 s contre une resolution en 1 jour."""
        arb = symmetric_arb(0.50, 0.045, "sports", 1.0)
        vrai = arb.bps_per_day()
        faux = arb.net_bps_on_capital() / (5.0 / 86_400.0)
        self.assertGreater(faux / vrai, 10_000.0)

    def test_duree_nulle_refusee(self):
        with self.assertRaises(ValueError):
            symmetric_arb(0.50, 0.03, "sports", 0.0).bps_per_day()


class TestSeuilDeRentabilite(unittest.TestCase):

    def test_sports_taker_taker_coute_environ_3_cents(self):
        be = breakeven_edge(0.50, "sports")
        self.assertAlmostEqual(be, 0.030, places=3)

    def test_la_moitie_basse_des_ecarts_rapportes_perd_de_l_argent(self):
        """1,5 c est SOUS le seuil de 3,0 c : la fourchette annoncee par la
        litterature commerciale inclut des trades structurellement perdants.
        """
        be = breakeven_edge(0.50, "sports")
        self.assertLess(REPORTED_EDGE_LOW, be)
        self.assertGreater(REPORTED_EDGE_HIGH, be)
        perdant = symmetric_arb(0.50, REPORTED_EDGE_LOW, "sports", 1.0)
        self.assertFalse(perdant.is_profitable())

    def test_etre_maker_sur_kalshi_abaisse_nettement_le_seuil(self):
        self.assertLess(breakeven_edge(0.50, "sports", kalshi_maker=True),
                        breakeven_edge(0.50, "sports"))

    def test_geopolitique_a_le_seuil_le_plus_bas(self):
        seuils = {c: breakeven_edge(0.50, c)
                  for c in ("crypto", "sports", "politics", "geopolitics")}
        self.assertEqual(min(seuils, key=seuils.get), "geopolitics")
        self.assertEqual(max(seuils, key=seuils.get), "crypto")

    def test_sans_frais_le_seuil_tombe_a_zero(self):
        """Controle : le seuil vient bien des baremes, pas du code."""
        arb = symmetric_arb(0.50, 0.001, "geopolitics", 1.0,
                            kalshi_maker=False, poly_maker=True)
        arb_nul = Arb(Leg("poly", arb.leg_a.price, 0.0),
                      Leg("kalshi", arb.leg_b.price, 0.0), 1.0)
        self.assertTrue(arb_nul.is_profitable())


class TestObjectif(unittest.TestCase):

    def test_le_sport_a_1_jour_est_le_seul_scenario_atteignable(self):
        rep = scan(OBJ)
        atteignables = [r["scenario"] for r in rep["scenarios"]
                        if r["atteignable_dans_ecarts_rapportes"]]
        self.assertTrue(all("1 jour" in s for s in atteignables))
        self.assertTrue(atteignables)

    def test_la_politique_longue_est_hors_d_atteinte(self):
        """A 180 jours, aucun ecart admissible n'atteint l'objectif."""
        need = required_edge_for_objective(0.50, "politics", 180.0, OBJ)
        self.assertTrue(math.isnan(need))

    def test_l_ecart_requis_depasse_le_seuil_de_rentabilite(self):
        """Survivre aux frais ne suffit pas : l'objectif exige davantage."""
        be = breakeven_edge(0.50, "sports")
        need = required_edge_for_objective(0.50, "sports", 1.0, OBJ)
        self.assertGreater(need, be)

    def test_l_objectif_n_est_atteint_qu_au_haut_de_la_fourchette(self):
        """3,6 c requis contre 4,5 c annonces au mieux : la marge tient
        entierement dans un chiffre que ce depot n'a pas verifie.
        """
        need = required_edge_for_objective(0.50, "sports", 1.0, OBJ)
        self.assertGreater(need, (REPORTED_EDGE_LOW + REPORTED_EDGE_HIGH) / 2)
        self.assertLess(need, REPORTED_EDGE_HIGH)

    def test_horizon_plus_long_exige_toujours_plus(self):
        court = required_edge_for_objective(0.50, "politics", 30.0, OBJ)
        long = required_edge_for_objective(0.50, "politics", 90.0, OBJ)
        self.assertLess(court, long)


class TestHonnetete(unittest.TestCase):

    def test_les_ecarts_rapportes_sont_signales_comme_non_verifies(self):
        rep = scan(OBJ)
        self.assertIn("non verifies", rep["avertissement_ecarts"])

    def test_aucun_scenario_n_est_declare_rentable_sans_frais_comptes(self):
        for r in scan(OBJ)["scenarios"]:
            best = r["au_meilleur_ecart_rapporte"]
            self.assertGreater(best["frais_c"], 0.0, r["scenario"])
            self.assertLess(best["net_c"], best["ecart_brut_c"], r["scenario"])

    def test_parametres_impossibles_refuses(self):
        with self.assertRaises(ValueError):
            symmetric_arb(1.5, 0.03, "sports", 1.0)
        with self.assertRaises(ValueError):
            symmetric_arb(0.50, 0.03, "inexistant", 1.0)
        with self.assertRaises(ValueError):
            symmetric_arb(0.98, 0.03, "sports", 1.0)


if __name__ == "__main__":
    unittest.main()
