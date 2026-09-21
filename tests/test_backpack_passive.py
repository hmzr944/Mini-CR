"""Le fill passif sur perpetuel est-il mesure avec le bon SIGNE ?

Ce module de test existe pour une raison precise : dans l'economie du fill
passif, une inversion de sens echange le demi-spread encaisse et le markout,
et retourne donc la conclusion sans rien casser visiblement. Le depot a deja
paye ce genre de faute (prix d'impression pris pour prix de fill, cout de
neutralisation NEGATIF, « arbitrage » dont la meme mesure avait etabli
l'inexistence). Les conventions sont donc figees ici, une par une.
"""
import unittest

from prism_v2.backpack.passive import (MARKOUT_HORIZONS_S, PassiveFill,
                                       breakeven_maker_fee_bps, estimate_mid,
                                       half_spread_earned_bps, markout_bps,
                                       simulate_passive_fills)
from prism_v2.subsidy.tape import Trade


def bid_hit(ts, px, size=1.0):
    """Preneur VENDEUR : il frappe un bid. Le maker de ce bid a ACHETE."""
    return Trade(ts=ts, price=px, size=size, taker_is_buy=False)


def ask_lift(ts, px, size=1.0):
    """Preneur ACHETEUR : il leve un ask. Le maker de cet ask a VENDU."""
    return Trade(ts=ts, price=px, size=size, taker_is_buy=True)


class TestEstimateurDeMid(unittest.TestCase):

    def test_le_mid_est_la_demi_somme_des_deux_cotes(self):
        m = estimate_mid([bid_hit(1.0, 99.0), ask_lift(2.0, 101.0)], 3.0)
        self.assertIsNotNone(m)
        self.assertEqual(m.mid, 100.0)
        self.assertAlmostEqual(m.spread_bps, 200.0)

    def test_un_seul_cote_ne_donne_aucun_mid(self):
        """Un cote manquant rend INCONNU, jamais le prix de l'autre cote."""
        self.assertIsNone(estimate_mid([bid_hit(1.0, 99.0)], 3.0))
        self.assertIsNone(estimate_mid([ask_lift(1.0, 101.0)], 3.0))

    def test_aucune_donnee_future_n_entre_dans_l_estimation(self):
        tape = [bid_hit(10.0, 99.0), ask_lift(20.0, 101.0)]
        self.assertIsNone(estimate_mid(tape, 15.0),
                          "l'ask de t=20 ne doit pas servir a estimer t=15")

    def test_un_cote_perime_invalide_l_estimation(self):
        tape = [bid_hit(0.0, 99.0), ask_lift(100.0, 101.0)]
        self.assertIsNotNone(estimate_mid(tape, 100.0, max_staleness_s=200.0))
        self.assertIsNone(estimate_mid(tape, 100.0, max_staleness_s=50.0))

    def test_deux_observations_croisees_sont_refusees(self):
        """bid > ask : le prix a bouge entre les deux, la demi-somme ne decrit
        aucun carnet. Rendre un nombre ici fabriquerait un spread negatif."""
        tape = [ask_lift(1.0, 99.0), bid_hit(2.0, 101.0)]
        self.assertIsNone(estimate_mid(tape, 3.0))

    def test_le_plus_recent_de_chaque_cote_gagne(self):
        tape = [bid_hit(1.0, 90.0), bid_hit(5.0, 99.0), ask_lift(6.0, 101.0)]
        self.assertEqual(estimate_mid(tape, 7.0).bid_px, 99.0)


class TestSigneDuDemiSpread(unittest.TestCase):

    def test_un_achat_passif_se_fait_SOUS_le_mid_et_encaisse(self):
        self.assertAlmostEqual(
            half_spread_earned_bps(99.0, passive_is_buy=True, mid_before=100.0),
            1e4 * 1.0 / 99.0)

    def test_une_vente_passive_se_fait_AU_DESSUS_du_mid_et_encaisse(self):
        self.assertAlmostEqual(
            half_spread_earned_bps(101.0, passive_is_buy=False, mid_before=100.0),
            1e4 * 1.0 / 101.0)

    def test_les_deux_cotes_encaissent_positivement(self):
        """Une inversion de sens rendrait l'un des deux negatif : c'est le
        mode d'echec que ce test existe pour attraper."""
        self.assertGreater(half_spread_earned_bps(99.0, True, 100.0), 0)
        self.assertGreater(half_spread_earned_bps(101.0, False, 100.0), 0)

    def test_un_prix_non_positif_leve_au_lieu_de_calculer(self):
        with self.assertRaises(ValueError):
            half_spread_earned_bps(0.0, True, 100.0)


class TestSigneDuMarkout(unittest.TestCase):

    def test_un_achat_suivi_d_une_baisse_est_adverse(self):
        self.assertLess(markout_bps(99.0, True, mid_before=100.0, mid_after=95.0), 0)

    def test_un_achat_suivi_d_une_hausse_est_favorable(self):
        self.assertGreater(markout_bps(99.0, True, mid_before=100.0, mid_after=105.0), 0)

    def test_une_vente_suivie_d_une_hausse_est_adverse(self):
        self.assertLess(markout_bps(101.0, False, mid_before=100.0, mid_after=105.0), 0)

    def test_une_vente_suivie_d_une_baisse_est_favorable(self):
        self.assertGreater(markout_bps(101.0, False, mid_before=100.0, mid_after=95.0), 0)

    def test_un_mid_immobile_ne_produit_AUCUN_markout(self):
        """La faute que ce test fige : mesurer la derive depuis le PRIX DE
        FILL au lieu du mid rendrait ici +101 bps sur un marche ou rien n'a
        bouge — un demi-spread fantome, compte une seconde fois."""
        self.assertAlmostEqual(
            markout_bps(99.0, True, mid_before=100.0, mid_after=100.0), 0.0)

    def test_demi_spread_plus_markout_egale_le_PnL_du_fill(self):
        """L'identite qui garantit que les deux termes sont disjoints."""
        fill, mid_avant, mid_apres = 99.0, 100.0, 103.0
        hs = half_spread_earned_bps(fill, True, mid_avant)
        mo = markout_bps(fill, True, mid_avant, mid_apres)
        self.assertAlmostEqual(hs + mo, 1e4 * (mid_apres - fill) / fill)


class TestConditionPlutotQueResultat(unittest.TestCase):

    def test_le_frais_d_equilibre_est_la_somme_des_deux_termes(self):
        self.assertAlmostEqual(breakeven_maker_fee_bps(5.0, -3.0), 2.0)

    def test_un_markout_qui_mange_le_demi_spread_exige_un_REBATE(self):
        """Valeur negative = perdant meme a frais nul. C'est une condition sur
        la venue, pas un net : le rebate du programme n'est pas public."""
        self.assertAlmostEqual(breakeven_maker_fee_bps(2.0, -7.0), -5.0)


class TestSimulation(unittest.TestCase):

    def _tape(self):
        return [bid_hit(0.0, 99.0), ask_lift(1.0, 101.0),
                bid_hit(10.0, 99.0), ask_lift(11.0, 101.0),
                bid_hit(20.0, 99.0), ask_lift(21.0, 101.0)]

    def test_un_fill_sans_mid_estimable_est_omis_pas_compte_a_zero(self):
        """Les premiers echanges n'ont pas de mid anterieur des deux cotes :
        ils ne produisent aucun fill, plutot qu'un fill a demi-spread nul."""
        fills = simulate_passive_fills(self._tape(), horizons_s=(5.0,))
        self.assertLess(len(fills), len(self._tape()))
        self.assertTrue(all(f.half_spread_bps != 0.0 for f in fills))

    def test_le_sens_du_maker_est_l_inverse_de_celui_du_preneur(self):
        fills = simulate_passive_fills(self._tape(), horizons_s=(5.0,))
        par_ts = {f.ts: f for f in fills}
        self.assertTrue(par_ts[10.0].passive_is_buy, "bid frappe -> maker acheteur")
        self.assertFalse(par_ts[11.0].passive_is_buy, "ask leve -> maker vendeur")

    def test_un_horizon_non_estimable_reste_absent_et_rend_None(self):
        """Le markout manquant ne doit jamais se lire comme une derive nulle."""
        fills = simulate_passive_fills(self._tape(), horizons_s=(1e9,))
        self.assertTrue(fills)
        for f in fills:
            self.assertEqual(f.markout_bps, {})
            self.assertIsNone(f.breakeven_fee_bps(1e9))

    def test_un_marche_parfaitement_stationnaire_a_un_markout_nul(self):
        """Carnet fige a 99/101 : le mid ne bouge jamais, donc le marche ne
        reprend rien. Seul le demi-spread subsiste."""
        fills = simulate_passive_fills(self._tape(), horizons_s=(5.0,))
        self.assertTrue(fills)
        for f in fills:
            if 5.0 in f.markout_bps:
                self.assertAlmostEqual(f.markout_bps[5.0], 0.0, places=9)
                self.assertGreater(f.breakeven_fee_bps(5.0), 0.0)

    def test_les_horizons_par_defaut_sont_nommes_pas_en_ligne(self):
        self.assertEqual(MARKOUT_HORIZONS_S, (60.0, 300.0, 1_800.0))

    def test_le_frais_d_equilibre_se_lit_par_horizon(self):
        f = PassiveFill(0.0, 100.0, 1.0, True, 5.0, {60.0: -2.0})
        self.assertAlmostEqual(f.breakeven_fee_bps(60.0), 3.0)
        self.assertIsNone(f.breakeven_fee_bps(300.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPlaceboDeDerive(unittest.TestCase):
    """Un markout doit se lire contre la derive, pas contre zero."""

    def test_un_markout_positif_sous_la_derive_est_en_fait_ADVERSE(self):
        """Le cas NEAR, fige : markout +1,18 pour une derive de +1,36. Lu
        contre zero il semble favorable ; lu contre la derive il est negatif."""
        from prism_v2.backpack.passive import excess_markout_bps
        self.assertGreater(1.18, 0.0)
        self.assertLess(excess_markout_bps(1.18, 1.36), 0.0)

    def test_un_marche_sans_derive_laisse_le_markout_inchange(self):
        from prism_v2.backpack.passive import excess_markout_bps
        self.assertAlmostEqual(excess_markout_bps(-2.0, 0.0), -2.0)

    def test_la_correction_mord_dans_les_deux_sens(self):
        """Une fenetre BAISSIERE flatterait le vendeur passif de la meme facon."""
        from prism_v2.backpack.passive import excess_markout_bps
        self.assertAlmostEqual(excess_markout_bps(-1.0, -3.0), 2.0)
