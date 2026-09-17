"""Detecteur de differentiel de funding, et surtout son haircut.

Le haircut est le seul endroit de ce module ou une erreur produirait de
FAUSSES OPPORTUNITES plutot que de fausses pertes. Un premier jet appliquait
un ratio unique (mesure au seuil 100 %/an) a tous les signaux : sur une
lecture a 234 %/an il predisait 61 bps la ou la mesure en donne 21, et le bot
acceptait le trade. Les tests ci-dessous verrouillent la forme mesuree.
"""
from __future__ import annotations

import unittest

from prism_v2.core_types import Direction
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.opp_funding import (
    DEFAULT_HORIZON_H, FAMILY, HORIZONS_MEASURED, MEASURED_SURFACE,
    FundingSpreadOpportunity, VenueFunding, decay_factor,
    expected_capture_bps, realized_apr,
)
from prism_v2.opportunity import DetectionStatus, MarketContext


def spec(base="ADA"):
    return InstrumentSpec(
        inst_id=f"{base}-USDT-SWAP", exchange="OKX",
        inst_type=InstrumentType.SWAP_LINEAR, ct_type="linear", base=base,
        quote="USDT", settle_ccy="USDT", ct_val=0.01, ct_val_ccy=base,
        ct_mult=1.0, tick_size=0.0001, lot_size=1.0, min_size=1.0,
        state="live", fetched_at="2026-01-01T00:00:00Z")


def ctx(near_apr, far_apr, base="ADA", capacity=1e6):
    # rate = apr / (heures par an) * period_h
    near = VenueFunding("OKX", near_apr / (24 * 365) * 8.0, 8.0, 1000)
    far = VenueFunding("HYPERLIQUID", far_apr / (24 * 365) * 1.0, 1.0, 1000)
    return MarketContext(instrument=spec(base),
                         funding={"long_venue": near, "short_venue": far},
                         extras={"capacity_usd": capacity})


class TestSurfaceMesuree(unittest.TestCase):

    def test_le_realise_sature_et_redescend(self):
        """Le fait economique central : un signal 25x plus grand ne rapporte
        pas 25x plus. A 168 h, 20 %/an realise 7,3 et 500 %/an realise 5,5.
        """
        r = [MEASURED_SURFACE[t][168] for t in sorted(MEASURED_SURFACE)]
        self.assertLess(r[-1], r[0])          # 500 % rend MOINS que 20 %
        self.assertEqual(max(r), MEASURED_SURFACE[1.00][168])

    def test_le_ratio_decroit_avec_la_force_du_signal(self):
        ratios = [decay_factor(t, 168) for t in (0.20, 0.50, 1.00, 2.00, 5.00)]
        self.assertEqual(ratios, sorted(ratios, reverse=True))

    def test_aucune_extrapolation_vers_le_haut(self):
        """Le bug corrige : un signal absurde ne doit pas produire une
        capture absurde."""
        plafond = max(MEASURED_SURFACE[t][168] for t in MEASURED_SURFACE)
        for apr in (5.0, 20.0, 200.0, 10_000.0):
            self.assertLessEqual(realized_apr(apr, 168), plafond + 1e-12)

    def test_le_signal_extreme_est_rejete_la_ou_le_ratio_unique_l_acceptait(self):
        """SOPH a +234 %/an : 21 bps mesures, contre 61 bps avec un ratio
        unique de 0,137. C'est tout l'ecart entre accepter et refuser.
        """
        mesure = expected_capture_bps(2.34, 168)
        ratio_unique = 2.34 * 0.137 * (168 / (24 * 365)) * 10_000.0
        self.assertLess(mesure, 25.0)
        self.assertGreater(ratio_unique, 55.0)

    def test_capture_coherente_avec_la_mesure_historique(self):
        """A 100 %/an sur 168 h le protocole mesurait 22 a 27 bps de brut."""
        self.assertTrue(22.0 <= expected_capture_bps(1.0, 168) <= 27.0)

    def test_le_signe_du_signal_est_indifferent(self):
        self.assertAlmostEqual(expected_capture_bps(1.0, 24),
                               expected_capture_bps(-1.0, 24))

    def test_horizon_borne_aux_deux_extremites(self):
        self.assertAlmostEqual(realized_apr(1.0, 1),
                               MEASURED_SURFACE[1.00][HORIZONS_MEASURED[0]])
        self.assertAlmostEqual(realized_apr(1.0, 9999),
                               MEASURED_SURFACE[1.00][HORIZONS_MEASURED[-1]])

    def test_un_signal_nul_ne_capture_rien(self):
        self.assertAlmostEqual(expected_capture_bps(0.0, 168), 0.0)
        self.assertAlmostEqual(decay_factor(0.0, 168), 0.0)

    def test_horizon_invalide_refuse(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                realized_apr(1.0, bad)


class TestVenueFunding(unittest.TestCase):

    def test_la_cadence_convertit_le_taux(self):
        """Un taux par 8 h et le meme par 4 h ne sont pas le meme APR."""
        a = VenueFunding("OKX", 0.0008, 8.0, 0)
        b = VenueFunding("OKX", 0.0008, 4.0, 0)
        self.assertAlmostEqual(b.apr, 2.0 * a.apr)

    def test_cadence_nulle_refusee(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                VenueFunding("OKX", 0.001, bad, 0)


class TestDetecteur(unittest.TestCase):

    def setUp(self):
        self.opp = FundingSpreadOpportunity(min_abs_apr=0.20)

    def test_sous_le_seuil_de_bruit_aucune_candidate(self):
        res = self.opp.detect(ctx(0.10, 0.15))
        self.assertIs(res.status, DetectionStatus.OK)
        self.assertEqual(res.candidates, [])

    def test_au_dessus_du_seuil_une_candidate_classee(self):
        res = self.opp.detect(ctx(-0.50, 0.50))
        self.assertIs(res.status, DetectionStatus.OK)
        self.assertEqual(len(res.candidates), 1)
        c = res.candidates[0]
        self.assertEqual(c.family, FAMILY)
        self.assertEqual(len(c.legs), 2)
        self.assertEqual(c.required_execution, "TAKER")

    def test_le_sens_suit_le_signe_du_differentiel(self):
        haut = self.opp.detect(ctx(-0.50, 0.50)).candidates[0]
        bas = self.opp.detect(ctx(0.50, -0.50)).candidates[0]
        self.assertIs(haut.direction, Direction.LONG)
        self.assertIs(bas.direction, Direction.SHORT)

    def test_la_candidate_porte_le_haircut_et_sa_source(self):
        c = self.opp.detect(ctx(-0.50, 0.50)).candidates[0]
        self.assertTrue(c.metadata["haircut_applique"])
        self.assertIn("FUNDING_ARB_PROTOCOL", c.metadata["decay_source"])
        brut_sans_haircut = c.observed_state["capture_si_aucune_decroissance_bps"]
        self.assertLess(c.gross_capture_bps, brut_sans_haircut)

    def test_funding_absent_donne_insufficient_pas_zero(self):
        res = self.opp.detect(MarketContext(instrument=spec(), funding={}))
        self.assertIs(res.status, DetectionStatus.INSUFFICIENT_DATA)
        self.assertEqual(res.candidates, [])

    def test_la_candidate_est_causale(self):
        c = self.opp.detect(ctx(-0.50, 0.50)).candidates[0]
        self.assertIsNotNone(c.causal_reference_ts_ms)
        self.assertEqual(c.expected_horizon_ms, DEFAULT_HORIZON_H * 3_600_000)

    def test_les_conditions_d_invalidation_nomment_la_jambe_unique(self):
        c = self.opp.detect(ctx(-0.50, 0.50)).candidates[0]
        self.assertIn("une_jambe_non_remplie", c.invalidation_conditions)

    def test_seuil_negatif_refuse(self):
        with self.assertRaises(ValueError):
            FundingSpreadOpportunity(min_abs_apr=-0.1)


if __name__ == "__main__":
    unittest.main()
