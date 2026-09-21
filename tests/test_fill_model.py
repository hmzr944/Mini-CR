"""Tests de prism_v2/fill_model.py.

Le point critique est le SIGNE du markout. Le dépôt s'est déjà trompé dessus
une fois (SCAN_DONNEES.md §4 : sélection adverse négative, +400 bps/jour, un
résultat impossible arrêté par l'invraisemblance économique et non par la
statistique). Plusieurs tests verrouillent ce signe des deux côtés.
"""
import statistics as st
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prism_v2.fill_model import (MIN_FILL_RATE, MIN_WINDOWS, FillStats,
                                 Snapshot, measure, simulate_passive_order,
                                 verdict)

S = 1000  # ms


def snap(t, bid=100.0, bid_sz=10.0, ask=100.1, ask_sz=10.0, agg=(), inst="X"):
    return Snapshot(ts=t * S, inst=inst, bid=bid, bid_sz=bid_sz,
                    ask=ask, ask_sz=ask_sz, agg=list(agg))


def test_no_aggressive_flow_means_no_fill():
    w = [snap(t) for t in range(0, 40, 5)]
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is False


def test_fill_requires_consuming_the_whole_queue_ahead():
    """9 contrats vendus contre une file de 10 : pas encore nous."""
    w = [snap(0, bid_sz=10.0),
         snap(5, agg=[[5 * S, 100.0, 9.0, "sell"]]),
         snap(60)]
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is False
    w[1] = snap(5, agg=[[5 * S, 100.0, 11.0, "sell"]])
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is True


def test_buys_do_not_fill_a_passive_bid():
    w = [snap(0, bid_sz=1.0),
         snap(5, agg=[[5 * S, 100.0, 500.0, "buy"]]),
         snap(60)]
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is False


def test_a_sell_above_our_bid_does_not_reach_us():
    w = [snap(0, bid_sz=1.0),
         snap(5, agg=[[5 * S, 100.05, 500.0, "sell"]]),
         snap(60)]
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is False


def test_the_touch_moving_away_cancels_the_attempt():
    """Le marché s'éloigne : on n'est plus au meilleur prix, donc pas rempli."""
    w = [snap(0, bid=100.0, bid_sz=1.0),
         snap(5, bid=100.05, agg=[[5 * S, 100.0, 500.0, "sell"]]),
         snap(60)]
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is False


def test_horizon_is_respected():
    w = [snap(0, bid_sz=1.0), snap(500, agg=[[500 * S, 100.0, 50.0, "sell"]])]
    assert simulate_passive_order(w, 0, "bid", 60, 10).filled is False


# ── le signe du markout ──────────────────────────────────────────────────────

def test_markout_is_positive_when_price_rises_after_we_bought():
    w = [snap(0, bid_sz=1.0),
         snap(5, agg=[[5 * S, 100.0, 50.0, "sell"]]),
         snap(40, bid=101.0, ask=101.1)]
    o = simulate_passive_order(w, 0, "bid", 120, 30)
    assert o.filled and o.markout_bps > 0


def test_markout_is_negative_when_price_falls_after_we_bought():
    """C'est la sélection adverse : on achète, puis ça baisse."""
    w = [snap(0, bid_sz=1.0),
         snap(5, agg=[[5 * S, 100.0, 50.0, "sell"]]),
         snap(40, bid=99.0, ask=99.1)]
    o = simulate_passive_order(w, 0, "bid", 120, 30)
    assert o.filled and o.markout_bps < 0


def test_markout_sign_is_mirrored_for_a_passive_ask():
    """On vend à l'ask : on gagne si ça BAISSE. Même convention économique."""
    up = [snap(0, ask_sz=1.0),
          snap(5, agg=[[5 * S, 100.1, 50.0, "buy"]]),
          snap(40, bid=101.0, ask=101.1)]
    down = [snap(0, ask_sz=1.0),
            snap(5, agg=[[5 * S, 100.1, 50.0, "buy"]]),
            snap(40, bid=99.0, ask=99.1)]
    assert simulate_passive_order(up, 0, "ask", 120, 30).markout_bps < 0
    assert simulate_passive_order(down, 0, "ask", 120, 30).markout_bps > 0


def test_a_symmetric_market_pays_exactly_the_half_spread_and_no_more():
    """Garde d'invraisemblance.

    Marché symétrique, autant de hausses que de baisses : le passif touche le
    DEMI-SPREAD, et rien d'autre. Si ce test sortait un multiple du demi-spread,
    c'est que le signe ou la référence sont faux — l'erreur exacte commise une
    fois dans ce dépôt (SCAN_DONNEES.md §4 : sélection adverse négative et
    +400 bps/jour, arrêtée par l'invraisemblance économique).
    """
    marks = []
    for after in (101.0, 99.0):
        w = [snap(0, bid_sz=1.0),
             snap(5, agg=[[5 * S, 100.0, 50.0, "sell"]]),
             snap(40, bid=after, ask=after + 0.1)]
        marks.append(simulate_passive_order(w, 0, "bid", 120, 30).markout_bps)
    half_spread_bps = 1e4 * 0.05 / 100.0            # (ask-bid)/2 sur le prix
    assert st.fmean(marks) == pytest.approx(half_spread_bps, abs=0.5)


def test_a_market_that_always_moves_against_the_fill_shows_adverse_selection():
    """Le cas qui tue le maker : on n'est rempli QUE quand ça continue contre nous."""
    marks = []
    for drift in (-0.5, -1.0, -1.5):
        w = [snap(0, bid_sz=1.0),
             snap(5, agg=[[5 * S, 100.0, 50.0, "sell"]]),
             snap(40, bid=100.0 + drift, ask=100.1 + drift)]
        marks.append(simulate_passive_order(w, 0, "bid", 120, 30).markout_bps)
    assert all(m < 0 for m in marks)
    assert st.fmean(marks) < -50          # nettement au-delà du demi-spread


# ── agrégation et verdict ────────────────────────────────────────────────────

def test_measure_ignores_attempts_with_no_future_left():
    w = [snap(t) for t in range(0, 300, 10)]
    stats = measure(w, "X", horizon_s=240, markout_s=30)
    assert stats.n_attempts > 0
    assert (w[-1].ts - w[stats.n_attempts - 1].ts) / 1000.0 >= 240


def test_measure_separates_instruments():
    w = [snap(t, inst="A") for t in range(0, 400, 10)] + \
        [snap(t, inst="B") for t in range(0, 400, 10)]
    assert measure(w, "A").inst == "A"
    assert measure(w, "B").n_attempts == measure(w, "A").n_attempts


def test_fill_rate_is_zero_safe_on_an_empty_sample():
    assert FillStats("X", 0, 0, None, None, 0, n_windows=0).fill_rate == 0.0


def test_verdict_refuses_to_conclude_on_too_few_attempts():
    assert verdict(FillStats("X", 10, 10, 1.0, 0.5, 10, n_windows=99),
                   2.0).startswith("INSUFFISANT")


def test_verdict_refuses_to_conclude_on_too_few_DISTINCT_windows():
    """Dix mille essais dans deux fenêtres restent deux observations."""
    s = FillStats("X", 10_000, 9_000, 1.0, 0.5, 9_000, n_windows=2)
    assert verdict(s, 2.0).startswith("INSUFFISANT")
    assert "fenêtres distinctes" in verdict(s, 2.0)


def test_measure_counts_distinct_windows_not_snapshots():
    """Deux fenêtres séparées de plusieurs heures comptent pour deux."""
    a = [snap(t) for t in range(0, 480, 3)]
    b = [snap(t) for t in range(20_000, 20_480, 3)]
    stats = measure(a + b, "X", horizon_s=240, markout_s=30)
    assert stats.n_windows == 2
    assert stats.n_attempts > 100          # beaucoup d'essais, deux observations


def test_an_attempt_cannot_borrow_the_future_from_the_next_window():
    """Une fenêtre trop courte ne devient pas évaluable grâce à la suivante."""
    a = [snap(t) for t in range(0, 60, 3)]          # 1 min : trop court
    b = [snap(t) for t in range(20_000, 20_060, 3)]
    assert measure(a + b, "X", horizon_s=240, markout_s=30).n_attempts == 0


def test_verdict_fails_on_a_low_fill_rate():
    s = FillStats("X", 500, int(500 * (MIN_FILL_RATE - 0.1)), 5.0, 0.5, 100, n_windows=50)
    assert verdict(s, 2.0).startswith("ECHEC remplissage")


def test_verdict_fails_when_adverse_selection_exceeds_the_spread():
    s = FillStats("X", 500, 400, 5.0, -3.0, 400, n_windows=50)   # 3 bps d'adverse
    assert verdict(s, 2.0).startswith("ECHEC selection adverse")


def test_verdict_passes_when_both_conditions_hold():
    s = FillStats("X", 500, 400, 5.0, -1.0, 400, n_windows=50)   # 1 bps d'adverse < 2 de spread
    assert verdict(s, 2.0).startswith("OK")
