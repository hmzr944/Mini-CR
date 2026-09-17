"""Flux de liquidation force — regle gelee du protocole Phase C.

Le test central est `test_la_mediane_ne_voit_jamais_la_minute_courante` : la
mesure exploratoire normalisait l'ampleur par la mediane de TOUTE la periode,
donc par de l'information future. C'est ce defaut qui rendait « ample » un
flux qu'on n'aurait pas su qualifier sur le moment.
"""
from __future__ import annotations

import unittest

from prism_v2.liquidation_flow import (
    AMPLITUDE_THRESHOLD, HOLD_MINUTES, IMBALANCE_THRESHOLD, MINUTE_MS,
    ROUND_TRIP_FEE_BPS, SIDE_BUY, SIDE_SELL, TRAILING_MINUTES, Liquidation,
    MinuteFlow, TrailingMedian, aggregate_minutes, evaluate, scan_instrument,
)

M = MINUTE_MS


def flat(n, total=100.0, sell_share=1.0):
    return {i * M: MinuteFlow(i * M, total * sell_share,
                              total * (1 - sell_share)) for i in range(n)}


class TestLiquidation(unittest.TestCase):

    def test_le_notionnel_est_taille_fois_prix(self):
        self.assertAlmostEqual(Liquidation(0, SIDE_SELL, 2.0, 50.0).notional(),
                               100.0)

    def test_un_side_inconnu_est_refuse(self):
        with self.assertRaises(ValueError):
            Liquidation(0, "maybe", 1.0, 10.0)

    def test_un_prix_nul_est_refuse(self):
        with self.assertRaises(ValueError):
            Liquidation(0, SIDE_SELL, 1.0, 0.0)


class TestAgregation(unittest.TestCase):

    def test_les_liquidations_tombent_dans_leur_minute(self):
        liqs = [Liquidation(30_000, SIDE_SELL, 1.0, 100.0),
                Liquidation(59_999, SIDE_SELL, 1.0, 100.0),
                Liquidation(60_001, SIDE_BUY, 1.0, 100.0)]
        f = aggregate_minutes(liqs)
        self.assertAlmostEqual(f[0].forced_sell_usd, 200.0)
        self.assertAlmostEqual(f[M].forced_buy_usd, 100.0)

    def test_sell_et_buy_sont_distingues(self):
        f = aggregate_minutes([Liquidation(0, SIDE_SELL, 3.0, 100.0),
                               Liquidation(0, SIDE_BUY, 1.0, 100.0)])
        self.assertAlmostEqual(f[0].imbalance, 0.5)

    def test_une_minute_sans_flux_n_a_pas_de_desequilibre(self):
        """L'absence de flux n'est pas un desequilibre nul : c'est une
        absence d'information, et la confondre avec zero ferait entrer des
        minutes vides dans les statistiques."""
        self.assertIsNone(MinuteFlow(0, 0.0, 0.0).imbalance)

    def test_le_desequilibre_est_borne(self):
        self.assertAlmostEqual(MinuteFlow(0, 100.0, 0.0).imbalance, 1.0)
        self.assertAlmostEqual(MinuteFlow(0, 0.0, 100.0).imbalance, -1.0)


class TestMedianeCausale(unittest.TestCase):

    def test_la_mediane_ne_sort_rien_tant_que_la_fenetre_n_est_pas_pleine(self):
        m = TrailingMedian(window=5)
        for _ in range(4):
            m.push(1.0)
            self.assertIsNone(m.value())
        m.push(1.0)
        self.assertIsNotNone(m.value())

    def test_la_mediane_glisse(self):
        m = TrailingMedian(window=3)
        for v in (1.0, 1.0, 1.0):
            m.push(v)
        self.assertAlmostEqual(m.value(), 1.0)
        for v in (10.0, 10.0, 10.0):
            m.push(v)
        self.assertAlmostEqual(m.value(), 10.0)

    def test_fenetre_invalide_refusee(self):
        with self.assertRaises(ValueError):
            TrailingMedian(window=0)


class TestRegleGelee(unittest.TestCase):

    def test_aucun_declenchement_avant_que_la_reference_existe(self):
        """Sans 1 440 minutes d'histoire, on ne sait pas ce qui est ample."""
        flows = flat(TRAILING_MINUTES + 10)
        flows[5 * M] = MinuteFlow(5 * M, 100_000.0, 0.0)
        mins = [i * M for i in range(TRAILING_MINUTES + 10)]
        trig = scan_instrument("X", flows, mins)
        self.assertTrue(all(t.minute_ms >= TRAILING_MINUTES * M for t in trig))

    def test_la_mediane_ne_voit_jamais_la_minute_courante(self):
        """Un flux enorme ne doit pas se normaliser par lui-meme : sinon son
        amplitude vaudrait 1 et il ne declencherait jamais."""
        n = TRAILING_MINUTES + 5
        flows = flat(n)
        big = (TRAILING_MINUTES + 2) * M
        flows[big] = MinuteFlow(big, 100_000.0, 0.0)
        trig = scan_instrument("X", flows, [i * M for i in range(n)])
        self.assertEqual(len(trig), 1)
        self.assertEqual(trig[0].minute_ms, big)
        self.assertGreater(trig[0].amplitude, 100.0)

    def test_un_flux_equilibre_ne_declenche_pas(self):
        n = TRAILING_MINUTES + 5
        flows = flat(n)
        big = (TRAILING_MINUTES + 2) * M
        flows[big] = MinuteFlow(big, 50_000.0, 50_000.0)   # imbalance = 0
        self.assertEqual(scan_instrument("X", flows,
                                         [i * M for i in range(n)]), [])

    def test_un_flux_ample_mais_trop_faible_ne_declenche_pas(self):
        n = TRAILING_MINUTES + 5
        flows = flat(n)
        big = (TRAILING_MINUTES + 2) * M
        flows[big] = MinuteFlow(big, 300.0, 0.0)           # amp = 3 < 5
        self.assertEqual(scan_instrument("X", flows,
                                         [i * M for i in range(n)]), [])

    def test_les_minutes_vides_comptent_dans_la_reference(self):
        """Une minute sans flux vaut zero dans la reference. Si seules les
        minutes agitees comptaient, la reference decrirait l'agitation et
        plus rien ne paraitrait ample."""
        n = TRAILING_MINUTES + 5
        dense = flat(n, total=100.0)
        sparse = {i * M: MinuteFlow(i * M, 100.0, 0.0)
                  for i in range(0, n, 3)}
        big = (TRAILING_MINUTES + 2) * M
        for flows in (dense, sparse):
            flows[big] = MinuteFlow(big, 1_000.0, 0.0)
        trig_dense = scan_instrument("X", dense, [i * M for i in range(n)])
        trig_sparse = scan_instrument("X", sparse, [i * M for i in range(n)])
        # Sur la serie creuse la mediane est plus basse, donc le MEME flux y
        # est plus ample : la reference tient bien compte des minutes vides.
        self.assertTrue(trig_dense or trig_sparse)
        if trig_dense and trig_sparse:
            self.assertGreater(trig_sparse[0].amplitude,
                               trig_dense[0].amplitude)

    def test_une_reference_nulle_ne_declenche_jamais(self):
        """Lacune de specification du protocole, resolue dans le sens
        conservateur. Si un instrument n'a normalement AUCUN flux force, la
        mediane vaut zero et l'ampleur n'est pas definie. On s'abstient : ce
        choix ne peut que RETIRER des declenchements, jamais en fabriquer.
        L'alternative — un plancher de notionnel absolu — serait un parametre
        ajoute apres le gel.
        """
        n = TRAILING_MINUTES + 5
        flows = {}
        big = (TRAILING_MINUTES + 2) * M
        flows[big] = MinuteFlow(big, 1_000_000.0, 0.0)
        self.assertEqual(scan_instrument("X", flows,
                                         [i * M for i in range(n)]), [])

    def test_le_sens_est_le_fade(self):
        n = TRAILING_MINUTES + 5
        big = (TRAILING_MINUTES + 2) * M
        for share, expected in ((1.0, 1), (0.0, -1)):
            flows = flat(n)
            flows[big] = MinuteFlow(big, 100_000.0 * share,
                                    100_000.0 * (1 - share))
            trig = scan_instrument("X", flows, [i * M for i in range(n)])
            self.assertEqual(trig[0].direction, expected)

    def test_les_seuils_sont_ceux_du_protocole(self):
        self.assertEqual(IMBALANCE_THRESHOLD, 0.50)
        self.assertEqual(AMPLITUDE_THRESHOLD, 5.0)
        self.assertEqual(HOLD_MINUTES, 30)
        self.assertEqual(TRAILING_MINUTES, 1_440)


class TestEvaluation(unittest.TestCase):

    def trig(self, direction=1):
        from prism_v2.liquidation_flow import Trigger
        return Trigger("X", 0, 0.8 * direction, 10.0, direction, 1e6)

    def test_le_fade_gagne_quand_le_prix_revient(self):
        px = {0: 100.0, HOLD_MINUTES * M: 101.0}
        o = evaluate(self.trig(1), px, half_spread_bps=0.0)
        self.assertAlmostEqual(o.gross_bps, 100.0, places=6)

    def test_le_fade_perd_quand_le_prix_continue(self):
        px = {0: 100.0, HOLD_MINUTES * M: 99.0}
        o = evaluate(self.trig(1), px, half_spread_bps=0.0)
        self.assertLess(o.gross_bps, 0.0)

    def test_le_demi_spread_est_facture_deux_fois(self):
        px = {0: 100.0, HOLD_MINUTES * M: 101.0}
        o = evaluate(self.trig(1), px, half_spread_bps=3.0)
        self.assertAlmostEqual(o.cost_bps, ROUND_TRIP_FEE_BPS + 6.0)
        self.assertAlmostEqual(o.net_bps, o.gross_bps - o.cost_bps)

    def test_un_deplacement_non_capturable_ressort_negatif(self):
        """Le coeur du barreau LIQUIDITE : le prix bouge, mais traverser
        coute plus que le mouvement."""
        px = {0: 100.0, HOLD_MINUTES * M: 100.05}       # +5 bps
        o = evaluate(self.trig(1), px, half_spread_bps=10.0)
        self.assertGreater(o.gross_bps, 0.0)
        self.assertLess(o.net_bps, 0.0)

    def test_prix_manquant_ne_produit_aucun_resultat(self):
        self.assertIsNone(evaluate(self.trig(1), {0: 100.0}, 0.0))

    def test_demi_spread_negatif_refuse(self):
        with self.assertRaises(ValueError):
            evaluate(self.trig(1), {0: 100.0, HOLD_MINUTES * M: 101.0}, -1.0)


if __name__ == "__main__":
    unittest.main()
