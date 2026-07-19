#!/usr/bin/env python3
"""Sweep de nuit PRISM v33 — exploration paramètres non testés + validation MC.

Génère un rapport progressif (écrit au fur et à mesure, lisible à tout moment)
dans overnight_report_YYYYMMDD.md. AUCUN déploiement automatique : tout candidat
qui bat la référence de +5%+ sur PF_OOS sans dégrader l'IS est marqué CANDIDAT
et doit être validé en WFO 5 fenêtres avant tout déploiement (règle du projet).

Usage : python tools/overnight_sweep.py  (tourne plusieurs heures, safe à interrompre)
"""
import sys, time
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import backtest_v33 as bt
import prism.strategy as strat

CAPITAL = 1000.0
KW = dict(btc_filter=False, btc_1h_filter=True, asian_filter=True, next_bar_entry=True)
bt.INITIAL_CAPITAL = CAPITAL
rng = np.random.default_rng(7)

REPORT = ROOT / f"overnight_report_{datetime.now():%Y%m%d}.md"

def w(text=""):
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)

w(f"# PRISM v33 — Sweep de nuit ({datetime.now():%Y-%m-%d %H:%M})\n")
w("Aucun déploiement automatique. Candidats = à valider en WFO avant tout changement.\n")

print("Chargement des données...", flush=True)
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

def dd_of(trades, cap=CAPITAL):
    eq = cap; peak = cap; mdd = 0.0
    for t in sorted(trades, key=lambda x: str(x.get("exit_ts", ""))):
        eq += t["pnl"]; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    return mdd * 100, eq

def run_full():
    ti, _, _ = bt.run_backtest(sym_data, T_START, T_END, **KW)
    to, _, _ = bt.run_backtest(sym_data, OOS,     T_END, **KW)
    dd, eq = dd_of(ti)
    return dict(n_is=len(ti), pf_is=pf_of(ti), dd_is=dd, eq_is=eq,
                n_oos=len(to), pf_oos=pf_of(to), pnl_oos=sum(t["pnl"] for t in to), ti=ti)

def mc_q5(trades, n_paths=5000):
    rs = []
    for t in sorted(trades, key=lambda x: str(x.get("exit_ts", ""))):
        eb = float(t.get("equity_after", 0)) - float(t["pnl"])
        if eb > 0: rs.append(float(t["pnl"]) / eb)
    rs = np.array(rs)
    n = len(rs)
    if n < 5: return None
    idx = rng.integers(0, n, size=(n_paths, n))
    eq = np.cumprod(1.0 + rs[idx], axis=1) * CAPITAL
    return float(np.percentile(eq[:, -1], 5))

t0 = time.time()
w("## Référence (config prod actuelle)\n")
ref = run_full()
ref_q5 = mc_q5(ref["ti"])
w(f"- IS : N={ref['n_is']} PF={ref['pf_is']:.2f} DD={ref['dd_is']:.1f}% eq={ref['eq_is']:.0f}€")
w(f"- OOS 3M : N={ref['n_oos']} PF={ref['pf_oos']:.2f} PnL={ref['pnl_oos']:+.0f}€")
w(f"- MC q5 (pire 5%) : {ref_q5:.0f}€\n")

candidates = []

def sweep(name, getter, setter, values, section):
    w(f"## {section}\n")
    cur = getter()
    w(f"Paramètre `{name}` — courant = {cur}\n")
    w(f"| Valeur | N_IS | PF_IS | DD_IS | N_OOS | PF_OOS | PnL_OOS |")
    w(f"|---|---|---|---|---|---|---|")
    for v in values:
        setter(v)
        try:
            r = run_full()
        except Exception as e:
            w(f"| {v} | ERREUR: {e} | | | | | |")
            setter(cur)
            continue
        setter(cur)
        tag = " **(référence)**" if v == cur else ""
        w(f"| {v}{tag} | {r['n_is']} | {r['pf_is']:.2f} | {r['dd_is']:.1f}% | "
          f"{r['n_oos']} | {r['pf_oos']:.2f} | {r['pnl_oos']:+.0f}€ |")
        if v != cur and r["pf_oos"] > ref["pf_oos"] * 1.05 and r["pf_is"] >= ref["pf_is"] * 0.95 and r["n_is"] >= ref["n_is"] * 0.7:
            candidates.append((name, v, r))
    w("")

try:
    sweep("ADX_MAX_C", lambda: bt.ADX_MAX_C, lambda v: setattr(bt, "ADX_MAX_C", v),
          [32, 35, 38, 42, 46], "1. ADX_MAX_C — plafond de trend établi (dead code confirmé)")

    sweep("TIME_STOP_H", lambda: bt.TIME_STOP_H, lambda v: setattr(bt, "TIME_STOP_H", v),
          [72, 84, 96, 108, 120, 144], "2. TIME_STOP_H — durée max de position")

    sweep("RR_RATIO", lambda: bt.RR_RATIO, lambda v: setattr(bt, "RR_RATIO", v),
          [3.0, 3.5, 4.0, 4.5, 5.0, 5.5], "3. RR_RATIO — ratio risk/reward Pattern C")

    sweep("SQUEEZE_BARS_C", lambda: strat.SQUEEZE_BARS_C, lambda v: setattr(strat, "SQUEEZE_BARS_C", v),
          [2, 3, 4, 5], "4. SQUEEZE_BARS_C — barres de compression requises")

    sweep("COOLDOWN_BARS", lambda: bt.COOLDOWN_BARS, lambda v: setattr(bt, "COOLDOWN_BARS", v),
          [4, 6, 8, 10, 12], "5. COOLDOWN_BARS — délai anti ré-entrée Pattern C")

    sweep("NEAR_ATH_THRESH", lambda: bt.NEAR_ATH_THRESH, lambda v: setattr(bt, "NEAR_ATH_THRESH", v),
          [0.02, 0.03, 0.04, 0.05], "6. NEAR_ATH_THRESH — sensibilité blocage shorts")

    sweep("MAX_POS", lambda: bt.MAX_POS, lambda v: setattr(bt, "MAX_POS", v),
          [6, 7, 8, 9, 10, 12], "7. MAX_POS — nombre de positions simultanées")

    sweep("MAX_MARGIN_RATIO", lambda: bt.MAX_MARGIN_RATIO, lambda v: setattr(bt, "MAX_MARGIN_RATIO", v),
          [0.60, 0.75, 0.90], "8. MAX_MARGIN_RATIO — plafond de marge totale")

    w("## 9. Validation des candidats (Monte Carlo)\n")
    if not candidates:
        w("Aucun candidat n'a battu la référence de +5% OOS sans dégrader l'IS.")
        w("**Conclusion : la config actuelle reste optimale sur toutes les zones testées.**\n")
    else:
        w(f"{len(candidates)} candidat(s) détecté(s) — validation WFO OBLIGATOIRE avant déploiement :\n")
        for name, v, r in candidates:
            q5 = mc_q5(r["ti"])
            better_q5 = "meilleur" if (q5 or 0) > (ref_q5 or 0) else "pire"
            w(f"### {name} = {v}")
            w(f"- PF_OOS {ref['pf_oos']:.2f} → {r['pf_oos']:.2f} | PF_IS {ref['pf_is']:.2f} → {r['pf_is']:.2f}")
            w(f"- MC q5 : {ref_q5:.0f}€ → {q5:.0f}€ ({better_q5})")
            w(f"- **Statut : CANDIDAT — nécessite validation WFO 5 fenêtres avant déploiement**\n")

except Exception:
    import traceback
    w(f"\n## ERREUR durant le sweep\n```\n{traceback.format_exc()}\n```")

elapsed = (time.time() - t0) / 60
w(f"\n---\n_Sweep terminé en {elapsed:.1f} min — {datetime.now():%Y-%m-%d %H:%M}_")
print(f"\nRapport écrit : {REPORT}")
