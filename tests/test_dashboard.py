"""Le tableau de bord peut-il mentir ?

C'est le seul artefact du projet dont la fonction est de dire si le travail
rapproche ou eloigne de l'objectif. S'il peut etre embelli, il ne sert a rien.
Ces tests verrouillent les trois facons de l'embellir :

  1. presenter un INCONNU comme une valeur ;
  2. presenter du PnL papier comme du PnL realise ;
  3. retenir la formulation d'objectif la plus douce.
"""
import unittest

from prism_v2.dashboard import (DERIVED, MEASURED, UNKNOWN, Dashboard, Metric,
                                Objective)
from prism_v2.modes import SystemMode

OBJ = Objective(capital_eur=1_000.0, target_eur_per_day=20.0,
                multiple=5.0, days=60.0)


class TestObjectif(unittest.TestCase):

    def test_vingt_euros_par_jour_valent_deux_cents_bps(self):
        self.assertAlmostEqual(OBJ.bps_per_day_from_daily_target(), 200.0,
                               places=9)

    def test_x5_en_60_jours_en_compose(self):
        self.assertAlmostEqual(OBJ.bps_per_day_from_multiple(),
                               (5.0 ** (1 / 60) - 1) * 10_000, places=9)
        self.assertGreater(OBJ.bps_per_day_from_multiple(), 200.0)

    def test_le_seuil_retenu_est_le_plus_exigeant(self):
        """Prendre le plus doux laisserait passer une economie qui rate
        l'une des deux promesses."""
        self.assertEqual(OBJ.binding_bps_per_day(),
                         max(OBJ.bps_per_day_from_daily_target(),
                             OBJ.bps_per_day_from_multiple()))
        self.assertGreater(OBJ.binding_bps_per_day(), 270.0)

    def test_capital_nul_refuse(self):
        with self.assertRaises(ValueError):
            Objective(0.0, 20.0, 5.0, 60.0).bps_per_day_from_daily_target()


class TestMetriqueNeDeguisePasUnInconnu(unittest.TestCase):

    def test_valeur_absente_impose_inconnu(self):
        with self.assertRaises(ValueError):
            Metric("slippage reel", None, "bps", MEASURED)

    def test_valeur_presente_ne_peut_pas_se_declarer_inconnue(self):
        with self.assertRaises(ValueError):
            Metric("cout", 11.0, "bps", UNKNOWN)

    def test_inconnu_s_affiche_inconnu(self):
        m = Metric("latence", None, "ms", UNKNOWN, "jamais mesuree")
        self.assertIn("INCONNU", m.render())
        self.assertNotIn("0", m.render().split("INCONNU")[0])

    def test_qualite_inventee_refusee(self):
        with self.assertRaises(ValueError):
            Metric("x", 1.0, "bps", "PROBABLE")


class TestPnLRealise(unittest.TestCase):

    def test_hors_live_le_pnl_realise_vaut_zero_pas_le_papier(self):
        for mode in (SystemMode.DISCOVERY, SystemMode.PAPER, SystemMode.DEMO):
            d = Dashboard(mode=mode, objective=OBJ)
            m = d.realised_pnl_eur()
            self.assertEqual(m.value, 0.0)
            self.assertIn("aucun ordre reel", m.source)

    def test_le_pnl_realise_n_est_pas_fourni_par_l_appelant(self):
        """Il est derive du mode : personne ne peut l'ecrire a la main."""
        d = Dashboard(mode=SystemMode.PAPER, objective=OBJ)
        d.add(Metric("PnL papier", 500.0, "EUR", DERIVED, "simulation"))
        self.assertEqual(d.realised_pnl_eur().value, 0.0)

    def test_live_non_implemente_rend_inconnu(self):
        d = Dashboard(mode=SystemMode.LIVE, objective=OBJ)
        self.assertIsNone(d.realised_pnl_eur().value)
        self.assertEqual(d.realised_pnl_eur().quality, UNKNOWN)


class TestDistanceAObjectif(unittest.TestCase):

    def test_economie_inconnue_donne_distance_inconnue(self):
        d = Dashboard(mode=SystemMode.DISCOVERY, objective=OBJ)
        self.assertIsNone(d.distance_to_objective())
        self.assertIn("INCONNU", d.render())

    def test_distance_est_un_rapport_au_seuil_qui_mord(self):
        d = Dashboard(mode=SystemMode.DISCOVERY, objective=OBJ,
                      best_economy_bps_per_day=12.84, best_economy_label="test")
        self.assertAlmostEqual(d.distance_to_objective(),
                               12.84 / OBJ.binding_bps_per_day(), places=9)
        self.assertLess(d.distance_to_objective(), 0.05)

    def test_conversion_en_euros_par_jour(self):
        d = Dashboard(mode=SystemMode.DISCOVERY, objective=OBJ,
                      best_economy_bps_per_day=200.0, best_economy_label="t")
        self.assertAlmostEqual(d.eur_per_day_at_best(), 20.0, places=9)

    def test_le_rendu_affiche_le_facteur_manquant(self):
        d = Dashboard(mode=SystemMode.DISCOVERY, objective=OBJ,
                      best_economy_bps_per_day=12.84, best_economy_label="t",
                      bottleneck="magnitude brute", next_action="mesurer")
        txt = d.render()
        self.assertIn("DISTANCE A L'OBJECTIF", txt)
        self.assertIn("facteur", txt)
        self.assertIn("GOULOT", txt)
        self.assertIn("PROCHAINE ACTION", txt)


if __name__ == "__main__":
    unittest.main()
