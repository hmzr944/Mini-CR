"""Le croisement d'instruments peut-il lire le futur ?

C'est le risque propre a ce module, et il est plus sournois que le
look-ahead habituel. Les lignes du panneau ne sont pas alignees par indice :
a un meme indice, l'horodatage varie jusqu'a 2,3 secondes d'un instrument a
l'autre. Croiser par indice ferait donc lire, a l'instrument dont l'horloge
retarde, des valeurs que les autres n'ont publiees qu'APRES son propre
instant. Sur une question d'avance-retard a l'echelle de la seconde, c'est
exactement l'artefact qu'on croirait avoir trouve.

Ces tests verrouillent les trois proprietes qui l'empechent :

  1. la grille ne retient jamais qu'une ligne d'horodatage <= T ;
  2. apres reindexation, un indice designe le meme instant partout ;
  3. les agregats transversaux excluent l'instrument lui-meme.
"""
import random
import unittest

from prism_v2.policy.crosssec import (CROSS_FEATURES, CrossSection, Grid,
                                      align, resample)


def _serie(debut, n, pas, px0=100.0, seed=0):
    rng = random.Random(seed)
    rows, px = [], px0
    for i in range(n):
        px *= 1.0 + rng.gauss(0, 0.0005)
        rows.append((debut + i * pas, round(px * 0.9999, 6),
                     round(px * 1.0001, 6), rng.randint(1, 9),
                     rng.randint(1, 9), rng.random() * 10, rng.random() * 10))
    return rows


class TestGrilleCausale(unittest.TestCase):

    def setUp(self):
        # Trois instruments DESALIGNES a dessein : pas differents, departs
        # differents. C'est la situation reelle du panneau.
        self.panel = {
            "A": _serie(1_000, 500, 300, seed=1),
            "B": _serie(1_150, 500, 310, seed=2),
            "C": _serie(1_075, 500, 290, seed=3),
        }
        self.grid = align(self.panel, 300)

    def test_la_ligne_retenue_precede_toujours_l_instant_de_grille(self):
        """La propriete fondamentale : jamais une ligne posterieure a T."""
        for inst, rows in self.panel.items():
            for k, i in enumerate(self.grid.idx[inst]):
                if i < 0:
                    continue
                self.assertLessEqual(
                    rows[i][0], self.grid.times[k],
                    f"{inst} lit une ligne posterieure a l'instant de grille")

    def test_la_ligne_retenue_est_la_plus_recente_disponible(self):
        """Pas de retard gratuit : c'est bien la DERNIERE ligne connue."""
        for inst, rows in self.panel.items():
            for k, i in enumerate(self.grid.idx[inst]):
                if i < 0 or i + 1 >= len(rows):
                    continue
                self.assertGreater(
                    rows[i + 1][0], self.grid.times[k],
                    f"{inst} ignore une ligne pourtant deja publiee")

    def test_la_grille_commence_quand_tous_ont_publie(self):
        self.assertGreaterEqual(self.grid.times[0],
                                max(r[0][0] for r in self.panel.values()))

    def test_apres_reindexation_un_indice_est_un_instant(self):
        r = resample(self.panel, self.grid)
        for k in (0, len(self.grid) // 2, len(self.grid) - 1):
            ts = {r[i][k][0] for i in r if r[i][k]}
            self.assertEqual(len(ts), 1, f"indices non alignes a k={k}")

    def test_le_flux_est_conserve_sans_perte_ni_double_compte(self):
        """Chaque transaction source apparait une fois et une seule."""
        r = resample(self.panel, self.grid)
        for inst, rows in self.panel.items():
            dernier = max(i for i in self.grid.idx[inst])
            premier = min(i for i in self.grid.idx[inst] if i >= 0)
            attendu = sum(x[5] + x[6] for x in rows[:dernier + 1])
            obtenu = sum(x[5] + x[6] for x in r[inst] if x)
            # Le resample agrege depuis la ligne 0 jusqu'a la derniere
            # franchie : tout ce qui precede le premier point de grille y
            # est inclus une fois.
            self.assertAlmostEqual(obtenu, attendu, places=6,
                                   msg=f"flux perdu ou double sur {inst}")
            self.assertGreaterEqual(premier, 0)

    def test_un_panneau_sans_recouvrement_est_refuse(self):
        with self.assertRaises(ValueError):
            align({"A": _serie(0, 10, 300), "B": _serie(1_000_000, 10, 300)},
                  300)


class TestExclusionDeSoi(unittest.TestCase):
    """Un agregat qui contient l'instrument se predit lui-meme."""

    def setUp(self):
        self.panel = {n: _serie(1_000, 400, 300, seed=i)
                      for i, n in enumerate("ABCD")}
        self.grid = align(self.panel, 300)
        self.r = resample(self.panel, self.grid)
        self.xs = CrossSection(self.r, Grid(self.grid.times,
                                            {i: tuple(range(len(self.grid)))
                                             for i in self.r}))

    def test_l_agregat_ignore_l_instrument_lui_meme(self):
        """Changer SEULEMENT A ne doit pas changer ce que A voit du marche."""
        vu = self.xs.at("A", 300, 20, 200)
        self.assertIsNotNone(vu)

        modifie = dict(self.r)
        modifie["A"] = [(t, b * 2.0, a * 2.0, bs, asz, bu, su)
                        for (t, b, a, bs, asz, bu, su) in self.r["A"]]
        xs2 = CrossSection(modifie, Grid(self.grid.times,
                                         {i: tuple(range(len(self.grid)))
                                          for i in modifie}))
        vu2 = xs2.at("A", 300, 20, 200)
        for f in ("mkt_ret_court", "mkt_ret_long", "mkt_ofi", "mkt_disp"):
            self.assertAlmostEqual(vu[f], vu2[f], places=9,
                                   msg=f"{f} contient l'instrument lui-meme")

    def test_le_residu_est_bien_propre_moins_marche(self):
        vu = self.xs.at("A", 300, 20, 200)
        mids = self.xs.mids["A"]
        propre = (mids[300] - mids[280]) / mids[280] * 10_000.0
        self.assertAlmostEqual(vu["residu_court"],
                               propre - vu["mkt_ret_court"], places=9)

    def test_toutes_les_variables_annoncees_sont_presentes(self):
        vu = self.xs.at("A", 300, 20, 200)
        self.assertEqual(set(vu), set(CROSS_FEATURES))

    def test_un_historique_insuffisant_rend_none(self):
        self.assertIsNone(self.xs.at("A", 5, 20, 200))


if __name__ == "__main__":
    unittest.main()
