"""Post-mortem et porte d'hypothese.

Le test central est `test_une_famille_peut_franchir_le_cout_et_mourir_quand_meme` :
c'est le fait que la matrice existe pour rendre visible, et que « ca ne marche
pas » detruisait. Trois des cinq familles mesurees franchissent le barreau du
cout et meurent un cran plus tot.
"""
from __future__ import annotations

import unittest

from prism_v2.capital_efficiency import Objective
from prism_v2.hypothesis_gate import (
    ACCEPTED, MIN_ANSWER_CHARS, QUESTIONS, REFUSED, GateRefused, Hypothesis,
    evaluate_hypothesis, render,
)
from prism_v2.postmortem import (
    COST_PER_TURNOVER_BPS, FAIL, PASS, RUNGS, UNKNOWN, Family, WindowResult,
    measured_families, render_matrix, summary,
)

OBJ = Objective(10_000.0, 10.0, 365.0)


def w(name, bps, turn, gross, ratio):
    return WindowResult(name, bps, turn, gross, ratio)


class TestEchelle(unittest.TestCase):

    def test_l_ordre_des_barreaux_va_du_signal_a_l_economie(self):
        self.assertEqual(RUNGS[0], "SIGNAL_APPARENT")
        self.assertEqual(RUNGS[1], "STABILITE_TEMPORELLE")
        self.assertEqual(RUNGS[-1], "PNL_PAR_EUR_PAR_JOUR")
        self.assertLess(RUNGS.index("STABILITE_TEMPORELLE"),
                        RUNGS.index("COUT_TURNOVER"))

    def test_un_signe_qui_s_inverse_tue_a_la_stabilite(self):
        f = Family("x", "V", 100, [w("d", 5.0, 0.03, 1000.0, 30.0),
                                   w("v", -5.0, 0.03, -1000.0, -30.0)])
        self.assertEqual(f.temporal_stability(), FAIL)
        self.assertEqual(f.killed_at(), "STABILITE_TEMPORELLE")

    def test_un_signe_constant_franchit_la_stabilite(self):
        f = Family("x", "V", 100, [w("d", 5.0, 0.03, 1000.0, 30.0),
                                   w("v", 4.0, 0.03, 900.0, 28.0)])
        self.assertEqual(f.temporal_stability(), PASS)

    def test_une_seule_fenetre_ne_permet_pas_de_juger_la_stabilite(self):
        """Avec une seule observation, « stable » n'a pas de sens."""
        f = Family("x", "V", 100, [w("d", 5.0, 0.03, 1000.0, 30.0)])
        self.assertEqual(f.temporal_stability(), UNKNOWN)

    def test_le_barreau_du_cout_compare_bien_au_cout_mesure(self):
        bas = Family("bas", "V", 100, [w("d", 1.0, 0.4, 100.0, 2.0),
                                       w("v", 1.0, 0.4, 100.0, 2.0)])
        haut = Family("haut", "V", 100, [w("d", 1.0, 0.03, 100.0, 30.0),
                                         w("v", 1.0, 0.03, 100.0, 28.0)])
        self.assertEqual(bas.turnover_cost(), FAIL)
        self.assertEqual(haut.turnover_cost(), PASS)
        self.assertAlmostEqual(COST_PER_TURNOVER_BPS, 6.54, places=2)

    def test_le_cout_se_juge_en_valeur_absolue(self):
        """« L'information est-elle assez ample » est une question distincte
        de « va-t-elle dans le bon sens ». Les confondre masquerait qu'une
        famille peut franchir le cout et mourir a la stabilite."""
        f = Family("x", "V", 100, [w("d", 5.0, 0.03, 1000.0, 30.0),
                                   w("v", -5.0, 0.03, -1000.0, -30.0)])
        self.assertEqual(f.turnover_cost(), PASS)
        self.assertEqual(f.killed_at(), "STABILITE_TEMPORELLE")

    def test_les_barreaux_non_mesures_restent_unknown(self):
        """Les compter comme franchis transformerait une absence de mesure
        en resultat."""
        f = measured_families()[0]
        v = f.verdicts()
        for rung in ("LIQUIDITE", "CAPACITE", "CAPITAL_IMMOBILISE",
                     "PNL_PAR_EUR_PAR_JOUR"):
            self.assertEqual(v[rung], UNKNOWN, rung)

    def test_unknown_ne_tue_pas_une_famille(self):
        f = Family("x", "V", 100, [w("d", 5.0, 0.03, 1000.0, 30.0),
                                   w("v", 4.0, 0.03, 900.0, 28.0)])
        self.assertIsNone(f.killed_at())


class TestMatriceMesuree(unittest.TestCase):

    def test_une_famille_peut_franchir_le_cout_et_mourir_quand_meme(self):
        """LE fait que la matrice existe pour rendre visible."""
        fams = measured_families()
        s = summary(fams)
        franchissent = set(s["franchissent_le_cout"])
        self.assertIn("carry (funding)", franchissent)
        self.assertIn("momentum 30 j", franchissent)
        for f in fams:
            if f.name in franchissent:
                self.assertNotEqual(f.killed_at(), "COUT_TURNOVER", f.name)

    def test_la_stabilite_est_le_tueur_dominant(self):
        s = summary(measured_families())
        tues = s["tuees_par_barreau"]
        self.assertEqual(max(tues, key=tues.get), "STABILITE_TEMPORELLE")

    def test_toutes_les_familles_mesurees_sont_mortes(self):
        for f in measured_families():
            self.assertIsNotNone(f.killed_at(), f.name)

    def test_la_queue_economique_n_a_jamais_ete_atteinte(self):
        s = summary(measured_families())
        self.assertIn("CAPACITE", s["jamais_atteint"])
        self.assertIn("PNL_PAR_EUR_PAR_JOUR", s["jamais_atteint"])

    def test_la_matrice_se_rend(self):
        txt = render_matrix(measured_families())
        self.assertIn("carry", txt)
        self.assertIn("STABILITE", txt)


class TestPorte(unittest.TestCase):

    def plein(self, **kw):
        base = dict(
            name="test",
            pourquoi_existe="a" * MIN_ANSWER_CHARS,
            pourquoi_repetable="b" * MIN_ANSWER_CHARS,
            pourquoi_pas_arbitree="c" * MIN_ANSWER_CHARS,
            capital_necessaire="d" * MIN_ANSWER_CHARS,
            pnl_par_capital_par_jour="e" * MIN_ANSWER_CHARS,
            estimated_bps_per_day=100.0, capital_usd=10_000.0,
            falsifying_test="un test qui peut la tuer",
            expected_weakest_rung="COUT_TURNOVER")
        base.update(kw)
        return Hypothesis(**base)

    def test_une_idee_vide_est_refusee(self):
        d = evaluate_hypothesis(Hypothesis(name="vague"), OBJ)
        self.assertEqual(d.status, REFUSED)
        self.assertFalse(d.accepted)

    def test_une_reponse_trop_courte_ne_compte_pas(self):
        """Une porte qui accepte « oui » ne filtre rien."""
        d = evaluate_hypothesis(self.plein(pourquoi_existe="oui"), OBJ)
        self.assertEqual(d.status, REFUSED)
        self.assertTrue(any("pourquoi_existe" in r for r in d.reasons))

    def test_sans_test_falsifiant_c_est_refuse(self):
        d = evaluate_hypothesis(self.plein(falsifying_test=""), OBJ)
        self.assertTrue(any("falsifiant" in r for r in d.reasons))

    def test_sans_barreau_faible_identifie_c_est_refuse(self):
        d = evaluate_hypothesis(self.plein(expected_weakest_rung=""), OBJ)
        self.assertTrue(any("barreau" in r for r in d.reasons))

    def test_une_estimation_sous_l_objectif_est_refusee(self):
        """Tester une idee qui, de l'aveu de son auteur, n'atteint pas
        l'objectif consomme de la donnee propre pour un resultat connu."""
        d = evaluate_hypothesis(self.plein(estimated_bps_per_day=40.0), OBJ)
        self.assertEqual(d.status, REFUSED)
        self.assertLess(d.ratio_to_objective, 1.0)

    def test_la_porte_aurait_refuse_le_carry(self):
        """Retroactif : mon estimation a priori valait 0,63x l'objectif."""
        d = evaluate_hypothesis(self.plein(name="carry",
                                           estimated_bps_per_day=40.0), OBJ)
        self.assertFalse(d.accepted)
        self.assertAlmostEqual(d.ratio_to_objective, 40.0 / 63.28, places=2)

    def test_une_hypothese_complete_et_ambitieuse_passe(self):
        d = evaluate_hypothesis(self.plein(), OBJ)
        self.assertEqual(d.status, ACCEPTED)
        self.assertIn("ne garantit rien", render(d))

    def test_require_leve_sur_une_hypothese_refusee(self):
        with self.assertRaises(GateRefused):
            evaluate_hypothesis(Hypothesis(name="vague"), OBJ).require()

    def test_require_laisse_passer_une_hypothese_acceptee(self):
        self.assertTrue(evaluate_hypothesis(self.plein(), OBJ).require().accepted)

    def test_capital_absurde_refuse(self):
        d = evaluate_hypothesis(self.plein(capital_usd=-5.0), OBJ)
        self.assertTrue(any("capital" in r for r in d.reasons))

    def test_les_cinq_questions_sont_bien_celles_du_protocole(self):
        self.assertEqual(QUESTIONS, (
            "pourquoi_existe", "pourquoi_repetable", "pourquoi_pas_arbitree",
            "capital_necessaire", "pnl_par_capital_par_jour"))


if __name__ == "__main__":
    unittest.main()
