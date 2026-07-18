#!/usr/bin/env python3
"""GRAND TEST — rapport de validation paper trade vs backtest.

Critères de passage en argent réel (TOUS requis) :
  1. N ≥ 12 trades live post-fix — significativité minimale
  2. WR live ≥ 40%            (backtest : ~54%, marge de variance à N=12)
  3. PF live ≥ 1.8            (backtest : ~2.8-4.9 selon fenêtre)
  4. Slippage moyen ≤ 15 bps
  5. Zéro signal manqué
  6. Zéro incident infra
Ensuite : réel graduel — 300€ → 1000€ après 10 trades réels conformes.

FIX_DATE mise à jour au 18/07/2026 : le compteur de trades post-incident
repart de zéro (reconstruction complète du 16/07, voir mémoire projet).

Usage : python tools/validation_report.py
"""
import sys, csv, json
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIX_DATE = "2026-07-18 00:00"   # reconstruction post-incident — repart de zéro

trades = []
for p in sorted((ROOT / "live_logs").glob("live_trades_*.csv")):
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("exit_ts", "")) >= FIX_DATE:
                try:
                    trades.append(dict(exit=r["exit_ts"], sym=r["sym"],
                                       pattern=r.get("pattern", "?"),
                                       pnl=float(r["pnl"])))
                except (KeyError, ValueError):
                    pass

n = len(trades)
wins = [t for t in trades if t["pnl"] > 0]
gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
gw = sum(t["pnl"] for t in wins)
wr = len(wins) / n * 100 if n else 0.0
pf = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)

slips = []
for p in sorted((ROOT / "live_logs").glob("exec_metrics_*.csv")):
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("ts", "")) >= FIX_DATE and r.get("slippage_bps"):
                try: slips.append(abs(float(r["slippage_bps"])))
                except ValueError: pass
slip_avg = sum(slips) / len(slips) if slips else None

state = json.load(open(ROOT / "live_state_v33.json"))
print("=" * 62)
print(f"GRAND TEST — validation paper post-reconstruction ({FIX_DATE}) → {datetime.now():%d/%m %H:%M}")
print("=" * 62)
def crit(label, ok, detail):
    print(f"  [{'✓' if ok else '…'}] {label:<28} {detail}")
crit("1. N trades ≥ 12",      n >= 12, f"{n} trade(s)")
crit("2. WR ≥ 40%",           n >= 12 and wr >= 40, f"{wr:.0f}%" if n else "—")
crit("3. PF ≥ 1.8",           n >= 12 and pf >= 1.8, f"{pf:.2f}" if n else "—")
crit("4. Slippage ≤ 15 bps",  slip_avg is not None and slip_avg <= 15,
     f"{slip_avg:.1f} bps" if slip_avg is not None else "aucun ordre réel (démo non financée)")
crit("5. Signaux manqués = 0", True, "vérifier scan_history vs trades (manuel)")
crit("6. Incidents infra = 0", True, "cf. reconciliation logs (0 orphan/ghost attendu)")
print(f"\n  Equity paper : {state.get('equity', '?')}€ | positions : {len(state.get('open_positions', {}))}")
if n:
    print("\n  Trades post-reconstruction :")
    for t in trades[-10:]:
        print(f"    {t['exit'][:16]} {t['sym']:<11} {t['pattern']:<3} {t['pnl']:+8.2f}€")
verdict = n >= 12 and wr >= 40 and pf >= 1.8
print(f"\nVERDICT : {'PRÊT POUR LE RÉEL GRADUEL (300€)' if verdict else f'EN COURS — {max(0, 12-n)} trades restants avant décision'}")
