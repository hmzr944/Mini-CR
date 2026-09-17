"""Le recensement des flux calcule-t-il le rendement du CAPITAL, correctement ?

Deux erreurs opposees sont possibles et toutes deux ont circule dans ce projet :

  - comparer un rendement sur NOTIONNEL a un seuil sur CAPITAL, ce qui
    sous-estime la strategie du facteur de levier ;
  - lever le flux sans lever le cout, ce qui fabrique un profit inexistant.

Ces tests rendent les deux impossibles.
"""
import unittest

from prism_v2.capital import SurvivalBuffer
from prism_v2.core_types import Direction
from prism_v2.flow_census import (Flow, Leg, OBSERVED, declared_horizon,
                                  evaluate_at, grid_upper_bound,
                                  horizon_curve, render)
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


INV = _spec("BTC-USD-SWAP", InstrumentType.SWAP_INVERSE, 100.0, "BTC-USD")
LIN = _spec("BTC-USDT-SWAP", InstrumentType.SWAP_LINEAR, 0.01, "BTC-USDT")
TIERS = parse_tiers([{"tier": "1", "minSz": "0", "maxSz": "100000",
                      "imr": "0.01", "mmr": "0.004", "maxLever": "100"}])
SCHED = {"BTC-USD": MarginSchedule("BTC-USD", TIERS),
         "BTC-USDT": MarginSchedule("BTC-USDT", TIERS)}
ENTRY = {"BTC-USD-SWAP": 100.0, "BTC-USDT-SWAP": 100.0}


def _flat_prices(n=40_000, step_ms=3_600_000):
    """Prix rigoureusement plat : coussin nul, levier = 1/marge initiale."""
    return [(i * step_ms, 100.0) for i in range(n)]


PRICES = {"BTC-USD-SWAP": _flat_prices(), "BTC-USDT-SWAP": _flat_prices()}


def _flow(rate, cost):
    return Flow(name="test", legs=[Leg(INV, Direction.SHORT, 10.0),
                                   Leg(LIN, Direction.LONG, 100.0)],
                rate_bps_per_day=rate, round_trip_bps=cost,
                evidence=OBSERVED, source="test")


class TestConversionNotionnelCapital(unittest.TestCase):

    def test_le_levier_multiplie_le_flux_ET_le_cout(self):
        """Lever seulement le flux fabriquerait un profit inexistant."""
        e = evaluate_at(_flow(1.0, 20.0), 1.0, PRICES, SCHED, ENTRY)
        self.assertIsNotNone(e)
        attendu = (1.0 - 20.0 / 1.0) * e.leverage
        self.assertAlmostEqual(e.net_bps_per_day_capital, attendu, places=9)
        self.assertLess(e.net_bps_per_day_capital, 0.0)

    def test_un_flux_positif_sur_notionnel_reste_positif_sur_capital(self):
        e = evaluate_at(_flow(1.0, 1.0), 10.0, PRICES, SCHED, ENTRY)
        self.assertGreater(e.net_bps_per_day_notional, 0.0)
        self.assertGreater(e.net_bps_per_day_capital,
                           e.net_bps_per_day_notional)

    def test_cout_amorti_sur_la_duree(self):
        """c/T : c'est tout l'interet de la forme par flux."""
        court = evaluate_at(_flow(1.0, 20.0), 1.0, PRICES, SCHED, ENTRY)
        long_ = evaluate_at(_flow(1.0, 20.0), 30.0, PRICES, SCHED, ENTRY)
        self.assertAlmostEqual(court.cost_bps_per_day_notional, 20.0, places=9)
        self.assertAlmostEqual(long_.cost_bps_per_day_notional, 20.0 / 30.0,
                               places=9)
        self.assertGreater(long_.net_bps_per_day_notional,
                           court.net_bps_per_day_notional)

    def test_les_marges_des_deux_jambes_s_additionnent(self):
        """Devises de marge differentes : pas de compensation supposee."""
        e = evaluate_at(_flow(1.0, 1.0), 1.0, PRICES, SCHED, ENTRY)
        self.assertFalse(e.capital.netting_assumed)
        self.assertEqual(len(e.capital.legs), 2)
        self.assertAlmostEqual(e.capital.capital_usd,
                               sum(l.capital_usd for l in e.capital.legs),
                               places=9)


class TestRefusPlutotQueSupposition(unittest.TestCase):

    def test_prix_manquants_rendent_none(self):
        self.assertIsNone(evaluate_at(_flow(1.0, 1.0), 1.0,
                                      {"BTC-USD-SWAP": _flat_prices()},
                                      SCHED, ENTRY))

    def test_bareme_manquant_rend_none(self):
        self.assertIsNone(evaluate_at(_flow(1.0, 1.0), 1.0, PRICES,
                                      {"BTC-USD": SCHED["BTC-USD"]}, ENTRY))

    def test_prix_d_entree_manquant_rend_none(self):
        self.assertIsNone(evaluate_at(_flow(1.0, 1.0), 1.0, PRICES, SCHED,
                                      {"BTC-USD-SWAP": 100.0}))

    def test_duree_nulle_rend_none(self):
        self.assertIsNone(evaluate_at(_flow(1.0, 1.0), 0.0, PRICES, SCHED, ENTRY))

    def test_taille_hors_bareme_rend_none(self):
        f = Flow(name="trop gros",
                 legs=[Leg(INV, Direction.SHORT, 1e9), Leg(LIN, Direction.LONG, 1.0)],
                 rate_bps_per_day=1.0, round_trip_bps=1.0,
                 evidence=OBSERVED, source="test")
        self.assertIsNone(evaluate_at(f, 1.0, PRICES, SCHED, ENTRY))


class TestCourbeSansSelection(unittest.TestCase):

    def test_la_borne_est_nommee_borne_pas_resultat(self):
        curve = horizon_curve(_flow(1.0, 20.0), PRICES, SCHED, ENTRY)
        self.assertTrue(curve)
        b = grid_upper_bound(curve)
        self.assertEqual(b.net_bps_per_day_capital,
                         max(e.net_bps_per_day_capital for e in curve))
        txt = render(_flow(1.0, 20.0), curve, 63.28)
        self.assertIn("BORNE SUPERIEURE", txt)
        self.assertNotIn("optimum", txt)

    def test_horizon_declare_ne_choisit_pas_le_meilleur(self):
        """Un horizon declare d'avance rend SON point, pas le maximum."""
        curve = horizon_curve(_flow(1.0, 20.0), PRICES, SCHED, ENTRY)
        d = declared_horizon(curve, 1.0)
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d.horizon_days, 1.0, places=9)
        self.assertLessEqual(d.net_bps_per_day_capital,
                             grid_upper_bound(curve).net_bps_per_day_capital)
        self.assertIsNone(declared_horizon(curve, 999.0))

    def test_coussin_croissant_fait_baisser_le_levier(self):
        """Prix volatils : detenir plus longtemps coute plus de capital."""
        import random
        random.seed(3)
        px, pts = 100.0, []
        for i in range(40_000):
            px *= 1.0 + random.gauss(0.0, 0.004)
            pts.append((i * 3_600_000, px))
        prices = {"BTC-USD-SWAP": pts, "BTC-USDT-SWAP": pts}
        entry = {"BTC-USD-SWAP": pts[0][1], "BTC-USDT-SWAP": pts[0][1]}
        curve = horizon_curve(_flow(5.0, 20.0), prices, SCHED, entry)
        leviers = [e.leverage for e in curve]
        self.assertGreater(leviers[0], leviers[-1])
        self.assertTrue(all(a >= b - 1e-9 for a, b in zip(leviers, leviers[1:])),
                        f"le levier doit decroitre avec la duree : {leviers}")

    def test_rendu_ne_cache_ni_l_objectif_ni_la_fiabilite(self):
        curve = horizon_curve(_flow(1.0, 20.0), PRICES, SCHED, ENTRY)
        txt = render(_flow(1.0, 20.0), curve, 63.28)
        self.assertIn("objectif", txt)
        self.assertIn("63.28", txt)
        self.assertIn("BORNE SUPERIEURE", txt)


if __name__ == "__main__":
    unittest.main()


class TestCritereDAdmission(unittest.TestCase):
    """Le seuil qui transforme la recherche en crible."""

    def test_inverse_exactement_la_formule_du_rendement(self):
        from prism_v2.flow_census import required_rate_bps_per_day
        r = required_rate_bps_per_day(63.28, 3.4, 25.0, 1.0)
        # en injectant r, on doit retomber sur l'objectif
        self.assertAlmostEqual(3.4 * (r - 25.0 / 1.0), 63.28, places=9)

    def test_plus_de_levier_abaisse_le_seuil(self):
        from prism_v2.flow_census import required_rate_bps_per_day
        self.assertLess(required_rate_bps_per_day(63.28, 10.0, 25.0, 1.0),
                        required_rate_bps_per_day(63.28, 3.0, 25.0, 1.0))

    def test_detention_plus_longue_abaisse_le_seuil(self):
        from prism_v2.flow_census import required_rate_bps_per_day
        self.assertLess(required_rate_bps_per_day(63.28, 3.0, 25.0, 10.0),
                        required_rate_bps_per_day(63.28, 3.0, 25.0, 1.0))

    def test_sans_levier_pas_de_position(self):
        from prism_v2.flow_census import required_rate_bps_per_day
        self.assertIsNone(required_rate_bps_per_day(63.28, 0.0, 25.0, 1.0))
        self.assertIsNone(required_rate_bps_per_day(63.28, -1.0, 25.0, 1.0))
        self.assertIsNone(required_rate_bps_per_day(63.28, 3.0, 25.0, 0.0))

    def test_le_seuil_mesure_du_projet(self):
        """Levier 3,4x et 25 bps d'aller-retour, mesures : ~43,6 bps/jour."""
        from prism_v2.flow_census import required_rate_bps_per_day
        r = required_rate_bps_per_day(63.28, 3.4, 25.0, 1.0)
        self.assertAlmostEqual(r, 43.6, places=1)
