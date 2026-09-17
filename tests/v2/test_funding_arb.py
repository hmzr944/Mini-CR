"""Arbitrage de funding inter-venues.

Le test central est `test_cadence_deduite_et_non_supposee` : coder 8 h en dur
pour OKX faussait le signal d'un facteur 2 a 8 sur 90 des 142 actifs, et le
symptome (un differentiel lu a +3879 %/an) ressemblait a une opportunite
plutot qu'a un bug. Les autres tests protegent la comptabilite des paiements,
qui est l'autre endroit ou ce genre de mesure ment facilement.
"""
from __future__ import annotations

import unittest

from prism_v2.funding_arb import (
    DEFAULT_HALF_SPREAD_BPS, HOUR_MS, N_CROSSINGS, Asset, VenueFunding,
    infer_period_h, non_overlapping, round_trip_cost_bps, summarise,
)

H = HOUR_MS


def series(n, rate, period_h=1, t0=0):
    return [(t0 + i * period_h * H, rate) for i in range(n)]


def asset(hl_rate=0.0, okx_rate=0.0, n_hl=400, okx_period=8, oi=1e6):
    return Asset("X", {
        "hl": series(n_hl, hl_rate, 1),
        "okx": series(n_hl // okx_period, okx_rate, okx_period),
        "meta": {"oi_usd": oi, "vol24_usd": oi},
    })


class TestCadence(unittest.TestCase):

    def test_cadence_deduite_et_non_supposee(self):
        """OKX n'a pas une cadence unique : 4 h sur la majorite des actifs."""
        self.assertEqual(infer_period_h([0, 4 * H, 8 * H, 12 * H]), 4.0)
        self.assertEqual(infer_period_h([0, 8 * H, 16 * H]), 8.0)
        self.assertEqual(infer_period_h([0, H, 2 * H]), 1.0)

    def test_cadence_robuste_aux_paiements_manquants(self):
        """Un trou ne doit pas deplacer la mediane."""
        self.assertEqual(infer_period_h([0, 8 * H, 24 * H, 32 * H, 40 * H]), 8.0)

    def test_une_mauvaise_cadence_fausse_le_signal_proportionnellement(self):
        """Le bug reel, reproduit : meme serie, deux cadences declarees."""
        pays = series(50, 0.0008, 4)
        vrai = VenueFunding(pays)                      # deduit 4 h
        faux = VenueFunding(pays, period_h=8.0)        # suppose 8 h
        self.assertEqual(vrai.period_h, 4.0)
        self.assertAlmostEqual(
            vrai.last_rate_per_hour_at(10 ** 12) or 0,
            2.0 * (faux.last_rate_per_hour_at(10 ** 12) or 0), places=12)

    def test_cadence_indeduisible_refusee(self):
        with self.assertRaises(ValueError):
            infer_period_h([42])


class TestCausalite(unittest.TestCase):

    def test_le_signal_n_utilise_aucun_paiement_futur(self):
        v = VenueFunding(series(10, 0.001, 1))
        self.assertIsNone(v.last_rate_per_hour_at(-1))
        self.assertIsNotNone(v.last_rate_per_hour_at(0))

    def test_absence_d_historique_renvoie_none_jamais_zero(self):
        """Un zero se lirait comme « differentiel nul », donc comme une
        information. L'absence d'information doit rester un refus.
        """
        v = VenueFunding(series(3, 0.001, 1, t0=100 * H))
        self.assertIsNone(v.last_rate_per_hour_at(0))

    def test_entree_strictement_apres_le_signal(self):
        a = asset(hl_rate=0.0, okx_rate=0.008)
        tr = a.simulate(50 * H, 24, DEFAULT_HALF_SPREAD_BPS)
        self.assertIsNotNone(tr)
        self.assertEqual(tr.entry_ts, tr.signal_ts + HOUR_MS)
        self.assertGreater(tr.entry_ts, tr.signal_ts)

    def test_fenetre_incomplete_refusee(self):
        a = asset(n_hl=100)
        self.assertIsNone(a.simulate(90 * H, 168, DEFAULT_HALF_SPREAD_BPS))


class TestComptabiliteDesPaiements(unittest.TestCase):

    def test_seuls_les_paiements_horodates_dans_la_fenetre_comptent(self):
        """Une position de 8 h ne touche pas forcement un paiement OKX :
        cela depend de l'heure d'entree. Compter « 8 h de taux » au lieu des
        paiements reels surestimerait le revenu.
        """
        v = VenueFunding(series(10, 0.001, 8))     # paiements a 0,8,16,...
        self.assertAlmostEqual(v.sum_between(1 * H, 7 * H), 0.0)
        self.assertAlmostEqual(v.sum_between(1 * H, 8 * H), 0.001)
        self.assertAlmostEqual(v.sum_between(0, 16 * H), 0.002)

    def test_borne_basse_exclue_borne_haute_incluse(self):
        v = VenueFunding(series(5, 0.001, 8))
        self.assertAlmostEqual(v.sum_between(0, 8 * H), 0.001)
        self.assertAlmostEqual(v.sum_between(8 * H, 8 * H), 0.0)

    def test_le_sens_de_la_position_suit_le_signe_du_differentiel(self):
        """d > 0 => short OKX / long HL ; on encaisse |d| si cela tient."""
        haut = asset(hl_rate=0.0, okx_rate=0.008)
        tr = haut.simulate(50 * H, 72, 0.0)
        self.assertEqual(tr.direction, 1)
        self.assertGreater(tr.gross_bps, 0.0)
        bas = asset(hl_rate=0.001, okx_rate=0.0)
        tr2 = bas.simulate(50 * H, 72, 0.0)
        self.assertEqual(tr2.direction, -1)
        self.assertGreater(tr2.gross_bps, 0.0)

    def test_un_differentiel_nul_ne_rapporte_rien(self):
        a = asset(hl_rate=0.001, okx_rate=0.008)   # 0.008/8h == 0.001/h
        tr = a.simulate(50 * H, 72, 0.0)
        self.assertAlmostEqual(tr.gross_bps, 0.0, places=9)


class TestCouts(unittest.TestCase):

    def test_quatre_traversees_comptees(self):
        self.assertEqual(N_CROSSINGS, 4)
        self.assertAlmostEqual(round_trip_cost_bps(0.0), 2 * (5.0 + 4.5))
        self.assertAlmostEqual(round_trip_cost_bps(5.0), 19.0 + 20.0)

    def test_le_spread_n_est_jamais_implicitement_nul(self):
        self.assertGreater(DEFAULT_HALF_SPREAD_BPS, 0.0)
        self.assertGreater(round_trip_cost_bps(), round_trip_cost_bps(0.0))

    def test_spread_negatif_refuse(self):
        with self.assertRaises(ValueError):
            round_trip_cost_bps(-1.0)

    def test_le_cout_est_soustrait_du_brut(self):
        a = asset(hl_rate=0.0, okx_rate=0.008)
        tr = a.simulate(50 * H, 72, 5.0)
        self.assertAlmostEqual(tr.net_bps_notional,
                               tr.gross_bps - tr.cost_bps, places=9)


class TestCapital(unittest.TestCase):

    def test_le_capital_est_deux_fois_le_notionnel_a_1x(self):
        """Les deux jambes sont margees separement : aucun netting entre
        venues n'existe."""
        a = asset(hl_rate=0.0, okx_rate=0.008)
        tr = a.simulate(50 * H, 72, 0.0)
        self.assertAlmostEqual(tr.net_bps_capital(1.0), tr.net_bps_notional / 2)

    def test_le_levier_multiplie_mais_ne_cree_pas_d_edge(self):
        a = asset(hl_rate=0.0, okx_rate=0.008)
        tr = a.simulate(50 * H, 72, 0.0)
        self.assertAlmostEqual(tr.net_bps_capital(10.0),
                               10.0 * tr.net_bps_capital(1.0))

    def test_un_net_negatif_le_reste_a_tout_levier(self):
        a = asset(hl_rate=0.0, okx_rate=0.0)
        tr = a.simulate(50 * H, 8, DEFAULT_HALF_SPREAD_BPS)
        self.assertLess(tr.net_bps_notional, 0.0)
        for lev in (1.0, 5.0, 20.0):
            self.assertLess(tr.bps_per_day(lev), 0.0)

    def test_levier_invalide_refuse(self):
        a = asset(hl_rate=0.0, okx_rate=0.008)
        tr = a.simulate(50 * H, 72, 0.0)
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                tr.net_bps_capital(bad)


class TestRecouvrement(unittest.TestCase):

    def test_les_positions_qui_se_recouvrent_sont_ecartees(self):
        """Entrer a chaque heure produit des observations non independantes ;
        toute dispersion calculee dessus serait fausse.
        """
        a = asset(hl_rate=0.0, okx_rate=0.008)
        trades = [a.simulate(t * H, 24, 0.0) for t in range(50, 60)]
        trades = [t for t in trades if t]
        self.assertGreater(len(trades), 5)
        kept = non_overlapping(trades)
        self.assertLess(len(kept), len(trades))
        kept.sort(key=lambda t: t.entry_ts)
        for x, y in zip(kept, kept[1:]):
            self.assertGreaterEqual(y.entry_ts, x.exit_ts)

    def test_resume_vide_ne_fabrique_pas_de_chiffre(self):
        self.assertEqual(summarise([]), {"n": 0})


if __name__ == "__main__":
    unittest.main()
