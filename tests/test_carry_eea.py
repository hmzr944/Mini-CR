"""Le carry EEA est-il annualise avec le BON multiplicateur ?

Ce module de test existe pour un piege precis. `legal_carry.py` annualise en
multipliant par 3 x 365 : il suppose un funding toutes les 8 h, convention
d'OKX. Backpack regle TOUTES LES HEURES. Appliquer le multiplicateur d'OKX aux
donnees de Backpack sous-estimerait d'un FACTEUR 8 — et ce depot a deja paye
une faute d'unite exactement de cette forme (maker_base_fee lu comme une
fraction, facteur 10 000, qui avait produit « la famille est tuee par son
propre cout »).

L'intervalle doit donc etre MESURE, et les tests le figent.
"""
import unittest

from prism_v2.scans.carry_eea import (EXTREME_APR_CAP, MIN_FUNDING_SAMPLES,
                                      CarryRow, annualised_apr_pct,
                                      measured_interval_hours,
                                      round_trip_cost_bps, stability)


def row(**kw):
    base = dict(perp="X_USDC_PERP", spot="X_USDC", vol_usd=1e9,
                interval_hours=1.0, n_samples=1000, raw_mean_rate=1e-5,
                funding_apr=10.0, stability=2.0, spot_spread_bps=1.0,
                perp_spread_bps=1.0, cost_bps=21.0)
    base.update(kw)
    return CarryRow(**base)


class TestIntervalleMesure(unittest.TestCase):

    def test_un_intervalle_horaire_est_reconnu(self):
        s = ["2026-09-22T08:00:00", "2026-09-22T07:00:00", "2026-09-22T06:00:00"]
        self.assertAlmostEqual(measured_interval_hours(s), 1.0)

    def test_un_intervalle_de_huit_heures_est_reconnu(self):
        s = ["2026-09-22T00:00:00", "2026-09-22T08:00:00", "2026-09-22T16:00:00"]
        self.assertAlmostEqual(measured_interval_hours(s), 8.0)

    def test_trop_peu_d_horodatages_rend_INCONNU(self):
        self.assertIsNone(measured_interval_hours(["2026-09-22T08:00:00"]))

    def test_des_horodatages_identiques_rendent_INCONNU(self):
        """Zero ecart : rendre 0 ferait exploser l'annualisation."""
        s = ["2026-09-22T08:00:00"] * 4
        self.assertIsNone(measured_interval_hours(s))


class TestAnnualisation(unittest.TestCase):

    def test_le_multiplicateur_suit_l_intervalle_MESURE(self):
        taux = 1e-5
        self.assertAlmostEqual(annualised_apr_pct(taux, 1.0), taux * 24 * 365 * 100)
        self.assertAlmostEqual(annualised_apr_pct(taux, 8.0), taux * 3 * 365 * 100)

    def test_le_facteur_huit_separe_les_deux_conventions(self):
        """La faute que ce test existe pour interdire : appliquer la
        convention 8 h d'OKX a une venue qui regle toutes les heures."""
        taux = 1e-5
        horaire = annualised_apr_pct(taux, 1.0)
        huit_h = annualised_apr_pct(taux, 8.0)
        self.assertAlmostEqual(horaire / huit_h, 8.0)

    def test_un_intervalle_nul_leve_au_lieu_de_calculer(self):
        with self.assertRaises(ValueError):
            annualised_apr_pct(1e-5, 0.0)

    def test_un_funding_negatif_reste_negatif(self):
        """Short le perp : un funding negatif est un COUT, pas un revenu."""
        self.assertLess(annualised_apr_pct(-1e-5, 1.0), 0.0)


class TestCoutDesQuatreTraversees(unittest.TestCase):

    def test_un_carry_paie_QUATRE_jambes_pas_deux(self):
        """Entrer le spot, entrer le perp, sortir le spot, sortir le perp."""
        c = round_trip_cost_bps(spot_spread_bps=0.0, perp_spread_bps=0.0,
                                taker_bps=5.0)
        self.assertAlmostEqual(c, 20.0)

    def test_chaque_jambe_paie_son_DEMI_spread(self):
        c = round_trip_cost_bps(spot_spread_bps=2.0, perp_spread_bps=4.0,
                                taker_bps=0.0)
        self.assertAlmostEqual(c, 2 * (1.0 + 2.0))

    def test_un_spread_negatif_leve_au_lieu_de_reduire_le_cout(self):
        with self.assertRaises(ValueError):
            round_trip_cost_bps(-1.0, 1.0)


class TestStabilite(unittest.TestCase):

    def test_un_funding_constant_est_degenere_et_rend_zero(self):
        """Ecart-type nul : rendre l'infini ferait passer le filtre a coup sur."""
        self.assertEqual(stability([1e-5] * 50), 0.0)

    def test_un_signe_fiable_donne_une_stabilite_elevee(self):
        self.assertGreater(stability([1e-5, 1.1e-5, 0.9e-5, 1e-5]), 1.0)


class TestStatut(unittest.TestCase):

    def test_un_funding_extreme_est_REJETE_meme_s_il_rapporte(self):
        """La regle qui separe un rendement d'un marche casse."""
        r = row(funding_apr=EXTREME_APR_CAP + 1.0)
        self.assertGreater(r.net_pct(), 0.0)
        self.assertEqual(r.status(), "REJETE (stress)")

    def test_un_terme_inconnu_rend_NON_RESOLU_jamais_un_net(self):
        self.assertEqual(row(cost_bps=None).status(), "NON RESOLU")
        self.assertIsNone(row(cost_bps=None).net_pct())
        self.assertEqual(row(interval_hours=None).status(), "NON RESOLU")

    def test_un_echantillon_trop_court_rend_NON_RESOLU(self):
        self.assertEqual(row(n_samples=MIN_FUNDING_SAMPLES - 1).status(),
                         "NON RESOLU")

    def test_un_signe_instable_est_rejete(self):
        self.assertEqual(row(stability=0.5).status(), "REJETE (signe instable)")

    def test_un_net_negatif_est_rejete(self):
        self.assertEqual(row(funding_apr=0.5).status(), "REJETE (net <= 0)")

    def test_un_candidat_passe_toutes_les_portes(self):
        self.assertEqual(row().status(), "CANDIDAT")

    def test_le_scenario_defavorable_ne_garde_que_le_COUT(self):
        """Funding retombe a zero des l'entree : on perd les frais, pas plus."""
        r = row()
        self.assertAlmostEqual(r.net_pct_adverse(), -r.cost_bps / 100.0)
        self.assertLess(r.net_pct_adverse(), 0.0)
        self.assertLess(r.net_pct_adverse(), r.net_pct())


if __name__ == "__main__":
    unittest.main(verbosity=2)
