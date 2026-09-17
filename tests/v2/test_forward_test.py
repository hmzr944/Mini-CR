"""Test forward du flux force — la comptabilite du resultat.

C'est le module qui produira le verdict. Ses erreurs seraient donc les plus
couteuses du depot : un cout compte une seule fois au lieu de deux double
l'edge apparent, un t-stat publie sur trois observations transforme du bruit
en decouverte.
"""
from __future__ import annotations

import unittest

from prism_v2.forward_test import (
    DEFAULT_HALF_SPREAD_BPS, ClosedTrade, PendingTrade, compute_outcome,
    economics, summarise,
)
from prism_v2.liquidation_flow import HOLD_MINUTES, ROUND_TRIP_FEE_BPS


def pend(direction=1, entry=100.0, inst="X"):
    return PendingTrade(inst, 0, direction, 0.8 * direction, 10.0, 1e6,
                        entry, 0, HOLD_MINUTES * 60_000)


class TestResultat(unittest.TestCase):

    def test_le_fade_gagne_quand_le_prix_revient(self):
        t = compute_outcome(pend(1), 101.0, 0.0)
        self.assertAlmostEqual(t.gross_bps, 100.0, places=6)

    def test_le_short_gagne_quand_le_prix_descend(self):
        t = compute_outcome(pend(-1), 99.0, 0.0)
        self.assertAlmostEqual(t.gross_bps, 100.0, places=6)

    def test_le_cout_est_facture_a_l_ALLER_ET_AU_RETOUR(self):
        """N'en facturer qu'un doublerait l'edge apparent."""
        t = compute_outcome(pend(1), 101.0, 3.0)
        self.assertAlmostEqual(t.cost_bps, ROUND_TRIP_FEE_BPS + 2 * 3.0)
        self.assertAlmostEqual(t.net_bps, t.gross_bps - t.cost_bps)

    def test_le_demi_spread_par_defaut_n_est_pas_nul(self):
        self.assertGreater(DEFAULT_HALF_SPREAD_BPS, 0.0)
        t = compute_outcome(pend(1), 101.0)
        self.assertGreater(t.cost_bps, ROUND_TRIP_FEE_BPS)

    def test_un_mouvement_inferieur_au_cout_ressort_negatif(self):
        """Le coeur de l'economie : bouger ne suffit pas, il faut bouger
        plus que le peage."""
        t = compute_outcome(pend(1), 100.05, 0.5)      # +5 bps brut
        self.assertGreater(t.gross_bps, 0.0)
        self.assertLess(t.net_bps, 0.0)

    def test_prix_absurdes_refuses(self):
        with self.assertRaises(ValueError):
            compute_outcome(pend(1, entry=0.0), 100.0)
        with self.assertRaises(ValueError):
            compute_outcome(pend(1), 0.0)
        with self.assertRaises(ValueError):
            compute_outcome(pend(1), 101.0, -1.0)


class TestStatistiques(unittest.TestCase):

    def trades(self, nets, inst="X"):
        return [ClosedTrade(inst, i, 1, 0.8, 10.0, 100.0, 101.0,
                            n + 11.0, 11.0, n) for i, n in enumerate(nets)]

    def test_echantillon_vide_ne_produit_aucun_chiffre(self):
        self.assertEqual(summarise([]), {"n": 0})

    def test_une_seule_observation_ne_produit_pas_de_t(self):
        """Un t-stat sur une observation serait du bruit presente comme
        une decouverte."""
        s = summarise(self.trades([10.0]))
        self.assertEqual(s["n"], 1)
        self.assertIsNone(s["t"])

    def test_une_serie_constante_ne_produit_pas_de_t(self):
        self.assertIsNone(summarise(self.trades([5.0] * 30))["t"])

    def test_le_seuil_de_significativite_est_explicite(self):
        faible = summarise(self.trades([1.0, -1.0] * 25 + [2.0]))
        self.assertFalse(faible["significatif_t2"])
        fort = summarise(self.trades([10.0, 12.0, 11.0, 9.0] * 25))
        self.assertTrue(fort["significatif_t2"])
        self.assertGreaterEqual(fort["t"], 2.0)

    def test_la_p_value_est_unilaterale(self):
        s = summarise(self.trades([-10.0, -12.0, -11.0] * 20))
        self.assertGreater(s["p_unilaterale"], 0.9)

    def test_le_brut_et_le_net_different_du_cout(self):
        s = summarise(self.trades([5.0] * 10 + [7.0] * 10))
        self.assertAlmostEqual(s["gross_bps_moyen"] - s["net_bps_moyen"],
                               s["cout_bps"], places=6)


class TestEconomie(unittest.TestCase):

    def stats(self, net, n=100):
        return {"n": n, "net_bps_moyen": net}

    def test_le_capital_est_celui_des_positions_SIMULTANEES(self):
        """Compter le capital de tous les trades du jour effacerait la
        difference entre une famille a rotation rapide et une qui dort."""
        e = economics(self.stats(10.0), size_usd=500.0, hold_minutes=30,
                      triggers_per_day=288.0)
        self.assertAlmostEqual(e["positions_simultanees"], 6.0, places=2)
        self.assertAlmostEqual(e["capital_immobilise_usd"], 3000.0, places=0)

    def test_une_rotation_plus_rapide_ameliore_le_ratio(self):
        lent = economics(self.stats(10.0), 500.0, 240, 288.0)
        rapide = economics(self.stats(10.0), 500.0, 30, 288.0)
        self.assertGreater(rapide["bps_par_jour"], lent["bps_par_jour"])

    def test_un_net_negatif_donne_un_ratio_negatif(self):
        e = economics(self.stats(-5.0), 500.0, 30, 288.0)
        self.assertLess(e["bps_par_jour"], 0.0)
        self.assertLess(e["ratio_objectif"], 0.0)

    def test_le_capital_ne_descend_jamais_sous_une_position(self):
        e = economics(self.stats(10.0), 500.0, 30, 1.0)
        self.assertGreaterEqual(e["capital_immobilise_usd"], 500.0)

    def test_pas_d_economie_sans_statistique(self):
        self.assertEqual(economics({"n": 0}, 500.0, 30, 288.0), {})

    def test_le_ratio_se_refere_bien_au_seuil_de_l_objectif(self):
        e = economics(self.stats(10.0), 500.0, 30, 288.0)
        self.assertAlmostEqual(e["ratio_objectif"],
                               e["bps_par_jour"] / 63.28, places=3)


if __name__ == "__main__":
    unittest.main()
