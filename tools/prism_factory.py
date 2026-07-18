#!/usr/bin/env python3
"""PRISM Factory — screening automatisé de l'univers OKX pour expansion whitelist.

Outil de recherche HORS-LIGNE : ne touche à rien du code live. Réutilisable
(manuel ou cron mensuel). Automatise la procédure validée du projet.

Pipeline :
  1. DÉCOUVERTE  : liste tous les perpétuels USDT OKX (API publique), classés
                   par volume 24h, top TOP_N retenus (hors univers actuel).
  2. INGESTION   : télécharge 12 mois de bougies 1H par candidat via le cache.
  3. SCREENING   : moteur V33 RÉEL (backtest_v33.run_backtest importé — pas de
                   réplique) sur {BTC + candidat}, IS 12M + OOS 3M.
                   Filtres (TOUS requis) :
                     N_IS ≥ 5 · WR_IS > 50% · PF_IS ≥ 2.0 · PF_OOS ≥ 1.5 · DD_IS ≤ 20%
  4. PORTEFEUILLE: pour chaque survivant, simulation 26+candidat vs baseline 26.
                   Barrière anti-faux-positif :
                     PF_IS ≥ 95% baseline · DD_IS ≤ baseline+3pts · PnL ≥ baseline
  5. RAPPORTS    : backtest_results/factory_report_<date>.md + validated_whitelist.json

IMPORTANT : validated_whitelist.json est une RECOMMANDATION. L'intégration d'un
symbole reste une décision manuelle (commit + validation segments), jamais automatique.

Usage : python tools/prism_factory.py [TOP_N=80]
"""
import sys, json, math, time, traceback
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import requests
import backtest_v33 as bt

TOP_N   = int(sys.argv[1]) if len(sys.argv) > 1 else 80
CAPITAL = 1000.0
KW = dict(btc_filter=False, btc_1h_filter=True, asian_filter=True, next_bar_entry=True)
bt.INITIAL_CAPITAL = CAPITAL

F_N_IS, F_WR_IS, F_PF_IS, F_PF_OOS, F_DD_IS = 5, 50.0, 2.0, 1.5, 20.0
P_PF_RATIO, P_DD_EXTRA = 0.95, 3.0

ORIGINAL = list(bt.SYMBOLS)
OUT_DIR  = ROOT / "backtest_results"
OUT_DIR.mkdir(exist_ok=True)
REPORT   = OUT_DIR / f"factory_report_{datetime.now():%Y%m%d}.md"
WHITELIST = OUT_DIR / "validated_whitelist.json"

def w(text=""):
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text, flush=True)

def discover_universe():
    r = requests.get("https://www.okx.com/api/v5/public/instruments",
                     params={"instType": "SWAP"}, timeout=20).json()
    swaps = {i["instId"][:-5] for i in r.get("data", [])
             if i.get("instId", "").endswith("-USDT-SWAP") and i.get("state") == "live"}
    r2 = requests.get("https://www.okx.com/api/v5/market/tickers",
                      params={"instType": "SWAP"}, timeout=20).json()
    vol = {}
    for t in r2.get("data", []):
        iid = t.get("instId", "")
        if iid.endswith("-USDT-SWAP"):
            try:
                vol[iid[:-5]] = float(t.get("volCcy24h") or 0)
            except ValueError:
                pass
    cands = [s for s in swaps if s not in ORIGINAL]
    cands.sort(key=lambda s: vol.get(s, 0), reverse=True)
    return cands[:TOP_N], vol

def stats(trades, cap=CAPITAL):
    if not trades:
        return dict(n=0, wr=0.0, pf=0.0, pnl=0.0, dd=0.0)
    wins = [t for t in trades if t["pnl"] > 0]
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    gw = sum(t["pnl"] for t in wins)
    eq = cap; peak = cap; mdd = 0.0
    for t in sorted(trades, key=lambda x: str(x.get("exit_ts", ""))):
        eq += t["pnl"]; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    return dict(n=len(trades), wr=len(wins)/len(trades)*100,
                pf=gw/(gl+1e-9), pnl=sum(t["pnl"] for t in trades), dd=mdd*100)

def screen_one(sym, btc_sd, t_start, t_end, oos_start):
    df = bt.load_or_fetch(sym, months=12, no_fetch=False)
    if df is None or len(df) < 2500:
        return None, "données insuffisantes (<2500 barres)"
    sd = bt.prepare(sym, df.copy())
    sym_data = {"BTC-USDT": btc_sd, sym: sd}
    ti, _, _ = bt.run_backtest(sym_data, t_start, t_end, **KW)
    to, _, _ = bt.run_backtest(sym_data, oos_start, t_end, **KW)
    si = stats([t for t in ti if t["sym"] == sym])
    so = stats([t for t in to if t["sym"] == sym])
    return (si, so), None

def eligible(si, so):
    if si["n"] < F_N_IS:      return f"N_IS={si['n']}<{F_N_IS}"
    if si["wr"] <= F_WR_IS:   return f"WR={si['wr']:.0f}%≤{F_WR_IS:.0f}%"
    if si["pf"] < F_PF_IS:    return f"PF_IS={si['pf']:.2f}<{F_PF_IS}"
    if so["pf"] < F_PF_OOS:   return f"PF_OOS={so['pf']:.2f}<{F_PF_OOS}"
    if si["dd"] > F_DD_IS:    return f"DD={si['dd']:.0f}%>{F_DD_IS:.0f}%"
    return None

def main():
    t0 = time.time()
    w(f"# PRISM Factory — screening univers ({datetime.now():%Y-%m-%d %H:%M})\n")
    w(f"Filtres individuels : N≥{F_N_IS} · WR>{F_WR_IS:.0f}% · PF_IS≥{F_PF_IS} · "
      f"PF_OOS≥{F_PF_OOS} · DD≤{F_DD_IS:.0f}%")
    w(f"Barrière portefeuille : PF≥{P_PF_RATIO*100:.0f}% baseline · DD≤base+{P_DD_EXTRA}pts · PnL≥baseline\n")

    cands, vol = discover_universe()
    w(f"Univers découvert : {len(cands)} candidats (top {TOP_N} par volume 24h, hors 26 actuels)\n")

    btc_df = bt.load_or_fetch("BTC-USDT", months=12, no_fetch=True)
    btc_sd = bt.prepare("BTC-USDT", btc_df.copy())
    btc_ts = sorted(btc_sd["ts_index"])
    t_start, t_end = pd.Timestamp(btc_ts[0]), pd.Timestamp(btc_ts[-1])
    oos_start = t_end - pd.DateOffset(months=3)

    results = []
    for i, sym in enumerate(cands, 1):
        try:
            res, err = screen_one(sym, btc_sd, t_start, t_end, oos_start)
        except Exception as e:
            res, err = None, f"erreur: {e}"
        if err:
            print(f"  [{i}/{len(cands)}] {sym}: {err}", flush=True)
            continue
        si, so = res
        miss = eligible(si, so)
        results.append(dict(sym=sym, vol24h=vol.get(sym, 0), is_=si, oos=so, miss=miss))
        mark = "PASS ◄" if miss is None else f"— {miss}"
        print(f"  [{i}/{len(cands)}] {sym}: N={si['n']} WR={si['wr']:.0f}% "
              f"PF={si['pf']:.2f} OOS={so['pf']:.2f} DD={si['dd']:.0f}% {mark}", flush=True)

    results.sort(key=lambda r: r["is_"]["pf"], reverse=True)
    w("## Classement individuel (PF_IS décroissant)\n")
    w("| Symbole | Vol 24h M$ | N_IS | WR | PF_IS | PF_OOS | DD | Verdict |")
    w("|---|---|---|---|---|---|---|---|")
    for r in results:
        v = "**PASS**" if r["miss"] is None else r["miss"]
        w(f"| {r['sym']} | {r['vol24h']/1e6:.0f} | {r['is_']['n']} | {r['is_']['wr']:.0f}% | "
          f"{r['is_']['pf']:.2f} | {r['oos']['pf']:.2f} | {r['is_']['dd']:.0f}% | {v} |")

    passers = [r for r in results if r["miss"] is None]
    w(f"\n## Étape portefeuille — {len(passers)} candidat(s) individuel(s)\n")
    validated = []
    if passers:
        sym_data_26 = {}
        for s in ORIGINAL:
            for months in [12, 9, 6]:
                df = bt.load_or_fetch(s, months=months, no_fetch=True)
                if df is not None and len(df) > 300:
                    sym_data_26[s] = bt.prepare(s, df.copy())
                    break
        tb, _, _ = bt.run_backtest(sym_data_26, t_start, t_end, **KW)
        base = stats(tb)
        w(f"Baseline 26 : N={base['n']} PF={base['pf']:.2f} DD={base['dd']:.1f}% PnL={base['pnl']:+.0f}€\n")
        for r in passers:
            sym = r["sym"]
            df = bt.load_or_fetch(sym, months=12, no_fetch=True)
            sd27 = dict(sym_data_26)
            sd27[sym] = bt.prepare(sym, df.copy())
            tc, _, _ = bt.run_backtest(sd27, t_start, t_end, **KW)
            comb = stats(tc)
            ok = (comb["pf"] >= base["pf"] * P_PF_RATIO
                  and comb["dd"] <= base["dd"] + P_DD_EXTRA
                  and comb["pnl"] >= base["pnl"])
            verdict = "VALIDÉ ◄◄" if ok else "REJETÉ (dégrade le portefeuille)"
            w(f"- **{sym}** : portefeuille PF {base['pf']:.2f}→{comb['pf']:.2f}, "
              f"DD {base['dd']:.1f}%→{comb['dd']:.1f}%, PnL {base['pnl']:+.0f}→{comb['pnl']:+.0f}€ → {verdict}")
            if ok:
                validated.append(dict(sym=sym, individual=dict(is_=r["is_"], oos=r["oos"]),
                                      portfolio=dict(baseline=base, combined=comb)))
    else:
        w("Aucun candidat n'a passé les filtres individuels.")

    json.dump(dict(generated=str(datetime.now()), top_n=TOP_N,
                   filters=dict(n=F_N_IS, wr=F_WR_IS, pf_is=F_PF_IS, pf_oos=F_PF_OOS, dd=F_DD_IS),
                   validated=validated),
              open(WHITELIST, "w", encoding="utf-8"), indent=2, default=str)
    w(f"\n## Verdict final : {len(validated)} symbole(s) validé(s) → {WHITELIST.name}")
    w("Rappel : intégration MANUELLE uniquement (commit + validation segments).")
    w(f"\n---\n_Terminé en {(time.time()-t0)/60:.1f} min_")

    try:
        import telegram_notif as tg
        if validated:
            syms = ", ".join(v["sym"] for v in validated)
            msg = (f"🛰 <b>PRISM Factory</b> — screening mensuel terminé\n"
                   f"Candidats scannés : {len(cands)}\n"
                   f"✅ <b>{len(validated)} symbole(s) validé(s)</b> : {syms}\n"
                   f"→ Voir {REPORT.name} — intégration manuelle requise.")
        else:
            msg = (f"🛰 <b>PRISM Factory</b> — screening mensuel terminé\n"
                   f"Candidats scannés : {len(cands)} | filtres passés : {len(passers)}\n"
                   f"Verdict : <b>0 symbole validé</b> — l'univers des 26 reste optimal.")
        tg._send(msg)
    except Exception:
        pass

if __name__ == "__main__":
    try:
        main()
    except Exception:
        w(f"\n## ERREUR\n```\n{traceback.format_exc()}\n```")
        raise
