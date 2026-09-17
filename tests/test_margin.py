"""Le capital immobilise est-il celui que l'exchange bloque vraiment ?

L'objectif du projet est un ratio dont le denominateur est le capital
immobilise. Ce denominateur etait un nombre ecrit a la main. Ces tests
verifient qu'il vient desormais du bareme public d'OKX, qu'il est ECHELONNE
par la taille, et qu'il refuse de repondre plutot que d'extrapoler.
"""
import unittest

from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.margin import (MarginSchedule, MarginTier, capital_required_usd,
                             parse_tiers, schedules_from_payload)


def _spec(inst_id, inst_type, ct_val, family):
    return InstrumentSpec(
        inst_id=inst_id, exchange="OKX", inst_type=inst_type,
        ct_type="inverse" if inst_type is InstrumentType.SWAP_INVERSE else "linear",
        base=inst_id.split("-")[0], quote=inst_id.split("-")[1],
        settle_ccy=inst_id.split("-")[0], ct_val=ct_val,
        ct_val_ccy="USD" if inst_type is InstrumentType.SWAP_INVERSE else inst_id.split("-")[0],
        ct_mult=1.0, tick_size=0.1, lot_size=0.1, min_size=0.1,
        state="live", fetched_at="2026-01-01T00:00:00Z", lever=100.0,
        family=family)


BTC_INV = _spec("BTC-USD-SWAP", InstrumentType.SWAP_INVERSE, 100.0, "BTC-USD")
ADA_LIN = _spec("ADA-USDT-SWAP", InstrumentType.SWAP_LINEAR, 100.0, "ADA-USDT")

# Paliers reels, releves chez OKX le 2026-09-17.
BTC_ROWS = [{"tier": "1", "minSz": "0", "maxSz": "2000", "imr": "0.01",
             "mmr": "0.004", "maxLever": "100"},
            {"tier": "2", "minSz": "2000.1", "maxSz": "5000", "imr": "0.015",
             "mmr": "0.005", "maxLever": "66.66"},
            {"tier": "3", "minSz": "5000.1", "maxSz": "20000", "imr": "0.02",
             "mmr": "0.0075", "maxLever": "50"}]
ADA_ROWS = [{"tier": "1", "minSz": "0", "maxSz": "2200", "imr": "0.02",
             "mmr": "0.01", "maxLever": "50"},
            {"tier": "2", "minSz": "2200.1", "maxSz": "4500", "imr": "0.025",
             "mmr": "0.015", "maxLever": "40"}]

BTC = MarginSchedule("BTC-USD", parse_tiers(BTC_ROWS))
ADA = MarginSchedule("ADA-USDT", parse_tiers(ADA_ROWS))


class TestBareme(unittest.TestCase):

    def test_le_palier_suit_la_taille(self):
        self.assertEqual(BTC.tier_for(100).tier, 1)
        self.assertEqual(BTC.tier_for(3000).tier, 2)
        self.assertEqual(BTC.tier_for(10000).tier, 3)

    def test_au_dela_du_dernier_palier_on_refuse(self):
        """L'exchange refuse la position ; il ne la tarifie pas plus cher."""
        self.assertIsNone(BTC.tier_for(20_001))
        self.assertIsNone(BTC.initial_margin_usd(BTC_INV, 20_001, 75_000.0))
        self.assertIsNone(BTC.effective_leverage(20_001))

    def test_une_taille_nulle_n_est_pas_une_position(self):
        self.assertIsNone(BTC.tier_for(0))
        self.assertIsNone(BTC.tier_for(-5))

    def test_marge_inverse_independante_du_prix(self):
        """Sur un inverse, le notionnel ne depend pas du prix, la marge non plus."""
        a = BTC.initial_margin_usd(BTC_INV, 100, 30_000.0)
        b = BTC.initial_margin_usd(BTC_INV, 100, 120_000.0)
        self.assertAlmostEqual(a, 100 * 100.0 * 0.01, places=9)
        self.assertEqual(a, b)

    def test_marge_lineaire_depend_du_prix(self):
        m = ADA.initial_margin_usd(ADA_LIN, 1000, 0.35)
        self.assertAlmostEqual(m, 100.0 * 1000 * 0.35 * 0.02, places=9)

    def test_le_levier_reel_baisse_quand_la_taille_monte(self):
        """C'est tout l'interet : le levier affiche ne vaut qu'au premier palier."""
        petit = BTC.effective_leverage(100)
        grand = BTC.effective_leverage(10_000)
        self.assertAlmostEqual(petit, 100.0, places=6)
        self.assertAlmostEqual(grand, 50.0, places=6)
        self.assertLess(grand, petit)
        # et il ne vaut jamais le `lever` affiche sur l'instrument a grande taille
        self.assertLess(grand, BTC_INV.lever)

    def test_marge_croit_plus_vite_que_le_notionnel(self):
        """Doubler la taille peut plus que doubler le capital bloque."""
        m1 = BTC.initial_margin_usd(BTC_INV, 2000, 75_000.0)
        m2 = BTC.initial_margin_usd(BTC_INV, 4000, 75_000.0)
        self.assertGreater(m2 / m1, 2.0)

    def test_notionnel_maximal_autorise(self):
        self.assertAlmostEqual(BTC.max_notional_usd(BTC_INV, 75_000.0),
                               20_000 * 100.0, places=6)


class TestPositionMultiJambes(unittest.TestCase):

    SCHED = {"BTC-USD": BTC, "ADA-USDT": ADA}

    def test_les_marges_s_additionnent(self):
        legs = [(BTC_INV, 100, 75_000.0), (ADA_LIN, 1000, 0.35)]
        got = capital_required_usd(legs, self.SCHED)
        self.assertAlmostEqual(got, 100 * 100.0 * 0.01
                               + 100.0 * 1000 * 0.35 * 0.02, places=9)

    def test_une_jambe_hors_bareme_annule_la_position(self):
        legs = [(BTC_INV, 100, 75_000.0), (ADA_LIN, 99_999_999, 0.35)]
        self.assertIsNone(capital_required_usd(legs, self.SCHED))

    def test_famille_inconnue_ne_donne_pas_zero(self):
        """Absence de bareme n'est pas absence de capital."""
        inconnu = _spec("ZZZ-USDT-SWAP", InstrumentType.SWAP_LINEAR, 1.0, "ZZZ-USDT")
        self.assertIsNone(capital_required_usd([(inconnu, 10, 1.0)], self.SCHED))


class TestLecture(unittest.TestCase):

    def test_ligne_illisible_ecartee_pas_devinee(self):
        rows = BTC_ROWS + [{"tier": "4", "minSz": "x", "maxSz": "1", "imr": "0.1",
                            "mmr": "0.05", "maxLever": "10"}]
        self.assertEqual(len(parse_tiers(rows)), 3)

    def test_imr_nul_rejete(self):
        """Une marge initiale nulle signifierait un levier infini."""
        rows = [{"tier": "1", "minSz": "0", "maxSz": "10", "imr": "0",
                 "mmr": "0.004", "maxLever": "100"}]
        self.assertEqual(parse_tiers(rows), [])

    def test_famille_sans_palier_valide_est_absente(self):
        out = schedules_from_payload({"VIDE": [], "BTC-USD": BTC_ROWS})
        self.assertNotIn("VIDE", out)
        self.assertIn("BTC-USD", out)


if __name__ == "__main__":
    unittest.main()
