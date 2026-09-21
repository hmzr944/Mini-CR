"""La frontiere d'atteinte dit-elle la verite quand elle n'a pas de solution ?

Le tableau de bord repondait a « quelles conditions seraient necessaires ? »
par un ratio : « il manque un facteur 8,2 ». Un ratio suppose qu'un facteur
multiplicatif existe. Quand l'edge brut est SOUS le cout, il n'en existe
aucun : le net est n * L * (e - c) avec (e - c) < 0, et augmenter n ou L
aggrave. Ces tests figent ce comportement — en particulier le refus de rendre
un grand nombre la ou la reponse est « aucune ».

Aucun test ici ne touche au reseau : seule l'arithmetique est figee.
"""
import unittest

from prism_v2.scans.conditions import (MAGNITUDES_BRUTES, OBJECTIF,
                                       net_bps_per_day, required_gross_bps,
                                       rotations_required)


class TestFrontiere(unittest.TestCase):

    def test_le_cout_est_un_plancher_additif(self):
        """Ni la rotation ni le levier ne font descendre l'exigence sous c."""
        c = 10.77
        for n in (1, 10, 100, 10_000):
            for L in (1.0, 9.43, 100.0):
                e = required_gross_bps(271.87, n, L, c)
                self.assertGreater(e, c)

    def test_l_exigence_converge_vers_le_cout_et_ne_l_atteint_jamais(self):
        c = 10.77
        e_petit = required_gross_bps(271.87, 1, 1.0, c)
        e_grand = required_gross_bps(271.87, 100_000, 9.43, c)
        self.assertLess(e_grand, e_petit)
        self.assertGreater(e_grand, c)

    def test_rotation_nulle_rend_none_et_non_l_infini(self):
        self.assertIsNone(required_gross_bps(271.87, 0, 9.43, 10.0))
        self.assertIsNone(required_gross_bps(271.87, 10, 0.0, 10.0))

    def test_sous_le_cout_aucune_rotation_ne_repond(self):
        """LE RESULTAT CENTRAL : e <= c n'a pas de solution, et le dit."""
        self.assertIsNone(rotations_required(271.87, 6.50, 10.77, 9.43))
        self.assertIsNone(rotations_required(271.87, 10.77, 10.77, 9.43))
        self.assertIsNotNone(rotations_required(271.87, 10.78, 10.77, 9.43))

    def test_sous_le_cout_la_rotation_aggrave(self):
        """Doubler la rotation double la perte. C'est le point du verdict."""
        p1 = net_bps_per_day(6.50, 10.77, 10, 9.43)
        p2 = net_bps_per_day(6.50, 10.77, 20, 9.43)
        self.assertLess(p1, 0.0)
        self.assertAlmostEqual(p2, 2 * p1, places=9)

    def test_la_solution_trouvee_est_bien_une_solution(self):
        """Boucle fermee : l'exigence rendue atteint exactement la cible."""
        seuil = OBJECTIF.binding_bps_per_day()
        e = required_gross_bps(seuil, 25, 9.43, 10.77)
        self.assertAlmostEqual(net_bps_per_day(e, 10.77, 25, 9.43), seuil,
                               places=6)
        r = rotations_required(seuil, e, 10.77, 9.43)
        self.assertAlmostEqual(r, 25.0, places=6)


class TestMagnitudes(unittest.TestCase):

    def test_aucune_magnitude_mesuree_ne_couvre_le_cout_taker(self):
        """Le fait economique central du projet, fige en test.

        Si une mesure future depasse le cout taker, ce test echoue — et c'est
        voulu : ce serait le premier resultat du projet a le faire, et il doit
        forcer une relecture, pas passer inapercu.
        """
        # 10,77 bps : la mesure directe la PLUS BASSE relevee (les dix grands
        # perpetuels, spread median 0,77 bps + 10 de frais taker). L'univers
        # derive du volume 24 h donne plutot 12,48. On garde la plus basse :
        # c'est la barre la plus severe pour ce test, donc la seule honnete.
        cout_taker_1_jambe = 10.77
        for nom, brut, _src in MAGNITUDES_BRUTES:
            self.assertLess(brut, cout_taker_1_jambe,
                            f"{nom} depasse le cout : a verifier, pas a "
                            f"ignorer")

    def test_chaque_magnitude_cite_sa_source(self):
        for nom, brut, src in MAGNITUDES_BRUTES:
            self.assertTrue(src, f"{nom} sans source")
            self.assertGreater(brut, 0.0)


if __name__ == "__main__":
    unittest.main()
