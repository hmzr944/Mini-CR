"""Le registre des plafonds peut-il laisser passer une piste morte ?

Section 21 du mandat : une piste doit etre abandonnee quand son plafond
economique est demontre trop faible, et ne doit pas etre re-optimisee parce
qu'elle a deja coute du temps. Section 25 : aucune conclusion n'est protegee,
mais une revision exige une preuve.

Ces deux exigences se contredisent si elles sont mal implementees. Ces tests
verifient qu'elles tiennent ensemble.
"""
import unittest

from prism_v2.kill_registry import (COST_DOMINATES, EXECUTABLE, FILL_UNKNOWN,
                                    MECHANISM_UNPROVEN, NOT_HEDGEABLE,
                                    NOT_PERSISTENT, NO_MAGNITUDE,
                                    Ceiling, KillRegistry)

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
        """Le facteur manquant se compte depuis une economie EXECUTABLE.

        Ce test exigeait auparavant le mot « facteur » sur un registre ne
        contenant QUE des bornes superieures. C'etait precisement la faute
        corrigee : annoncer « il manque un facteur N » a partir d'un nombre
        qui n'est pas une economie. Le facteur n'apparait donc que lorsqu'un
        plafond EXECUTABLE strictement positif existe.
        """
        txt = REG.render(SEUIL)
        self.assertIn("non-crypto couvert", txt)
        self.assertNotIn("il manque un facteur", txt)

        avec = KillRegistry.from_list(list(REG.ceilings.values()) + [
            Ceiling("executable positif", 34.0, COST_DOMINATES, 500,
                    "taker, bareme reel", evidence=EXECUTABLE)])
        txt2 = avec.render(SEUIL)
        self.assertIn("il manque un facteur", txt2)
        self.assertIn("objectif", txt2)


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


class TestAgregation(unittest.TestCase):
    """Un rendement PAR PAIRE et un rendement en PORTEFEUILLE ne sont pas
    comparables : la mutualisation du coussin les separe d'un facteur mesure
    (2,21 sur 16 paires). Lire 19,6 par paire comme une degradation de 33,3 en
    portefeuille est la faute que ce verrou empeche."""

    def test_agregation_inconnue_refusee(self):
        from prism_v2.kill_registry import Ceiling as C
        with self.assertRaises(ValueError):
            C("x", 1.0, COST_DOMINATES, 10, "m", aggregation="SECTEUR")

    def test_comparer_paire_et_portefeuille_est_refuse(self):
        from prism_v2.kill_registry import CAPITAL, PAIR, PORTFOLIO, Ceiling as C
        c = C("x", 33.3, COST_DOMINATES, 10, "m", aggregation=PORTFOLIO)
        self.assertTrue(c.kills(20.0, CAPITAL, PORTFOLIO))
        with self.assertRaises(ValueError):
            c.kills(19.6, CAPITAL, PAIR)

    def test_defaut_est_portefeuille(self):
        from prism_v2.kill_registry import PORTFOLIO, Ceiling as C
        self.assertEqual(C("x", 1.0, COST_DOMINATES, 10, "m").aggregation,
                         PORTFOLIO)

    def test_le_rendu_affiche_l_agregation(self):
        from prism_v2.kill_registry import PAIR, Ceiling as C, KillRegistry as K
        txt = K.from_list([C("f", 19.6, COST_DOMINATES, 17, "m",
                             aggregation=PAIR)]).render(SEUIL)
        self.assertIn("PAIRE", txt)


class TestClasseDePreuve(unittest.TestCase):
    """Le defaut : une borne superieure affichee comme une economie.

    `best_known` renvoyait « flux couvert, duree optimale » a 33,30 bps/jour,
    et le tableau de bord titrait dessus « MEILLEURE ECONOMIE DEMONTREE », a
    « un facteur 8,2 » de l'objectif. Ce plafond est la borne superieure d'un
    mecanisme ayant atteint son propre critere d'abandon declare d'avance
    (alpha >= 0,45 ; mesure 0,493). Le denominateur et l'agregation etaient
    types ; la CLASSE DE PREUVE ne l'etait pas, et la faute est passee par la.

    Ces tests figent la separation. Ils n'autorisent aucun chiffre nouveau :
    ils empechent un chiffre ancien de porter un titre qu'il ne merite pas.
    """

    def _reg(self):
        return KillRegistry.from_list([
            Ceiling("borne d'un mecanisme mort", 33.30, COST_DOMINATES, 30_576,
                    "critere d'abandon atteint",
                    evidence=MECHANISM_UNPROVEN),
            Ceiling("borne a remplissage suppose", 13.60, NO_MAGNITUDE, 97,
                    "file supposee gagnee", evidence=FILL_UNKNOWN),
            Ceiling("resultat executable", 0.0, NO_MAGNITUDE, 21_240,
                    "taker, bareme reel, coupure temporelle",
                    evidence=EXECUTABLE),
        ])

    def test_classe_inconnue_refusee(self):
        with self.assertRaises(ValueError):
            Ceiling("x", 1.0, COST_DOMINATES, 10, "m", evidence="PROMETTEUR")

    def test_classe_par_defaut_jamais_promue_en_tete(self):
        """Un plafond dont la classe n'est pas tranchee ne titre jamais."""
        reg = KillRegistry.from_list([
            Ceiling("non qualifie", 99.0, COST_DOMINATES, 10, "m"),
        ])
        self.assertEqual(reg.best_known().ceiling_bps_per_day, 99.0)
        self.assertIsNone(reg.best_demonstrated())

    def test_best_known_reste_la_borne_la_plus_haute(self):
        """Pour TUER un candidat, la borne la plus haute est le bon majorant."""
        b = self._reg().best_known()
        self.assertEqual(b.ceiling_bps_per_day, 33.30)
        self.assertEqual(b.evidence, MECHANISM_UNPROVEN)

    def test_best_demonstrated_ignore_les_bornes(self):
        """LE DEFAUT LUI-MEME : 33,30 ne doit plus sortir comme economie."""
        d = self._reg().best_demonstrated()
        self.assertEqual(d.family, "resultat executable")
        self.assertEqual(d.ceiling_bps_per_day, 0.0)

    def test_aucun_executable_donne_inconnu_et_non_zero(self):
        """Sans plafond executable, la reponse est None — jamais 0,0."""
        reg = KillRegistry.from_list([
            Ceiling("borne seule", 50.0, COST_DOMINATES, 10, "m",
                    evidence=FILL_UNKNOWN)])
        self.assertIsNone(reg.best_demonstrated())

    def test_le_rendu_nomme_les_deux_et_ne_les_confond_pas(self):
        txt = self._reg().render(272.0)
        self.assertIn("borne superieure la plus haute", txt)
        self.assertIn("MEILLEURE ECONOMIE EXECUTABLE DEMONTREE", txt)
        # le titre « economie » ne doit pas porter le 33,30
        eco = txt.split("MEILLEURE ECONOMIE EXECUTABLE DEMONTREE")[1]
        self.assertNotIn("33.30", eco)
        self.assertIn("resultat executable", eco)

    def test_pas_de_facteur_multiplicatif_depuis_zero(self):
        """Annoncer « il manque un facteur N » depuis 0 serait un mensonge."""
        txt = self._reg().render(272.0)
        self.assertIn("AUCUN facteur ne comble un ecart depuis zero", txt)


class TestEtatReel(unittest.TestCase):
    """Le tableau de bord reel, et non un jeu d'essai."""

    def test_etat_ne_titre_plus_sur_une_borne(self):
        from prism_v2.scans.etat import PLAFONDS, build
        reg = KillRegistry.from_list(PLAFONDS)
        self.assertEqual(reg.best_known().ceiling_bps_per_day, 33.30)
        d = reg.best_demonstrated()
        self.assertIsNotNone(d)
        self.assertEqual(d.ceiling_bps_per_day, 0.0)
        txt = build().render()
        self.assertIn("BORNE SUPERIEURE LA PLUS HAUTE", txt)
        self.assertIn("ECONOMIE EXECUTABLE DEMONTREE      0.00 bps/jour", txt)

    def test_toute_famille_declare_sa_classe(self):
        """Aucun plafond du registre reel ne reste non classe par oubli.

        UNQUALIFIED est un choix legitime — « je ne peux pas trancher » — mais
        il doit etre ECRIT, pas subi. Ce test echoue si une famille nouvelle
        arrive sans que sa classe ait ete examinee.
        """
        from prism_v2.scans.etat import PLAFONDS
        src = open("prism_v2/scans/etat.py", encoding="utf-8").read()
        self.assertEqual(src.count("evidence="), len(PLAFONDS))
