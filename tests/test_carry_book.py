"""Tests de prism_v2/carry_book.py.

Deux familles :
  - l'arithmetique (levier, coussin, cout, R(T)) doit etre exacte ;
  - la SELECTION doit rejeter pour la bonne raison, et le dire.

Le second point est le plus important. Le projet a deja produit un faux negatif
ou une regle ne pouvait structurellement pas se declencher (commit 5ef49cb :
« zero declenchement, pour toujours, sur n'importe quelle donnee... faux negatif
indiscernable d'un vrai resultat »). Un livre vide doit toujours nommer le
critere qui l'a vide.
"""
import statistics as st
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prism_v2.carry_book import (MAKER_FEE_BPS, MAX_RESIDUAL_DRIFT_BPS,
                                 MIN_DEPTH_USD, MIN_MEDIAN_OVER_MEAN,
                                 MIN_RESIDUAL_HOURS, MIN_SETTLEMENTS,
                                 MIN_T_STAT, SAFETY_FACTOR, TAKER_FEE_BPS,
                                 Book, Pair, select)


def make_pair(coin="TEST", diff=None, **kw):
    """Une paire qui passe TOUS les criteres, sauf ce que le test change."""
    if diff is None:
        # moyenne 0,30 bps/reglement, dispersion faible -> t tres au-dessus du seuil
        diff = [0.30 + 0.01 * ((i % 7) - 3) for i in range(300)]
    base = dict(
        coin=coin, diff_bps_per_settlement=diff,
        imr_inverse=0.02, imr_linear=0.02, mmr_inverse=0.01, mmr_linear=0.01,
        depth_usd_inverse=50_000.0, depth_usd_linear=50_000.0,
        half_spread_inverse_bps=1.0, half_spread_linear_bps=1.0,
        worst_residual_drift_bps=100.0, residual_hours=9_000)
    base.update(kw)
    return Pair(**base)


# ── arithmetique ─────────────────────────────────────────────────────────────

def test_r_is_three_settlements_per_day():
    p = make_pair(diff=[1.0] * 300)
    assert p.r_bps_per_day == pytest.approx(3.0)


def test_margins_add_because_there_is_no_netting():
    """Sans compensation (risk unit merge = VIP3), les deux marges s'additionnent."""
    p = make_pair(imr_inverse=0.05, imr_linear=0.02)
    assert p.imr_sum == pytest.approx(0.07)
    assert p.max_leverage == pytest.approx(1 / 0.07)


def test_liquidation_buffer_shrinks_as_leverage_grows():
    p = make_pair(mmr_inverse=0.01, mmr_linear=0.01)
    assert p.liquidation_buffer_bps(10) > p.liquidation_buffer_bps(25)
    # a levier L, coussin = 1/L - MMR, en bps
    assert p.liquidation_buffer_bps(25) == pytest.approx(1e4 * (0.04 - 0.02))


def test_safe_leverage_honours_the_safety_factor():
    p = make_pair(worst_residual_drift_bps=100.0, mmr_inverse=0.01, mmr_linear=0.01)
    L = p.safe_leverage()
    assert p.liquidation_buffer_bps(L) >= SAFETY_FACTOR * 100.0 - 1e-6


def test_safe_leverage_never_exceeds_the_margin_cap():
    """Un residu minuscule ne doit pas autoriser un levier que la marge interdit."""
    p = make_pair(worst_residual_drift_bps=0.01)
    assert p.safe_leverage() == pytest.approx(p.max_leverage)


def test_taker_cost_includes_both_spreads_twice():
    p = make_pair(half_spread_inverse_bps=2.0, half_spread_linear_bps=3.0)
    assert p.roundtrip_cost_bps(maker=False) == pytest.approx(4 * TAKER_FEE_BPS + 2 * 5.0)
    assert p.roundtrip_cost_bps(maker=True) == pytest.approx(4 * MAKER_FEE_BPS)


def test_net_return_is_negative_below_the_cost_breakeven_horizon():
    b = Book(pairs=[make_pair(diff=[0.1] * 300)])       # r = 0,3 bps/jour
    c = 4 * MAKER_FEE_BPS                                # 8 bps
    breakeven = c / 0.3                                  # ~26,7 jours
    assert b.net_bps_per_day_of_capital(breakeven * 0.5, maker=True) < 0
    assert b.net_bps_per_day_of_capital(breakeven * 2.0, maker=True) > 0


def test_book_leverage_is_the_minimum_not_the_mean():
    """Une paire liquidee emporte le collateral commun : c'est le min qui gouverne."""
    safe = make_pair("SAFE", worst_residual_drift_bps=10.0)
    risky = make_pair("RISKY", worst_residual_drift_bps=200.0)
    b = Book(pairs=[safe, risky])
    assert b.leverage() == pytest.approx(min(safe.safe_leverage(), risky.safe_leverage()))
    assert b.leverage() < st.fmean([safe.safe_leverage(), risky.safe_leverage()])


def test_capacity_caps_at_a_quarter_of_the_thinnest_leg():
    b = Book(pairs=[make_pair(depth_usd_inverse=8_000.0, depth_usd_linear=90_000.0)])
    assert b.capacity_usd() == pytest.approx(2_000.0)


def test_empty_book_is_zero_everywhere_and_does_not_raise():
    b = Book(pairs=[])
    assert b.leverage() == 0 and b.capacity_usd() == 0
    assert b.r_bps_per_day() == 0 and b.t_stat() == 0
    assert b.net_bps_per_day_of_capital(30, maker=True) == 0


# ── selection ────────────────────────────────────────────────────────────────

def test_a_fully_compliant_pair_is_kept():
    book = select([make_pair()])
    assert book.coins == ["TEST"] and book.rejected == []


@pytest.mark.parametrize("kw,criterion", [
    (dict(diff=[0.3] * (MIN_SETTLEMENTS - 1)), "echantillon_funding"),
    (dict(residual_hours=MIN_RESIDUAL_HOURS - 1), "heures_residu"),
    (dict(worst_residual_drift_bps=MAX_RESIDUAL_DRIFT_BPS + 1), "derive_residu_14j"),
    (dict(depth_usd_inverse=MIN_DEPTH_USD - 1), "profondeur_inverse"),
    (dict(depth_usd_linear=MIN_DEPTH_USD - 1), "profondeur_lineaire"),
])
def test_each_criterion_rejects_and_names_itself(kw, criterion):
    book = select([make_pair(**kw)])
    assert book.pairs == []
    assert [r.criterion for r in book.rejected] == [criterion]


def test_a_weak_t_stat_is_rejected():
    noisy = [50.0 if i % 2 else -49.0 for i in range(300)]   # moyenne ~0,5, t minuscule
    book = select([make_pair(diff=noisy)])
    assert book.pairs == []
    assert book.rejected[0].criterion == "t_differentiel"
    assert book.rejected[0].value < MIN_T_STAT


def test_a_tail_driven_differential_is_rejected():
    """Mediane nulle : la paire paie rarement et beaucoup. Rejetee."""
    tail = [0.0] * 280 + [30.0] * 20        # moyenne 2,0 ; mediane 0,0 ; t eleve
    p = make_pair(diff=tail)
    assert p.t_stat > MIN_T_STAT            # elle passerait le test de significativite
    assert p.median_over_mean < MIN_MEDIAN_OVER_MEAN
    book = select([p])
    assert book.rejected[0].criterion == "mediane_sur_moyenne"


def test_rejection_is_reported_for_the_FIRST_failing_criterion_only():
    book = select([make_pair(residual_hours=1, depth_usd_inverse=1.0)])
    assert len(book.rejected) == 1
    assert book.rejected[0].criterion == "heures_residu"


def test_an_empty_book_always_explains_itself():
    """La garde anti-faux-negatif : jamais de livre vide muet."""
    pairs = [make_pair("A", worst_residual_drift_bps=1e9),
             make_pair("B", depth_usd_linear=0.0)]
    book = select(pairs)
    assert book.pairs == []
    assert {r.coin for r in book.rejected} == {"A", "B"}
    assert all(r.criterion and r.threshold for r in book.rejected)


def test_selection_keeps_and_rejects_in_the_same_pass():
    book = select([make_pair("GOOD"), make_pair("BAD", depth_usd_inverse=0.0)])
    assert book.coins == ["GOOD"]
    assert [r.coin for r in book.rejected] == ["BAD"]


def test_portfolio_series_aligns_on_the_shortest_pair():
    a = make_pair("A", diff=[1.0] * 300)
    b = make_pair("B", diff=[3.0] * 250)
    book = Book(pairs=[a, b])
    s = book.portfolio_series()
    assert len(s) == 250
    assert all(v == pytest.approx(2.0) for v in s)
