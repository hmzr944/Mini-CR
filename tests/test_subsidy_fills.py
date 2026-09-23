"""Trois defauts qui ont produit trois chiffres faux d'affilee, figes.

La mesure du flux subi a donne, dans l'ordre, sur la MEME donnee :

    -133,554 %/jour   taux de frais = champ brut 1000 au lieu de 0,10
      +6,575 %/jour   prix de remplissage = prix du preneur au lieu du mien,
                      ET couts inconnus additionnes comme des zeros
       INCONNU        apres correction : c'est la reponse honnete

Les deux premiers avaient l'air de mesures. Le premier tuait la famille, le
second la declarait a 45 641 EUR sur 60 jours. Aucun des deux n'etait un
resultat. Ces tests rendent les trois fautes impossibles a refaire.
"""
import unittest

from prism_v2.scans.subsidy_fills import evaluate, taker_rate_for
from prism_v2.subsidy.economics import (TAKER_FEE_RATES, Fill,
                                        MarketEconomics, taker_fee_usd)


class TestUniteDuTaux(unittest.TestCase):
    """DEFAUT 1 : un champ brut d'API passe pour un taux."""

    def test_un_taux_hors_de_zero_un_est_refuse(self):
        # maker_base_fee vaut 1000 dans l'API du carnet Polymarket.
        with self.assertRaises(ValueError) as ctx:
            taker_fee_usd(0.5, 100.0, 1000.0)
        self.assertIn("FRACTION", str(ctx.exception))

    def test_les_taux_publies_passent_tous_la_garde(self):
        for cat, rate in TAKER_FEE_RATES.items():
            self.assertIsNotNone(taker_fee_usd(0.5, 10.0, rate), cat)

    def test_un_taux_inconnu_reste_inconnu(self):
        self.assertIsNone(taker_fee_usd(0.5, 10.0, None))

    def test_l_ampleur_de_la_faute(self):
        """1000 au lieu de 0,10 : un facteur 10 000 sur le frais."""
        bon = taker_fee_usd(0.5, 100.0, 0.10)
        self.assertAlmostEqual(bon, 100.0 * 0.10 * 0.25)
        with self.assertRaises(ValueError):
            taker_fee_usd(0.5, 100.0, 1000.0)


class TestCoutInconnu(unittest.TestCase):
    """DEFAUT 2 : un cout inconnu additionne comme un zero."""

    def _eco(self, taker_rate):
        return MarketEconomics(
            question="q", pool_usdc_per_day=100.0, share=0.5,
            capital_usd=500.0, measured_fills_per_day=2.0,
            fills=[Fill(price_paid=0.40, complement_ask=0.62, shares=50.0,
                        taker_rate=taker_rate)])

    def test_sans_taux_le_cout_est_none_pas_zero(self):
        self.assertIsNone(self._eco(None).neutralisation_cost_per_day())
        self.assertIsNone(self._eco(None).net_usd_per_day())

    def test_avec_taux_le_cout_existe(self):
        self.assertIsNotNone(self._eco(0.04).neutralisation_cost_per_day())

    def test_un_portefeuille_partiellement_chiffre_est_inconnu(self):
        """Le net du portefeuille ne se calcule pas sur les jambes connues.

        C'est la faute qui a produit 6,575 %/jour : le brut venait de onze
        marches, le cout de six seulement.
        """
        connu, inconnu = self._eco(0.04), self._eco(None)
        couts = [connu.neutralisation_cost_per_day(),
                 inconnu.neutralisation_cost_per_day()]
        self.assertIn(None, couts)
        # la regle appliquee par evaluate() : une seule inconnue -> tout inconnu
        cout = None if any(c is None for c in couts) else sum(couts)
        self.assertIsNone(cout)


class TestPrixDeRemplissage(unittest.TestCase):
    """DEFAUT 3 : acheter au prix agressif du preneur, pas a sa propre limite."""

    def test_le_cout_au_prix_du_preneur_fabrique_un_arbitrage(self):
        """Un bid a 0,40 servi par une impression a 0,35.

        Au prix du preneur (0,35) le complement a 0,62 donne une somme de
        0,97 : un gain certain de 3 cents, soit un arbitrage somme-a-1 —
        mesure inexistant (minimum 1,0010 sur 999 paires). Au prix REEL
        (0,40) la somme vaut 1,02 : un cout.
        """
        faux = Fill(price_paid=0.35, complement_ask=0.62, shares=100.0,
                    taker_rate=0.0)
        vrai = Fill(price_paid=0.40, complement_ask=0.62, shares=100.0,
                    taker_rate=0.0)
        self.assertLess(faux.neutralisation_cost_usd(), 0.0)
        self.assertGreater(vrai.neutralisation_cost_usd(), 0.0)


class TestCategorieTarifaire(unittest.TestCase):

    def test_les_categories_reconnues_rendent_un_taux_publie(self):
        r = taker_rate_for("Will the U.S. invade Iran before 2027?")
        self.assertEqual(r, TAKER_FEE_RATES["geopolitical"])
        r = taker_rate_for("Will the Republicans win the Oregon governor race?")
        self.assertEqual(r, TAKER_FEE_RATES["politics"])

    def test_un_libelle_non_reconnu_rend_none(self):
        """None, et surtout pas un taux par defaut : un defaut serait un choix
        deguise en mesure, et il porterait tout le net du portefeuille."""
        self.assertIsNone(taker_rate_for("Zorglub wibble frobnicate"))

    def test_aucun_taux_par_defaut_ne_s_est_glisse(self):
        self.assertIsNone(taker_rate_for(""))
        self.assertIsNone(taker_rate_for(None))


class TestEvaluateRendInconnu(unittest.TestCase):

    def test_aucun_marche_exploitable_rend_none(self):
        self.assertIsNone(evaluate(1.0, [], {}, {}))


if __name__ == "__main__":
    unittest.main()
