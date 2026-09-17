"""Couche de correspondance — celle qui manquait a l'architecture.

Quatorze defauts ont ete trouves dans ce projet alors que 866 tests passaient.
Aucun test ne les a attrapes : ils verifiaient ce que le code FAISAIT, pas si
la quantite calculee correspondait a la realite qu'elle nommait.

Chaque test ci-dessous rejoue un defaut REEL, avec les chiffres observes a
l'epoque.
"""
from __future__ import annotations

import unittest

from prism_v2.sanity import (
    ALERTE, IMPOSSIBLE, OK, PLAUSIBLE, check_causal_direction,
    check_firing_rate, check_invariant, check_magnitude, check_novelty,
    check_publication_lag, gate, render,
)


class TestGrandeurs(unittest.TestCase):
    """Defaut reel : constante de venue supposee uniforme."""

    def test_la_cadence_de_funding_codee_en_dur_est_attrapee(self):
        """OKX paie toutes les 4 h sur 90 des 142 instruments ; supposer 8 h
        produisait un differentiel lu a +3879 %/an."""
        v = check_magnitude("diff", [38.79, 0.5], "funding_apr")
        self.assertEqual(v.status, ALERTE)
        self.assertAlmostEqual(v.value, 38.79)

    def test_les_tailles_en_contrats_sont_attrapees(self):
        """Les tailles OKX sont en contrats ; les lire en unites de base
        surestimait la profondeur BTC d'un facteur 100."""
        self.assertEqual(
            check_magnitude("prof", [1.61e9], "level_notional_usd").status,
            ALERTE)

    def test_une_valeur_plausible_passe(self):
        self.assertEqual(
            check_magnitude("diff", [0.5, -1.2, 2.0], "funding_apr").status, OK)

    def test_une_unite_inconnue_ne_conclut_pas(self):
        self.assertEqual(
            check_magnitude("x", [1.0], "unite_imaginaire").status, IMPOSSIBLE)

    def test_aucune_valeur_ne_conclut_pas(self):
        self.assertEqual(check_magnitude("x", [], "funding_apr").status,
                         IMPOSSIBLE)

    def test_les_domaines_sont_declares(self):
        for unit, (lo, hi) in PLAUSIBLE.items():
            self.assertLess(lo, hi, unit)


class TestDeclenchement(unittest.TestCase):
    """Defaut reel : regle degeneree, silencieuse."""

    def test_une_regle_muette_est_attrapee(self):
        """La reference mediane sur TOUTES les minutes valait zero partout :
        0 declenchement sur 753 minutes. Le resultat aurait ete
        indiscernable d'un vrai negatif."""
        v = check_firing_rate("flux", 0, 753)
        self.assertEqual(v.status, ALERTE)
        self.assertIn("pas une hypothese", v.detail)

    def test_un_taux_trop_bas_est_attrape(self):
        self.assertEqual(check_firing_rate("x", 1, 100_000).status, ALERTE)

    def test_un_seuil_qui_ne_filtre_rien_est_attrape(self):
        self.assertEqual(check_firing_rate("x", 500, 1000).status, ALERTE)

    def test_un_taux_plausible_passe(self):
        self.assertEqual(check_firing_rate("x", 287, 30_000).status, OK)

    def test_aucune_occasion_ne_conclut_pas(self):
        self.assertEqual(check_firing_rate("x", 0, 0).status, IMPOSSIBLE)


class TestCausalite(unittest.TestCase):
    """Defaut reel : prendre un symptome pour une cause (Phase C entiere)."""

    def test_un_symptome_est_attrape(self):
        """Le prix bougeait de 14,55 bps dans la direction de la liquidation
        pendant les 60 s la precedant, contre 2,97 bps apres."""
        r = check_causal_direction("liq", [14.55] * 40, [2.97] * 40)
        self.assertEqual(r.verdict.status, ALERTE)
        self.assertGreater(r.ratio, 2.0)
        self.assertIn("symptome", r.verdict.detail)

    def test_une_vraie_cause_passe(self):
        r = check_causal_direction("x", [0.5] * 40, [8.0] * 40)
        self.assertEqual(r.verdict.status, OK)

    def test_un_mouvement_anterieur_nul_passe(self):
        r = check_causal_direction("x", [0.0] * 40, [5.0] * 40)
        self.assertEqual(r.verdict.status, OK)

    def test_trop_peu_d_evenements_ne_conclut_pas(self):
        r = check_causal_direction("x", [10.0] * 5, [1.0] * 5)
        self.assertEqual(r.verdict.status, IMPOSSIBLE)
        self.assertIsNone(r.ratio)


class TestInvariants(unittest.TestCase):
    """Defaut reel : invariant declare mais jamais mesure."""

    def test_le_beta_d_un_livre_dollar_neutre_est_attrape(self):
        """23,8 % de la variance du PnL venait du marche."""
        v = check_invariant("beta", 0.1109, 0.05, "neutralite marche")
        self.assertEqual(v.status, ALERTE)

    def test_une_neutralite_reelle_passe(self):
        self.assertEqual(
            check_invariant("beta", -0.0099, 0.05, "neutralite").status, OK)

    def test_la_tolerance_est_bilaterale(self):
        self.assertEqual(check_invariant("x", -0.2, 0.05, "n").status, ALERTE)


class TestNouveaute(unittest.TestCase):
    """Defaut reel : retester une famille fermee sous un autre nom."""

    def test_une_famille_renommee_est_attrapee(self):
        """La Phase C conditionnait sur un mouvement de prix passe, comme la
        premiere famille du projet, fermee sur 617 820 evenements."""
        closed = [i % 10 == 0 for i in range(500)]
        new = [c for c in closed]
        v = check_novelty("flux", new, closed)
        self.assertEqual(v.status, ALERTE)
        self.assertIn("meme sous un autre nom", v.detail)

    def test_une_famille_reellement_nouvelle_passe(self):
        closed = [i % 10 == 0 for i in range(500)]
        new = [i % 10 == 5 for i in range(500)]
        self.assertEqual(check_novelty("x", new, closed).status, OK)

    def test_trop_peu_de_declenchements_ne_conclut_pas(self):
        closed = [False] * 500
        new = [i < 3 for i in range(500)]
        self.assertEqual(check_novelty("x", new, closed).status, IMPOSSIBLE)

    def test_des_series_incompatibles_ne_concluent_pas(self):
        self.assertEqual(
            check_novelty("x", [True] * 10, [True] * 5).status, IMPOSSIBLE)


class TestHorodatage(unittest.TestCase):
    """Defaut reel : dater un evenement de sa decouverte."""

    def test_un_delai_de_publication_massif_est_attrape(self):
        """Delai median OKX 2 434 s ; 77 % au-dela de 30 s."""
        v = check_publication_lag("liq", [2461.0, 9271.1, 747.7] * 5, 30.0)
        self.assertEqual(v.status, ALERTE)
        self.assertIn("DECOUVERTE", v.detail)

    def test_un_delai_negligeable_passe(self):
        self.assertEqual(
            check_publication_lag("x", [0.2, 0.5, 0.3] * 5, 30.0).status, OK)


class TestPorte(unittest.TestCase):

    def test_une_seule_alerte_bloque_la_conclusion(self):
        vs = [check_magnitude("a", [0.5], "funding_apr"),
              check_invariant("b", 0.9, 0.05, "neutralite")]
        passed, _ = gate(vs)
        self.assertFalse(passed)

    def test_un_impossible_ne_bloque_pas_mais_est_rapporte(self):
        vs = [check_magnitude("a", [], "funding_apr")]
        passed, out = gate(vs)
        self.assertTrue(passed)
        self.assertEqual(out[0].status, IMPOSSIBLE)
        self.assertIn("----", render(vs))

    def test_le_rendu_dit_explicitement_le_verdict(self):
        vs = [check_invariant("b", 0.9, 0.05, "neutralite")]
        self.assertIn("aucune conclusion economique", render(vs))


if __name__ == "__main__":
    unittest.main()
