#!/usr/bin/env python3
"""Suite de robustesse PRISM v33 — Monte Carlo + stress frais/slippage.

1. Monte Carlo (10 000 chemins) : bootstrap des rendements par trade
   (pnl / equity_avant) → distribution des equity finales et des drawdowns.
   Répond à : quelle est la probabilité réelle de DD > 20/30/40 % ?
   Quel est le pire trimestre plausible (quantile 5 %) ?

2. Stress exécution : slippage ×2 / ×4, commission ×1.5, combiné pire cas.
   Répond à : l'edge survit-il à des conditions d'exécution dégradées
   (fills réels moins bons que la simulation) ?

Usage : python tools/robustness_suite.py   (~5 min, cache local requis)
"""
import sys, math
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import backtest_v33 as bt

CAPITAL = 1000.0
N_PATHS = 10_000
KW = dict(btc_filter=False, btc_1h_filter=True, asian_filter=True, next_bar_entry=True)
rng = np.random.default_rng(42)

bt.INITIAL_CAPITAL = CAPITAL

sym_data = {}
for sym in bt.SYMBOLS:
    for months in [12, 9, 6]:
        df = bt.load_or_fetch(sym, months=months, no_fetch=True)
        if df is not None and len(df) > 300:
            sym_data[sym] = bt.prepare(sym, df.copy())
            break

btc_ts  = sorted(sym_data["BTC-USDT"]["ts_index"])
T_START = pd.Timestamp(btc_ts[0]); T_END = pd.Timestamp(btc_ts[-1])
OOS     = T_END - pd.DateOffset(months=3)

def trade_returns(trades):
    rs = []
    for t in sorted(trades, key=lambda x: str(x.get("exit_ts", ""))):
        eq_after = float(t.get("equity_after", 0))
        pnl = float(t["pnl"])
        eq_before = eq_after - pnl
        if eq_before > 0:
            rs.append(pnl / eq_before)
    return np.array(rs)

def mc(returns, n_paths=N_PATHS, label=""):
    n = len(returns)
    if n < 5:
        print(f"  [{label}] Pas assez de trades ({n})")
        return
    idx = rng.integers(0, n, size=(n_paths, n))
    paths = 1.0 + returns[idx]
    eq = np.cumprod(paths, axis=1) * CAPITAL
    eq_full = np.concatenate([np.full((n_paths, 1), CAPITAL), eq], axis=1)
    peaks = np.maximum.accumulate(eq_full, axis=1)
    dd = ((peaks - eq_full) / peaks).max(axis=1) * 100
    final = eq_full[:, -1]
    q = lambda a, p: float(np.percentile(a, p))
    print(f"  [{label}] N_trades={n}, {n_paths} chemins bootstrap :")
    print(f"    Equity finale : médiane {q(final,50):,.0f}€ | q5 {q(final,5):,.0f}€ | q95 {q(final,95):,.0f}€")
    print(f"    P(perte nette)          : {(final < CAPITAL).mean()*100:5.1f}%")
    print(f"    Max DD : médiane {q(dd,50):.1f}% | q95 {q(dd,95):.1f}%")
    print(f"    P(DD > 20%) : {(dd > 20).mean()*100:5.1f}%   "
          f"P(DD > 30%) : {(dd > 30).mean()*100:5.1f}%   "
          f"P(DD > 40%) : {(dd > 40).mean()*100:5.1f}%")

def run(start):
    tr, _, eq = bt.run_backtest(sym_data, start, T_END, **KW)
    return tr, eq

def pf_of(trades):
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    return gw / (gl + 1e-9)

print("=" * 74)
print("SUITE DE ROBUSTESSE PRISM v33")
print("=" * 74)

print("\n1) MONTE CARLO — incertitude de séquence (l'ordre des trades est le hasard)")
tr_is, _ = run(T_START)
tr_oos, _ = run(OOS)
mc(trade_returns(tr_is),  label="IS complet")
mc(trade_returns(tr_oos), label="OOS 3 mois")

print("\n2) STRESS EXÉCUTION — l'edge survit-il à des fills dégradés ?")
base_slip, base_exit, base_comm = bt.SLIPPAGE, bt.EXIT_SLIPPAGE, bt.COMMISSION
scenarios = [
    ("Baseline (sim)",        1.0, 1.0),
    ("Slippage ×2",           2.0, 1.0),
    ("Slippage ×4",           4.0, 1.0),
    ("Commission ×1.5",       1.0, 1.5),
    ("PIRE CAS (slip×4+com×1.5)", 4.0, 1.5),
]
print(f"  {'Scénario':<28} {'N':>4} {'PF':>6} {'PnL IS':>10} {'PnL OOS':>11}")
for label, s_mult, c_mult in scenarios:
    bt.SLIPPAGE      = base_slip * s_mult
    bt.EXIT_SLIPPAGE = base_exit * s_mult
    bt.COMMISSION    = base_comm * c_mult
    t_i, _ = run(T_START)
    t_o, _ = run(OOS)
    print(f"  {label:<28} {len(t_i):>4} {pf_of(t_i):>6.2f} "
          f"{sum(t['pnl'] for t in t_i):>+10.0f} {sum(t['pnl'] for t in t_o):>+11.0f}")
bt.SLIPPAGE, bt.EXIT_SLIPPAGE, bt.COMMISSION = base_slip, base_exit, base_comm

print("\nLecture : si le PIRE CAS garde PF > 2, l'edge est réel et non un artefact")
print("de simulation optimiste. Si P(DD>30%) est élevée, réduire la taille réelle.")
