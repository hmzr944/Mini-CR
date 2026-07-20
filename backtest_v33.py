#!/usr/bin/env python3
"""PRISM v33 — Moteur de backtest.

RECONSTRUCTION du 18/07/2026 après perte du dossier projet (voir prism/strategy.py
pour le détail de l'incident). Ce fichier n'a PAS de sauvegarde source retrouvée
(contrairement à live_monitor_v33.py, récupéré verbatim depuis un container Docker
vivant) — reconstruit à partir des fragments et valeurs documentés dans la mémoire
projet et l'historique de conversation. Utilise prism/strategy.py (lui-même
verbatim) comme source unique de vérité pour toute la logique de patterns.

FIABILITÉ : structurellement fidèle (mêmes patterns, mêmes seuils, même
architecture de sizing/exits) mais NON garanti bit-exact avec l'ancien moteur.
À utiliser pour la recherche / le screening — PAS pour trader en réel sans
revalidation. Le bot live (live_monitor_v33.py) n'en dépend pas.
"""
from __future__ import annotations
import argparse
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from prism.strategy import (
    ADX_MIN_C, ADX_MIN_MOM, ADX_MIN_S, RSI_LONG_MAX_D, RSI_LONG_MIN_D,
    RSI_MOM_MAX, RSI_MOM_MIN, RSI_SHORT_MAX_D, RSI_SHORT_MIN_D, RSI_S_MAX,
    RSI_S_MIN, SCORE_MIN_V, SQUEEZE_BARS_C, VOL_MULT_V, VOL_RATIO_C,
    VOL_RATIO_MOM, _ADX_1BAR,
    compute_indicators, prepare, _compute_scores, _score_size_mult,
    check_pattern_c, check_pattern_d, check_pattern_r, check_pattern_s,
    check_pattern_mom, check_pattern_v,
)

# ── Config ───────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "backtest_data"
RESULT_DIR = BASE_DIR / "backtest_results"
DATA_DIR.mkdir(exist_ok=True)
RESULT_DIR.mkdir(exist_ok=True)

OKX_URL      = "https://www.okx.com/api/v5/market/history-candles"
FUNDING_URL  = "https://www.okx.com/api/v5/public/funding-rate-history"

SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT",
    "AVAX-USDT", "ADA-USDT", "LINK-USDT", "XRP-USDT", "DOT-USDT", "ATOM-USDT",
    "LTC-USDT", "DOGE-USDT", "NEAR-USDT", "TRX-USDT", "INJ-USDT", "OP-USDT",
    "ARB-USDT", "SUI-USDT", "UNI-USDT", "AAVE-USDT", "TIA-USDT",
    "SEI-USDT", "HBAR-USDT", "ICP-USDT", "JUP-USDT",
    "STX-USDT", "WIF-USDT",
]

INITIAL_CAPITAL = 1000.0
COMMISSION      = 0.001
SLIPPAGE        = 0.0005
EXIT_SLIPPAGE   = 0.0003
ATR_SL_MULT     = 1.5
RR_RATIO        = 4.0
ATR_SL_MIN_C    = 0.006
ATR_SL_MAX_C    = 0.025
TIME_STOP_H     = 96
COOLDOWN_BARS   = 8
BASE_LEVERAGE   = 10
HIGH_LEVERAGE   = 15
DAILY_LOSS_CAP  = 0.12
EQUITY_FLOOR    = 200.0
MAX_MARGIN_RATIO = 0.60
RISK_PCT        = 0.28
MAX_POS         = 8
SCORE_MIN       = 70
ADX_MAX_C       = 38  # NON APPLIQUÉ (confirmé 09/07) : dead code, jamais branché

SCORE_MIN_D     = 85
ADX_MIN_D       = 28
RISK_PCT_D      = 0.16
BASE_LEVERAGE_D = 8
HIGH_LEVERAGE_D = 10

PATTERN_D_WHITELIST = {"DOT-USDT", "OP-USDT", "TRX-USDT", "ETH-USDT", "ADA-USDT"}
PATTERN_D_BULL_EXTRA = PATTERN_D_WHITELIST
PATTERN_C_BLACKLIST = {"ICP-USDT", "DOGE-USDT", "INJ-USDT"}
PATTERN_S_BLACKLIST = {"STX-USDT", "LINK-USDT", "DOGE-USDT", "OP-USDT", "AVAX-USDT"}

RISK_PCT_R      = 0.12
BASE_LEVERAGE_R = 5
ATR_SL_MULT_R   = 1.0
RR_RATIO_R      = 2.0
ATR_SL_MIN_R    = 0.004
ATR_SL_MAX_R    = 0.012
SCORE_MIN_R     = 85
COOLDOWN_BARS_R = 8
R_CB_LOSSES     = 3
R_CB_PAUSE_H    = 48

BTC_CRASH_THRESH   = -0.04
SURVIVE_DD         = 0.12
PANIC_VEL          = 0.025
PANIC_THRESH       = 0.80
NEAR_ATH_THRESH    = 0.03
BTC_BEAR_R_THRESH  = 0.12
ADX_RANGE_MAX      = 22
BTC_ADX_MIN_BEAR_D = 28  # BTC ADX minimum pour D shorts en bear_mode (trending bear seulement)
BEAR_D_NONWL_SCORE_MIN = 92  # score minimum pour D non-whitelist en bear_mode
MAX_BEAR_D_NONWL_POS   = 3   # max 3 shorts D non-whitelist simultanés (corrélation)

SCORE_MIN_S     = 82
S_MARGIN_CAP    = 600.0
BASE_LEVERAGE_S = 8
RR_RATIO_S      = 4.0
ATR_SL_MULT_S   = 1.5
ATR_SL_MIN_S    = 0.006
ATR_SL_MAX_S    = 0.025
COOLDOWN_BARS_S = 6

RISK_PCT_MOM      = 0.08
BASE_LEVERAGE_MOM = 8
RR_RATIO_MOM      = 3.0
COOLDOWN_BARS_MOM = 12
MAX_POS_MOM       = 0   # désactivé en backtest par défaut ; live=2

BEAR_MARGIN_SCALE = 0.50
MONTHLY_CB_PCT    = 0.15
MONTHLY_CB_SCALE  = 0.60
BULL_LEV_SCORE    = 88
BULL_LEV_MAX      = 18

RISK_PCT_V      = 0.05
BASE_LEVERAGE_V = 8
RR_RATIO_V      = 3.0
ATR_SL_MULT_V   = 1.2
ATR_SL_MIN_V    = 0.005
ATR_SL_MAX_V    = 0.020
COOLDOWN_BARS_V = 5

c_consec_loss_pause_default = 3
c_consec_loss_pause_h_default = 72


# ── Données ──────────────────────────────────────────────────────────────────
def fetch_symbol(sym: str, months: int = 6) -> pd.DataFrame | None:
    bars_needed = months * 30 * 24 + 400
    all_rows, after = [], None
    page_retries = 0
    while len(all_rows) < bars_needed:
        params = {"instId": sym, "bar": "1H", "limit": 100}
        if after:
            params["after"] = after
        try:
            r = requests.get(OKX_URL, params=params, timeout=20)
            data = r.json()
        except Exception:
            page_retries += 1
            if page_retries > 8:
                break
            time.sleep(3 * page_retries)
            continue
        code = data.get("code", "")
        if code == "50011":
            page_retries += 1
            time.sleep(5 * page_retries)
            continue
        if code != "0" or not data.get("data"):
            break
        batch = data["data"]
        all_rows.extend(batch)
        page_retries = 0
        if len(batch) < 100:
            break
        after = batch[-1][0]
        time.sleep(0.25)

    if len(all_rows) < 200:
        return None
    df = pd.DataFrame(all_rows,
                      columns=["timestamp", "open", "high", "low", "close",
                               "volume", "a", "b", "confirm"])
    # BUG FIX 09/07/2026 : history-candles inclut la bougie en formation.
    df = df[df["confirm"].astype(str) == "1"]
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True).dt.tz_convert(None)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")].dropna(subset=["close"])
    return df


def load_or_fetch(sym: str, months: int, no_fetch: bool) -> pd.DataFrame | None:
    path = DATA_DIR / f"{sym.replace('-', '_')}_{months}m.csv"
    if path.exists() and no_fetch:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    if path.exists():
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if age_h < 6:
            return pd.read_csv(path, index_col=0, parse_dates=True)
    if no_fetch:
        return None
    df = fetch_symbol(sym, months)
    if df is not None:
        df.to_csv(path)
    return df


# ── Moteur backtest ──────────────────────────────────────────────────────────
def run_backtest(sym_data: dict, start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                 activation_atr: float = 0.0, trail_distance_atr: float = 0.0,
                 time_stop_c: int = 0,
                 score_min_d: int = None, adx_min_d: float = None,
                 no_btc_d: bool = False,
                 btc_filter: bool = False,
                 btc_1h_filter: bool = True,
                 asian_filter: bool = True,
                 bad_hours_c: set = None,
                 risk_pct_c: float = None,
                 score_mult_fn=None,
                 c_blacklist: set = None,
                 use_c_blacklist: bool = True,
                 next_bar_entry: bool = True,
                 partial_ratio: float = 0.0,
                 partial_rr: float = 2.5,
                 c_consec_loss_pause: int = 3,
                 c_consec_loss_pause_h: int = 72):
    if score_min_d is None: score_min_d = SCORE_MIN_D
    if adx_min_d   is None: adx_min_d   = ADX_MIN_D
    if risk_pct_c  is None: risk_pct_c  = RISK_PCT
    if score_mult_fn is None: score_mult_fn = _score_size_mult
    if c_blacklist is None: c_blacklist = PATTERN_C_BLACKLIST

    btc_sd  = sym_data.get("BTC-USDT")
    all_ts  = sorted(btc_sd["ts_index"])
    test_ts = [ts for ts in all_ts if start_ts <= ts <= end_ts]

    equity          = INITIAL_CAPITAL
    peak_equity     = INITIAL_CAPITAL
    day_start_eq    = INITIAL_CAPITAL
    current_day     = ""
    month_start_eq  = INITIAL_CAPITAL
    current_month   = ""
    open_positions  = {}
    pending_entries = {}
    cooldown_tracker = {}
    trades          = []
    equity_curve    = [{"ts": str(start_ts), "equity": equity}]
    c_consec_losses = 0
    c_pause_until   = None
    r_consec_losses = 0
    r_cb_until      = None

    for bar_ts in test_ts:
        today = str(bar_ts)[:10]
        if today != current_day:
            current_day  = today
            day_start_eq = equity
        this_month = str(bar_ts)[:7]
        if this_month != current_month:
            current_month  = this_month
            month_start_eq = equity

        # ── Exits ────────────────────────────────────────────────────────────
        for pk, pos in list(open_positions.items()):
            sd = sym_data.get(pos["sym"])
            if sd is None:
                continue
            bar = sd["ts_to_pos"].get(bar_ts)
            if bar is None:
                continue
            hi, lo, cl = float(sd["high"][bar]), float(sd["low"][bar]), float(sd["close"][bar])
            entry, sl, tp, side = pos["entry_price"], pos["sl"], pos["tp"], pos["side"]

            if activation_atr > 0 and trail_distance_atr > 0:
                atr = float(sd["atr14"][bar])
                if not math.isnan(atr) and atr > 0:
                    if side == "long":
                        peak = max(pos.get("peak_price", entry), hi)
                        pos["peak_price"] = peak
                        if peak >= entry + activation_atr * atr:
                            trail_sl = peak - trail_distance_atr * atr
                            if trail_sl > sl:
                                pos["sl"] = sl = trail_sl
                                pos["trailing"] = True
                    else:
                        trough = min(pos.get("peak_price", entry), lo)
                        pos["peak_price"] = trough
                        if trough <= entry - activation_atr * atr:
                            trail_sl = trough + trail_distance_atr * atr
                            if trail_sl < sl:
                                pos["sl"] = sl = trail_sl
                                pos["trailing"] = True

            exit_price = exit_reason = None
            if side == "long":
                if hi >= tp and not pos.get("trailing"):
                    exit_price, exit_reason = tp, "take_profit"
                elif lo <= sl:
                    exit_reason = "trail_stop" if pos.get("trailing") else "stop_loss"
                    exit_price  = sl
            else:
                if lo <= tp and not pos.get("trailing"):
                    exit_price, exit_reason = tp, "take_profit"
                elif hi >= sl:
                    exit_reason = "trail_stop" if pos.get("trailing") else "stop_loss"
                    exit_price  = sl

            entry_ts  = pos["entry_ts"]
            elapsed_h = (bar_ts - entry_ts).total_seconds() / 3600
            if exit_price is None:
                if time_stop_c > 0 and pos.get("pattern") == "C" and elapsed_h >= time_stop_c:
                    exit_price, exit_reason = cl, "time_stop_c"
                elif elapsed_h >= TIME_STOP_H:
                    exit_price, exit_reason = cl, "time_stop"

            if exit_price is not None:
                exit_price = exit_price * (1 - EXIT_SLIPPAGE) if side == "long" else exit_price * (1 + EXIT_SLIPPAGE)
                side_mult = 1 if side == "long" else -1
                notional  = pos["margin"] * pos["leverage"]
                raw_pnl   = (exit_price - entry) / (entry + 1e-10) * side_mult * notional
                fees      = notional * COMMISSION * 2
                net_pnl   = raw_pnl - fees
                equity   += net_pnl
                peak_equity = max(peak_equity, equity)
                trade = {**pos, "exit_ts": bar_ts, "exit_price": exit_price,
                         "reason": exit_reason, "pnl": round(net_pnl, 2),
                         "equity_after": round(equity, 2)}
                trades.append(trade)
                del open_positions[pk]
                equity_curve.append({"ts": str(bar_ts), "equity": round(equity, 2)})
                if pos.get("pattern") == "C":
                    if net_pnl < 0:
                        c_consec_losses += 1
                        if c_consec_losses >= c_consec_loss_pause:
                            c_pause_until = bar_ts + pd.Timedelta(hours=c_consec_loss_pause_h)
                    else:
                        c_consec_losses = 0
                if pos.get("pattern") == "R":
                    if net_pnl < 0:
                        r_consec_losses += 1
                        if r_consec_losses >= R_CB_LOSSES:
                            r_cb_until = bar_ts + pd.Timedelta(hours=R_CB_PAUSE_H)
                    else:
                        r_consec_losses = 0

        # ── Circuit-breakers ────────────────────────────────────────────────
        if equity <= EQUITY_FLOOR:
            continue
        day_dd = (day_start_eq - equity) / day_start_eq if day_start_eq > 0 else 0
        if day_dd >= DAILY_LOSS_CAP:
            continue
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        dd_scale = max(0.5, 1.0 - drawdown * 2.5)
        month_dd = (month_start_eq - equity) / month_start_eq if month_start_eq > 0 else 0
        monthly_cb_active = month_dd >= MONTHLY_CB_PCT
        combined_scale = dd_scale * (MONTHLY_CB_SCALE if monthly_cb_active else 1.0)

        b = btc_sd["ts_to_pos"].get(bar_ts)
        if b is None or b < 152:
            continue
        btc_adx     = float(btc_sd["adx"][b])
        btc_e20_4h  = float(btc_sd["ema20_4h"][b])
        btc_e50_4h  = float(btc_sd["ema50_4h"][b])
        btc_close   = float(btc_sd["close"][b])
        btc_bbw     = float(btc_sd["bbw"][b])
        btc_bbwq    = float(btc_sd["bbw_q15"][b])

        # BUG FIX 20/07/2026 : seuil ADX bear_macro était 22, la vraie valeur
        # (live_monitor_v33.py verbatim) est 20.
        bear_macro = (not math.isnan(btc_e20_4h) and not math.isnan(btc_e50_4h)
                      and not math.isnan(btc_adx)
                      and btc_e20_4h < btc_e50_4h and btc_adx > 20)
        bull_macro = (not math.isnan(btc_e20_4h) and not math.isnan(btc_e50_4h)
                      and btc_e20_4h > btc_e50_4h)

        # BUG FIX 19/07/2026 : chaos_regime totalement absent de la reconstruction
        # (récupéré verbatim depuis live_monitor_v33.py) — ATR BTC vs médiane 30j.
        chaos_regime = False
        btc_atr_b = float(btc_sd["atr14"][b])
        if btc_close > 0 and not math.isnan(btc_atr_b) and b >= 30:
            btc_atr_pct = btc_atr_b / btc_close
            past_atrs = [float(btc_sd["atr14"][max(0, b - i)]) /
                         (float(btc_sd["close"][max(0, b - i)]) + 1e-10)
                         for i in range(1, 31)]
            med_atr_pct = sorted(past_atrs)[15]
            if btc_atr_pct / (med_atr_pct + 1e-10) > 2.0:
                chaos_regime = True

        # BUG FIX 19/07/2026 : btc_1h_filter était accepté en paramètre mais
        # jamais appliqué — laissait passer ~2× trop de trades vs référence
        # (196 vs 93 sur la fenêtre de validation). Logique récupérée verbatim
        # depuis live_monitor_v33.py : micro-trend BTC 3H, seuil ±0.3%.
        btc_1h_bias = None
        if btc_1h_filter and b >= 3:
            cl_now = btc_close
            cl_3h  = float(btc_sd["close"][b - 3])
            if cl_3h > 0 and not math.isnan(cl_now):
                m3h = (cl_now - cl_3h) / cl_3h
                if m3h > 0.003:
                    btc_1h_bias = "bull"
                elif m3h < -0.003:
                    btc_1h_bias = "bear"

        # BUG FIX 20/07/2026 : ranging_regime utilisait bbw_q15 (15e percentile
        # roulant 40 barres) au lieu de la vraie formule (médiane BBW 30j × 1.2
        # de tolérance) — récupéré verbatim depuis live_monitor_v33.py.
        ranging_regime = False
        if not math.isnan(btc_adx) and not math.isnan(btc_bbw) and btc_adx < ADX_RANGE_MAX and b >= 30:
            past_bbws = [float(btc_sd["bbw"][max(0, b - i)]) for i in range(1, 31)]
            med_bbw = sorted(past_bbws)[15]
            if btc_bbw <= med_bbw * 1.2:
                ranging_regime = True

        # BUG FIX 20/07/2026 : btc_4h_bull totalement absent — filtre directionnel
        # sur momentum SOUTENU 24H+48H (distinct de bear_macro/bull_macro qui sont
        # basés sur EMA). None = pas de filtre ; True bloque tous les SHORTS C/D ;
        # False (déclenché uniquement sur crash confirmé) bloque tous les LONGS.
        btc_4h_bull = None
        if b >= 48:
            cl_now = btc_close
            cl_24h = float(btc_sd["close"][b - 24])
            cl_48h = float(btc_sd["close"][b - 48])
            if cl_24h > 0 and cl_48h > 0:
                m24h = (cl_now - cl_24h) / cl_24h
                m48h = (cl_now - cl_48h) / cl_48h
                if m24h < -0.03 and m48h < -0.04:
                    btc_4h_bull = False
                elif m24h > 0.03 and m48h > 0.04:
                    btc_4h_bull = True

        btc_near_ath   = False
        btc_bear_for_r = False
        survive_mode   = False
        if b >= 90:
            peak90 = max(float(btc_sd["close"][max(0, b - d * 24)]) for d in range(91))
            if peak90 > 0:
                dd90 = (peak90 - btc_close) / peak90
                if dd90 < NEAR_ATH_THRESH:
                    btc_near_ath = True
                if dd90 > BTC_BEAR_R_THRESH:
                    btc_bear_for_r = True
        if b >= 50 and not math.isnan(btc_e50_4h):
            peak30 = max(float(btc_sd["close"][max(0, b - d * 24)]) for d in range(31))
            if peak30 > 0:
                dd30 = (peak30 - btc_close) / peak30
                if dd30 > SURVIVE_DD and btc_close < btc_e50_4h:
                    survive_mode = True

        btc_24h_crash = False
        if b >= 24:
            cl24 = float(btc_sd["close"][b - 24])
            if cl24 > 0 and (btc_close - cl24) / cl24 < BTC_CRASH_THRESH:
                btc_24h_crash = True

        # BUG FIX 19/07/2026 : market panic (corrélation extrême entre altcoins)
        # défini (PANIC_VEL/PANIC_THRESH) mais jamais implémenté. Panique DOWN
        # (≥80% des actifs -2.5%/4H) bloque C/D LONG ; panique UP bloque SHORT.
        market_panic_down = market_panic_up = False
        if b >= 4:
            moves = []
            for s2, sd2 in sym_data.items():
                if s2 == "BTC-USDT":
                    continue
                b2 = sd2["ts_to_pos"].get(bar_ts)
                if b2 is None or b2 < 4:
                    continue
                c_now = float(sd2["close"][b2])
                c_4h  = float(sd2["close"][b2 - 4])
                if c_4h > 0 and not math.isnan(c_now):
                    moves.append((c_now - c_4h) / c_4h)
            if len(moves) >= 5:  # BUG FIX 20/07 : garde minimum absent (évite faux signal sur peu de données)
                down = sum(1 for m in moves if m < -PANIC_VEL) / len(moves)
                up   = sum(1 for m in moves if m > PANIC_VEL) / len(moves)
                market_panic_down = down >= PANIC_THRESH
                market_panic_up   = up >= PANIC_THRESH

        # BUG FIX 20/07/2026 : le throttling survive_mode était TOTALEMENT absent.
        # En survive_mode (BTC >12% sous pic 30j + sous EMA50_4H), le vrai bot
        # réduit drastiquement risque/positions/exigences de score — récupéré
        # verbatim depuis live_monitor_v33.py (bear_mode = survive_mode).
        bear_mode = survive_mode
        if bear_mode:
            active_risk_pct = risk_pct_c * 0.30
            active_max_pos  = 3
            score_min_c_eff = SCORE_MIN + 15
            active_score_d  = SCORE_MIN_D + 8
        else:
            active_risk_pct = risk_pct_c
            active_max_pos  = MAX_POS
            score_min_c_eff = (SCORE_MIN - 3) if bull_macro else SCORE_MIN
            active_score_d  = SCORE_MIN_D
        max_margin = equity * MAX_MARGIN_RATIO
        total_margin_used = sum(p["margin"] for p in open_positions.values())

        c_paused = c_pause_until is not None and bar_ts <= c_pause_until
        r_paused = r_cb_until is not None and bar_ts <= r_cb_until

        micro_adj = 0

        syms_in_pos = {p["sym"] for p in open_positions.values()}

        # BUG FIX 20/07/2026 : restructuration complète C+D. L'ancienne version
        # ouvrait la 1ère position valide rencontrée en parcourant SYMBOLS dans
        # l'ordre fixe (biais vers BTC/ETH/SOL...), sans comparer la qualité des
        # signaux entre eux. Le vrai moteur (live_monitor_v33.py, verbatim)
        # COLLECTE tous les candidats valides sur les 26 symboles, les TRIE par
        # (score, ADX) décroissant, puis n'alloue les slots MAX_POS/marge qu'aux
        # meilleurs. Explique une bonne part du surplus de trades ET du WR trop
        # bas (des signaux médiocres passaient alors qu'un meilleur candidat
        # était disponible la même barre).

        # ── Pattern C — collecte ────────────────────────────────────────────
        candidates_c = []
        if not ranging_regime and not c_paused:
            for sym in SYMBOLS:
                if sym == "BTC-USDT" or sym in syms_in_pos:
                    continue
                if use_c_blacklist and sym in c_blacklist:
                    continue
                sd = sym_data.get(sym)
                if sd is None:
                    continue
                bar = sd["ts_to_pos"].get(bar_ts)
                if bar is None or bar < 152:
                    continue
                if bar - cooldown_tracker.get(sym + "C", -9999) < COOLDOWN_BARS:
                    continue
                adx_val = float(sd["adx"][bar])
                if math.isnan(adx_val):
                    continue
                action = check_pattern_c(sd, bar, adx_val)
                if action is None:
                    continue
                if btc_4h_bull is not None:
                    if (action == "BUY" and not btc_4h_bull) or (action == "SELL" and btc_4h_bull):
                        continue
                if (btc_1h_bias == "bear" and action == "BUY") or (btc_1h_bias == "bull" and action == "SELL"):
                    continue
                if action == "BUY" and btc_24h_crash:
                    continue
                if bear_mode and action == "BUY":
                    continue
                if (market_panic_down and action == "BUY") or (market_panic_up and action == "SELL"):
                    continue
                if action == "SELL" and btc_near_ath:
                    continue
                score = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
                if score < score_min_c_eff:
                    continue
                candidates_c.append(dict(sym=sym, bar=bar, action=action, score=score, adx=adx_val))

        candidates_c.sort(key=lambda c: (c["score"], c["adx"]), reverse=True)
        for c in candidates_c:
            if len(open_positions) >= active_max_pos:
                break
            if total_margin_used + 1e-6 > max_margin:
                break
            sym, bar, action, score = c["sym"], c["bar"], c["action"], c["score"]
            sd = sym_data[sym]
            side = "long" if action == "BUY" else "short"
            entry_bar = bar + 1 if next_bar_entry else bar
            if entry_bar >= len(sd["open"]):
                continue
            open_px = float(sd["open"][entry_bar])
            ep = open_px * (1 + SLIPPAGE) if side == "long" else open_px * (1 - SLIPPAGE)
            atr = float(sd["atr14"][bar])
            if math.isnan(atr) or atr <= 0:
                atr = ep * 0.015
            sl_pct = max(ATR_SL_MIN_C, min(ATR_SL_MAX_C, ATR_SL_MULT * atr / (ep + 1e-10)))
            tp_pct = sl_pct * RR_RATIO
            sl = ep * (1 - sl_pct) if side == "long" else ep * (1 + sl_pct)
            tp = ep * (1 + tp_pct) if side == "long" else ep * (1 - tp_pct)
            entry_ts = sd["ts_index"][entry_bar] if next_bar_entry else bar_ts
            mult = score_mult_fn(score, "C")
            lev  = HIGH_LEVERAGE if (bull_macro and score >= BULL_LEV_SCORE) else BASE_LEVERAGE
            margin = equity * active_risk_pct * combined_scale * mult
            if total_margin_used + margin > max_margin:
                continue
            pk = sym + "C" + str(bar_ts)
            open_positions[pk] = {
                "sym": sym, "side": side, "pattern": "C",
                "entry_ts": entry_ts, "entry_price": ep,
                "sl": sl, "tp": tp, "margin": margin,
                "leverage": lev, "score": score,
            }
            cooldown_tracker[sym + "C"] = bar
            total_margin_used += margin
            syms_in_pos.add(sym)

        # ── Pattern D — collecte ────────────────────────────────────────────
        candidates_d = []
        if not ranging_regime and not no_btc_d:
            d_universe_restricted = not bear_mode and not bear_macro
            for sym in SYMBOLS:
                if sym == "BTC-USDT" or sym in syms_in_pos:
                    continue
                if d_universe_restricted and sym not in (PATTERN_D_WHITELIST | PATTERN_D_BULL_EXTRA):
                    continue
                sd = sym_data.get(sym)
                if sd is None:
                    continue
                bar = sd["ts_to_pos"].get(bar_ts)
                if bar is None or bar < 152:
                    continue
                if bar - cooldown_tracker.get(sym + "D", -9999) < COOLDOWN_BARS:
                    continue
                adx_val = float(sd["adx"][bar])
                if math.isnan(adx_val) or adx_val < adx_min_d:
                    continue
                action = check_pattern_d(sd, bar, adx_val)
                if action is None:
                    continue
                if bear_macro and action == "BUY":
                    continue
                if btc_4h_bull is not None:
                    if (action == "BUY" and not btc_4h_bull) or (action == "SELL" and btc_4h_bull):
                        continue
                if (btc_1h_bias == "bear" and action == "BUY") or (btc_1h_bias == "bull" and action == "SELL"):
                    continue
                if action == "BUY" and btc_24h_crash:
                    continue
                if bear_mode and action == "BUY":
                    continue
                if action == "SELL" and bear_mode and btc_adx < BTC_ADX_MIN_BEAR_D:
                    continue
                if (market_panic_down and action == "BUY") or (market_panic_up and action == "SELL"):
                    continue
                if action == "SELL" and btc_near_ath:
                    continue
                score = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
                if score < active_score_d:
                    continue
                # BUG FIX 20/07/2026 : score plus strict pour D non-whitelist en
                # bear_mode — constante définie mais jamais appliquée.
                if (bear_mode and action == "SELL" and sym not in PATTERN_D_WHITELIST
                        and score < BEAR_D_NONWL_SCORE_MIN):
                    continue
                candidates_d.append(dict(sym=sym, bar=bar, action=action, score=score, adx=adx_val))

        candidates_d.sort(key=lambda c: (c["score"], c["adx"]), reverse=True)
        # BUG FIX 20/07/2026 : garde corrélation — max MAX_BEAR_D_NONWL_POS shorts
        # D non-whitelist simultanés en bear_mode — jamais appliquée non plus.
        bear_nonwl_d_count = sum(
            1 for p in open_positions.values()
            if p.get("pattern") == "D" and p.get("side") == "short"
            and p.get("sym") not in PATTERN_D_WHITELIST)
        for c in candidates_d:
            if len(open_positions) >= active_max_pos:
                break
            if total_margin_used + 1e-6 > max_margin:
                break
            sym, bar, action, score = c["sym"], c["bar"], c["action"], c["score"]
            sd = sym_data[sym]
            side = "long" if action == "BUY" else "short"
            if (side == "short" and bear_mode and sym not in PATTERN_D_WHITELIST
                    and bear_nonwl_d_count >= MAX_BEAR_D_NONWL_POS):
                continue
            entry_bar = bar + 1 if next_bar_entry else bar
            if entry_bar >= len(sd["open"]):
                continue
            open_px = float(sd["open"][entry_bar])
            ep = open_px * (1 + SLIPPAGE) if side == "long" else open_px * (1 - SLIPPAGE)
            atr = float(sd["atr14"][bar])
            if math.isnan(atr) or atr <= 0:
                atr = ep * 0.015
            sl_pct = max(ATR_SL_MIN_C, min(ATR_SL_MAX_C, ATR_SL_MULT * atr / (ep + 1e-10)))
            tp_pct = sl_pct * RR_RATIO
            sl = ep * (1 - sl_pct) if side == "long" else ep * (1 + sl_pct)
            tp = ep * (1 + tp_pct) if side == "long" else ep * (1 - tp_pct)
            entry_ts = sd["ts_index"][entry_bar] if next_bar_entry else bar_ts
            mult = score_mult_fn(score, "D")
            lev  = HIGH_LEVERAGE_D if score >= 92 else BASE_LEVERAGE_D
            margin = equity * RISK_PCT_D * combined_scale * mult
            if total_margin_used + margin > max_margin:
                continue
            pk = sym + "D" + str(bar_ts)
            open_positions[pk] = {
                "sym": sym, "side": side, "pattern": "D",
                "entry_ts": entry_ts, "entry_price": ep,
                "sl": sl, "tp": tp, "margin": margin,
                "leverage": lev, "score": score,
            }
            cooldown_tracker[sym + "D"] = bar
            total_margin_used += margin
            syms_in_pos.add(sym)
            if side == "short" and sym not in PATTERN_D_WHITELIST:
                bear_nonwl_d_count += 1

        for sym in SYMBOLS:
            if sym == "BTC-USDT":
                continue
            sd = sym_data.get(sym)
            if sd is None:
                continue
            bar = sd["ts_to_pos"].get(bar_ts)
            if bar is None or bar < 152:
                continue
            adx_val = float(sd["adx"][bar])
            if math.isnan(adx_val):
                continue
            if len(open_positions) >= active_max_pos:
                break

            # ── Pattern MOM ──────────────────────────────────────────────────
            if bull_macro and sym not in syms_in_pos:
                mom_count = sum(1 for p in open_positions.values() if p.get("pattern") == "MOM")
                if mom_count < MAX_POS_MOM:
                    mk = sym + "MOM"
                    if bar - cooldown_tracker.get(mk, -9999) >= COOLDOWN_BARS_MOM:
                        if check_pattern_mom(sd, bar, adx_val) == "BUY" and not btc_24h_crash:
                            score = int(sd["buy_sc"][bar])
                            if score >= 65:
                                entry_bar = bar + 1 if next_bar_entry else bar
                                if entry_bar < len(sd["open"]):
                                    open_px = float(sd["open"][entry_bar])
                                    ep = open_px * (1 + SLIPPAGE)
                                    atr = float(sd["atr14"][bar])
                                    if math.isnan(atr) or atr <= 0:
                                        atr = ep * 0.015
                                    sl_pct = max(0.008, min(0.025, 1.3 * atr / (ep + 1e-10)))
                                    tp_pct = sl_pct * RR_RATIO_MOM
                                    sl, tp = ep * (1 - sl_pct), ep * (1 + tp_pct)
                                    entry_ts = sd["ts_index"][entry_bar] if next_bar_entry else bar_ts
                                    margin = equity * RISK_PCT_MOM * combined_scale
                                    if total_margin_used + margin <= max_margin:
                                        pk = mk + str(bar_ts)
                                        open_positions[pk] = {
                                            "sym": sym, "side": "long", "pattern": "MOM",
                                            "entry_ts": entry_ts, "entry_price": ep,
                                            "sl": sl, "tp": tp, "margin": margin,
                                            "leverage": BASE_LEVERAGE_MOM, "score": score,
                                        }
                                        cooldown_tracker[mk] = bar
                                        total_margin_used += margin
                                        syms_in_pos.add(sym)

            # ── Pattern R (régime range uniquement) ─────────────────────────
            if ranging_regime and not r_paused and sym not in syms_in_pos and not survive_mode:
                rk = sym + "R"
                if bar - cooldown_tracker.get(rk, -9999) >= COOLDOWN_BARS_R:
                    action = check_pattern_r(sd, bar)
                    if action is not None:
                        if action == "BUY" and btc_bear_for_r:
                            action = None
                    if action is not None:
                        score = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
                        if score >= SCORE_MIN_R:
                            side = "long" if action == "BUY" else "short"
                            entry_bar = bar + 1 if next_bar_entry else bar
                            if entry_bar < len(sd["open"]):
                                open_px = float(sd["open"][entry_bar])
                                ep = open_px * (1 + SLIPPAGE) if side == "long" else open_px * (1 - SLIPPAGE)
                                atr = float(sd["atr14"][bar])
                                if math.isnan(atr) or atr <= 0:
                                    atr = ep * 0.015
                                sl_pct = max(ATR_SL_MIN_R, min(ATR_SL_MAX_R, ATR_SL_MULT_R * atr / (ep + 1e-10)))
                                tp_pct = sl_pct * RR_RATIO_R
                                sl = ep * (1 - sl_pct) if side == "long" else ep * (1 + sl_pct)
                                tp = ep * (1 + tp_pct) if side == "long" else ep * (1 - tp_pct)
                                entry_ts = sd["ts_index"][entry_bar] if next_bar_entry else bar_ts
                                margin = equity * RISK_PCT_R * combined_scale
                                if total_margin_used + margin <= max_margin:
                                    pk = rk + str(bar_ts)
                                    open_positions[pk] = {
                                        "sym": sym, "side": side, "pattern": "R",
                                        "entry_ts": entry_ts, "entry_price": ep,
                                        "sl": sl, "tp": tp, "margin": margin,
                                        "leverage": BASE_LEVERAGE_R, "score": score,
                                    }
                                    cooldown_tracker[rk] = bar
                                    total_margin_used += margin
                                    syms_in_pos.add(sym)

        # ── Pattern S — passe dédiée, max 1 position/bar ──────────────────────
        # BUG FIX 19/07/2026 : reconstruction initiale ouvrait 1 position S PAR
        # SYMBOLE éligible (jusqu'à 25/bar), causant N=25 vs référence N~5 sur
        # 13 mois. Logique exacte récupérée verbatim depuis live_monitor_v33.py :
        # gate asset 4H bear individuel (en plus du bear_macro BTC), chaos/survive
        # exclus, near_ath exclus, candidats classés par score, 1 SEUL retenu,
        # sizing = budget restant entier (pas 10% d'equity par trade).
        if bear_macro and not chaos_regime and not survive_mode:
            s_margin_used = sum(p["margin"] for p in open_positions.values() if p.get("pattern") == "S")
            s_budget = S_MARGIN_CAP - s_margin_used
            if s_budget > 0:
                candidates_s = []
                for sym in SYMBOLS:
                    if sym == "BTC-USDT" or sym in PATTERN_S_BLACKLIST or sym in syms_in_pos:
                        continue
                    sd = sym_data.get(sym)
                    if sd is None:
                        continue
                    bar = sd["ts_to_pos"].get(bar_ts)
                    if bar is None or bar < 52:
                        continue
                    if bar - cooldown_tracker.get(sym + "S", -9999) < COOLDOWN_BARS_S:
                        continue
                    e20_4h_s = float(sd["ema20_4h"][bar]); e50_4h_s = float(sd["ema50_4h"][bar])
                    if math.isnan(e20_4h_s) or math.isnan(e50_4h_s) or e20_4h_s > e50_4h_s:
                        continue  # asset lui-même doit être baissier 4H
                    adx_val_s = float(sd["adx"][bar])
                    if math.isnan(adx_val_s):
                        continue
                    if check_pattern_s(sd, bar, adx_val_s) != "SELL":
                        continue
                    if market_panic_up or btc_near_ath:
                        continue
                    score = int(sd["sell_sc"][bar])
                    if score < SCORE_MIN_S:
                        continue
                    candidates_s.append({"sym": sym, "bar": bar, "score": score})
                candidates_s.sort(key=lambda c: c["score"], reverse=True)
                for c in candidates_s[:1]:
                    sym, bar, score = c["sym"], c["bar"], c["score"]
                    sd = sym_data[sym]
                    entry_bar = bar + 1 if next_bar_entry else bar
                    if entry_bar >= len(sd["open"]):
                        continue
                    open_px = float(sd["open"][entry_bar])
                    ep = open_px * (1 - SLIPPAGE)
                    atr = float(sd["atr14"][bar])
                    if math.isnan(atr) or atr <= 0:
                        atr = ep * 0.015
                    sl_pct = max(ATR_SL_MIN_S, min(ATR_SL_MAX_S, ATR_SL_MULT_S * atr / (ep + 1e-10)))
                    tp_pct = sl_pct * RR_RATIO_S
                    sl, tp = ep * (1 + sl_pct), ep * (1 - tp_pct)
                    entry_ts = sd["ts_index"][entry_bar] if next_bar_entry else bar_ts
                    margin = min(s_budget, S_MARGIN_CAP)
                    if total_margin_used + margin <= max_margin:
                        pk = sym + "S" + str(bar_ts)
                        open_positions[pk] = {
                            "sym": sym, "side": "short", "pattern": "S",
                            "entry_ts": entry_ts, "entry_price": ep,
                            "sl": sl, "tp": tp, "margin": margin,
                            "leverage": BASE_LEVERAGE_S, "score": score,
                        }
                        cooldown_tracker[sym + "S"] = bar
                        total_margin_used += margin

    return trades, equity_curve, equity


# ── CLI ──────────────────────────────────────────────────────────────────────
def print_report(trades, equity_curve, final_eq, months=None, start_ts=None, end_ts=None):
    if not trades:
        print("Aucun trade.")
        return
    wins = [t for t in trades if t["pnl"] > 0]
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    gw = sum(t["pnl"] for t in wins)
    print(f"N={len(trades)} WR={len(wins)/len(trades)*100:.1f}% "
          f"PF={gw/(gl+1e-9):.2f} PnL={sum(t['pnl'] for t in trades):+.0f}€ "
          f"Equity finale={final_eq:.0f}€")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=13)
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()

    sym_data = {}
    for sym in SYMBOLS:
        df = load_or_fetch(sym, args.months, args.no_fetch)
        if df is not None and len(df) > 300:
            sym_data[sym] = prepare(sym, df.copy())
    if "BTC-USDT" not in sym_data:
        print("BTC-USDT introuvable — abandon.")
        return
    btc_ts = sorted(sym_data["BTC-USDT"]["ts_index"])
    start_ts, end_ts = pd.Timestamp(btc_ts[0]), pd.Timestamp(btc_ts[-1])
    kw = dict(btc_filter=False, btc_1h_filter=True, asian_filter=True, next_bar_entry=True)
    trades, curve, eq = run_backtest(sym_data, start_ts, end_ts, **kw)
    print_report(trades, curve, eq, args.months, start_ts, end_ts)


if __name__ == "__main__":
    main()
