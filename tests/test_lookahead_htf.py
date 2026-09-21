"""Sentinelle du look-ahead 4H/1D de `prism/strategy.compute_indicators`.

Ces tests DOCUMENTENT le comportement actuel (ils passent sur le code tel
qu'il est livre) et verifient que `tools/causal_htf.patch` le supprime.

Si un jour quelqu'un applique le correctif a `prism/strategy.py`,
`test_shipped_compute_indicators_looks_ahead` echouera -- c'est voulu : il
faudra alors le retirer, en connaissance de cause, et recalibrer.

cf. AUDIT_V33_INDEPENDANT.md section D.1
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prism import strategy          # noqa: E402
from tools.causal_htf import causal_htf_ema, patch   # noqa: E402


def _ramp(n=600):
    """Prix strictement croissants : la valeur d'une barre identifie son instant."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    c = pd.Series(np.arange(n, dtype=float) + 100.0, index=idx)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": np.ones(n)}, index=idx)


def test_resample_label_is_the_bucket_start_not_its_close():
    """La cause racine, isolee de toute la strategie."""
    df = _ramp(12)
    buckets = df[["close"]].resample("4h").last()
    # le seau etiquete 00:00 porte la cloture de 03:00
    assert buckets.loc["2026-01-01 00:00", "close"] == df.loc["2026-01-01 03:00", "close"]
    seen = buckets["close"].reindex(df.index, method="ffill")
    # ... et le ffill la rend visible des 00:00
    assert seen.loc["2026-01-01 00:00"] == df.loc["2026-01-01 03:00", "close"]


def test_shipped_compute_indicators_looks_ahead():
    """Le code livre laisse fuir jusqu'a 3 h de futur dans ema20_4h."""
    df = strategy.compute_indicators(_ramp())
    e20 = df["ema20_4h"].dropna()
    # sur une rampe croissante, une EMA causale ne peut jamais depasser le
    # dernier prix connu ; ici elle le fait, parce qu'elle voit en avant.
    ahead = (e20 > df.loc[e20.index, "close"]).sum()
    assert ahead > 0, "le look-ahead a disparu : retirer ce test et recalibrer"


def test_causal_htf_ema_never_uses_a_future_close():
    """Le correctif ne lit jamais au-dela de la barre courante."""
    df = _ramp()
    (e20,) = causal_htf_ema(df["close"], "4h", (20,), df.index)
    e20 = e20.dropna()
    assert (e20 <= df.loc[e20.index, "close"] + 1e-9).all()


def test_patch_removes_the_leak_and_restores():
    df = _ramp()
    original = patch(strategy)
    try:
        patched = strategy.compute_indicators(df.copy())
        e20 = patched["ema20_4h"].dropna()
        assert (e20 <= patched.loc[e20.index, "close"] + 1e-9).all()
    finally:
        strategy.compute_indicators = original
    # le comportement d'origine est bien restaure
    again = strategy.compute_indicators(df.copy())
    assert (again["ema20_4h"].dropna() > again["close"].reindex(
        again["ema20_4h"].dropna().index)).sum() > 0


@pytest.mark.parametrize("col", ["ema20_4h", "ema50_4h", "ema50d", "ema200d"])
def test_patch_covers_every_higher_timeframe_column(col):
    df = _ramp(3000)
    original = patch(strategy)
    try:
        out = strategy.compute_indicators(df.copy())
        s = out[col].dropna()
        assert len(s) > 0
        assert (s <= out.loc[s.index, "close"] + 1e-9).all(), f"{col} voit en avant"
    finally:
        strategy.compute_indicators = original
