"""Le critere de capital — teste surtout contre ses propres facons de mentir.

Ce module convertit des mesures en une unite commune. Une conversion peut
flatter une strategie de deux facons : en choisissant le denominateur qui
l'arrange, et en supposant un recyclage du capital qui n'existe pas. Les
tests visent ces deux points avant de verifier l'arithmetique.
"""
from __future__ import annotations

import unittest

from prism_v2.capital_efficiency import (
    DAYS_PER_YEAR, HYPOTHESIS, LITERATURE, OBSERVED, Family, Objective,
    build_report, established_families, latency_arb_counterfactual,
    polymarket_taker_fee_bps,
)


class TestObjective(unittest.TestCase):

    def test_seuil_est_compose_donc_plus_indulgent_que_le_simple(self):
        obj = Objective(1000.0, 10.0, 365.0)
        compose = obj.required_bps_per_day()
        simple = (10.0 - 1.0) * 10_000.0 / 365.0
        self.assertLess(compose, simple)
        self.assertAlmostEqual(compose, 63.28, places=1)

    def test_le_seuil_reconstruit_bien_le_multiple(self):
        """Un capital croissant au seuil doit atteindre le multiple, pile."""
        obj = Objective(1000.0, 10.0, 365.0)
        r = obj.required_bps_per_day() / 10_000.0
        self.assertAlmostEqual((1.0 + r) ** 365.0, 10.0, places=6)

    def test_horizon_plus_court_exige_davantage(self):
        court = Objective(1000.0, 10.0, 90.0).required_bps_per_day()
        long = Objective(1000.0, 10.0, 365.0).required_bps_per_day()
        self.assertGreater(court, long)

    def test_le_capital_ne_change_pas_le_seuil(self):
        """Le seuil est un taux : 100 EUR et 1 M EUR exigent le meme taux.

        C'est precisement pour cela que « peu de capital » ne rend pas
        l'objectif plus facile — seul l'horizon et le multiple comptent.
        """
        petit = Objective(100.0, 10.0, 365.0).required_bps_per_day()
        gros = Objective(1_000_000.0, 10.0, 365.0).required_bps_per_day()
        self.assertEqual(petit, gros)

    def test_parametres_absurdes_refuses(self):
        with self.assertRaises(ValueError):
            Objective(1000.0, 10.0, 0.0).required_bps_per_day()
        with self.assertRaises(ValueError):
            Objective(1000.0, -1.0, 365.0).required_bps_per_day()


class TestFraisTaker(unittest.TestCase):

    def test_denominateur_est_le_capital_engage_pas_le_nominal(self):
        """rate x (1-p), pas rate x p x (1-p).

        Rapporter le frais au nominal de 1 $ diviserait le chiffre par deux
        a 50 c et ferait passer l'arbitrage pour deux fois moins couteux
        qu'il ne l'est.
        """
        bps = polymarket_taker_fee_bps(0.50, 0.07)
        self.assertAlmostEqual(bps, 0.07 * 0.50 * 10_000.0)
        self.assertAlmostEqual(bps, 350.0)
        nominal = 0.07 * 0.50 * 0.50 * 10_000.0
        self.assertAlmostEqual(bps, 2.0 * nominal)

    def test_accord_avec_la_formule_officielle(self):
        """fee = C x rate x p x (1-p), rapporte a C x p."""
        for price in (0.10, 0.25, 0.50, 0.75, 0.93):
            shares, rate = 1000.0, 0.07
            fee = shares * rate * price * (1.0 - price)
            capital = shares * price
            self.assertAlmostEqual(
                polymarket_taker_fee_bps(price, rate),
                fee / capital * 10_000.0, places=9)

    def test_cout_en_capital_decroit_avec_le_prix(self):
        """En dollars le frais est symetrique autour de 50 c ; en % du
        capital il ne l'est pas, parce que le capital engage grandit avec p.
        """
        couts = [polymarket_taker_fee_bps(p, 0.07)
                 for p in (0.10, 0.30, 0.50, 0.70, 0.90)]
        self.assertEqual(couts, sorted(couts, reverse=True))

    def test_le_frais_en_dollars_est_bien_maximal_a_50(self):
        """C'est la propriete qui fait du bareme une contre-mesure ciblee."""
        def dollars(p):
            return 0.07 * p * (1.0 - p)
        self.assertGreater(dollars(0.50), dollars(0.30))
        self.assertGreater(dollars(0.50), dollars(0.70))
        self.assertAlmostEqual(dollars(0.30), dollars(0.70), places=9)

    def test_taux_zero_donne_cout_zero(self):
        self.assertEqual(polymarket_taker_fee_bps(0.5, 0.0), 0.0)

    def test_prix_hors_bornes_refuses(self):
        for p in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                polymarket_taker_fee_bps(p, 0.07)
        with self.assertRaises(ValueError):
            polymarket_taker_fee_bps(0.5, -0.01)


class TestFamily(unittest.TestCase):

    def test_le_recyclage_est_le_levier_dominant(self):
        """Une famille faible qui recycle bat une famille forte qui dort.

        C'est toute la raison d'etre du critere : sans cela le carry a
        191 bps/an paraitrait meilleur qu'un edge de 0,5 bps.
        """
        lente = Family("lente", 200.0, 1.0, OBSERVED, "test")
        rapide = Family("rapide", 0.5, 500.0, OBSERVED, "test")
        self.assertGreater(rapide.bps_per_day(), lente.bps_per_day())

    def test_carry_1x_vaut_bien_191_bps_par_an(self):
        carry = [f for f in established_families()
                 if f.name.startswith("carry inverse")][0]
        self.assertAlmostEqual(carry.bps_per_year(), 191.0, places=6)
        self.assertAlmostEqual(carry.bps_per_day(), 191.0 / DAYS_PER_YEAR,
                               places=9)

    def test_une_perte_reste_une_perte_apres_conversion(self):
        for fam in established_families():
            if fam.net_bps_per_cycle < 0:
                self.assertLess(fam.bps_per_day(), 0.0, fam.name)


class TestHonnetete(unittest.TestCase):
    """Le module doit rester lisible comme un constat, pas comme un argument."""

    def test_le_netting_20x_reste_marque_hypothese(self):
        """C'est le seul chiffre flatteur de la table. S'il passait un jour
        pour OBSERVE sans qu'OKX l'ait confirme, la table mentirait.
        """
        netting = [f for f in established_families()
                   if "netting de marge parfait" in f.name][0]
        self.assertEqual(netting.evidence, HYPOTHESIS)

    def test_chaque_famille_porte_une_source_et_un_niveau_de_preuve(self):
        valides = {OBSERVED, LITERATURE, HYPOTHESIS}
        for fam in established_families() + [latency_arb_counterfactual()]:
            self.assertIn(fam.evidence, valides, fam.name)
            self.assertTrue(fam.source.strip(), fam.name)

    def test_seul_l_arbitrage_de_latence_recycle_vraiment(self):
        """Toutes les autres familles sont a 1 recyclage par jour. Si l'une
        d'elles gagnait un multiplicateur non justifie, son ratio exploserait
        sans qu'aucune mesure n'ait change.
        """
        for fam in established_families():
            self.assertEqual(fam.recycles_per_day, 1.0, fam.name)
        self.assertGreater(latency_arb_counterfactual().recycles_per_day, 1.0)

    def test_l_arbitrage_de_latence_est_compte_en_cout_pas_en_gain(self):
        """Sa ligne represente le bareme taker subi, jamais un rendement."""
        self.assertLess(latency_arb_counterfactual().net_bps_per_cycle, 0.0)


class TestRapport(unittest.TestCase):

    def test_aucune_famille_etablie_n_atteint_le_seuil(self):
        rep = build_report(Objective(1000.0, 10.0, 365.0))
        self.assertTrue(rep["aucune_famille_atteint_le_seuil"])
        for r in rep["familles"]:
            self.assertFalse(r["atteint_le_seuil"], r["famille"])

    def test_le_meilleur_candidat_reste_un_ordre_de_grandeur_en_dessous(self):
        rep = build_report(Objective(1000.0, 10.0, 365.0))
        self.assertLess(rep["meilleur_ratio"], 0.2)

    def test_meme_le_carry_hypothetique_echoue(self):
        """Le scenario le plus genereux du depot, contre le seuil le plus
        indulgent : il echoue quand meme. C'est le coeur du verdict.
        """
        rep = build_report(Objective(1000.0, 10.0, 365.0))
        netting = [r for r in rep["familles"]
                   if "netting de marge parfait" in r["famille"]][0]
        self.assertFalse(netting["atteint_le_seuil"])
        self.assertLess(netting["ratio_objectif"], 1.0)

    def test_le_rapport_est_trie_du_meilleur_au_pire(self):
        rep = build_report(Objective(1000.0, 10.0, 365.0))
        vals = [r["bps_par_jour"] for r in rep["familles"]]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_un_objectif_modeste_devient_atteignable(self):
        """Controle de non-degenerescence : le rapport n'est pas cable sur
        « rien ne marche ». Vise x1,015 en un an et le carry passe.

        La marge est mince, et c'est le constat : viser x1,02 sur un an
        demande 0,543 bps/jour, deja au-dessus du carry mesure a 0,523.
        """
        rep = build_report(Objective(1000.0, 1.015, 365.0))
        self.assertFalse(rep["aucune_famille_atteint_le_seuil"])
        carry = [r for r in rep["familles"]
                 if r["famille"].startswith("carry inverse")][0]
        self.assertTrue(carry["atteint_le_seuil"])


if __name__ == "__main__":
    unittest.main()
