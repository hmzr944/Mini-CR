#!/usr/bin/env python3
"""PRISM Incubator — incubation FORWARD des candidats hors whitelist.

Complément de prism_factory (qui backteste le passé) : l'incubateur mesure les
candidats sur des données qui N'EXISTAIENT PAS quand ils sont entrés en
incubation — de l'out-of-sample par construction, immunisé contre le sur-fit.

Fonctionnement (tâche quotidienne) :
  1. Univers : top INCUBATE_N perps USDT par volume (hors 26), rafraîchi à
     chaque run ; tout nouveau symbole reçoit sa date d'entrée en incubation.
  2. Pour chaque candidat : moteur V33 RÉEL ({BTC + candidat}, 12 mois),
     mais seuls les trades dont entry_ts > date d'incubation sont comptés.
  3. Stats forward cumulées dans incubator_state.json.
  4. Alerte Telegram quand un candidat atteint N≥5 trades forward avec
     WR>50% et PF>2.0 → candidat à la revue MANUELLE (barrière portefeuille
     + validation segments avant toute intégration — règle charte).

Usage : python tools/incubator.py   (cron quotidien recommandé)
"""
import sys, json, time, traceback
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
import pandas as pd
import backtest_v33 as bt

INCUBATE_N = 40
CAPITAL    = 1000.0
KW = dict(btc_filter=False, btc_1h_filter=True, asian_filter=True, next_bar_entry=True)
bt.INITIAL_CAPITAL = CAPITAL

STATE = ROOT / "incubator_state.json"
F_N, F_WR, F_PF = 5, 50.0, 2.0

def load_state():
    if STATE.exists():
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {"candidates": {}, "alerted": []}

def discover():
    r = requests.get("https://www.okx.com/api/v5/market/tickers",
                     params={"instType": "SWAP"}, timeout=20).json()
    rows = []
    for t in r.get("data", []):
        iid = t.get("instId", "")
        if iid.endswith("-USDT-SWAP"):
            try:
                rows.append((iid[:-5], float(t.get("volCcy24h") or 0)))
            except ValueError:
                pass
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows if s not in set(bt.SYMBOLS)][:INCUBATE_N]

def main():
    st = load_state()
    now = str(datetime.now())
    universe = discover()
    for sym in universe:
        if sym not in st["candidates"]:
            st["candidates"][sym] = {"incubated_since": now, "n": 0, "wr": 0.0,
                                     "pf": 0.0, "pnl": 0.0, "last_check": ""}
            print(f"  + {sym} entre en incubation ({now[:16]})")

    btc_df = bt.load_or_fetch("BTC-USDT", months=12, no_fetch=False)
    btc_sd = bt.prepare("BTC-USDT", btc_df.copy())
    btc_ts = sorted(btc_sd["ts_index"])
    t_start, t_end = pd.Timestamp(btc_ts[0]), pd.Timestamp(btc_ts[-1])

    promoted = []
    for sym, info in list(st["candidates"].items()):
        try:
            df = bt.load_or_fetch(sym, months=12, no_fetch=False)
            if df is None or len(df) < 500:
                continue
            sd = bt.prepare(sym, df.copy())
            ti, _, _ = bt.run_backtest({"BTC-USDT": btc_sd, sym: sd}, t_start, t_end, **KW)
            since = pd.Timestamp(info["incubated_since"])
            fwd = [t for t in ti if t["sym"] == sym and pd.Timestamp(t["entry_ts"]) >= since]
            if fwd:
                wins = [t for t in fwd if t["pnl"] > 0]
                gl = abs(sum(t["pnl"] for t in fwd if t["pnl"] <= 0))
                info.update(n=len(fwd), wr=len(wins)/len(fwd)*100,
                            pf=(sum(t["pnl"] for t in wins) / gl) if gl > 0 else 99.0,
                            pnl=sum(t["pnl"] for t in fwd))
            info["last_check"] = now
            if (info["n"] >= F_N and info["wr"] > F_WR and info["pf"] >= F_PF
                    and sym not in st["alerted"]):
                promoted.append((sym, info))
                st["alerted"].append(sym)
            print(f"  {sym}: forward N={info['n']} WR={info['wr']:.0f}% "
                  f"PF={info['pf']:.2f} PnL={info['pnl']:+.0f}€", flush=True)
        except Exception as e:
            print(f"  {sym}: erreur {e}", flush=True)

    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1, default=str)

    if promoted:
        try:
            import telegram_notif as tg
            lines = [f"🧪 <b>INCUBATEUR</b> : candidat(s) forward-validé(s) !"]
            for sym, i in promoted:
                lines.append(f"• <b>{sym}</b> — N={i['n']} WR={i['wr']:.0f}% "
                             f"PF={i['pf']:.2f} (depuis {i['incubated_since'][:10]})")
            lines.append("→ Revue MANUELLE requise : barrière portefeuille + "
                         "validation segments avant toute intégration.")
            tg._send("\n".join(lines))
        except Exception:
            pass
    n_active = sum(1 for i in st["candidates"].values() if i["n"] > 0)
    print(f"\nIncubateur : {len(st['candidates'])} candidats suivis, "
          f"{n_active} avec trades forward, {len(promoted)} alerte(s).")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
        raise
