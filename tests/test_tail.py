"""Une queue droite peut-elle se faire passer pour un profit ?

Ce module de test existe parce que la recherche de QUEUES introduit une faute
symetrique de celle que le registre commettait. Le registre declarait mortes
des familles dont la moyenne masquait une queue. Le risque inverse est de
declarer prometteuse une famille parce qu'elle possede quelques valeurs
extremes — sans qu'elles soient capturables, ni les pertes supportables.

Les gardes sont donc figees ici, une par une, et chacune correspond a une
maniere precise de se tromper.
"""
import unittest

from prism_v2.tail import (CAPTURABLE, CAPTURE_VALIDATION, MAX_GAIN_CONCENTRATION,
                           NET, OBSERVED, REPORTED_QUANTILES, TAIL_DISCOVERY,
                           Episode, Event, RiskLimits, TailProfile,
                           capturable_bps, evaluate_tail, group_into_episodes,
                           net_bps, quantile)


def profil(vals, level=NET):
    return TailProfile("famille test", level, list(vals), gap_s=60.0)


class TestRegroupementEnEpisodes(unittest.TestCase):

    def test_une_cascade_ne_compte_que_pour_UN_episode(self):
        """La faute que ce regroupement existe pour empecher : dix
        liquidations en trois secondes suivies du MEME mouvement, comptees
        comme dix observations independantes."""
        evs = [Event(ts=float(i), instrument="BTC", size_usd=100.0)
               for i in range(10)]
        eps = group_into_episodes(evs, gap_s=60.0)
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0].n_events, 10)

    def test_deux_episodes_eloignes_restent_distincts(self):
        evs = [Event(0.0, "BTC"), Event(1.0, "BTC"),
               Event(1000.0, "BTC"), Event(1001.0, "BTC")]
        self.assertEqual(len(group_into_episodes(evs, gap_s=60.0)), 2)

    def test_deux_instruments_ne_fusionnent_jamais(self):
        evs = [Event(0.0, "BTC"), Event(1.0, "ETH")]
        eps = group_into_episodes(evs, gap_s=60.0)
        self.assertEqual(len(eps), 2)
        self.assertEqual({e.instrument for e in eps}, {"BTC", "ETH"})

    def test_la_taille_de_l_episode_est_la_somme(self):
        evs = [Event(0.0, "BTC", 100.0), Event(1.0, "BTC", 250.0)]
        self.assertAlmostEqual(
            group_into_episodes(evs, gap_s=60.0)[0].total_size_usd, 350.0)

    def test_un_ecart_non_positif_leve(self):
        with self.assertRaises(ValueError):
            group_into_episodes([Event(0.0, "BTC")], gap_s=0.0)


class TestNiveauxDeMesure(unittest.TestCase):

    def test_le_gain_part_du_prix_d_ENTREE_pas_du_declencheur(self):
        """Une dislocation resorbee pendant la latence est entierement REELLE
        et entierement INACCESSIBLE. Mesurer depuis le declencheur la
        compterait comme un gain."""
        # declencheur a 100, le prix est deja revenu a 110 quand on entre,
        # et la sortie se fait a 110 : rien n'a ete capture.
        self.assertAlmostEqual(
            capturable_bps(price_at_trigger=100.0, price_at_entry=110.0,
                           price_at_exit=110.0, is_long=True), 0.0)

    def test_un_short_gagne_quand_le_prix_BAISSE(self):
        self.assertGreater(
            capturable_bps(100.0, 110.0, 100.0, is_long=False), 0.0)

    def test_le_cout_est_soustrait_et_jamais_ajoute(self):
        self.assertAlmostEqual(net_bps(captured=100.0, cost_bps=12.0), 88.0)

    def test_un_cout_negatif_leve_au_lieu_de_bonifier(self):
        with self.assertRaises(ValueError):
            net_bps(100.0, -5.0)

    def test_un_niveau_inconnu_est_refuse(self):
        with self.assertRaises(ValueError):
            TailProfile("x", "INVENTE", [1.0, 2.0], gap_s=60.0)


class TestQuantiles(unittest.TestCase):

    def test_les_quantiles_rapportes_sont_figes_d_avance(self):
        self.assertEqual(REPORTED_QUANTILES,
                         (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99))

    def test_une_seule_observation_ne_donne_aucun_quantile(self):
        self.assertIsNone(quantile([1.0], 0.5))

    def test_la_mediane_est_correcte(self):
        self.assertAlmostEqual(quantile([1.0, 2.0, 3.0], 0.5), 2.0)

    def test_un_quantile_hors_bornes_leve(self):
        with self.assertRaises(ValueError):
            quantile([1.0, 2.0], 1.5)


class TestConcentration(unittest.TestCase):

    def test_une_loterie_est_detectee(self):
        """Un episode enorme et 99 minuscules : l'esperance repose sur un
        tirage, donc elle n'est pas gouvernable a 1 000 EUR."""
        p = profil([1000.0] + [0.1] * 99)
        self.assertGreater(p.gain_concentration(), MAX_GAIN_CONCENTRATION)

    def test_des_gains_repartis_ne_sont_pas_concentres(self):
        p = profil([10.0] * 100)
        self.assertLess(p.gain_concentration(), MAX_GAIN_CONCENTRATION)

    def test_sans_gain_la_concentration_est_INCONNUE_pas_zero(self):
        """Rendre 0 fabriquerait une propriete que la donnee ne porte pas."""
        self.assertIsNone(profil([-5.0] * 50).gain_concentration())


class TestBornesDeRisque(unittest.TestCase):

    def _lim(self):
        return RiskLimits(max_loss_per_episode_bps=200.0,
                          max_cumulative_loss_bps=2000.0)

    def test_une_borne_non_positive_est_refusee(self):
        with self.assertRaises(ValueError):
            RiskLimits(0.0, 100.0)

    def test_un_quantile_bas_hors_bornes_est_refuse(self):
        with self.assertRaises(ValueError):
            RiskLimits(100.0, 1000.0, left_quantile=0.7)

    def test_un_PIRE_episode_ruineux_est_attrape_meme_s_il_est_RARE(self):
        """Le cas que la garde ambigue laissait passer : une queue gauche
        moins frequente que la droite, mais qui ferme le compte."""
        vals = [50.0] * 199 + [-5000.0]
        breches = self._lim().breaches(profil(vals))
        self.assertTrue(any("pire episode" in b for b in breches))

    def test_une_erosion_SANS_episode_spectaculaire_est_attrapee(self):
        """Aucun episode ne franchit la borne unitaire, le cumul si."""
        vals = [-50.0] * 100
        breches = self._lim().breaches(profil(vals))
        self.assertTrue(any("cumulee" in b for b in breches))
        self.assertFalse(any("pire episode" in b for b in breches))

    def test_une_distribution_saine_ne_declenche_aucune_breche(self):
        self.assertEqual(self._lim().breaches(profil([50.0] * 100)), [])


class TestVerdict(unittest.TestCase):

    def _lim(self):
        return RiskLimits(1e9, 1e9)

    def test_un_centile_de_MOUVEMENT_ne_peut_jamais_conclure(self):
        """La faute centrale, figee : j'ai ecrit « il faut 390 bps » a propos
        d'un centile de mouvement, comme si c'etait un PnL."""
        for niveau in (OBSERVED, CAPTURABLE):
            v = evaluate_tail(profil([1000.0] * 300, level=niveau),
                              self._lim(), min_n=200, min_net_bps=390.0)
            self.assertFalse(v.passes)
            self.assertEqual(v.status, TAIL_DISCOVERY)
            self.assertTrue(any("exige le niveau" in r for r in v.reasons))

    def test_une_queue_droite_sans_esperance_positive_ne_passe_pas(self):
        """Quelques gains enormes et beaucoup de pertes : le q95 passe, pas
        l'esperance. Une queue droite ne suffit pas.

        Les valeurs sont choisies pour que l'esperance soit REELLEMENT
        negative — un premier jeu a -100 donnait +5 bps de moyenne, et le
        test verifiait donc autre chose que ce qu'il annonçait."""
        # 20 gains sur 300 = 6,7 % : le q95 tombe DANS la zone positive.
        # Un premier jeu a 15 gains (5,0 %) placait le q95 pile a la
        # frontiere, donc dans les pertes — le test ne testait rien.
        vals = [2000.0] * 20 + [-200.0] * 280
        p = profil(vals)
        self.assertLess(p.mean(), 0.0)
        self.assertGreater(quantile(vals, 0.95), 390.0)
        v = evaluate_tail(p, self._lim(), 200, 390.0)
        self.assertFalse(v.passes)
        self.assertTrue(any("esperance" in r for r in v.reasons), v.reasons)

    def test_un_echantillon_trop_petit_ne_passe_pas(self):
        v = evaluate_tail(profil([500.0] * 50), self._lim(), 200, 390.0)
        self.assertFalse(v.passes)
        self.assertTrue(any("sous le minimum 200" in r for r in v.reasons))

    def test_une_loterie_rentable_est_refusee_pour_CONCENTRATION(self):
        vals = [50000.0] + [10.0] * 299
        v = evaluate_tail(profil(vals), self._lim(), 200, 390.0)
        self.assertGreater(profil(vals).mean(), 0.0)
        self.assertFalse(v.passes)
        self.assertTrue(any("loterie" in r for r in v.reasons))

    def test_une_famille_saine_passe_et_est_nommee_VALIDATION(self):
        vals = [400.0 + i for i in range(300)]
        v = evaluate_tail(profil(vals), self._lim(), 200, 390.0)
        self.assertTrue(v.passes, v.reasons)
        self.assertEqual(v.status, CAPTURE_VALIDATION)

    def test_le_risque_est_lu_AVANT_l_economie(self):
        """Une famille rentable mais ruineuse doit sortir sur le risque."""
        lim = RiskLimits(max_loss_per_episode_bps=100.0,
                         max_cumulative_loss_bps=1e9)
        vals = [500.0] * 299 + [-9000.0]
        v = evaluate_tail(profil(vals), lim, 200, 390.0)
        self.assertFalse(v.passes)
        self.assertTrue(any("pire episode" in r for r in v.reasons))


if __name__ == "__main__":
    unittest.main(verbosity=2)
