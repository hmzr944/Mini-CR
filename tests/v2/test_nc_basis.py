"""Gardes du module nc_basis — chacune verrouille une erreur deja payee.

Ces tests ne verifient pas que la mesure trouve un edge. Ils verifient qu'elle
ne peut pas en fabriquer un : conversion de devise, causalite stricte, blocs
disjoints, prix d'entree executable, barres sans echange.
"""
import math
import statistics as st
import unittest

from prism_v2.scans.nc_basis import (BAR_MS, FILL_CLOSE, FILL_OPEN,
                                     basis_series, benjamini_hochberg,
                                     causal_sigma, causal_z, session_of,
                                     staleness, trades, tstat)

M5 = BAR_MS["5m"]


def _rows(n, price=100.0, vol=1.0, start=0, step=M5):
    """Bougies [o,h,l,c,vol,volCcy,...] toutes identiques par defaut."""
    return {start + i * step: [price, price, price, price, vol, vol * price]
            for i in range(n)}


class TestConversionDeDevise(unittest.TestCase):
    """Le perp est cote en USDT, l'index en USD. Omettre la conversion
    fabrique une prime egale a la decote du peg — la classe d'erreur qui avait
    produit 1 902 faux survivants."""

    def test_peg_neutre_donne_une_base_nulle(self):
        perp = _rows(3, 100.0)
        idx = {t: [100.0, 100.0, 100.0, 100.0] for t in perp}
        peg = {t: 1.0 for t in perp}
        bas = basis_series(perp, idx, peg)
        for t in bas:
            self.assertAlmostEqual(bas[t][1], 0.0, places=9)

    def test_le_peg_est_bien_applique(self):
        """USDT a 0,99912 : un perp a 100 USDT vaut 99,912 USD. Contre un index
        a 99,912 USD la base est NULLE. Sans conversion elle vaudrait +8,8 bps."""
        perp = _rows(3, 100.0)
        idx = {t: [99.912] * 4 for t in perp}
        peg = {t: 0.99912 for t in perp}
        bas = basis_series(perp, idx, peg)
        for t in bas:
            self.assertAlmostEqual(bas[t][1], 0.0, places=6)
        sans_conversion = 1e4 * math.log(100.0 / 99.912)
        self.assertGreater(sans_conversion, 8.0)

    def test_l_ouverture_et_le_volume_sont_conserves(self):
        perp = {0: [99.0, 101.0, 98.0, 100.0, 7.0, 700.0]}
        bas = basis_series(perp, {0: [100.0] * 4}, {0: 1.0})
        self.assertEqual(bas[0][0], 100.0)   # cloture
        self.assertEqual(bas[0][2], 99.0)    # ouverture
        self.assertEqual(bas[0][3], 7.0)     # volume


class TestCausalite(unittest.TestCase):
    def test_causal_sigma_exclut_la_barre_courante(self):
        """Inclure t dans l'estimation de sigma laisse la dispersion du jour
        decider du seuil du jour : c'est un lookahead."""
        z = {i * M5: (100.0 if i == 50 else 0.0) for i in range(60)}
        sig = causal_sigma(z, window_bars=60, min_obs=5, bar_ms=M5)
        self.assertAlmostEqual(sig[50 * M5], 0.0, places=9)
        self.assertGreater(sig[51 * M5], 0.0)

    def test_causal_z_n_utilise_que_le_passe(self):
        """Une valeur future ne doit pas modifier un z deja calcule."""
        base = {i * M5: (1.0, float(i), 1.0, 1.0) for i in range(40)}
        z1 = causal_z(base, 10, M5)
        base[40 * M5] = (1.0, 10_000.0, 1.0, 1.0)
        z2 = causal_z(base, 10, M5)
        for t in z1:
            self.assertAlmostEqual(z1[t], z2[t], places=9)

    def test_sigma_incrementale_egale_la_version_naive(self):
        import random
        random.seed(7)
        z = {i * M5: random.gauss(0, 3) for i in range(400)}
        sig = causal_sigma(z, 50, 20, M5)
        ts = sorted(z)
        for i, t in enumerate(ts):
            hist = [z[s] for s in ts[:i] if s > t - 50 * M5]
            if len(hist) < 20:
                self.assertNotIn(t, sig)
            else:
                self.assertAlmostEqual(sig[t], st.pstdev(hist), places=8)


class TestExecution(unittest.TestCase):
    def _fixture(self, prices, vols=None):
        n = len(prices)
        vols = vols if vols is not None else [1.0] * n
        bas = {i * M5: (prices[i], 0.0, prices[i], vols[i]) for i in range(n)}
        z = {i * M5: 100.0 for i in range(n)}
        sig = {i * M5: 1.0 for i in range(n)}
        return bas, z, sig

    def test_blocs_disjoints(self):
        """Une position occupe sa fenetre : aucune autre n'y entre. Sans cela,
        h recouvrements comptent la meme trajectoire h fois."""
        bas, z, sig = self._fixture([100.0] * 40)
        tr = trades(bas, z, sig, 1.0, 4, 0, 0.0, M5, FILL_CLOSE)
        ts = [t for t, _, _ in tr]
        for a, b in zip(ts, ts[1:]):
            self.assertGreaterEqual(b - a, 4 * M5)

    def test_fill_open_entre_apres_la_barre_du_signal(self):
        """La cloture de la barre du signal est le prix qui a DECLENCHE le
        signal. Entrer dessus suppose d'avoir vendu au sommet de la meche."""
        # La meche n'existe QUE dans la cloture de la barre du signal : le
        # marche n'y a jamais rouvert. (cloture, base, ouverture, volume)
        bas = {0 * M5: (110.0, 0.0, 100.0, 1.0),     # barre du signal : meche
               1 * M5: (100.0, 0.0, 100.0, 1.0),
               2 * M5: (100.0, 0.0, 100.0, 1.0)}
        z, sig = {0: 100.0}, {0: 1.0}
        close = trades(bas, z, sig, 1.0, 1, 0, 0.0, M5, FILL_CLOSE)
        opened = trades(bas, z, sig, 1.0, 1, 0, 0.0, M5, FILL_OPEN)
        self.assertEqual(len(close), 1)
        self.assertEqual(len(opened), 1)
        # Vendre la meche a 110 et racheter a 100 « rapporte » 953 bps ...
        self.assertAlmostEqual(close[0][1], 1e4 * math.log(110.0 / 100.0),
                               places=6)
        # ... alors qu'aucun prix reellement offert ne l'a jamais permis.
        self.assertAlmostEqual(opened[0][1], 0.0, places=9)

    def test_barre_sans_echange_exclue(self):
        """Une barre a volume nul recopie la cloture precedente : la reversion
        qu'on y mesure est une reversion de COTATION."""
        bas, z, sig = self._fixture([100.0] * 10, vols=[0.0] * 10)
        self.assertEqual(trades(bas, z, sig, 1.0, 1, 0, 0.0, M5, FILL_OPEN), [])
        bas2, z2, sig2 = self._fixture([100.0] * 10)
        self.assertGreater(len(trades(bas2, z2, sig2, 1.0, 1, 0, 0.0, M5,
                                      FILL_OPEN)), 0)

    def test_le_cout_est_soustrait_du_brut(self):
        bas, z, sig = self._fixture([100.0] * 10)
        tr = trades(bas, z, sig, 1.0, 1, 0, 11.4, M5, FILL_CLOSE)
        self.assertTrue(tr)
        for _, g, n in tr:
            self.assertAlmostEqual(g - n, 11.4, places=9)

    def test_le_signe_fade_l_ecart(self):
        """z > 0 (perp riche) doit VENDRE : une hausse ensuite est une perte."""
        prices = [100.0, 100.0, 101.0]
        bas = {i * M5: (prices[i], 0.0, prices[i], 1.0) for i in range(3)}
        tr = trades(bas, {0: 50.0}, {0: 1.0}, 1.0, 2, 0, 0.0, M5, FILL_CLOSE)
        self.assertLess(tr[0][1], 0.0)
        tr = trades(bas, {0: -50.0}, {0: 1.0}, 1.0, 2, 0, 0.0, M5, FILL_CLOSE)
        self.assertGreater(tr[0][1], 0.0)


class TestFraicheurEtStats(unittest.TestCase):
    def test_staleness_detecte_les_prix_figes(self):
        rows = _rows(10, 100.0, vol=0.0)
        zv, zr = staleness(rows, M5)
        self.assertAlmostEqual(zv, 1.0)
        self.assertAlmostEqual(zr, 1.0)

    def test_staleness_depend_de_la_taille_de_barre(self):
        """Lues comme des barres horaires, des bougies de 5 min n'ont aucune
        paire consecutive : le taux ne doit pas passer pour 0 %."""
        rows = _rows(10, 100.0)
        _, zr_bon = staleness(rows, M5)
        _, zr_faux = staleness(rows, BAR_MS["1H"])
        self.assertAlmostEqual(zr_bon, 1.0)
        self.assertAlmostEqual(zr_faux, 1.0)   # aucune paire -> declare perime

    def test_benjamini_hochberg_porte_sur_tous_les_tests(self):
        self.assertEqual(benjamini_hochberg([0.001] + [0.9] * 9, 0.10),
                         [True] + [False] * 9)
        self.assertEqual(sum(benjamini_hochberg([0.02] * 100, 0.10)), 100)
        self.assertEqual(sum(benjamini_hochberg([0.06] + [0.9] * 99, 0.10)), 0)

    def test_tstat(self):
        m, se, t = tstat([1.0] * 10)
        self.assertAlmostEqual(m, 1.0)
        self.assertEqual(t, 0.0)          # ecart-type nul : pas de t
        m, se, t = tstat([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(m, 2.5)
        self.assertGreater(t, 0.0)


class TestSession(unittest.TestCase):
    def test_sessions_americaines(self):
        import datetime as dt

        def ms(y, mo, d, h):
            return int(dt.datetime(y, mo, d, h).replace(
                tzinfo=dt.timezone.utc).timestamp() * 1000)

        self.assertEqual(session_of(ms(2026, 9, 16, 15)), "RTH")    # mer 11h ET
        self.assertEqual(session_of(ms(2026, 9, 16, 3)), "DARK")    # mer 23h ET
        self.assertEqual(session_of(ms(2026, 9, 16, 10)), "EXT")    # pre-marche
        self.assertEqual(session_of(ms(2026, 9, 19, 15)), "WKND")   # samedi
        self.assertEqual(session_of(ms(2026, 9, 21, 3)), "WKND")    # lundi 03h


if __name__ == "__main__":
    unittest.main()
