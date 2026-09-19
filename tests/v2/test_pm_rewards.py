"""Gardes du module pm_rewards — chacune verrouille une erreur deja payee.

Ces tests ne verifient pas qu'une recompense est rentable. Ils verifient que
la mesure ne peut pas fabriquer un revenu sans le cout qui va avec.
"""
import unittest

from prism_v2.scans.pm_rewards import (book_score, market_economics, my_score,
                                       simulate)


def _lv(px, sz):
    return {"price": str(px), "size": str(sz)}


def _hist(prices):
    return [{"t": i, "p": p} for i, p in enumerate(prices)]


class TestScore(unittest.TestCase):
    """La formule publiee : S(v, s) = v * ((maxSpread - s)/maxSpread)^2."""

    def test_au_milieu_le_poids_vaut_un(self):
        self.assertAlmostEqual(my_score(100.0, 0.0, 4.0), 100.0)

    def test_le_poids_decroit_en_carre(self):
        self.assertAlmostEqual(my_score(100.0, 2.0, 4.0), 25.0)
        self.assertAlmostEqual(my_score(100.0, 3.0, 4.0), 6.25)

    def test_au_dela_de_la_bande_le_score_est_nul(self):
        self.assertEqual(my_score(100.0, 4.1, 4.0), 0.0)

    def test_carnet_hors_bande_ignore(self):
        bids = [_lv(0.50, 1000), _lv(0.40, 1_000_000)]   # 0.40 est a 10 c
        q = book_score(bids, 0.505, 4.0, "bid")
        self.assertLess(q, 1000.0)      # le million hors bande ne compte pas
        self.assertGreater(q, 0.0)

    def test_le_cote_compte(self):
        """Un ordre au-dessus du milieu n'est pas un bid valide."""
        self.assertEqual(book_score([_lv(0.60, 100)], 0.50, 4.0, "bid"), 0.0)
        self.assertGreater(book_score([_lv(0.51, 100)], 0.50, 4.0, "ask"), 0.0)


class TestSimulation(unittest.TestCase):
    def test_un_prix_immobile_ne_remplit_rien(self):
        pnl, inv, peak, fills = simulate(_hist([0.50] * 200), 1.0, 100.0)
        self.assertEqual(fills, 0)
        self.assertEqual(inv, 0.0)
        self.assertAlmostEqual(pnl, 0.0)

    def test_une_tendance_remplit_toujours_du_mauvais_cote(self):
        """C'est la selection adverse : un ordre passif n'est touche que
        lorsque le prix vient a lui, donc lorsqu'il bouge CONTRE lui."""
        # Le pas doit depasser la distance de cotation : le simulateur recote
        # a chaque minute, donc seule une variation SUPERIEURE a `s` dans une
        # minute vient chercher l'ordre. Une derive plus lente est suivie, pas
        # subie — c'est ce que fait un teneur de marche qui annule et replace.
        montee = [0.30 + 0.02 * i for i in range(30)]
        pnl, inv, _peak, fills = simulate(_hist(montee), 1.0, 100.0)
        self.assertGreater(fills, 0)
        self.assertLess(inv, 0.0)        # on a vendu dans une hausse
        self.assertLess(pnl, 0.0)        # et on l'a paye

    def test_le_plafond_d_inventaire_borne_l_exposition(self):
        montee = [0.10 + 0.02 * i for i in range(40)]
        _pnl, _inv, peak, _f = simulate(_hist(montee), 1.0, 50.0, inv_cap=100.0)
        self.assertLessEqual(peak, 100.0)

    def test_coter_plus_loin_remplit_moins(self):
        p = [0.50 + 0.01 * ((i % 7) - 3) for i in range(300)]
        _a, _b, _c, proche = simulate(_hist(p), 0.5, 100.0)
        _a, _b, _c, loin = simulate(_hist(p), 3.0, 100.0)
        self.assertGreater(proche, loin)


class TestEconomieComplete(unittest.TestCase):
    """La garde centrale : la taille COTEE et la taille REMPLIE sont la meme
    grandeur. Une premiere version cotait 1 000 parts et n'en remplissait que
    20 : l'inventaire en ressortait cinquante fois trop petit et le net
    paraissait positif partout."""

    def _m(self, **kw):
        m = {"q": "test", "rate": 100.0, "maxSpread": 4.0, "minSize": 10.0,
             "bids": [_lv(0.49, 500)], "asks": [_lv(0.51, 500)],
             "hist": _hist([0.50 + 0.02 * i for i in range(20)]),
             "end": None, "vol24": 0.0}
        m.update(kw)
        return m

    def test_la_taille_cotee_respecte_le_capital(self):
        """Coter v parts des deux cotes immobilise v dollars ; porter
        inv_cap_mult fois v en inventaire en exige autant de plus."""
        r = market_economics(self._m(), 1.0, 1000.0, inv_cap_mult=3.0)
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r["qm"], my_score(250.0, 1.0, 4.0))

    def test_l_exposition_reste_bornee_par_le_capital(self):
        """`inv_cap_mult` echange la taille COTEE contre le plafond
        d'INVENTAIRE a exposition totale constante : v*(1+mult) = capital.
        La perte n'est donc pas monotone en `mult` — elle depend de la
        trajectoire — mais l'exposition, elle, ne doit jamais depasser le
        capital. C'est cet invariant qui protege, pas l'ordre des pertes."""
        for mult in (1.0, 3.0, 9.0):
            r = market_economics(self._m(), 1.0, 1000.0, inv_cap_mult=mult)
            self.assertIsNotNone(r)
            v = 1000.0 / (1.0 + mult)
            self.assertAlmostEqual(r["qm"], my_score(v, 1.0, 4.0))
            # une part vaut au plus 1 USD : l'inventaire de pointe plus la
            # taille cotee ne peuvent pas exceder le capital engage
            self.assertLessEqual(r["peak"] + v, 1000.0 + 1e-6)

    def test_une_tendance_coute_quelle_que_soit_la_repartition(self):
        for mult in (1.0, 3.0, 9.0):
            r = market_economics(self._m(), 1.0, 1000.0, inv_cap_mult=mult)
            self.assertLess(r["pnl"], 0.0)

    def test_taille_minimale_refusee_et_non_contournee(self):
        self.assertIsNone(market_economics(self._m(minSize=1e9), 1.0, 1000.0))

    def test_hors_bande_aucun_score_donc_aucune_recompense(self):
        self.assertIsNone(market_economics(self._m(), 9.0, 1000.0))

    def test_la_part_se_dilue_avec_la_concurrence(self):
        seul = market_economics(self._m(bids=[_lv(0.49, 1)],
                                        asks=[_lv(0.51, 1)]), 1.0, 1000.0)
        foule = market_economics(self._m(bids=[_lv(0.499, 10_000_000)],
                                         asks=[_lv(0.501, 10_000_000)]),
                                 1.0, 1000.0)
        self.assertGreater(seul["share"], 0.9)
        self.assertLess(foule["share"], 0.01)
        self.assertLess(foule["reward"], seul["reward"])

    def test_le_net_additionne_recompense_et_inventaire(self):
        r = market_economics(self._m(), 1.0, 1000.0)
        self.assertAlmostEqual(r["net"], r["reward"] + r["pnl"])


if __name__ == "__main__":
    unittest.main()
