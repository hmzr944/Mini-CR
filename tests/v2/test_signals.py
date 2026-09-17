"""Les quatre signaux pre-enregistres.

Le test le plus important est `test_le_melange_de_signaux_opposes_est_nul` :
il verrouille la raison algebrique pour laquelle S5 a ete retire du protocole
AVANT les donnees. Si quelqu'un reintroduisait un melange de ce type, le test
le rattraperait.
"""
from __future__ import annotations

import random
import unittest

from prism_v2.signals import (
    BLEND_NAME, CARRY_BARS, MOM_BARS, REV_BARS, SIGNALS, AssetWindow,
    compute_all, s1_carry_plus, s2_carry_minus, s3_tsmom, s4_reversal,
    zscore,
)


def win(funding=None, returns=None, n=60):
    return AssetWindow(funding=funding if funding is not None else [0.0] * n,
                       returns=returns if returns is not None else [0.0] * n)


class TestSignauxIndividuels(unittest.TestCase):

    def test_carry_plus_favorise_le_funding_negatif(self):
        """Porter un actif dont le funding est negatif rapporte."""
        self.assertGreater(s1_carry_plus(win(funding=[-0.001] * 60)), 0.0)
        self.assertLess(s1_carry_plus(win(funding=[0.001] * 60)), 0.0)

    def test_carry_minus_est_l_exact_oppose(self):
        w = win(funding=[0.0007] * 60)
        self.assertAlmostEqual(s1_carry_plus(w), -s2_carry_minus(w), places=18)

    def test_tsmom_suit_la_tendance(self):
        self.assertGreater(s3_tsmom(win(returns=[0.01] * 60)), 0.0)
        self.assertLess(s3_tsmom(win(returns=[-0.01] * 60)), 0.0)

    def test_reversal_est_l_exact_oppose_du_momentum_court(self):
        w = win(returns=[0.01] * 60)
        self.assertLess(s4_reversal(w), 0.0)

    def test_les_fenetres_sont_celles_du_protocole(self):
        self.assertEqual((CARRY_BARS, MOM_BARS, REV_BARS), (42, 42, 6))

    def test_le_reversal_regarde_plus_court_que_le_momentum(self):
        """Six barres contre quarante-deux : ils ne voient pas la meme chose."""
        rets = [0.05] * 54 + [-0.05] * 6      # tendance haussiere, repli recent
        w = win(returns=rets)
        self.assertGreater(s3_tsmom(w), 0.0)      # momentum : encore haussier
        self.assertGreater(s4_reversal(w), 0.0)   # reversal : achete le repli

    def test_historique_insuffisant_rend_none_jamais_zero(self):
        for fn in (s1_carry_plus, s2_carry_minus, s3_tsmom):
            self.assertIsNone(fn(win(n=10)))
        self.assertIsNone(s4_reversal(AssetWindow([], [0.01] * 3)))


class TestZscore(unittest.TestCase):

    def test_le_zscore_est_impair(self):
        """z(-x) = -z(x). C'est la raison pour laquelle melanger un signal et
        sa negation exacte ne peut produire que zero."""
        random.seed(11)
        v = {f"a{i}": random.gauss(0, 1) for i in range(40)}
        z, zn = zscore(v), zscore({k: -x for k, x in v.items()})
        for k in v:
            self.assertAlmostEqual(z[k], -zn[k], places=12)

    def test_dispersion_nulle_rend_zero_et_non_une_division_par_epsilon(self):
        """Sans information transversale, fabriquer des positions serait
        transformer du bruit en convictions."""
        self.assertEqual(zscore({"a": 5.0, "b": 5.0, "c": 5.0}),
                         {"a": 0.0, "b": 0.0, "c": 0.0})

    def test_un_seul_actif_ne_produit_aucun_score(self):
        self.assertEqual(zscore({"a": 3.0}), {"a": 0.0})

    def test_le_zscore_est_centre(self):
        random.seed(3)
        z = zscore({f"a{i}": random.gauss(2, 5) for i in range(30)})
        self.assertAlmostEqual(sum(z.values()) / len(z), 0.0, places=12)


class TestEnsemble(unittest.TestCase):

    def test_les_quatre_signaux_sont_calcules(self):
        w = {"A": win(funding=[0.001] * 60, returns=[0.01] * 60),
             "B": win(funding=[-0.001] * 60, returns=[-0.01] * 60)}
        out = compute_all(w)
        for name in SIGNALS:
            self.assertEqual(set(out[name]), {"A", "B"}, name)

    def test_le_melange_de_signaux_opposes_est_nul(self):
        """LE test. S2 = -S1 et S4 = -S3, donc la somme des z-scores vaut
        zero pour TOUT jeu de donnees. C'est pourquoi S5 a ete retire du
        protocole avant la moindre observation.
        """
        random.seed(5)
        w = {f"a{i}": win(funding=[random.gauss(0, 1e-4)] * 60,
                          returns=[random.gauss(0, 0.01)] * 60)
             for i in range(25)}
        blend = compute_all(w)[BLEND_NAME]
        self.assertTrue(blend)
        for v in blend.values():
            self.assertAlmostEqual(v, 0.0, places=12)

    def test_un_actif_sans_historique_est_absent_partout(self):
        w = {"OK": win(funding=[0.001] * 60, returns=[0.01] * 60),
             "COURT": win(n=5)}
        out = compute_all(w)
        for name in SIGNALS:
            self.assertNotIn("COURT", out[name], name)

    def test_le_melange_n_utilise_que_les_actifs_communs(self):
        w = {"OK": win(funding=[0.001] * 60, returns=[0.01] * 60),
             "COURT": win(n=5)}
        self.assertNotIn("COURT", compute_all(w)[BLEND_NAME])


if __name__ == "__main__":
    unittest.main()
