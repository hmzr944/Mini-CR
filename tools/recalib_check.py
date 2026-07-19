#!/usr/bin/env python3
"""Recalibration mensuelle PRISM v33 — le bot ré-apprend de ses données.

Re-teste les 3 paramètres les plus sensibles au régime de marché sur les
données les plus récentes (IS complet + OOS 3 mois) :
  - SCORE_MIN_S   (qualité des shorts de continuation)
  - S_MARGIN_CAP  (budget Pattern S)
  - VOL_RATIO_C   (seuil volume breakout — cœur du Pattern C)

GARDE-FOU : cet outil ne modifie RIEN. Il signale un candidat seulement si
l'amélioration OOS dépasse +5 % ET que l'IS ne se dégrade pas — auquel cas
la procédure est : validation WFO 5 fenêtres complète avant tout déploiement,
jamais de changement sur un seul run.

Usage : python tools/recalib_check.py   (~8 min)
Cadence recommandée : 1×/mois, ou après tout changement de régime macro.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import backtest_v33 as bt
import prism.strategy as strat

CAPITAL = 1000.0
KW = dict(btc_filter=False, btc_1h_filter=True, asian_filter=True, next_bar_entry=True)
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

def pf_of(trades):
    gw = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    return gw / (gl + 1e-9)

def run():
    ti, _, _ = bt.run_backtest(sym_data, T_START, T_END, **KW)
    to, _, _ = bt.run_backtest(sym_data, OOS,     T_END, **KW)
    return pf_of(ti), pf_of(to), sum(t["pnl"] for t in ti)

PARAMS = [
    ("SCORE_MIN_S",  lambda: bt.SCORE_MIN_S,
     lambda v: setattr(bt, "SCORE_MIN_S", v),        [80, 82, 84]),
    ("S_MARGIN_CAP", lambda: bt.S_MARGIN_CAP,
     lambda v: setattr(bt, "S_MARGIN_CAP", v),       [500, 600, 700]),
    ("VOL_RATIO_C",  lambda: strat.VOL_RATIO_C,
     lambda v: setattr(strat, "VOL_RATIO_C", v),     [1.75, 1.90, 2.10]),
]

print("=" * 74)
print(f"RECALIBRATION CHECK — données jusqu'au {T_END.date()}")
print("=" * 74)

pf_is0, pf_oos0, pnl0 = run()
print(f"\nRéférence (config prod) : PF_IS={pf_is0:.2f}  PF_OOS={pf_oos0:.2f}  PnL={pnl0:+.0f}€\n")

candidates = []
for name, getter, setter, values in PARAMS:
    cur = getter()
    print(f"{name} (courant = {cur}) :")
    for v in values:
        if v == cur:
            print(f"  {v:>6} : PF_IS={pf_is0:.2f}  PF_OOS={pf_oos0:.2f}   (référence)")
            continue
        setter(v)
        try:
            pf_is, pf_oos, pnl = run()
        finally:
            setter(cur)
        flag = ""
        if pf_oos > pf_oos0 * 1.05 and pf_is >= pf_is0 * 0.98:
            flag = "  ← CANDIDAT (valider en WFO 5 fenêtres avant déploiement)"
            candidates.append((name, v, pf_is, pf_oos))
        print(f"  {v:>6} : PF_IS={pf_is:.2f}  PF_OOS={pf_oos:.2f}{flag}")
    print()

print("=" * 74)
if candidates:
    print("CANDIDATS DÉTECTÉS — procédure obligatoire avant déploiement :")
    for name, v, pf_is, pf_oos in candidates:
        print(f"  {name} = {v}  (PF_OOS {pf_oos0:.2f} → {pf_oos:.2f})")
    print("  1. WFO 5 fenêtres  2. ≥4/5 fenêtres améliorées  3. Déployer")
else:
    print("VERDICT : paramètres actuels toujours optimaux — aucun changement requis.")
