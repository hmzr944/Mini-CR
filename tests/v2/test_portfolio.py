"""Position cible continue.

L'architecture est le livrable ; le signal, lui, a ete rejete par son holdout.
Ces tests verrouillent donc la MECANIQUE : causalite, neutralite, effet
amortisseur de la bande, comptabilite du PnL. Chacun vise une facon precise
dont ce type de moteur se ment a lui-meme.
"""
from __future__ import annotations

import unittest

from prism_v2.portfolio import (
    BookState, RiskConfig, SignalConfig, TargetConfig, TradingConfig,
    apply_step, expected_returns, max_reachable_gross, move_toward_target,
    summarise_book, target_weights, volatilities,
)

HOURS_PER_YEAR = 24 * 365


class TestSignal(unittest.TestCase):

    def test_un_long_gagne_quand_le_funding_est_negatif(self):
        mu = expected_returns({"A": [-0.001] * 24}, SignalConfig(
            cross_sectional_demean=False))
        self.assertGreater(mu["A"], 0.0)

    def test_historique_insuffisant_est_absent_jamais_zero(self):
        """Un zero se lirait comme « esperance neutre mesuree » et l'actif
        entrerait dans le portefeuille avec un poids legitime."""
        mu = expected_returns({"A": [-0.001] * 5}, SignalConfig(lookback_h=24))
        self.assertNotIn("A", mu)

    def test_le_centrage_transversal_annule_le_facteur_commun(self):
        """Si tout le marche paie le meme funding, aucune position ne doit
        en resulter : ce serait un pari directionnel deguise en carry."""
        f = {k: [-0.001] * 24 for k in "ABCD"}
        mu = expected_returns(f, SignalConfig(cross_sectional_demean=True))
        self.assertAlmostEqual(sum(mu.values()), 0.0, places=12)
        for v in mu.values():
            self.assertAlmostEqual(v, 0.0, places=12)

    def test_sans_centrage_le_facteur_commun_subsiste(self):
        f = {k: [-0.001] * 24 for k in "ABCD"}
        mu = expected_returns(f, SignalConfig(cross_sectional_demean=False))
        self.assertGreater(sum(mu.values()), 0.0)

    def test_le_plafond_borne_les_lectures_extremes(self):
        cfg = SignalConfig(cross_sectional_demean=False, max_abs_mu_annual=1.0)
        mu = expected_returns({"A": [-1.0] * 24}, cfg)
        self.assertLessEqual(abs(mu["A"]), 1.0 / HOURS_PER_YEAR + 1e-15)

    def test_le_signe_inverse_exactement_le_signal(self):
        f = {"A": [-0.001] * 24, "B": [0.002] * 24}
        base = SignalConfig(cross_sectional_demean=False)
        flip = SignalConfig(cross_sectional_demean=False, signal_sign=-1.0)
        a, b = expected_returns(f, base), expected_returns(f, flip)
        for k in f:
            self.assertAlmostEqual(a[k], -b[k], places=15)

    def test_signe_invalide_refuse(self):
        for bad in (0.0, 2.0, -0.5):
            with self.assertRaises(ValueError):
                SignalConfig(signal_sign=bad)


class TestRisque(unittest.TestCase):

    def test_le_plancher_de_vol_empeche_la_position_infinie(self):
        """Un marche fige afficherait une vol nulle et attirerait un poids
        infini par 1/sigma^2. C'est la faille classique de ce modele."""
        vol = volatilities({"MORT": [0.0] * 168}, RiskConfig())
        self.assertGreaterEqual(vol["MORT"], RiskConfig().min_vol_hourly)

    def test_serie_trop_courte_est_absente(self):
        self.assertEqual(volatilities({"A": [0.01] * 5}, RiskConfig()), {})


class TestCible(unittest.TestCase):

    def setUp(self):
        self.vol = {"A": 0.01, "B": 0.01, "C": 0.01, "D": 0.01}

    def test_neutralite_dollar(self):
        mu = {"A": 1e-4, "B": 5e-5, "C": -5e-5, "D": -1e-4}
        w = target_weights(mu, self.vol, TargetConfig(dollar_neutral=True))
        self.assertAlmostEqual(sum(w.values()), 0.0, places=9)

    def test_le_brut_respecte_le_levier_quand_il_est_atteignable(self):
        mu = {"A": 1e-4, "B": 5e-5, "C": -5e-5, "D": -1e-4}
        cfg = TargetConfig(gross_leverage=0.3, max_weight=0.10)
        self.assertLessEqual(cfg.gross_leverage, max_reachable_gross(4, cfg))
        w = target_weights(mu, self.vol, cfg)
        self.assertAlmostEqual(sum(abs(v) for v in w.values()), 0.3, places=6)

    def test_un_levier_inatteignable_rend_le_maximum_pas_un_resultat_faux(self):
        """Avec 4 actifs plafonnes a 0,10, un brut de 2,0 est impossible.
        La borne de risque gagne — et la fonction rend le maximum atteignable
        au lieu d'annuler silencieusement le levier demande.
        """
        mu = {"A": 1e-4, "B": 5e-5, "C": -5e-5, "D": -1e-4}
        cfg = TargetConfig(gross_leverage=2.0, max_weight=0.10)
        w = target_weights(mu, self.vol, cfg)
        gross = sum(abs(v) for v in w.values())
        self.assertAlmostEqual(gross, max_reachable_gross(4, cfg), places=6)
        self.assertLess(gross, cfg.gross_leverage)

    def test_le_poids_maximal_est_respecte(self):
        mu = {"A": 1.0, "B": 1e-9, "C": 1e-9, "D": 1e-9}
        w = target_weights(mu, self.vol, TargetConfig(max_weight=0.05))
        for v in w.values():
            self.assertLessEqual(abs(v), 0.05 + 1e-12)

    def test_a_esperance_egale_on_porte_moins_ce_qui_bouge_plus(self):
        mu = {"CALME": 1e-4, "AGITE": 1e-4}
        vol = {"CALME": 0.005, "AGITE": 0.05}
        # Levier volontairement atteignable : un brut sature ecreterait les
        # deux actifs au meme plafond et masquerait l'effet de la volatilite.
        cfg = TargetConfig(dollar_neutral=False, gross_leverage=0.05,
                           max_weight=0.10)
        w = target_weights(mu, vol, cfg)
        self.assertGreater(abs(w["CALME"]), abs(w["AGITE"]))

    def test_esperances_nulles_donnent_un_livre_vide(self):
        w = target_weights({k: 0.0 for k in self.vol}, self.vol, TargetConfig())
        for v in w.values():
            self.assertAlmostEqual(v, 0.0, places=12)

    def test_un_actif_sans_vol_connue_est_ecarte(self):
        w = target_weights({"A": 1e-4, "X": 1e-4}, {"A": 0.01}, TargetConfig())
        self.assertNotIn("X", w)


class TestBande(unittest.TestCase):
    """La bande est le coeur economique : sans elle, position continue et
    suite d'allers-retours seraient la meme chose."""

    def test_un_petit_ecart_ne_declenche_rien(self):
        cfg = TradingConfig(cost_bps=4.5, band_multiple=2.0)
        cur = {"A": 0.010}
        tgt = {"A": 0.010 + cfg.band_width() * 0.5}
        self.assertEqual(move_toward_target(cur, tgt, cfg)["A"], 0.010)

    def test_un_grand_ecart_comble_jusqu_au_bord_de_la_bande(self):
        cfg = TradingConfig(cost_bps=4.5, band_multiple=2.0)
        cur, tgt = {"A": 0.0}, {"A": 0.05}
        new = move_toward_target(cur, tgt, cfg)["A"]
        self.assertAlmostEqual(new, 0.05 - cfg.band_width(), places=12)
        self.assertLess(new, 0.05)

    def test_la_bande_s_elargit_avec_le_cout(self):
        self.assertGreater(TradingConfig(cost_bps=25.0).band_width(),
                           TradingConfig(cost_bps=4.5).band_width())

    def test_un_actif_disparu_de_la_cible_n_est_pas_liquide(self):
        """Liquider sur donnee manquante serait une decision de marche prise
        pour une raison technique."""
        new = move_toward_target({"A": 0.03}, {}, TradingConfig())
        self.assertEqual(new["A"], 0.03)

    def test_le_sens_du_mouvement_suit_l_ecart(self):
        cfg = TradingConfig()
        self.assertLess(move_toward_target({"A": 0.05}, {"A": -0.05}, cfg)["A"], 0.05)
        self.assertGreater(move_toward_target({"A": -0.05}, {"A": 0.05}, cfg)["A"], -0.05)


class TestLivre(unittest.TestCase):

    def test_rester_immobile_ne_coute_rien(self):
        book = BookState(weights={"A": 0.5})
        apply_step(book, {"A": 0.5}, {}, {}, TradingConfig())
        self.assertEqual(book.cost_paid, 0.0)
        self.assertEqual(book.turnover, 0.0)
        self.assertEqual(book.n_rebalances, 0)

    def test_le_cout_porte_sur_le_mouvement_pas_sur_la_position(self):
        """Toute la difference avec un aller-retour : une position de 0,5
        deja detenue ne coute rien ; seul le delta de 0,1 est facture."""
        book = BookState(weights={"A": 0.5})
        apply_step(book, {"A": 0.6}, {}, {}, TradingConfig(cost_bps=10.0))
        self.assertAlmostEqual(book.turnover, 0.1, places=12)
        self.assertAlmostEqual(book.cost_paid, 0.1 * 10.0 / 10_000.0, places=15)

    def test_un_long_paie_le_funding_positif(self):
        book = BookState(weights={"A": 1.0})
        apply_step(book, {"A": 1.0}, {}, {"A": 0.001}, TradingConfig())
        self.assertAlmostEqual(book.pnl_funding, -0.001, places=15)

    def test_un_short_encaisse_le_funding_positif(self):
        book = BookState(weights={"A": -1.0})
        apply_step(book, {"A": -1.0}, {}, {"A": 0.001}, TradingConfig())
        self.assertAlmostEqual(book.pnl_funding, +0.001, places=15)

    def test_le_rendement_frappe_la_NOUVELLE_position(self):
        """Appliquer le rendement a l'ancienne position reviendrait a trader
        a un prix qu'on n'a pas paye — un look-ahead d'un pas."""
        book = BookState(weights={"A": 0.0})
        apply_step(book, {"A": 1.0}, {"A": 0.02}, {}, TradingConfig(cost_bps=0.0))
        self.assertAlmostEqual(book.pnl_price, 0.02, places=15)

    def test_le_net_est_la_somme_des_trois_termes(self):
        book = BookState(weights={"A": 0.0})
        apply_step(book, {"A": 1.0}, {"A": 0.01}, {"A": 0.001},
                   TradingConfig(cost_bps=10.0))
        self.assertAlmostEqual(
            book.pnl_net, book.pnl_price + book.pnl_funding - book.cost_paid,
            places=15)
        self.assertLess(book.pnl_net, book.pnl_price)

    def test_les_expositions_sont_mesurees(self):
        book = BookState(weights={"A": 0.4, "B": -0.4})
        self.assertAlmostEqual(book.gross_exposure(), 0.8)
        self.assertAlmostEqual(book.net_exposure(), 0.0)


class TestMesures(unittest.TestCase):

    def test_le_critere_est_bien_en_bps_par_jour(self):
        book = BookState()
        book.pnl_price = 0.01
        book.equity_curve = [0.0, 0.01]
        s = summarise_book(book, hours=24, capital_usd=10_000.0)
        self.assertAlmostEqual(s["bps_par_jour"], 100.0, places=6)
        self.assertAlmostEqual(s["pnl_net_usd"], 100.0, places=6)

    def test_le_drawdown_est_positif_ou_nul(self):
        book = BookState()
        book.equity_curve = [0.0, 0.05, -0.02, 0.01]
        book.pnl_price = 0.01
        s = summarise_book(book, hours=48)
        self.assertGreater(s["drawdown_max_bps"], 0.0)

    def test_duree_nulle_refusee(self):
        with self.assertRaises(ValueError):
            summarise_book(BookState(), hours=0)


if __name__ == "__main__":
    unittest.main()
