"""PRISM — Source unique de vérité stratégie (single source of truth).

RECONSTRUCTION du 18/07/2026 après perte accidentelle du dossier projet.
Ce fichier est reconstruit à partir d'une copie RÉELLE et COMPLÈTE du
07/07/2026 (extraite d'un container Docker resté en vie pendant l'incident),
donc verbatim et haute fidélité — pas une approximation. Les fixes déployés
entre le 07/07 et le 16/07 (documentés en mémoire projet) ont été réappliqués
par-dessus :
  - MOM : filtre vol_ratio > VOL_RATIO_MOM ajouté, SCORE_MIN_MOM 60→65 (08/07)
  - PATTERN_D_BULL_EXTRA aligné sur WHITELIST (retrait DOGE/SOL/LINK/NEAR
    jamais validés, DOGE avait WR=0% IS — 08/07)
  - ADX_MAX_C : confirmé dead code (jamais appliqué), non réintroduit

Importé PAR backtest_v33 ET live_monitor_v33 (single source of truth).
Validation obligatoire avant usage réel : comparer un backtest 13M à la
référence connue (IS N=93, eq=29099.93€) — voir tools/ ou mémoire projet.
"""
import math
import numpy as np
import pandas as pd

# ── Constantes stratégie (canoniques, validées IS+OOS — cf mémoire projet) ──
ADX_MIN_C        = 18     # abaissé 20→18 (audit 25/06 : +371% vs +345% REF)
SQUEEZE_BARS_C   = 3
VOL_RATIO_C      = 1.90
ADX_MIN_MOM      = 20
RSI_MOM_MIN      = 40
RSI_MOM_MAX      = 50
VOL_RATIO_MOM    = 1.2    # filtre volume rebond (audit 08/07 : PF 0.84→2.40)
ADX_MIN_S        = 20
RSI_S_MIN        = 45
RSI_S_MAX        = 65
RSI_LONG_MIN_D   = 62
RSI_LONG_MAX_D   = 65
RSI_SHORT_MIN_D  = 35
RSI_SHORT_MAX_D  = 52
SCORE_MIN_V      = 999    # désactivé v33.9 (N=1 WR=0% OOS_3M, drag pur)
VOL_MULT_V       = 3.0
_ADX_1BAR        = True   # ADX rising 1 barre (audit 24/06 : +339% vs +249% avec 2 barres)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    df["ema9"]  = c.ewm(span=9,  adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    ml    = ema12 - ema26
    ms    = ml.ewm(span=9, adjust=False).mean()
    df["macd_hist"]  = ml - ms
    df["macd_slope"] = (ml - ms).diff()
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    df["rsi14"] = 100 - 100 / (1 + gain / (loss + 1e-10))
    bb_mid         = c.rolling(20).mean()
    bb_std         = c.rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std
    bbw            = (df["bb_upper"] - df["bb_lower"]) / (bb_mid + 1e-10)
    df["bbw"]      = bbw
    df["bbw_q15"]  = bbw.rolling(40).quantile(0.15)
    df["vol_ratio"] = v / (v.rolling(20).mean() + 1e-10)
    tp_val = (h + l + c) / 3
    df["vwap"] = (tp_val * v).rolling(24).sum() / (v.rolling(24).sum() + 1e-10)
    low14, high14 = l.rolling(14).min(), h.rolling(14).max()
    sk = 100 * (c - low14) / (high14 - low14 + 1e-10)
    df["stoch_k"] = sk
    df["stoch_d"] = sk.rolling(3).mean()
    tr   = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    dm_p = (h - h.shift()).clip(lower=0)
    dm_m = (l.shift() - l).clip(lower=0)
    dm_p = dm_p.where(dm_p > dm_m, 0)
    dm_m = dm_m.where(dm_m > dm_p, 0)
    atr14 = tr.ewm(com=13, adjust=False).mean()
    dip   = 100 * dm_p.ewm(com=13, adjust=False).mean() / (atr14 + 1e-10)
    dim   = 100 * dm_m.ewm(com=13, adjust=False).mean() / (atr14 + 1e-10)
    dx    = 100 * (dip - dim).abs() / (dip + dim + 1e-10)
    df["atr14"]    = atr14
    df["adx"]      = dx.ewm(com=13, adjust=False).mean()
    df["di_plus"]  = dip
    df["di_minus"] = dim
    df_4h    = df[["close"]].resample("4h").last().dropna()
    ema20_4h = df_4h["close"].ewm(span=20, adjust=False).mean()
    ema50_4h = df_4h["close"].ewm(span=50, adjust=False).mean()
    df["ema20_4h"] = ema20_4h.reindex(df.index, method="ffill")
    df["ema50_4h"] = ema50_4h.reindex(df.index, method="ffill")
    df_1d   = df[["close"]].resample("1D").last().dropna()
    ema50d  = df_1d["close"].ewm(span=50,  adjust=False).mean()
    ema200d = df_1d["close"].ewm(span=200, adjust=False).mean()
    df["ema50d"]  = ema50d.reindex(df.index,  method="ffill")
    df["ema200d"] = ema200d.reindex(df.index, method="ffill")
    return df


def _compute_scores(sd: dict, n: int):
    buy_sc  = np.zeros(n, dtype=np.int32)
    sell_sc = np.zeros(n, dtype=np.int32)
    for i in range(n):
        bs = ss = 0
        e9, e21, e50 = sd["ema9"][i], sd["ema21"][i], sd["ema50"][i]
        if not any(math.isnan(v) for v in [e9, e21, e50]):
            if e9  > e21: bs += 12
            elif e9  < e21: ss += 12
            if e21 > e50: bs += 13
            elif e21 < e50: ss += 13
        r = sd["rsi14"][i]
        if not math.isnan(r):
            if 40 <= r <= 65:  bs += 15
            elif 35 <= r < 40: bs += 8
            elif 65 < r <= 70: bs += 5
            if 35 <= r <= 60:  ss += 15
            elif 60 < r <= 65: ss += 8
            elif 30 <= r < 35: ss += 5
        mh, mhs = sd["macd_hist"][i], sd["macd_slope"][i]
        if not any(math.isnan(v) for v in [mh, mhs]):
            if mh  > 0: bs += 12
            elif mh  < 0: ss += 12
            if mhs > 0: bs += 8
            elif mhs < 0: ss += 8
        vr = sd["vol_ratio"][i]
        if not math.isnan(vr):
            pts = 10 if vr >= 1.5 else 6 if vr >= 1.0 else 3 if vr >= 0.7 else 0
            bs += pts; ss += pts
        av = sd["adx"][i]
        if not math.isnan(av):
            pts = 10 if av >= 25 else 6 if av >= 18 else 0
            bs += pts; ss += pts
        cl, vw = sd["close"][i], sd["vwap"][i]
        if not any(math.isnan(v) for v in [cl, vw]):
            if cl > vw:  bs += 10
            elif cl < vw: ss += 10
        sk, sd_ = sd["stoch_k"][i], sd["stoch_d"][i]
        if not any(math.isnan(v) for v in [sk, sd_]):
            if sk > sd_ and sk < 75: bs += 10
            if sk < sd_ and sk > 25: ss += 10
        buy_sc[i]  = min(bs, 100)
        sell_sc[i] = min(ss, 100)
    return buy_sc, sell_sc


def _score_size_mult(score: int, pattern: str) -> float:
    """Multiplie la taille de position selon la conviction du signal (score).
    v33.5 : Kelly calibré — réduit signaux faibles, double les signaux forts.
    v33.7 : D différencié — sc>=92 → 1.8x — validé IS+OOS 4/4 (01/07)."""
    if pattern == "C":
        if score >= 85: return 2.00
        if score >= 75: return 1.20
        return 0.80
    if score >= 92: return 1.80
    if score >= 85: return 1.20
    return 1.00


def prepare(sym: str, df: pd.DataFrame) -> dict:
    df = compute_indicators(df)
    ts_idx    = df.index.tolist()
    ts_to_pos = {ts: i for i, ts in enumerate(ts_idx)}
    cols = ["close","high","low","open","atr14","adx","bbw","bbw_q15",
            "bb_upper","bb_lower","vol_ratio","ema9","ema21","ema50",
            "macd_hist","macd_slope","rsi14","stoch_k","stoch_d","vwap",
            "di_plus","di_minus","ema20_4h","ema50_4h","ema50d","ema200d"]
    sd = {"name": sym, "ts_index": ts_idx, "ts_to_pos": ts_to_pos}
    for col in cols:
        sd[col] = df[col].values
    n = len(ts_idx)
    sd["buy_sc"], sd["sell_sc"] = _compute_scores(sd, n)
    return sd


def check_pattern_c(sd: dict, bar: int, adx_val: float):
    if bar < SQUEEZE_BARS_C + 3:
        return None
    try:
        bbw_arr, bbwq_arr = sd["bbw"], sd["bbw_q15"]
        bbw_cur, bbwq_cur = bbw_arr[bar], bbwq_arr[bar]
        if math.isnan(bbw_cur) or math.isnan(bbwq_cur):
            return None
        for i in range(1, SQUEEZE_BARS_C + 1):
            bw, bq = bbw_arr[bar - i], bbwq_arr[bar - i]
            if math.isnan(bw) or math.isnan(bq) or bw >= bq:
                return None
        if bbw_cur <= bbwq_cur:
            return None
        adx_prev = sd["adx"][bar - 2]
        if math.isnan(adx_val) or math.isnan(adx_prev):
            return None
        if adx_val <= adx_prev + 1.5 or adx_val < ADX_MIN_C:
            return None
        close    = sd["close"][bar]
        bb_upper = sd["bb_upper"][bar]
        bb_lower = sd["bb_lower"][bar]
        vol_r    = sd["vol_ratio"][bar]
        ema20_4h = sd["ema20_4h"][bar]
        ema50_4h = sd["ema50_4h"][bar]
        if any(math.isnan(v) for v in [close, bb_upper, bb_lower, vol_r, ema20_4h, ema50_4h]):
            return None
        if vol_r < VOL_RATIO_C:
            return None
        asset_4h_bull = ema20_4h > ema50_4h
        if close > bb_upper and asset_4h_bull:
            return "BUY"
        if close < bb_lower and not asset_4h_bull:
            return "SELL"
    except Exception:
        pass
    return None


def check_pattern_d(sd: dict, bar: int, adx_val: float):
    """Pattern D : EMA9 > EMA21 > EMA50 (haussier) ou inverse (baissier)
    + RSI zone tendance + prix vs VWAP + alignement 4H. ADX doit monter
    depuis 1 barre (audit 24/06 : +339% vs +249% avec 2 barres)."""
    if bar < 152:
        return None
    try:
        e9, e21, e50 = sd["ema9"][bar], sd["ema21"][bar], sd["ema50"][bar]
        if any(math.isnan(v) for v in [e9, e21, e50]):
            return None
        rsi = sd["rsi14"][bar]
        if math.isnan(rsi):
            return None
        cl, vw = sd["close"][bar], sd["vwap"][bar]
        if any(math.isnan(v) for v in [cl, vw]):
            return None
        ema20_4h = sd["ema20_4h"][bar]
        ema50_4h = sd["ema50_4h"][bar]
        if any(math.isnan(v) for v in [ema20_4h, ema50_4h]):
            return None
        adx_1 = sd["adx"][bar - 1]
        if math.isnan(adx_1):
            return None
        if not (adx_val > adx_1):
            return None
        asset_4h_bull = ema20_4h > ema50_4h
        if e9 > e21 > e50 and RSI_LONG_MIN_D <= rsi <= RSI_LONG_MAX_D and cl > vw and asset_4h_bull:
            return "BUY"
        if e9 < e21 < e50 and RSI_SHORT_MIN_D <= rsi <= RSI_SHORT_MAX_D and cl < vw and not asset_4h_bull:
            return "SELL"
    except Exception:
        pass
    return None


def check_pattern_r(sd: dict, bar: int) -> "str | None":
    """Mean reversion sur extrêmes Bollinger Bands en marché latéral.
    Rejeté à l'usage (PF<1 en segments) — présent pour compatibilité,
    désactivé en prod via SCORE_MIN_R=85."""
    if bar < 30:
        return None
    try:
        cl       = sd["close"][bar]
        bb_upper = sd["bb_upper"][bar]
        bb_lower = sd["bb_lower"][bar]
        rsi      = sd["rsi14"][bar]
        sk       = sd["stoch_k"][bar]
        sd_val   = sd["stoch_d"][bar]
        if any(math.isnan(v) for v in [cl, bb_upper, bb_lower, rsi, sk, sd_val]):
            return None
        bb_range = bb_upper - bb_lower
        if bb_range <= 0:
            return None
        bb_pos = (cl - bb_lower) / bb_range
        if bb_pos < 0.30 and rsi < 43 and sk < 43 and sk > sd_val:
            return "BUY"
        if bb_pos > 0.70 and rsi > 57 and sk > 57 and sk < sd_val:
            return "SELL"
    except Exception:
        pass
    return None


def check_pattern_v(sd: dict, bar: int, adx_val: float = None) -> "str | None":
    """Volume Surge : vol 3x + BB extrême + score — hybride R/C indépendant
    du régime. SCORE_MIN_V=999 = désactivé en pratique."""
    if bar < 30:
        return None
    try:
        cl       = float(sd["close"][bar])
        vr       = float(sd["vol_ratio"][bar])
        bb_upper = float(sd["bb_upper"][bar])
        bb_lower = float(sd["bb_lower"][bar])
        rsi      = float(sd["rsi14"][bar])
        ema21    = float(sd["ema21"][bar])
        if any(math.isnan(v) for v in [cl, vr, bb_upper, bb_lower, rsi, ema21]):
            return None
        if vr < VOL_MULT_V:
            return None
        bb_range = bb_upper - bb_lower
        if bb_range <= 0:
            return None
        bb_pos = (cl - bb_lower) / bb_range
        buy_sc  = int(sd["buy_sc"][bar])
        sell_sc = int(sd["sell_sc"][bar])
        if bb_pos < 0.35 and rsi < 50 and cl < ema21 and buy_sc >= SCORE_MIN_V:
            return "BUY"
        if bb_pos > 0.65 and rsi > 50 and cl > ema21 and sell_sc >= SCORE_MIN_V:
            return "SELL"
    except Exception:
        pass
    return None


def check_pattern_mom(sd: dict, bar: int, adx_val: float) -> "str | None":
    """Bull pullback: EMA9>21>50 + 4H bull (asset) + RSI 40-50 dip rebondissant
    + close>EMA50. Filtre vol_ratio > VOL_RATIO_MOM : valide le rebond par le
    volume (audit 08/07 : PF 0.84→2.40)."""
    if bar < 60:
        return None
    try:
        e9  = float(sd["ema9"][bar]);  e21 = float(sd["ema21"][bar]); e50 = float(sd["ema50"][bar])
        rsi = float(sd["rsi14"][bar]); rsi1 = float(sd["rsi14"][bar - 1])
        cl  = float(sd["close"][bar]); vol = float(sd["vol_ratio"][bar])
        e20_4h = float(sd["ema20_4h"][bar]); e50_4h = float(sd["ema50_4h"][bar])
        if any(math.isnan(v) for v in [e9, e21, e50, rsi, rsi1, cl, e20_4h, e50_4h, adx_val, vol]):
            return None
        if not (e9 > e21 > e50):                          return None
        if not (e20_4h > e50_4h):                         return None
        if not (RSI_MOM_MIN <= rsi <= RSI_MOM_MAX):       return None
        if rsi <= rsi1:                                   return None
        if adx_val < ADX_MIN_MOM:                         return None
        if cl < e50:                                      return None
        if vol < VOL_RATIO_MOM:                           return None  # fix 08/07
        return "BUY"
    except Exception:
        pass
    return None


def check_pattern_s(sd: dict, bar: int, adx_val: float) -> "str | None":
    """Failed rally en bear macro : EMA9<21<50 + MACD<0 + RSI 45-65 déclinant
    + close<EMA21."""
    if bar < 52:
        return None
    try:
        e9  = float(sd["ema9"][bar]);  e21 = float(sd["ema21"][bar]); e50 = float(sd["ema50"][bar])
        rsi = float(sd["rsi14"][bar]); rsi2 = float(sd["rsi14"][bar - 2])
        mh  = float(sd["macd_hist"][bar])
        cl  = float(sd["close"][bar])
        if any(math.isnan(v) for v in [e9, e21, e50, rsi, rsi2, mh, cl, adx_val]):
            return None
        if not (e9 < e21 < e50):                   return None
        if mh >= 0:                                 return None
        if not (RSI_S_MIN <= rsi <= RSI_S_MAX):    return None
        if rsi >= rsi2:                             return None
        if cl >= e21:                               return None
        if adx_val < ADX_MIN_S:                    return None
        return "SELL"
    except Exception:
        pass
    return None
