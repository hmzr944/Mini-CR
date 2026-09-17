"""Le registre des plafonds peut-il laisser passer une piste morte ?

Section 21 du mandat : une piste doit etre abandonnee quand son plafond
economique est demontre trop faible, et ne doit pas etre re-optimisee parce
qu'elle a deja coute du temps. Section 25 : aucune conclusion n'est protegee,
mais une revision exige une preuve.

Ces deux exigences se contredisent si elles sont mal implementees. Ces tests
verifient qu'elles tiennent ensemble.
"""
import unittest

from prism_v2.kill_registry import (COST_DOMINATES, NOT_HEDGEABLE,
                                    NOT_PERSISTENT, Ceiling, KillRegistry)

SEUIL = 272.0

REG = KillRegistry.from_list([
    Ceiling("carry inverse/lineaire", 0.57, COST_DOMINATES, 4135,
            "R(T) sur bareme reel et coussin mesure"),
    Ceiling("funding inter-venues", -23.0, NOT_PERSISTENT, 26,
            "test de persistance du signe, 26 cellules"),
    Ceiling("non-crypto couvert", 12.84, NOT_HEDGEABLE, 2391,
            "differentiel couvert, residu 1 %/h"),
])


class TestPlafond(unittest.TestCase):

    def test_un_plafond_sans_observation_est_refuse(self):
        with self.assertRaises(ValueError):
            Ceiling("x", 1.0, COST_DOMINATES, 0, "methode")

    def test_un_plafond_sans_methode_est_refuse(self):
        with self.assertRaises(ValueError):
            Ceiling("x", 1.0, COST_DOMINATES, 10, "")

    def test_un_plafond_condamne_ce_qui_passe_dessous(self):
        c = REG.ceilings["non-crypto couvert"]
        self.assertTrue(c.kills(10.0))
        self.assertTrue(c.kills(12.84))
        self.assertFalse(c.kills(12.85))


class TestCrible(unittest.TestCase):

    def test_distingue_battre_le_record_et_atteindre_l_objectif(self):
        """Confondre les deux est la faute que ce module existe pour eviter."""
        r = REG.screen(50.0, SEUIL)
        self.assertTrue(r["beats_best_known"])
        self.assertFalse(r["reaches_objective"])
        self.assertEqual(r["verdict"], "MEILLEUR CONNU, MAIS SOUS L'OBJECTIF")

    def test_sous_un_plafond_connu_c_est_mort_sans_etude(self):
        r = REG.screen(5.0, SEUIL)
        self.assertFalse(r["beats_best_known"])
        self.assertIn("mort par arithmetique", r["verdict"])

    def test_atteindre_l_objectif_est_nomme_comme_tel(self):
        r = REG.screen(300.0, SEUIL)
        self.assertTrue(r["reaches_objective"])
        self.assertEqual(r["verdict"], "ATTEINT L'OBJECTIF")

    def test_le_facteur_manquant_est_rendu(self):
        r = REG.screen(12.84, SEUIL)
        self.assertAlmostEqual(r["gap_factor"], SEUIL / 12.84, places=9)

    def test_meilleur_plafond_connu(self):
        self.assertEqual(REG.best_known().family, "non-crypto couvert")

    def test_registre_vide_ne_condamne_personne(self):
        vide = KillRegistry.from_list([])
        self.assertIsNone(vide.best_known())
        self.assertTrue(vide.screen(1.0, SEUIL)["beats_best_known"])


class TestRevision(unittest.TestCase):

    def _reg(self):
        return KillRegistry.from_list(list(REG.ceilings.values()))

    def test_ranimer_sans_depasser_le_plafond_est_refuse(self):
        r = self._reg()
        with self.assertRaises(ValueError):
            r.revive("non-crypto couvert", 12.0, "nouvelle mesure", 500)
        with self.assertRaises(ValueError):
            r.revive("non-crypto couvert", 12.84, "egalite", 500)

    def test_ranimer_en_depassant_est_accepte_et_enregistre(self):
        r = self._reg()
        c = r.revive("non-crypto couvert", 40.0, "nouvelle fenetre, 900 obs", 900)
        self.assertEqual(r.ceilings["non-crypto couvert"].ceiling_bps_per_day, 40.0)
        self.assertEqual(c.reason, "REVISE")
        self.assertEqual(r.best_known().ceiling_bps_per_day, 40.0)

    def test_une_revision_exige_preuve_et_echantillon(self):
        r = self._reg()
        with self.assertRaises(ValueError):
            r.revive("non-crypto couvert", 40.0, "", 900)
        with self.assertRaises(ValueError):
            r.revive("non-crypto couvert", 40.0, "preuve", 0)

    def test_une_famille_inconnue_peut_etre_ajoutee(self):
        r = self._reg()
        r.revive("famille neuve", 3.0, "premiere mesure", 100)
        self.assertIn("famille neuve", r.ceilings)


class TestRendu(unittest.TestCase):

    def test_le_rendu_affiche_le_facteur_manquant(self):
        txt = REG.render(SEUIL)
        self.assertIn("facteur", txt)
        self.assertIn("non-crypto couvert", txt)
        self.assertIn("objectif", txt)


if __name__ == "__main__":
    unittest.main()


class TestDenominateur(unittest.TestCase):
    """Melanger un rendement sur notionnel et un seuil sur capital est
    l'erreur qui a fait juger le carry a 0,008x l'objectif alors que les deux
    nombres n'avaient pas le meme denominateur."""

    def test_denominateur_inconnu_refuse(self):
        from prism_v2.kill_registry import Ceiling as C
        with self.assertRaises(ValueError):
            C("x", 1.0, COST_DOMINATES, 10, "m", denominator="PIB")

    def test_comparer_deux_denominateurs_est_refuse(self):
        from prism_v2.kill_registry import CAPITAL, NOTIONAL, Ceiling as C
        c = C("x", 10.0, COST_DOMINATES, 10, "m", denominator=CAPITAL)
        self.assertTrue(c.kills(5.0, CAPITAL))
        with self.assertRaises(ValueError):
            c.kills(5.0, NOTIONAL)

    def test_le_crible_refuse_un_candidat_en_notionnel(self):
        from prism_v2.kill_registry import NOTIONAL
        with self.assertRaises(ValueError):
            REG.screen(12.84, SEUIL, denominator=NOTIONAL)

    def test_le_meilleur_connu_ignore_les_plafonds_en_notionnel(self):
        from prism_v2.kill_registry import NOTIONAL, Ceiling as C, KillRegistry as K
        r = K.from_list([C("sur capital", 5.0, COST_DOMINATES, 10, "m"),
                         C("sur notionnel", 900.0, COST_DOMINATES, 10, "m",
                           denominator=NOTIONAL)])
        self.assertEqual(r.best_known().family, "sur capital")

    def test_le_rendu_affiche_le_denominateur(self):
        from prism_v2.kill_registry import NOTIONAL, Ceiling as C, KillRegistry as K
        r = K.from_list([C("f", 9.0, COST_DOMINATES, 10, "m", denominator=NOTIONAL)])
        txt = r.render(SEUIL)
        self.assertIn("NOTIONNEL", txt)
