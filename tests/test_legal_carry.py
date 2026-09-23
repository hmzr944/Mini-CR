"""Le carry soutenable, et le filtre qui separe un rendement d'un marche casse.

Le piege que ces tests figent : cueillir le funding le plus fort revient a
cueillir le marche le plus dislocke. ONE-USDT affichait -304 %/an de funding
moyen et un « net » de 49,7 % sur 60 jours — un chiffre spectaculaire et
inexploitable (position neutre non tenable). Le filtre `is_harvestable` doit
l'exclure, et le retenir serait la faute.
"""
import unittest

from prism_v2.scans.legal_carry import (CarryRow, EXTREME_APR_CAP,
                                        MIN_STABILITY, MIN_VOL_USD,
                                        net_carry_pct, stability,
                                        sustainable_apr)


def _row(apr, stab=2.0, vol=1e9, setup=20.0):
    return CarryRow(inst_id="X-USDT-SWAP", vol_usd=vol, funding_apr=apr,
                    stability=stab, setup_bps=setup,
                    net_pct_60d=net_carry_pct(apr, setup, 60.0))


class TestNet(unittest.TestCase):

    def test_le_cout_s_amortit_sur_la_duree(self):
        """Tenir plus longtemps augmente le net : le cout est paye une fois."""
        n7 = net_carry_pct(10.0, 20.0, 7.0)
        n60 = net_carry_pct(10.0, 20.0, 60.0)
        self.assertLess(n7, n60)

    def test_on_capte_du_bon_cote_le_signe_ne_penalise_pas(self):
        """|funding| : un funding negatif se capte en inversant la position."""
        self.assertEqual(net_carry_pct(10.0, 20.0, 60.0),
                         net_carry_pct(-10.0, 20.0, 60.0))

    def test_un_carry_trop_faible_pour_son_cout_est_negatif(self):
        self.assertLess(net_carry_pct(1.0, 40.0, 60.0), 0.0)


class TestSoutenable(unittest.TestCase):

    def test_moyenne_annualisee(self):
        # 0,0001 par 8h -> *3*365*100 = 10,95 %/an
        apr = sustainable_apr([0.0001] * 40)
        self.assertAlmostEqual(apr, 0.0001 * 3 * 365 * 100)

    def test_historique_trop_court_rend_none(self):
        self.assertIsNone(sustainable_apr([0.0001] * 10))

    def test_stabilite_recompense_un_signe_fiable(self):
        # faible variance autour d'une moyenne franche vs signe qui alterne.
        stable = stability([0.0010, 0.0011, 0.0009, 0.0010] * 10)
        bruyant = stability([0.001 if i % 2 else -0.001 for i in range(40)])
        self.assertGreater(stable, bruyant)

    def test_variance_nulle_rend_zero_pas_l_infini(self):
        """Un funding parfaitement constant : on ne divise pas par zero."""
        self.assertEqual(stability([0.001] * 40), 0.0)


class TestFiltreExtreme(unittest.TestCase):
    """LE REGRESSEUR : un funding extreme n'est jamais un carry."""

    def test_un_carry_liquide_stable_normal_est_retenu(self):
        self.assertTrue(_row(7.0).is_harvestable())      # ~BTC

    def test_un_funding_extreme_est_exclu_malgre_un_net_enorme(self):
        """ONE-USDT : -304 %/an, net 60j ~50 %, stable — et pourtant EXCLU."""
        one = _row(-304.0, stab=1.08)
        self.assertGreater(one.net_pct_60d, 40.0)        # le net « brille »
        self.assertFalse(one.is_harvestable())           # mais c'est un piege

    def test_le_seuil_d_exclusion_est_bien_a_la_frontiere(self):
        self.assertTrue(_row(EXTREME_APR_CAP - 0.1, stab=2.0,
                             vol=1e9).is_harvestable())
        self.assertFalse(_row(EXTREME_APR_CAP + 0.1).is_harvestable())

    def test_un_funding_instable_est_exclu(self):
        self.assertFalse(_row(7.0, stab=MIN_STABILITY - 0.1).is_harvestable())

    def test_un_marche_peu_liquide_est_exclu(self):
        self.assertFalse(_row(7.0, vol=MIN_VOL_USD - 1).is_harvestable())

    def test_un_net_negatif_est_exclu(self):
        self.assertFalse(_row(0.5).is_harvestable())     # carry < cout


if __name__ == "__main__":
    unittest.main()
