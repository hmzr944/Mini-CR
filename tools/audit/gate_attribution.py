"""Quantitative rejection attribution for PRISM V33 Pattern C / D / MOM.

Walks every (symbol, bar) of the loaded history and records the FIRST gate
that rejects it, reproducing prism.strategy gate order exactly.
Answers: 'why did the live bot emit 0 signals?'
"""
import sys, math, collections, json
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
import pandas as pd
import backtest_v33 as B
from prism import strategy as S

CAUSAL = "--causal" in sys.argv
if CAUSAL:
    from tools.causal_htf import patch as _p; _p(S); B.prepare.__globals__['compute_indicators'] = S.compute_indicators

def gates_c(sd, bar):
    """Returns the name of the first failing gate, or 'PASS'."""
    if bar < S.SQUEEZE_BARS_C + 3: return "warmup"
    bbw, bbq = sd["bbw"], sd["bbw_q15"]
    if math.isnan(bbw[bar]) or math.isnan(bbq[bar]): return "nan_bbw"
    for i in range(1, S.SQUEEZE_BARS_C + 1):
        if math.isnan(bbw[bar-i]) or math.isnan(bbq[bar-i]) or bbw[bar-i] >= bbq[bar-i]:
            return "C1_no_squeeze_3bars"
    if bbw[bar] <= bbq[bar]: return "C2_no_expansion"
    adx, adx_prev = sd["adx"][bar], sd["adx"][bar-2]
    if math.isnan(adx) or math.isnan(adx_prev): return "nan_adx"
    if adx <= adx_prev + 1.5: return "C3_adx_not_rising_1.5"
    if adx < S.ADX_MIN_C: return "C4_adx_below_18"
    if sd["vol_ratio"][bar] < S.VOL_RATIO_C: return "C5_vol_ratio_below_1.90"
    cl, bu, bl = sd["close"][bar], sd["bb_upper"][bar], sd["bb_lower"][bar]
    bull = sd["ema20_4h"][bar] > sd["ema50_4h"][bar]
    if cl > bu and bull: return "PASS_BUY"
    if cl < bl and not bull: return "PASS_SELL"
    return "C6_no_breakout_or_4h_misaligned"

def gates_d(sd, bar):
    if bar < 152: return "warmup"
    e9,e21,e50 = sd["ema9"][bar], sd["ema21"][bar], sd["ema50"][bar]
    rsi, cl, vw = sd["rsi14"][bar], sd["close"][bar], sd["vwap"][bar]
    a4,b4 = sd["ema20_4h"][bar], sd["ema50_4h"][bar]
    adx, adx1 = sd["adx"][bar], sd["adx"][bar-1]
    if any(math.isnan(v) for v in [e9,e21,e50,rsi,cl,vw,a4,b4,adx,adx1]): return "nan"
    if not (adx > adx1): return "D1_adx_not_rising"
    bull = a4 > b4
    stackup, stackdn = e9>e21>e50, e9<e21<e50
    if not (stackup or stackdn): return "D2_ema_not_stacked"
    if stackup:
        if not (S.RSI_LONG_MIN_D <= rsi <= S.RSI_LONG_MAX_D): return "D3_rsi_outside_62_65"
        if not (cl > vw): return "D4_below_vwap"
        if not bull: return "D5_4h_misaligned"
        return "PASS_BUY"
    if not (S.RSI_SHORT_MIN_D <= rsi <= S.RSI_SHORT_MAX_D): return "D3_rsi_outside_35_52"
    if not (cl < vw): return "D4_above_vwap"
    if bull: return "D5_4h_misaligned"
    return "PASS_SELL"

def gates_mom(sd, bar):
    if bar < 60: return "warmup"
    e9,e21,e50 = sd["ema9"][bar], sd["ema21"][bar], sd["ema50"][bar]
    rsi, rsi1 = sd["rsi14"][bar], sd["rsi14"][bar-1]
    cl, vol = sd["close"][bar], sd["vol_ratio"][bar]
    a4,b4, adx = sd["ema20_4h"][bar], sd["ema50_4h"][bar], sd["adx"][bar]
    if any(math.isnan(v) for v in [e9,e21,e50,rsi,rsi1,cl,vol,a4,b4,adx]): return "nan"
    if not (e9>e21>e50): return "M1_ema_not_stacked"
    if not (a4>b4): return "M2_4h_not_bull"
    if not (S.RSI_MOM_MIN <= rsi <= S.RSI_MOM_MAX): return "M3_rsi_outside_40_50"
    if rsi <= rsi1: return "M4_rsi_not_rising"
    if adx < S.ADX_MIN_MOM: return "M5_adx_below_20"
    if cl < e50: return "M6_below_ema50"
    if vol < S.VOL_RATIO_MOM: return "M7_vol_below_1.2"
    return "PASS_BUY"

def main():
    sym_data = {}
    for sym in B.SYMBOLS:
        df = B.load_or_fetch(sym, 13, no_fetch=True)
        if df is not None and len(df) > 300:
            sym_data[sym] = B.prepare(sym, df.copy())
    print(f"symbols loaded: {len(sym_data)}  (causal={CAUSAL})")
    out = {}
    for name, fn in (("C", gates_c), ("D", gates_d), ("MOM", gates_mom)):
        cnt = collections.Counter(); total = 0
        for sym, sd in sym_data.items():
            n = len(sd["close"])
            for bar in range(n):
                cnt[fn(sd, bar)] += 1; total += 1
        out[name] = (cnt, total)
        print(f"\n=== Pattern {name} — {total:,} symbol-bars ===")
        for k, v in cnt.most_common():
            print(f"  {k:36s} {v:9,d}  {100*v/total:6.3f}%")
        p = sum(v for k, v in cnt.items() if k.startswith("PASS"))
        print(f"  {'-> raw signals':36s} {p:9,d}  {100*p/total:6.4f}%   1 per {total/max(p,1):,.0f} bars")
    json.dump({k: dict(v[0]) for k, v in out.items()},
              open(f"/tmp/gates_{'causal' if CAUSAL else 'orig'}.json","w"), indent=1)

main()
