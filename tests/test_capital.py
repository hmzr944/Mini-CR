"""Le capital immobilise est-il mesure, ou declare ?

Le denominateur de l'objectif du projet etait un nombre ecrit a la main. Ces
tests verifient qu'il vient desormais (a) du bareme de marge public d'OKX et
(b) d'un coussin de survie LU DANS LES PRIX, avec un aveu explicite quand
l'echantillon ne peut pas porter le quantile demande.

Le test central est `test_concorde_avec_la_force_brute` : l'implementation
rapide par deques monotones doit rendre exactement ce que rend la definition
naive. C'est la seule facon de savoir que le code calcule ce qu'on croit.
"""
import random
import unittest

from prism_v2.capital import (SurvivalBuffer, leg_capital, position_capital,
                              survival_buffer)
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.margin import MarginSchedule, parse_tiers


def _spec(inst_id, inst_type, ct_val, family):
    return InstrumentSpec(
        inst_id=inst_id, exchange="OKX", inst_type=inst_type,
        ct_type="inverse" if inst_type is InstrumentType.SWAP_INVERSE else "linear",
        base=inst_id.split("-")[0], quote=inst_id.split("-")[1],
        settle_ccy=inst_id.split("-")[0] if inst_type is InstrumentType.SWAP_INVERSE else "USDT",
        ct_val=ct_val,
        ct_val_ccy="USD" if inst_type is InstrumentType.SWAP_INVERSE else inst_id.split("-")[0],
        ct_mult=1.0, tick_size=0.1, lot_size=0.1, min_size=0.1, state="live",
        fetched_at="2026-01-01T00:00:00Z", lever=100.0, family=family)


BTC_INV = _spec("BTC-USD-SWAP", InstrumentType.SWAP_INVERSE, 100.0, "BTC-USD")
BTC_LIN = _spec("BTC-USDT-SWAP", InstrumentType.SWAP_LINEAR, 0.01, "BTC-USDT")
SCHED = MarginSchedule("BTC-USD", parse_tiers(
    [{"tier": "1", "minSz": "0", "maxSz": "2000", "imr": "0.01",
      "mmr": "0.004", "maxLever": "100"}]))


def _brute(prices, win_ms, direction=0):
    ts = [p[0] for p in prices]
    px = [p[1] for p in prices]
    out = []
    for i in range(len(ts)):
        if ts[i] + win_ms > ts[-1]:
            continue
        w = [px[k] for k in range(i, len(ts)) if ts[k] <= ts[i] + win_ms]
        up = (max(w) - px[i]) / px[i]
        down = (px[i] - min(w)) / px[i]
        out.append(up if direction > 0 else down if direction < 0 else max(up, down))
    return sorted(out)


class TestCoussinDeSurvie(unittest.TestCase):

    def test_concorde_avec_la_force_brute(self):
        """L'optimisation par deques ne doit rien changer au resultat."""
        random.seed(7)
        px, pts = 100.0, []
        for i in range(400):
            px *= 1.0 + random.gauss(0.0, 0.003)
            pts.append((1_700_000_000_000 + i * 1000, px))
        for horizon in (5, 30, 120):
            for direction in (-1, 0, 1):
                got = survival_buffer(pts, horizon, quantile=0.90,
                                      direction=direction)
                ref = _brute(pts, horizon * 1000, direction)
                self.assertIsNotNone(got)
                self.assertEqual(got.n_windows, len(ref))
                idx = min(len(ref) - 1, int(-(-0.90 * len(ref) // 1)) - 1)
                self.assertAlmostEqual(got.fraction, ref[idx], places=12,
                                       msg=f"h={horizon} d={direction}")

    def test_le_pire_traverse_pas_le_prix_de_sortie(self):
        """Une liquidation se declenche au plus bas traverse, pas a la sortie."""
        pts = [(0, 100.0), (1000, 80.0), (2000, 100.0)]
        b = survival_buffer(pts, 2, quantile=0.5, direction=-1)
        self.assertAlmostEqual(b.fraction, 0.20, places=9)

    def test_sens_long_et_court_sont_distincts(self):
        pts = [(0, 100.0), (1000, 130.0), (2000, 100.0)]
        haut = survival_buffer(pts, 2, quantile=0.5, direction=1)
        bas = survival_buffer(pts, 2, quantile=0.5, direction=-1)
        self.assertAlmostEqual(haut.fraction, 0.30, places=9)
        self.assertAlmostEqual(bas.fraction, 0.0, places=9)

    def test_echantillon_trop_court_est_signale(self):
        """Un quantile a 0,999 exige 1 000 fenetres disjointes."""
        pts = [(i * 1000, 100.0 + i) for i in range(500)]
        b = survival_buffer(pts, 60, quantile=0.999)
        self.assertFalse(b.reliable)
        self.assertIn("BORNE INFERIEURE", b.note)

    def test_echantillon_suffisant_est_declare_fiable(self):
        pts = [(i * 1000, 100.0) for i in range(20_000)]
        b = survival_buffer(pts, 10, quantile=0.99)
        self.assertTrue(b.reliable)
        self.assertEqual(b.note, "")

    def test_aucune_fenetre_complete_rend_none(self):
        self.assertIsNone(survival_buffer([(0, 100.0), (1000, 101.0)], 3600))
        self.assertIsNone(survival_buffer([], 60))
        self.assertIsNone(survival_buffer([(0, 100.0)], 0))


class TestCapitalDeJambe(unittest.TestCase):

    BUF = SurvivalBuffer(0.05, 0.99, 3600, 1000, 10.0, True)

    def test_capital_est_marge_plus_coussin(self):
        lc = leg_capital(BTC_INV, 100, 75_000.0, SCHED, self.BUF)
        self.assertAlmostEqual(lc.notional_usd, 10_000.0, places=6)
        self.assertAlmostEqual(lc.initial_margin_usd, 100.0, places=6)
        self.assertAlmostEqual(lc.buffer_usd, 500.0, places=6)
        self.assertAlmostEqual(lc.capital_usd, 600.0, places=6)
        self.assertAlmostEqual(lc.effective_leverage, 10_000.0 / 600.0, places=6)

    def test_le_levier_reel_est_tres_inferieur_au_levier_affiche(self):
        """100x affiche, mais il faut survivre entre l'entree et la sortie."""
        lc = leg_capital(BTC_INV, 100, 75_000.0, SCHED, self.BUF)
        self.assertLess(lc.effective_leverage, BTC_INV.lever)
        self.assertLess(lc.effective_leverage, 1.0 / 0.01)

    def test_sans_coussin_connu_pas_de_capital_connu(self):
        """Un coussin absent n'est pas un coussin nul."""
        self.assertIsNone(leg_capital(BTC_INV, 100, 75_000.0, SCHED, None))

    def test_hors_bareme_rend_none(self):
        self.assertIsNone(leg_capital(BTC_INV, 99_999, 75_000.0, SCHED, self.BUF))


class TestCapitalDePosition(unittest.TestCase):

    BUF = SurvivalBuffer(0.05, 0.99, 3600, 1000, 10.0, True)

    def _paire(self):
        a = leg_capital(BTC_INV, 100, 75_000.0, SCHED, self.BUF)
        b = leg_capital(BTC_LIN, 13.333333, 75_000.0,
                        MarginSchedule("BTC-USDT", SCHED.tiers), self.BUF)
        return [a, b]

    def test_sans_netting_les_capitaux_s_additionnent(self):
        p = position_capital(self._paire(), netting=False)
        self.assertAlmostEqual(p.capital_usd,
                               sum(l.capital_usd for l in self._paire()),
                               places=6)
        self.assertFalse(p.netting_assumed)

    def test_le_netting_est_une_borne_haute_jamais_le_defaut(self):
        sans = position_capital(self._paire())
        avec = position_capital(self._paire(), netting=True)
        self.assertFalse(sans.netting_assumed)
        self.assertLess(avec.capital_usd, sans.capital_usd)
        self.assertGreater(avec.effective_leverage, sans.effective_leverage)

    def test_notionnel_de_position_est_un_seul_cote(self):
        """Une paire couverte n'expose pas deux fois le notionnel."""
        p = position_capital(self._paire())
        self.assertAlmostEqual(p.notional_usd,
                               max(l.notional_usd for l in self._paire()),
                               places=6)

    def test_conversion_notionnel_vers_capital(self):
        """C'est la conversion que le projet omettait."""
        p = position_capital(self._paire())
        self.assertAlmostEqual(p.bps_per_day_on_capital(0.52),
                               0.52 * p.effective_leverage, places=9)
        self.assertGreater(p.bps_per_day_on_capital(0.52), 0.52)

    def test_une_jambe_non_fiable_rend_la_position_non_fiable(self):
        faible = SurvivalBuffer(0.05, 0.999, 3600, 10, 0.1, False, "court")
        legs = [leg_capital(BTC_INV, 100, 75_000.0, SCHED, self.BUF),
                leg_capital(BTC_LIN, 13.3, 75_000.0,
                            MarginSchedule("BTC-USDT", SCHED.tiers), faible)]
        self.assertFalse(position_capital(legs).reliable)

    def test_position_vide_rend_none(self):
        self.assertIsNone(position_capital([]))
        self.assertIsNone(position_capital([None, None]))


if __name__ == "__main__":
    unittest.main()


class TestPerteExacte(unittest.TestCase):
    """La perte d'une jambe se juge dans SA devise de marge, pas en prix."""

    def test_short_inverse_perte_bornee(self):
        from prism_v2.capital import loss_fraction
        from prism_v2.core_types import Direction
        self.assertAlmostEqual(loss_fraction(BTC_INV, Direction.SHORT, 100.0, 110.0),
                               0.10 / 1.10, places=9)
        # meme un doublement de prix ne fait pas perdre plus que la mise en coin
        self.assertLess(loss_fraction(BTC_INV, Direction.SHORT, 100.0, 1e6), 1.0)

    def test_long_inverse_perte_non_bornee(self):
        from prism_v2.capital import loss_fraction
        from prism_v2.core_types import Direction
        self.assertAlmostEqual(loss_fraction(BTC_INV, Direction.LONG, 100.0, 90.0),
                               0.10 / 0.90, places=9)
        self.assertGreater(loss_fraction(BTC_INV, Direction.LONG, 100.0, 90.0), 0.10)

    def test_lineaire_perte_egale_au_mouvement(self):
        from prism_v2.capital import loss_fraction
        from prism_v2.core_types import Direction
        self.assertAlmostEqual(loss_fraction(BTC_LIN, Direction.LONG, 100.0, 90.0),
                               0.10, places=9)

    def test_mouvement_favorable_ne_coute_rien(self):
        from prism_v2.capital import loss_fraction
        from prism_v2.core_types import Direction
        self.assertEqual(loss_fraction(BTC_INV, Direction.SHORT, 100.0, 90.0), 0.0)

    def test_le_sens_adverse_est_deduit_pas_demande(self):
        from prism_v2.capital import buffer_for_leg
        from prism_v2.core_types import Direction
        pts = [(0, 100.0), (1000, 120.0), (2000, 80.0), (3000, 100.0)]
        court = buffer_for_leg(BTC_INV, Direction.SHORT, pts, 3, quantile=0.5)
        long_ = buffer_for_leg(BTC_INV, Direction.LONG, pts, 3, quantile=0.5)
        # le court souffre de la hausse (+20 %), le long de la baisse (-20 %)
        self.assertAlmostEqual(court.fraction, 0.20 / 1.20, places=9)
        self.assertAlmostEqual(long_.fraction, 0.20 / 0.80, places=9)
        self.assertGreater(long_.fraction, court.fraction)
