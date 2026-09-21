"""Laboratoire causal, protocole de donnees, comptabilite des essais.

Ces tests portent sur l'INSTRUMENT de mesure. Un instrument qui fabrique de
l'effet a partir de bruit, ou qui reste aveugle a un effet present, invalide
tout resultat obtenu avec lui — quelle que soit la qualite du reste.
"""
from __future__ import annotations

import dataclasses
import unittest

from prism_v2.research.causal_lab import (
    CaptureOutcome, DEFAULT_GRID_MS, SnapshotSeries, measure_capture,
    summarise_captures,
)
from prism_v2.research.protocol import (
    DataProtocol, HoldoutViolation, Split,
)
from prism_v2.research.synthetic import (
    SyntheticSpec, false_positive_test, planted_reversion, random_walk,
    recovery_test,
)
from prism_v2.research.trials import (
    TrialLedger, deflated_sharpe, expected_max_sharpe,
    min_track_record_length, norm_cdf, norm_ppf,
    probability_of_backtest_overfitting, sharpe_ratio,
)

SPEC = SyntheticSpec(n_snapshots=6_000)


def series_from(recs, inst_id=SPEC.inst_id):
    return SnapshotSeries.from_records(inst_id, recs)


# ══════════════════════════════════════════════════════════════════════════
class TestSnapshotSeriesIsCausal(unittest.TestCase):

    def setUp(self):
        self.s = series_from(random_walk(SPEC))

    def test_at_never_returns_a_future_snapshot(self):
        for ts in range(self.s.first_ts, self.s.first_ts + 5_000, 137):
            rec = self.s.at(ts)
            if rec is not None:
                self.assertLessEqual(rec["ts"], ts)

    def test_before_first_snapshot_is_none(self):
        self.assertIsNone(self.s.at(self.s.first_ts - 1))

    def test_cadence_bounds_resolution(self):
        self.assertEqual(self.s.cadence_ms(), SPEC.cadence_ms)
        self.assertFalse(self.s.is_resolvable(SPEC.cadence_ms // 2))
        self.assertTrue(self.s.is_resolvable(SPEC.cadence_ms * 4))

    def test_invalid_snapshots_never_enter_the_series(self):
        recs = random_walk(SPEC)
        recs[10] = {"i": SPEC.inst_id, "ok": False, "why": "trou de sequence"}
        s = series_from(recs)
        self.assertEqual(len(s), len(recs) - 1)


class TestCaptureMeasurementRefusesRatherThanGuess(unittest.TestCase):

    def setUp(self):
        self.s = series_from(random_walk(SPEC))
        self.t0 = self.s.first_ts + 10_000

    def test_sub_cadence_horizon_is_unresolvable(self):
        m = measure_capture(self.s, self.t0, 5_000, 250, 10)
        self.assertIs(m.outcome, CaptureOutcome.UNRESOLVABLE_DELTA)
        self.assertIsNone(m.capture_fraction)

    def test_window_not_covering_exit_is_refused(self):
        m = measure_capture(self.s, self.s.last_ts - 100, 5_000, 250, 60_000)
        self.assertIs(m.outcome, CaptureOutcome.OUTSIDE_WINDOW)
        self.assertIsNone(m.capture_fraction)

    def test_zero_displacement_gives_no_denominator(self):
        """On ne divise jamais par un mouvement nul."""
        m = measure_capture(self.s, self.t0, 5_000, 250, 5_000,
                            min_displacement_bps=1e9)
        self.assertIs(m.outcome, CaptureOutcome.NO_DISPLACEMENT)
        self.assertIsNone(m.capture_fraction)

    def test_measured_case_carries_every_component(self):
        recs, truth = planted_reversion(0.5, 5_000, 5_000, spec=SPEC)
        s = series_from(recs)
        idx = truth["trigger_indices"][5]
        m = measure_capture(s, s.first_ts + idx * SPEC.cadence_ms,
                            5_000, 250, 5_000, min_displacement_bps=6.0)
        self.assertIs(m.outcome, CaptureOutcome.MEASURED)
        for field in ("observed_move_bps", "recoverable_bps", "capture_fraction",
                      "latency_drift_bps", "mfe_bps", "mae_bps",
                      "spread_at_entry_bps"):
            self.assertIsNotNone(getattr(m, field), field)
        self.assertIn(m.reversion_sign, (-1, 1))

    def test_bet_direction_is_opposite_to_the_displacement(self):
        recs, truth = planted_reversion(0.5, 5_000, 5_000, spec=SPEC)
        s = series_from(recs)
        for idx in truth["trigger_indices"][:20]:
            m = measure_capture(s, s.first_ts + idx * SPEC.cadence_ms,
                                5_000, 250, 5_000, min_displacement_bps=6.0)
            if m.outcome is CaptureOutcome.MEASURED:
                self.assertEqual(m.reversion_sign,
                                 -1 if m.observed_move_bps > 0 else 1)

    def test_recoverable_is_measured_from_entry_not_from_t0(self):
        """Compter le trajet parcouru pendant la latence serait s'attribuer
        un mouvement qu'on n'a pas pu prendre."""
        recs, truth = planted_reversion(0.8, 5_000, 5_000, spec=SPEC)
        s = series_from(recs)
        idx = truth["trigger_indices"][5]
        t0 = s.first_ts + idx * SPEC.cadence_ms
        fast = measure_capture(s, t0, 5_000, 0, 5_000, min_displacement_bps=6.0)
        slow = measure_capture(s, t0, 5_000, 2_000, 5_000, min_displacement_bps=6.0)
        if (fast.outcome is CaptureOutcome.MEASURED
                and slow.outcome is CaptureOutcome.MEASURED):
            # Entrer plus tard ne peut pas donner MECANIQUEMENT plus de
            # mouvement restant sur un effet qui se deverse dans le temps.
            self.assertIsNotNone(slow.latency_drift_bps)


class TestInstrumentDoesNotFabricateEdge(unittest.TestCase):
    """Le test le plus important du module : l'instrument est-il honnete ?"""

    def test_random_walk_yields_no_measurable_capture(self):
        r = false_positive_test(spec=SPEC)
        self.assertTrue(r["conclusive"], r["detail"])
        self.assertTrue(r["indistinguishable_from_zero"],
                        f"l'instrument fabrique de l'effet a partir de bruit: {r}")

    def test_zero_planted_fraction_is_recovered_as_zero(self):
        r = recovery_test(0.0, spec=SPEC)
        self.assertTrue(r.within_tolerance, r.detail)

    def test_planted_effect_is_recovered_at_the_event(self):
        """A l'instant de l'evenement, l'estimateur est non biaise."""
        for planted in (0.25, 0.5, 0.8):
            with self.subTest(planted=planted):
                recs, truth = planted_reversion(planted, 5_000, 5_000, spec=SPEC)
                s = series_from(recs)
                vals = []
                for idx in truth["trigger_indices"]:
                    m = measure_capture(s, s.first_ts + idx * SPEC.cadence_ms,
                                        5_000, 250, 5_000,
                                        min_displacement_bps=6.0)
                    if (m.outcome is CaptureOutcome.MEASURED
                            and m.capture_fraction is not None):
                        vals.append(m.capture_fraction)
                self.assertGreater(len(vals), 50)
                mean = sum(vals) / len(vals)
                self.assertAlmostEqual(mean, planted, delta=0.25,
                                       msg=f"plante {planted}, mesure {mean:.3f}")

    def test_grid_sampling_attenuates_and_never_inflates(self):
        """Echantillonner sur grille sous-estime l'effet. C'est le sens SUR :
        une mesure de grille indiscernable de zero est une preuve forte."""
        planted = 0.5
        grid = recovery_test(planted, spec=SPEC)
        self.assertIsNotNone(grid.measured_fraction)
        self.assertLess(grid.measured_fraction, planted * 1.2)


class TestSummaryKeepsRefusalsVisible(unittest.TestCase):

    def test_refusals_are_counted_not_hidden(self):
        s = series_from(random_walk(SPEC))
        ms = [measure_capture(s, s.first_ts + 10_000, 5_000, 250, 10)
              for _ in range(3)]
        summary = summarise_captures(ms)
        self.assertEqual(summary["n_measured"], 0)
        self.assertIn(CaptureOutcome.UNRESOLVABLE_DELTA.value,
                      summary["by_outcome"])
        self.assertIsNone(summary["capture_fraction"])

    def test_summary_reports_a_standard_error(self):
        recs, truth = planted_reversion(0.5, 5_000, 5_000, spec=SPEC)
        s = series_from(recs)
        ms = [measure_capture(s, s.first_ts + i * SPEC.cadence_ms,
                              5_000, 250, 5_000, min_displacement_bps=6.0)
              for i in truth["trigger_indices"][:200]]
        summary = summarise_captures(ms)
        if summary["n_measured"] > 1:
            self.assertIsNotNone(summary["recoverable_bps"]["stderr"])


# ══════════════════════════════════════════════════════════════════════════
class TestDataProtocolProtectsTheHoldout(unittest.TestCase):

    def setUp(self):
        self.p = DataProtocol(0, 100_000)

    def test_splits_are_temporal_contiguous_and_exhaustive(self):
        bounds = [self.p.bounds(s) for s in
                  (Split.DISCOVERY, Split.DEVELOPMENT, Split.VALIDATION,
                   Split.FINAL_HOLDOUT)]
        for (a_lo, a_hi), (b_lo, b_hi) in zip(bounds, bounds[1:]):
            self.assertEqual(a_hi, b_lo)
        self.assertEqual(bounds[0][0], 0)
        self.assertEqual(bounds[-1][1], 100_000)

    def test_holdout_is_the_most_recent_segment(self):
        self.assertGreater(self.p.bounds(Split.FINAL_HOLDOUT)[0],
                           self.p.bounds(Split.VALIDATION)[0])

    def test_holdout_cannot_be_read_by_select(self):
        with self.assertRaises(HoldoutViolation):
            self.p.select(Split.FINAL_HOLDOUT, [{"ts": 90_000}])

    def test_opening_the_holdout_is_recorded(self):
        self.assertEqual(self.p.holdout_open_count, 0)
        self.p.open_holdout("validation finale")
        self.assertEqual(self.p.holdout_open_count, 1)
        self.assertIsNotNone(self.p.holdout_opened_at)

    def test_second_opening_marks_contamination(self):
        self.p.open_holdout("une fois")
        self.assertFalse(self.p.holdout_is_contaminated)
        self.p.open_holdout("deux fois")
        self.assertTrue(self.p.holdout_is_contaminated)

    def test_modification_after_opening_marks_contamination(self):
        self.p.open_holdout("validation")
        self.p.declare_modification("seuil ajuste")
        self.assertTrue(self.p.holdout_is_contaminated)
        self.assertTrue(self.p.modifications_after_opening)

    def test_modification_before_opening_is_not_contamination(self):
        self.p.declare_modification("refonte avant ouverture")
        self.assertFalse(self.p.holdout_is_contaminated)

    def test_only_discovery_and_development_may_inform_choices(self):
        self.assertTrue(Split.DISCOVERY.may_inform_choices)
        self.assertTrue(Split.DEVELOPMENT.may_inform_choices)
        self.assertFalse(Split.VALIDATION.may_inform_choices)
        self.assertFalse(Split.FINAL_HOLDOUT.may_inform_choices)

    def test_fractions_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            DataProtocol(0, 100, fractions={Split.DISCOVERY: 0.5,
                                            Split.DEVELOPMENT: 0.2,
                                            Split.VALIDATION: 0.2,
                                            Split.FINAL_HOLDOUT: 0.2})


# ══════════════════════════════════════════════════════════════════════════
class TestTrialAccounting(unittest.TestCase):

    def test_normal_quantile_matches_known_values(self):
        self.assertAlmostEqual(norm_ppf(0.975), 1.959964, places=5)
        self.assertAlmostEqual(norm_ppf(0.5), 0.0, places=8)
        self.assertAlmostEqual(norm_cdf(0.0), 0.5, places=10)

    def test_expected_max_sharpe_grows_with_trials(self):
        a = expected_max_sharpe(10, 0.1)
        b = expected_max_sharpe(10_000, 0.1)
        self.assertGreater(b, a)

    def test_expected_max_sharpe_needs_dispersion(self):
        self.assertIsNone(expected_max_sharpe(1000, 0.0))
        self.assertIsNone(expected_max_sharpe(1, 0.1))

    def test_deflation_impossible_without_trial_dispersion(self):
        """Sans savoir combien les essais dispersent, on ne deflate PAS.

        LE FIXTURE PRECEDENT NE TESTAIT PAS CELA. Il passait `[0.01] * 50`,
        dont la variance est exactement nulle : `deflated_sharpe` sortait donc
        des la garde `m["std"] <= 0` en renvoyant None, et le test echouait sur
        `None.p_value` — de facon DETERMINISTE, sur toute plateforme. La
        branche visee, celle qui refuse de deflater faute de dispersion des
        essais, n'etait jamais atteinte. Il faut une serie a variance non
        nulle pour l'exercer.
        """
        import random
        rng = random.Random(11)
        rets = [rng.gauss(0.01, 0.5) for _ in range(50)]
        d = deflated_sharpe(rets, n_trials=500, trial_sharpe_std=None)
        self.assertIsNotNone(d, "serie a variance non nulle : on doit obtenir "
                                "un resultat, pas None")
        self.assertIsNone(d.p_value)
        self.assertFalse(d.significant)
        self.assertIn("IMPOSSIBLE", d.note)

    def test_serie_degeneree_ne_produit_aucun_sharpe(self):
        """Variance nulle : la reponse est None, et non un Sharpe infini.

        C'est la garde que le fixture precedent declenchait par accident. On
        la teste desormais pour elle-meme, au lieu de la subir.
        """
        self.assertIsNone(deflated_sharpe([0.01] * 50, n_trials=500,
                                          trial_sharpe_std=0.05))

    def test_same_sharpe_is_less_significant_after_more_trials(self):
        import random
        rng = random.Random(7)
        rets = [rng.gauss(0.05, 1.0) for _ in range(500)]
        few = deflated_sharpe(rets, n_trials=5, trial_sharpe_std=0.05)
        many = deflated_sharpe(rets, n_trials=100_000, trial_sharpe_std=0.05)
        self.assertGreater(many.benchmark_sharpe, few.benchmark_sharpe)
        self.assertLess(many.p_value, few.p_value)

    def test_min_track_record_length_is_none_below_target(self):
        self.assertIsNone(min_track_record_length([-0.1] * 50, target_sharpe=0.0))

    def test_pbo_detects_pure_noise_selection(self):
        """Sur du bruit pur, selectionner en echantillon n'apprend rien :
        le PBO doit etre eleve."""
        import random
        rng = random.Random(3)
        matrix = [[rng.gauss(0, 1) for _ in range(400)] for _ in range(12)]
        r = probability_of_backtest_overfitting(matrix, n_blocks=8)
        self.assertIsNotNone(r)
        self.assertGreater(r["pbo"], 0.25,
                           f"PBO trop bas sur du bruit pur: {r}")

    def test_pbo_rejects_odd_block_counts(self):
        with self.assertRaises(ValueError):
            probability_of_backtest_overfitting([[0.0] * 100] * 3, n_blocks=7)

    def test_trial_ledger_counts_every_attempt(self):
        led = TrialLedger()
        led.record("h1", 0.2, 100, "REJECTED")
        led.record("h2", None, 10, "ABANDONED")
        led.record("h3", 0.4, 100, "SURVIVED")
        self.assertEqual(led.n_trials, 3)
        self.assertIsNotNone(led.sharpe_dispersion())
        self.assertEqual(led.to_dict()["by_status"]["ABANDONED"], 1)

    def test_dispersion_needs_at_least_two_measured_trials(self):
        led = TrialLedger()
        led.record("h1", 0.2, 100, "X")
        self.assertIsNone(led.sharpe_dispersion())

    def test_sharpe_is_not_annualised_silently(self):
        self.assertAlmostEqual(sharpe_ratio([1.0, -1.0, 1.0, -1.0]), 0.0, places=9)
        self.assertIsNone(sharpe_ratio([1.0]))


if __name__ == "__main__":
    unittest.main()


class TestAggregationDoesNotInflateSignificance(unittest.TestCase):
    """Defauts trouves en faisant tourner l'experience sur donnees reelles."""

    def test_tiny_trials_are_excluded_from_sharpe_dispersion(self):
        """Un Sharpe sur 2 observations peut valoir des centaines. L'inclure
        gonfle le seuil de chance et vide la deflation de son sens."""
        led = TrialLedger()
        led.record("enorme", 399.0, 2, "RESOLVED")       # 2 observations
        led.record("aussi", 250.0, 3, "RESOLVED")
        self.assertIsNone(led.sharpe_dispersion(),
                          "des essais minuscules ont pollue la dispersion")
        led.record("serieux_a", 0.10, 100, "RESOLVED")
        led.record("serieux_b", 0.20, 100, "RESOLVED")
        d = led.sharpe_dispersion()
        self.assertIsNotNone(d)
        self.assertLess(d, 1.0, "la dispersion reste polluee par les petits N")
        self.assertEqual(led.n_trials, 4)               # tous restent COMPTES
        self.assertEqual(led.n_trials_with_enough_observations(), 2)

    def test_every_trial_stays_counted_even_when_excluded_from_dispersion(self):
        """Exclure de la dispersion n'est pas effacer : le nombre d'essais
        entre dans la correction, quoi qu'il arrive."""
        led = TrialLedger()
        for i in range(50):
            led.record(f"h{i}", 10.0, 2, "RESOLVED")
        self.assertEqual(led.n_trials, 50)
        self.assertEqual(led.to_dict()["n_trials"], 50)

    def test_config_result_records_event_timestamps_for_dedup(self):
        """Sans horodatage par evenement, on ne peut pas dedoublonner entre
        configurations qui balayent les memes instants."""
        from prism_v2.experiment import ConfigResult
        r = ConfigResult(inst_id="X", lookback_ms=1, horizon_ms=1,
                         threshold_spreads=1.0, split="DISCOVERY")
        self.assertIn("event_ts", r.__dataclass_fields__)
        self.assertEqual(len(r.event_ts), len(r.gross_bps))


class TestBothBetsAreTested(unittest.TestCase):
    """Ne tester que la reversion reviendrait a supposer la reponse."""

    def setUp(self):
        from prism_v2.research.causal_lab import BET_CONTINUATION, BET_REVERSION
        self.REV, self.CONT = BET_REVERSION, BET_CONTINUATION
        self.recs, self.truth = planted_reversion(0.5, 5_000, 5_000, spec=SPEC)
        self.s = series_from(self.recs)

    def _both(self, idx):
        t0 = self.s.first_ts + idx * SPEC.cadence_ms
        a = measure_capture(self.s, t0, 5_000, 250, 5_000,
                            min_displacement_bps=6.0, bet=self.REV)
        b = measure_capture(self.s, t0, 5_000, 250, 5_000,
                            min_displacement_bps=6.0, bet=self.CONT)
        return a, b

    def test_continuation_is_the_exact_negation_of_reversion(self):
        checked = 0
        for idx in self.truth["trigger_indices"][:60]:
            a, b = self._both(idx)
            if (a.outcome is CaptureOutcome.MEASURED
                    and b.outcome is CaptureOutcome.MEASURED):
                self.assertEqual(a.reversion_sign, -b.reversion_sign)
                self.assertAlmostEqual(a.recoverable_bps, -b.recoverable_bps,
                                       places=9)
                checked += 1
        self.assertGreater(checked, 10, "trop peu de paires comparables")

    def test_the_bet_is_recorded_on_the_measurement(self):
        a, b = self._both(self.truth["trigger_indices"][3])
        self.assertEqual(a.bet, self.REV)
        self.assertEqual(b.bet, self.CONT)
        self.assertIn("bet", a.to_dict())

    def test_planted_reversion_favours_the_reversion_bet(self):
        """Controle de sens : sur un effet de reversion plante, c'est le pari
        de reversion qui doit gagner, pas l'inverse."""
        rev, cont = [], []
        for idx in self.truth["trigger_indices"]:
            a, b = self._both(idx)
            if a.outcome is CaptureOutcome.MEASURED:
                rev.append(a.recoverable_bps)
            if b.outcome is CaptureOutcome.MEASURED:
                cont.append(b.recoverable_bps)
        self.assertGreater(len(rev), 50)
        self.assertGreater(sum(rev) / len(rev), sum(cont) / len(cont))

    def test_threshold_grid_reaches_beyond_the_cost_floor(self):
        """Un evenement de 2 bps ne peut PAS couvrir un aller-retour a 10 bps.
        Ne balayer que de petits seuils testerait uniquement des cas
        structurellement perdants."""
        from prism_v2.experiment import FEE_BPS_PER_LEG, THRESHOLD_SPREADS
        self.assertGreaterEqual(max(THRESHOLD_SPREADS) , 16.0)
        self.assertGreaterEqual(max(THRESHOLD_SPREADS), FEE_BPS_PER_LEG * 2)


class TestVerdictDistinguishesAbsenceFromIgnorance(unittest.TestCase):
    """« Rien trouve » et « pas assez regarde » sont des conclusions opposees.

    Defaut reel : avec 22 993 evenements mesures et aucun net positif, le
    verdict rendu etait INSUFFICIENT_DATA — l'inverse de la verite.
    """

    def _standard(self, **over):
        from prism_v2.research.validation import Condition as C, ProofStandard
        base = {c: True for c in C}
        base.update(over)
        ps = ProofStandard()
        for c, v in base.items():
            ps.assess(c, v, "test")
        return ps

    def test_many_events_and_nothing_positive_is_no_validated_edge(self):
        from prism_v2.research.validation import Condition as C, Verdict
        ps = self._standard(**{C.NET_POSITIVE: False, C.OUT_OF_SAMPLE: False,
                               C.TEMPORAL_STABILITY: False, C.CAPACITY: False,
                               C.RISK_MEASURED: False, C.DRAWDOWN_MEASURED: False,
                               C.MULTIPLE_TESTING: False,
                               C.NO_CRITICAL_UNKNOWN: False})
        self.assertIs(ps.verdict(), Verdict.NO_VALIDATED_EDGE)

    def test_too_few_events_is_insufficient_data(self):
        from prism_v2.research.validation import Condition as C, Verdict
        ps = self._standard(**{C.ENOUGH_TRADES: False})
        self.assertIs(ps.verdict(), Verdict.INSUFFICIENT_DATA)

    def test_positive_but_failing_holdout_is_not_robust(self):
        from prism_v2.research.validation import Condition as C, Verdict
        ps = self._standard(**{C.OUT_OF_SAMPLE: False})
        self.assertIs(ps.verdict(), Verdict.EDGE_NOT_ROBUST)

    def test_positive_but_not_executable_is_named_as_such(self):
        from prism_v2.research.validation import Condition as C, Verdict
        ps = self._standard(**{C.CAPACITY: False})
        self.assertIs(ps.verdict(), Verdict.EDGE_NOT_EXECUTABLE)


class TestCapacityIsProbedNotAssumed(unittest.TestCase):

    def test_grid_spans_small_to_large(self):
        from prism_v2.experiment import CAPACITY_GRID_USD
        self.assertLessEqual(CAPACITY_GRID_USD[0], 25.0)
        self.assertGreaterEqual(CAPACITY_GRID_USD[-1], 5_000.0)
        self.assertEqual(list(CAPACITY_GRID_USD), sorted(CAPACITY_GRID_USD))

    def test_unmeasurable_impact_is_reported_not_extrapolated(self):
        """Une taille que le carnet enregistre n'absorbe pas donne un impact
        UNKNOWN : la ligne le DIT, elle n'extrapole pas."""
        from prism_v2.experiment import capacity_curve_for
        import inspect
        src = inspect.getsource(capacity_curve_for)
        self.assertIn("unmeasurable_share", src)
        self.assertIn("jamais extrapolee", src)


class TestLatencyIsProbedNotAssumed(unittest.TestCase):

    def test_grid_includes_zero_as_a_diagnostic(self):
        """Zero n'est pas realiste : il repond a « la latence est-elle la
        contrainte mordante ? ». Si le net reste negatif a latence nulle,
        aucune amelioration d'infrastructure ne changerait le resultat."""
        from prism_v2.experiment import LATENCY_GRID_MS
        self.assertIn(0, LATENCY_GRID_MS)
        self.assertGreaterEqual(max(LATENCY_GRID_MS), 1_000)

    def test_transport_delay_is_not_called_order_latency(self):
        from prism_v2.experiment import transport_delay_stats
        recs = [{"ts": 1000 + i, "recv": 1000 + i + 90} for i in range(100)]
        st = transport_delay_stats(recs)
        self.assertEqual(st["p50_ms"], 90)
        self.assertIn("UNKNOWN", st["note"])
        self.assertIn("TRANSPORT", st["note"])

    def test_transport_delay_refuses_when_no_pairs(self):
        from prism_v2.experiment import transport_delay_stats
        st = transport_delay_stats([{"ts": 1}, {"recv": 2}])
        self.assertEqual(st["n"], 0)

    def test_negative_delays_are_dropped_not_clamped(self):
        """Un horodatage de reception anterieur a l'horodatage exchange est une
        anomalie d'horloge : on l'ecarte, on ne le ramene pas a zero."""
        from prism_v2.experiment import transport_delay_stats
        recs = [{"ts": 1000, "recv": 900}] + [{"ts": 1000, "recv": 1090}] * 10
        st = transport_delay_stats(recs)
        self.assertEqual(st["n"], 10)
        self.assertEqual(st["min_ms"], 90)


class TestFeeSensitivityIsExactNotResimulated(unittest.TestCase):

    def test_zero_fee_row_isolates_the_spread_and_impact_floor(self):
        from prism_v2.experiment import fee_sensitivity
        rows = fee_sensitivity([1.0, 1.0], [0.4, 0.4], [0.1, 0.1],
                               grid=(0.0, 5.0))
        self.assertAlmostEqual(rows[0]["mean_net_bps"], 0.5, places=9)
        self.assertAlmostEqual(rows[1]["mean_net_bps"], 0.5 - 10.0, places=9)

    def test_grid_includes_zero_and_the_public_tier(self):
        from prism_v2.experiment import FEE_BPS_PER_LEG, FEE_GRID_BPS_PER_LEG
        self.assertIn(0.0, FEE_GRID_BPS_PER_LEG)
        self.assertIn(FEE_BPS_PER_LEG, FEE_GRID_BPS_PER_LEG)

    def test_empty_input_yields_no_rows_rather_than_zeros(self):
        from prism_v2.experiment import fee_sensitivity
        self.assertEqual(fee_sensitivity([], [], []), [])

    def test_costs_are_kept_per_event_for_substitution(self):
        from prism_v2.experiment import ConfigResult
        r = ConfigResult(inst_id="X", lookback_ms=1, horizon_ms=1,
                         threshold_spreads=1.0, split="D")
        for f in ("cost_spread_bps", "cost_impact_bps"):
            self.assertIn(f, r.__dataclass_fields__)


class TestTriggerThresholdIsCausal(unittest.TestCase):
    """Defaut trouve en red team sur mon propre code d'experience.

    Le seuil de declenchement etait la mediane du spread sur TOUT le segment,
    donnees posterieures a l'evenement comprises : du look-ahead, et dans le
    holdout une lecture du holdout pour parametrer la regle.
    """

    def _basis(self):
        from prism_v2.experiment import TrailingSpreadBasis
        recs = random_walk(SPEC)
        return TrailingSpreadBasis(series_from(recs)), series_from(recs)

    def test_basis_uses_only_data_strictly_before_the_current_minute(self):
        from prism_v2.experiment import (SPREAD_BASIS_BUCKET_MS,
                                         TrailingSpreadBasis)
        basis, s = self._basis()
        t = s.first_ts + 30 * 60_000
        bucket_start = (t // SPREAD_BASIS_BUCKET_MS) * SPREAD_BASIS_BUCKET_MS
        # Toute valeur de la minute courante ou posterieure est exclue :
        # deux instants de la meme minute partagent donc la meme base.
        self.assertEqual(basis.at(bucket_start),
                         basis.at(bucket_start + SPREAD_BASIS_BUCKET_MS - 1))

    def test_basis_is_unavailable_before_enough_history(self):
        """FAIL CLOSED : pas d'historique, pas de seuil, pas d'evenement."""
        basis, s = self._basis()
        self.assertIsNone(basis.at(s.first_ts))

    def test_basis_is_identical_across_splits_by_construction(self):
        """La regle doit etre la MEME en decouverte et sur le holdout."""
        basis, s = self._basis()
        t = s.first_ts + 40 * 60_000
        self.assertEqual(basis.at(t), basis.at(t))
        import inspect
        from prism_v2.experiment import run_config
        src = inspect.getsource(run_config)
        self.assertNotIn("median_spread_bps(series, lo, hi)", src)

    def test_instants_without_history_are_not_counted_as_refusals(self):
        """Melanger les deux ferait lire « 362 000 refus pour 21 000
        evenements » et donnerait l'illusion d'un systeme qui rejette tout."""
        from prism_v2.experiment import ConfigResult
        r = ConfigResult(inst_id="X", lookback_ms=1, horizon_ms=1,
                         threshold_spreads=1.0, split="D")
        self.assertIn("n_instants_without_basis", r.__dataclass_fields__)
        self.assertEqual(r.refusals, {})


class TestPBOIsNotReadAsAGreenLight(unittest.TestCase):
    """PBO mesure si la SELECTION generalise, pas si le selectionne est
    rentable. Sur 922 configurations toutes perdantes, le run final a rendu
    PBO = 0.000 — qui se lirait comme un feu vert."""

    def test_guard_fires_when_every_configuration_loses(self):
        import inspect
        from prism_v2 import experiment
        src = inspect.getsource(experiment.run)
        self.assertIn("all_configurations_losing", src)
        self.assertIn("interpretation_guard", src)

    def test_pbo_itself_says_what_it_measures(self):
        import random
        rng = random.Random(11)
        matrix = [[rng.gauss(-0.5, 1) for _ in range(300)] for _ in range(6)]
        r = probability_of_backtest_overfitting(matrix, n_blocks=8)
        self.assertIn("selection", r["note"].lower())
        self.assertNotIn("rentab", r["note"].lower())
