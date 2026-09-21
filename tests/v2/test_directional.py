"""L'estimateur stratifié supprime-t-il vraiment la dérive ?

C'est toute la question. Un estimateur qui ne la supprime pas transformerait un
marché haussier en « edge » pour n'importe quel détecteur à biais long.
"""
from __future__ import annotations

import random
import unittest

from prism_v2.directional_test import (Event, block_bootstrap_ci,
                                       permutation_pvalue,
                                       stratified_difference, stratify, verdict)

T0 = 1_789_000_000_000
MIN_MS = 60_000


def ev(inst, minute, sign, r):
    return Event(inst, T0 + int(minute * MIN_MS), sign, r)


class TestDriftIsRemoved(unittest.TestCase):

    def test_pure_drift_with_balanced_signs_gives_zero(self):
        """Tout le monde monte de 10 bps. Aucune information dans le signe."""
        evs = [ev("BTC", i % 4, +1 if i % 2 else -1, 10.0) for i in range(40)]
        r = stratified_difference(evs, minutes=5.0)
        self.assertAlmostEqual(r.delta_bps, 0.0, places=9)

    def test_pure_drift_with_a_LONG_BIASED_signal_still_gives_zero(self):
        """Le piege exact : 80 % de LONG dans un marche qui monte.

        La moyenne naive est franchement positive ; la difference stratifiee
        doit rester nulle, parce que le signe n'explique rien.
        """
        evs = ([ev("BTC", 0, +1, 10.0) for _ in range(40)]
               + [ev("BTC", 0, -1, 10.0) for _ in range(10)])
        r = stratified_difference(evs, minutes=5.0)
        self.assertGreater(r.naive_signed_bps, 5.0)      # la naive se fait piegeer
        self.assertAlmostEqual(r.delta_bps, 0.0, places=9)   # la stratifiee, non

    def test_a_real_directional_signal_is_detected(self):
        """LONG gagne 8, SHORT perd 2 : le signe porte 10 bps d'information."""
        evs = ([ev("BTC", 0, +1, 8.0) for _ in range(20)]
               + [ev("BTC", 0, -1, -2.0) for _ in range(20)])
        self.assertAlmostEqual(stratified_difference(evs).delta_bps, 10.0, places=9)

    def test_drift_that_differs_between_strata_is_still_removed(self):
        """Chaque strate a sa propre derive. Aucune n'a d'information."""
        evs = []
        for minute, drift in ((0, +50.0), (6, -30.0), (12, +5.0)):
            evs += [ev("BTC", minute, +1, drift) for _ in range(9)]
            evs += [ev("BTC", minute, -1, drift) for _ in range(3)]
        r = stratified_difference(evs, minutes=5.0)
        self.assertAlmostEqual(r.delta_bps, 0.0, places=9)
        self.assertEqual(r.n_strata_used, 3)

    def test_instruments_are_stratified_separately(self):
        """BTC monte, ETH baisse. Un signal long-biaise sur BTC seulement."""
        evs = ([ev("BTC", 0, +1, 20.0) for _ in range(10)]
               + [ev("BTC", 0, -1, 20.0) for _ in range(10)]
               + [ev("ETH", 0, +1, -20.0) for _ in range(10)]
               + [ev("ETH", 0, -1, -20.0) for _ in range(10)])
        self.assertEqual(len(stratify(evs, 5.0)), 2)
        self.assertAlmostEqual(stratified_difference(evs).delta_bps, 0.0, places=9)


class TestSingleSignStrataAreDroppedAndCounted(unittest.TestCase):

    def test_a_one_sided_stratum_is_dropped_not_silently_used(self):
        evs = ([ev("BTC", 0, +1, 7.0) for _ in range(5)]          # une seule face
               + [ev("BTC", 6, +1, 3.0) for _ in range(4)]
               + [ev("BTC", 6, -1, 1.0) for _ in range(4)])
        r = stratified_difference(evs, minutes=5.0)
        self.assertEqual(r.n_strata_dropped, 1)
        self.assertEqual(r.n_strata_used, 1)
        self.assertAlmostEqual(r.delta_bps, 2.0, places=9)

    def test_everything_one_sided_yields_no_estimate_rather_than_a_number(self):
        evs = [ev("BTC", i, +1, 9.0) for i in range(20)]
        r = stratified_difference(evs, minutes=5.0)
        self.assertIsNone(r.delta_bps)
        self.assertEqual(r.n_strata_used, 0)


class TestPermutationNull(unittest.TestCase):

    def test_pure_drift_is_not_significant(self):
        rng = random.Random(3)
        evs = [ev("BTC", i % 6, rng.choice([1, -1]), 12.0 + rng.gauss(0, 1))
               for i in range(240)]
        p = permutation_pvalue(evs, minutes=5.0, n_perm=400)
        self.assertGreater(p, 0.10)

    def test_a_strong_real_signal_is_significant(self):
        rng = random.Random(4)
        evs = []
        for i in range(240):
            s = rng.choice([1, -1])
            evs.append(ev("BTC", i % 6, s, (6.0 if s > 0 else -6.0) + rng.gauss(0, 1)))
        self.assertLessEqual(permutation_pvalue(evs, 5.0, n_perm=400), 0.05)

    def test_permutation_keeps_events_inside_their_stratum(self):
        """Une permutation qui melangerait les strates casserait le controle."""
        evs = ([ev("BTC", 0, +1, 1.0)] * 3 + [ev("BTC", 0, -1, 1.0)] * 3
               + [ev("ETH", 0, +1, 99.0)] * 3 + [ev("ETH", 0, -1, 99.0)] * 3)
        # sous permutation interne, Delta reste 0 pour toute permutation
        self.assertGreater(permutation_pvalue(evs, 5.0, n_perm=100), 0.5)


class TestBlockBootstrap(unittest.TestCase):

    def test_ci_covers_zero_under_pure_drift(self):
        rng = random.Random(5)
        # 8 tranches de 5 min distinctes : i%8 MINUTES n'en ferait que 2
        evs = [ev("BTC", (i % 8) * 5, rng.choice([1, -1]), 15.0 + rng.gauss(0, 2))
               for i in range(320)]
        lo, hi = block_bootstrap_ci(evs, 5.0, n_boot=400)
        self.assertLessEqual(lo, 0.0)
        self.assertGreaterEqual(hi, 0.0)

    def test_ci_excludes_zero_for_a_strong_signal(self):
        rng = random.Random(6)
        evs = []
        for i in range(320):
            s = rng.choice([1, -1])
            evs.append(ev("BTC", (i % 8) * 5, s,
                          (7.0 if s > 0 else -7.0) + rng.gauss(0, 1)))
        lo, hi = block_bootstrap_ci(evs, 5.0, n_boot=400)
        self.assertGreater(lo, 0.0)

    def test_too_few_strata_yields_no_interval_rather_than_a_fake_one(self):
        evs = [ev("BTC", 0, +1, 1.0), ev("BTC", 0, -1, 2.0)]
        self.assertIsNone(block_bootstrap_ci(evs, 5.0, n_boot=50))


class TestVerdictAppliesTheDeclaredCriteria(unittest.TestCase):

    def test_negative_delta_is_refuted(self):
        self.assertTrue(verdict(-0.5, 0.01, (-1.0, -0.1)).startswith("REFUTEE"))

    def test_high_p_is_refuted(self):
        self.assertTrue(verdict(2.0, 0.30, (0.1, 3.0)).startswith("REFUTEE"))

    def test_ci_containing_zero_is_inconclusive(self):
        self.assertIn("contient 0", verdict(2.0, 0.02, (-0.4, 4.0)))

    def test_p_between_005_and_010_is_inconclusive(self):
        self.assertTrue(verdict(2.0, 0.08, (0.2, 3.0)).startswith("NON CONCLUANTE"))

    def test_promising_never_claims_net_capture(self):
        v = verdict(2.0, 0.01, (0.5, 3.5))
        self.assertTrue(v.startswith("PROMETTEUSE"))
        self.assertIn("AUCUNE capture nette", v)


if __name__ == "__main__":
    unittest.main()
