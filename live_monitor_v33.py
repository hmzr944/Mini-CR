#!/usr/bin/env python3
"""
PRISM v33 — Live Monitor (Paper + OKX Demo/Live)
=================================================
Moteur identique v33.
Si credentials OKX présents dans .env → ordres réels (demo ou live).
Sinon → mode paper pur.

Usage :
  python3 live_monitor_v33.py          # Boucle infinie (toutes les heures)
  python3 live_monitor_v33.py --once   # Une seule détection puis quitte
  python3 live_monitor_v33.py --status # Affiche l'état courant et quitte
"""

import argparse
import csv
import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from prism.strategy import (  # single source of truth (re-migré 19/07/2026)
    ADX_MIN_C, SQUEEZE_BARS_C, VOL_RATIO_C, ADX_MIN_MOM, RSI_MOM_MIN, RSI_MOM_MAX,
    VOL_RATIO_MOM, ADX_MIN_S, RSI_S_MIN, RSI_S_MAX, RSI_LONG_MIN_D, RSI_LONG_MAX_D,
    RSI_SHORT_MIN_D, RSI_SHORT_MAX_D, SCORE_MIN_V, VOL_MULT_V, _ADX_1BAR,
    compute_indicators, prepare, _compute_scores, _score_size_mult,
    check_pattern_c, check_pattern_d, check_pattern_r, check_pattern_s,
    check_pattern_mom, check_pattern_v,
)

try:
    import telegram_notif as _tg
except ImportError:
    _tg = None

try:
    from okx_trader import OKXTrader as _OKXTrader
except ImportError:
    _OKXTrader = None

try:
    from sentiment_v33 import get_analyzer as _get_sentiment
except ImportError:
    _get_sentiment = None

console = Console()

# ── Trader OKX (None = paper only) ──────────────────────────────────────────
_trader: "_OKXTrader | None" = None

def _init_trader():
    """Charge les credentials depuis .env et initialise le trader OKX."""
    global _trader
    if _OKXTrader is None:
        return
    env_file = BASE_DIR / ".env"
    creds = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                creds[k.strip()] = v.strip()
    key  = creds.get("OKX_API_KEY", "")
    sec  = creds.get("OKX_SECRET_KEY", "")
    pwd  = creds.get("OKX_PASSPHRASE", "")
    demo = creds.get("OKX_DEMO", "true").lower() != "false"
    # "inverse" par defaut : seul produit accessible aux comptes OKX EEA
    # (X-Perps, USD-margine) — USDT-margine ("linear") indisponible sous MiCA.
    contract_type = creds.get("OKX_CONTRACT_TYPE", "inverse").strip().lower()
    if key and sec and pwd:
        t = _OKXTrader(key, sec, pwd, demo=demo, contract_type=contract_type)
        if t.ping():
            _trader = t
            mode = "DEMO" if demo else "LIVE REEL"
            log.info(f"OKX {mode} connecté — ordres réels activés")
        else:
            log.warning("OKX credentials invalides — mode paper uniquement")
    else:
        log.info("Pas de credentials OKX — mode paper uniquement")

# ── Répertoire de travail ────────────────────────────────────────────────────
BASE_DIR          = Path(os.environ.get("PRISM_DATA_DIR", str(Path(__file__).parent)))
LOG_DIR           = BASE_DIR / "live_logs"
STATE_FILE        = BASE_DIR / "live_state_v33.json"
SCAN_HISTORY_FILE = BASE_DIR / "scan_history_v33.json"
LOG_DIR.mkdir(exist_ok=True)

# ── Logging fichier ──────────────────────────────────────────────────────────
def _setup_file_logger():
    today = datetime.now().strftime("%Y%m%d")
    log_path = LOG_DIR / f"live_session_{today}.log"
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    log = logging.getLogger("prism_live")
    log.setLevel(logging.DEBUG)
    if not log.handlers:
        log.addHandler(fh)
    return log

log = _setup_file_logger()

TRADE_LOG_PATH = LOG_DIR / f"live_trades_{datetime.now().strftime('%Y%m')}.csv"
_TRADE_HEADER  = ["exit_ts","sym","side","pattern","entry_ts","entry_px","exit_px",
                  "reason","margin","leverage","score","pnl","equity_after",
                  "funding_paid"]   # funding cumulé sur la durée de la position
_TRADE_LOCK    = threading.Lock()
_equity_warn_date: str = ""    # date du dernier warning "compte démo vide" (1 fois/jour)
_okx_account_funded: bool = False  # True dès que get_equity() retourne > EQUITY_FLOOR

def _write_trade(row: dict):
    with _TRADE_LOCK:
        exists = TRADE_LOG_PATH.exists()
        with open(TRADE_LOG_PATH, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_TRADE_HEADER)
            if not exists:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in _TRADE_HEADER})

# ── Métriques d'exécution (slippage, latence, fill réel) ─────────────────────
EXEC_LOG_PATH = LOG_DIR / f"exec_metrics_{datetime.now().strftime('%Y%m')}.csv"
_EXEC_HEADER  = [
    "ts", "sym", "side", "pattern", "score", "leverage",
    "expected_px",    # prix open bar (hypothèse backtest)
    "est_entry_px",   # prix last au moment de l'ordre (estimation OKX)
    "actual_fill_px", # prix de fill réel (direct si limit, sinon async)
    "slippage_bps",   # (fill - expected) / expected × 10000 × direction
    "latency_ms",     # durée set_leverage + place_order_hybrid (ms)
    "fill_ok",        # True si fill obtenu
    "limit_attempted",# True si score >= seuil → post_only tenté
    "maker_fill",     # True si limit rempli sans fallback (saving 8bps)
]
_EXEC_LOCK = threading.Lock()

def _write_exec_metric(row: dict):
    with _EXEC_LOCK:
        exists = EXEC_LOG_PATH.exists()
        with open(EXEC_LOG_PATH, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_EXEC_HEADER)
            if not exists:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in _EXEC_HEADER})

# ── Constantes (identiques v33) ──────────────────────────────────────────────
SYMBOLS = [
    # F2 revert 07/07 : ordre FWD original (WFO invalide ordre inversé -10.7% OOS)
    "BTC-USDT", "ETH-USDT", "SOL-USDT",
    "AVAX-USDT", "ADA-USDT", "LINK-USDT", "XRP-USDT", "DOT-USDT", "ATOM-USDT",
    "LTC-USDT", "DOGE-USDT", "NEAR-USDT", "TRX-USDT",
    "INJ-USDT", "OP-USDT",
    "ARB-USDT", "SUI-USDT", "UNI-USDT", "AAVE-USDT",
    "TIA-USDT", "SEI-USDT", "HBAR-USDT", "ICP-USDT",
    "JUP-USDT",
    "STX-USDT", "WIF-USDT",
]

# Symboles avec un contrat -USD-SWAP (X-Perps inverse) reellement disponible sur
# un compte OKX EEA (verifie via l'API publique le 30/06/2026). Univers complet
# (SYMBOLS, 26) reste la reference de backtest/strategie — ce sous-ensemble (11)
# filtre uniquement quelles positions peuvent etre executees reellement quand
# contract_type="inverse".
INVERSE_AVAILABLE_SYMS = {
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "AVAX-USDT", "ADA-USDT",
    "LINK-USDT", "DOT-USDT", "LTC-USDT", "DOGE-USDT", "SUI-USDT", "UNI-USDT",
}

INITIAL_CAPITAL  = 1000.0
TIMEFRAME        = "1H"
BARS_NEEDED      = 350          # warmup suffisant pour tous les indicateurs
COMMISSION       = 0.001
SLIPPAGE         = 0.0005
EXIT_SLIPPAGE    = 0.0003
ATR_SL_MULT      = 1.5
RR_RATIO         = 4.0  # abaissé 5→4 (v33.9 — audit 03/07 : +117pp OOS_3M 4/5)
ATR_SL_MIN_C     = 0.006
ATR_SL_MAX_C     = 0.025
TIME_STOP_H      = 96    # relevé 72→96h (audit 25/06 : +360% vs +345%, WR C 80%)
COOLDOWN_BARS    = 8    # relevé 5→8 : empêche re-entrée même journée après SL (29/06)
BASE_LEVERAGE    = 10
HIGH_LEVERAGE    = 15
DAILY_LOSS_CAP   = 0.12
EQUITY_FLOOR     = 200.0   # arrêt total si equity descend sous ce seuil
MAX_MARGIN_RATIO = 0.60
RISK_PCT         = 0.28  # ajusté 30%→28% (grids corrigés 27/06 : optimum sans biais temporel)
MAX_POS          = 8     # relevé 6→8 (audit 25/06 : +40k vs +36k à DD identique 25.8%)
SCORE_MIN        = 70  # relevé 65→70 (grids corrigés 27/06)

# ── Pattern D — EMA Trend Follow ─────────────────────────────────────────────
SCORE_MIN_D      = 85   # relevé 80→85 (grids corrigés 27/06 : PF min 1.63 SAIN)
ADX_MIN_D        = 28   # relevé 22→28 (autopsie 27/06 : wins avg ADX 30.9 vs losses 28.5)
VOL_RATIO_D      = 1.2
RISK_PCT_D       = 0.16   # 10%→16% : v33.9 audit 03/07 — D PF=7.21 sous-capitalisé, +61pp OOS_3M
BASE_LEVERAGE_D  = 8
HIGH_LEVERAGE_D  = 10

# Univers autorisé Pattern D — actifs validés OOS (audit 27/06)
# DOT : robuste OOS4sem + OOS2mois
# OP  : très sélectif (score≥86, ADX≥29), PF>10 IS, contribue OOS
# TRX : positif IS, neutre OOS — diversification utile
# ETH : +10D trades IS, OOS2 PF 3.16→3.44, IS +219pp — validé OOS (27/06)
# ADA : +4D trades IS, OOS2 PF +0.32, Bear -11% — validé OOS (27/06)
# Retiré AVAX : perd sur OOS. Retiré ARB : Bear -23%. SUI/TIA exclus.
PATTERN_D_WHITELIST = {
    "DOT-USDT", "OP-USDT", "TRX-USDT", "ETH-USDT", "ADA-USDT",
}
# Extension bull_macro : alignée sur WHITELIST (audit 08/07 — DOGE rejeté IS
# WR=0% PnL=-296€, SOL/LINK/NEAR jamais validés en D).
PATTERN_D_BULL_EXTRA = PATTERN_D_WHITELIST

# Blacklist Pattern C — actifs avec faux signaux BB Squeeze récurrents (OOS 27/06)
# ICP : WR<25%, -€229 OOS, -€2,420 IS — expansions de compression trop erratiques
# DOGE : -€201 OOS, -€1,985 IS — volatilité trop imprévisible sur Pattern C
PATTERN_C_BLACKLIST = {
    "ICP-USDT", "DOGE-USDT", "INJ-USDT",
}

# RSI zones Pattern D — resserrées (évite les zones de retournement 42-72 / 28-58)


# ── Pattern R — Mean Reversion (marché latéral) ───────────────────────────────
# Activé quand BTC ADX < 18 ET BBW sous sa médiane → marché sans direction
# Stratégie inverse de C/D : on trade les rebonds aux extrêmes des BB
RISK_PCT_R       = 0.12   # Kelly ≈11.5% (WR=41%, RR=2) → plein Kelly validé OOS
BASE_LEVERAGE_R  = 5      # levier faible — mouvement cible plus court
ATR_SL_MULT_R    = 1.0    # SL plus serré — si le rebond échoue, sortir vite
RR_RATIO_R       = 2.0    # TP = 2× SL — ratio réduit mais WR plus élevé
ATR_SL_MIN_R     = 0.004  # min 0.4%
ATR_SL_MAX_R     = 0.012  # max 1.2%
SCORE_MIN_R      = 85     # 56→85 : désactive Pattern R (PF=0.61 sur 12M, perd -612€ net — 29/06)
BTC_CRASH_THRESH = -0.04  # BTC 24H crash guard : bloque LONGS C+D si BTC < -4% en 24H
SURVIVE_DD       = 0.12   # Mode Survie : BTC >12% sous pic 30j ET sous EMA50_4H → SHORTS only
PANIC_VEL        = 0.025  # Vélocité panique : >2.5%/4H
PANIC_THRESH     = 0.80   # Seuil panique : 80% des altcoins dans même direction
NEAR_ATH_THRESH  = 0.03   # NearATH : BTC <3% sous pic 90j → SHORTS C/D bloqués
ADX_RANGE_MAX    = 22     # ADX BTC en dessous → régime latéral (élargi de 18→22)
COOLDOWN_BARS_R  = 8
BEAR_D_NONWL_SCORE_MIN = 92   # score minimum pour D non-whitelist en bear (29/06)
MAX_BEAR_D_NONWL_POS   = 3    # max 3 shorts D non-whitelist simultanés (corrélation)
BTC_ADX_MIN_BEAR_D     = 28   # BTC ADX minimum pour D shorts en bear (trending bear seulement)

# ── Pattern S — Short Continuation Bear Trend ────────────────────────────────
# Failed rally : RSI remonte 45-65, échoue à franchir EMA21, redescend → SHORT
# Gate : bear_macro actif (BTC EMA20_4H < EMA50_4H + ADX > 22)
# Budget isolé : S_MARGIN_CAP fixe — ne touche pas au capital composé C/D
# Bypass intentionnel du filtre 1H bias (S CIBLE les rebonds 1H en bear macro)
# Validé : IS PF=1.92, 4/4 OOS PF>1.2 (01/07)
SCORE_MIN_S          = 82    # 80→82 (WFO audit 07/07 : filtre 1 loser S, WR 50%→60%, +6.2% OOS)
S_MARGIN_CAP         = 600.0  # WFO audit 07/07 : 500→600€ optimal 5/5 fenêtres
BASE_LEVERAGE_S      = 8
RR_RATIO_S           = 4.0
ATR_SL_MULT_S        = 1.5
ATR_SL_MIN_S         = 0.006
ATR_SL_MAX_S         = 0.025
COOLDOWN_BARS_S      = 6
PATTERN_S_BLACKLIST  = {"STX-USDT", "LINK-USDT", "DOGE-USDT", "OP-USDT", "AVAX-USDT"}  # audit 03/07

# ── Pattern MOM — Bull Pullback (symétrique S en bull macro) ─────────────────
# Gate : bull_macro actif (BTC EMA20_4H > EMA50_4H)
# RSI 40-50 dip + rebond amorcé (RSI[bar] > RSI[bar-1]) + EMA9>21>50 + close>EMA50
# Audit 08/07 : + vol_ratio>1.2 (filtre rebond) + SCORE_MIN 60→65 → IS PF 0.84→2.40 OOS PF 1.02→2.69
SCORE_MIN_MOM     = 65
RISK_PCT_MOM      = 0.08     # 8% du capital (Kelly 11.2% → demi-Kelly conservateur)
BASE_LEVERAGE_MOM = 8
RR_RATIO_MOM      = 3.0      # RR plus court que C/D : pullback = mouvement cible limité
COOLDOWN_BARS_MOM = 12
MAX_POS_MOM       = 0        # DÉSACTIVÉ 21/07 : 0% WR sur 10/10 trades live-realistic (avr-juil),
                              # seuil de validation "10 premiers trades" du 08/07 atteint, négatif.
                              # Aligné sur backtest_v33.py (déjà à 0 par défaut).

# ── Optimisations performance (ajout v33.1) ─────────────────────────────────
BEAR_MARGIN_SCALE = 0.50   # marge ×0.50 en survive_mode (bear) : risque réduit, shorts autorisés
MONTHLY_CB_PCT    = 0.15   # circuit-breaker mensuel : si mois < -15% → size réduite
MONTHLY_CB_SCALE  = 0.60   # multiplicateur marge quand monthly CB actif
BULL_LEV_SCORE    = 88     # score min pour boost levier en bull fort (btc_near_ath)
BULL_LEV_MAX      = 18     # levier max en bull + score élevé (vs HIGH_LEVERAGE=15)

# ── Microstructure : Funding Rate + OI Divergence ────────────────────────────
FUNDING_BEAR_THRESH =  0.0008  # >+0.08%/8h → surchargé LONG  → bias SHORT (-5)
FUNDING_BULL_THRESH = -0.0003  # < -0.03%/8h → surchargé SHORT → bias LONG  (+5)
FUNDING_SCORE_ADJ   =  5
OI_SCORE_ADJ        =  3

# ── Pattern V — Volume Surge (hybride R/C) ────────────────────────────────────
# Détecte les mouvements institutionnels (vol 3×+ normal) aux extrêmes des BB
# Indépendant du régime — valide en TREND et RANGE
RISK_PCT_V      = 0.05    # risque minimal : évite le survive_mode sur perte unique
BASE_LEVERAGE_V = 8
RR_RATIO_V      = 3.0
ATR_SL_MULT_V   = 1.2
ATR_SL_MIN_V    = 0.005
ATR_SL_MAX_V    = 0.020
COOLDOWN_BARS_V = 5

OKX_URL       = "https://www.okx.com/api/v5/market/history-candles"
OKX_URL_FALLBACK = "https://my.okx.com/api/v5/market/history-candles"

# ── State (persistance JSON) ─────────────────────────────────────────────────
DEFAULT_STATE = {
    "equity":           INITIAL_CAPITAL,
    "peak_equity":      INITIAL_CAPITAL,
    "day_start_equity": INITIAL_CAPITAL,
    "current_day":      "",
    "open_positions":   {},
    "pending_entries":  {},
    "cooldown_tracker": {},
    "total_trades":     0,
    "total_wins":       0,
    "total_pnl":        0.0,
    "consec_losses":    0,
    "consec_pause_until": "",
    "started_at":       datetime.now().isoformat(),
    "last_run_ts":      "",
    "month_start_equity": INITIAL_CAPITAL,
    "current_month":    "",
    "live_synced":      False,  # True une fois l'equity reelle OKX synchronisee au moins 1x
}

def _parse_state(path) -> dict:
    with open(path) as f:
        s = json.load(f)
    for k, v in DEFAULT_STATE.items():
        s.setdefault(k, v)
    return s

def load_state() -> dict:
    """State machine crash-proof (09/07) : chaîne de récupération multi-niveaux.
    Ordre : state principal → .bak → backups journaliers (7 jours). Toute la
    chaîne corrompue → ARRÊT DE SÉCURITÉ (pas de reset silencieux amnésique)."""
    dailies = sorted(STATE_FILE.parent.glob("live_state_v33_*.dbak"), reverse=True)
    chain = [STATE_FILE, STATE_FILE.with_suffix(".bak")] + dailies
    errors = []
    for p in chain:
        if not p.exists():
            continue
        try:
            s = _parse_state(p)
            if p != STATE_FILE:
                msg = (f"STATE RECOVERY : {STATE_FILE.name} corrompu — "
                       f"restauré depuis {p.name} (last_run={s.get('last_run_ts','?')})")
                log.critical(msg)
                if _tg:
                    try: _tg.notify_error(msg)
                    except Exception: pass
            return s
        except Exception as e:
            errors.append(f"{p.name}: {e}")
    if STATE_FILE.exists() or errors:
        msg = ("ÉTAT IRRÉCUPÉRABLE : state + tous les backups corrompus — "
               "ARRÊT DE SÉCURITÉ. " + " | ".join(errors[:3]))
        log.critical(msg)
        if _tg:
            try: _tg.notify_error(msg)
            except Exception: pass
        raise SystemExit(msg)
    return dict(DEFAULT_STATE)   # premier démarrage légitime

def save_state(s: dict):
    s["last_run_ts"] = datetime.now().isoformat()
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2, default=str)
    if STATE_FILE.exists():
        try:
            shutil.copy2(STATE_FILE, STATE_FILE.with_suffix(".bak"))
            daily = STATE_FILE.parent / f"live_state_v33_{datetime.now():%Y%m%d}.dbak"
            if not daily.exists():
                shutil.copy2(STATE_FILE, daily)
                old = sorted(STATE_FILE.parent.glob("live_state_v33_*.dbak"))[:-7]
                for p in old:
                    p.unlink(missing_ok=True)
        except Exception as e:
            log.warning(f"Rotation backup state échouée : {e}")
    tmp.replace(STATE_FILE)   # atomique sur POSIX et Windows (même volume)

def save_scan_record(bar_ts, n_loaded: int, trades_closed: list,
                     signals_new: list, state: dict):
    record = {
        "ts":             datetime.now().isoformat(),
        "bar_ts":         str(bar_ts)[:16],
        "symbols_loaded": n_loaded,
        "signals": [
            {
                "sym":      s["sym"],
                "side":     "long" if s["action"] == "BUY" else "short",
                "score":    s["score"],
                "adx":      round(s["adx"], 1),
                "leverage": s["leverage"],
                "pattern":  s.get("pattern", "C"),
            }
            for s in signals_new
        ],
        "trades_closed": [
            {
                "sym":    t["sym"],
                "side":   t["side"],
                "reason": t["reason"],
                "pnl":    t["pnl"],
            }
            for t in trades_closed
        ],
        "equity":    round(state["equity"], 2),
        "n_open":    len(state["open_positions"]),
        "n_pending": len(state["pending_entries"]),
    }
    history = []
    if SCAN_HISTORY_FILE.exists():
        try:
            with open(SCAN_HISTORY_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.insert(0, record)
    history = history[:200]
    with open(SCAN_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)

# ── Funding Rate BTC-USDT-SWAP (live) ────────────────────────────────────────
_LIVE_FUNDING_CACHE: dict = {"history": [], "ts": 0.0}

def _fetch_live_funding_history(n: int = 90) -> list:
    """Retourne les n derniers funding rates BTC-USDT-SWAP. Cache 4H."""
    if time.time() - _LIVE_FUNDING_CACHE["ts"] < 4 * 3600 and _LIVE_FUNDING_CACHE["history"]:
        return _LIVE_FUNDING_CACHE["history"][-n:]
    try:
        rows, after_ts = [], None
        while len(rows) < n:
            p = {"instId": "BTC-USDT-SWAP", "limit": 100}
            if after_ts:
                p["after"] = after_ts
            r = requests.get(
                "https://www.okx.com/api/v5/public/funding-rate-history",
                params=p, timeout=10,
            ).json()
            if r.get("code") != "0" or not r.get("data"):
                break
            batch = r["data"]
            rows.extend(float(b["fundingRate"]) for b in batch)
            if len(batch) < 100:
                break
            after_ts = batch[-1]["fundingTime"]
        history = list(reversed(rows))  # du plus ancien au plus récent
        _LIVE_FUNDING_CACHE.update({"history": history, "ts": time.time()})
        log.info(f"FUNDING HISTORY: {len(history)} records, dernier={history[-1]*100:+.4f}%/8h" if history else "FUNDING HISTORY: vide")
        return history[-n:]
    except Exception as e:
        log.debug(f"Funding history fetch error: {e}")
        return _LIVE_FUNDING_CACHE.get("history", [])[-n:]

# ── Download (live, pas de cache long) ──────────────────────────────────────
def fetch_live(inst_id: str, limit: int = 350) -> pd.DataFrame | None:
    """Récupère les `limit` dernières bougies 1H complètes. Retry x3 + fallback URL."""
    for attempt in range(3):
        url = OKX_URL if attempt < 2 else OKX_URL_FALLBACK
        all_rows, after = [], None
        needed = limit
        try:
            for _ in range(20):
                params = {"instId": inst_id, "bar": TIMEFRAME, "limit": min(100, needed)}
                if after:
                    params["after"] = after
                r    = requests.get(url, params=params, timeout=15)
                data = r.json()
                if data.get("code") != "0" or not data.get("data"):
                    break
                batch = data["data"]
                all_rows.extend(batch)
                needed -= len(batch)
                if needed <= 0:
                    break
                after = batch[-1][0]
                time.sleep(0.12)
            if all_rows:
                break  # succès
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            log.error(f"fetch_live {inst_id}: {e}")
            return None

    if len(all_rows) < 50:
        return None

    df = pd.DataFrame(all_rows,
                      columns=["timestamp","open","high","low","close","volume","a","b","confirm"])
    # BUG FIX CRITIQUE 09/07/2026 : history-candles INCLUT la bougie en formation
    # (confirm=0). Sans ce filtre, current_bar_ts pointait sur une bougie de
    # ~3 min de volume → vol_ratio ≈0.01-0.08 → Patterns C/MOM/V ne pouvaient
    # JAMAIS se déclencher en live.
    df = df[df["confirm"].astype(str) == "1"]
    df = df[["timestamp","open","high","low","close","volume"]]
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(
        df["timestamp"].astype(int), unit="ms", utc=True
    ).dt.tz_convert(None)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")].dropna(subset=["close"])
    return df

# ── Indicateurs (identiques v33) ────────────────────────────────────────────



# ── Pattern C (identique v33) ────────────────────────────────────────────────

# ── Pattern D — EMA Trend Follow ────────────────────────────────────────────

# ── Pattern R — Mean Reversion ──────────────────────────────────────────────

# ── Pattern V — Volume Surge ─────────────────────────────────────────────────

# ── Pattern MOM — Bull Pullback ─────────────────────────────────────────────


# ── Pattern S — Short Continuation Bear Trend ───────────────────────────────


# ── Étape moteur (une barre) ─────────────────────────────────────────────────
def _sync_live_equity(state: dict):
    """
    Si un trader OKX est connecté (demo ou live), resynchronise state["equity"]
    sur le solde RÉEL du compte plutôt que de laisser dériver le compteur PnL
    interne (qui ne reflète ni les fills réels, ni le funding, ni les frais).
    Au premier passage en mode live (live_synced=False), réinitialise aussi
    peak/day/month_start pour éviter qu'un ancien pic de paper trading ne
    déclenche un faux drawdown / circuit-breaker dès le démarrage réel.

    Garde de sécurité : si le compte OKX retourne une equity ≤ EQUITY_FLOOR
    (compte vide ou non financé), on conserve l'equity locale et on log un
    avertissement — on ne laisse pas un compte démo non financé écraser le
    capital paper et déclencher le circuit-breaker.
    """
    if _trader is None:
        return
    real_eq = _trader.get_equity()
    if real_eq is None:
        log.warning("Sync equity OKX échouée — conservation de l'equity locale")
        return
    if real_eq <= EQUITY_FLOOR:
        global _equity_warn_date, _okx_account_funded
        _okx_account_funded = False
        today = datetime.now().strftime("%Y-%m-%d")
        if _equity_warn_date != today:
            log.warning(
                f"Compte OKX non financé ({real_eq:.2f}€ ≤ {EQUITY_FLOOR}€) "
                f"— mode paper pur activé. Financer le compte démo pour activer les ordres réels."
            )
            _equity_warn_date = today
        return
    _okx_account_funded = True
    if not state.get("live_synced", False):
        log.info(f"Premier sync live : equity locale {state['equity']:.2f}€ "
                 f"-> equity réelle OKX {real_eq:.2f}€ (reset peak/day/month)")
        state["peak_equity"]        = real_eq
        state["day_start_equity"]   = real_eq
        state["month_start_equity"] = real_eq
        state["live_synced"]        = True
    state["equity"] = real_eq

def reconcile_positions(state: dict):
    """Parité état local vs positions réelles OKX (blindage 08/07).

    Détecte les deux divergences dangereuses :
      - orphan : position ouverte chez OKX sans suivi local
      - ghost  : position locale avec okx_inst_id absente de l'exchange
    Alerte Telegram + log, AUCUNE correction automatique."""
    if _trader is None:
        return
    try:
        exch = {p["instId"] for p in _trader.get_positions()
                if float(p.get("pos") or 0) != 0}
    except Exception as e:
        log.warning(f"Reconciliation impossible (API positions) : {e}")
        return
    local = {pos["okx_inst_id"]: pk
             for pk, pos in state.get("open_positions", {}).items()
             if pos.get("okx_inst_id")}
    orphans = sorted(exch - set(local))
    ghosts  = sorted(set(local) - exch)
    if orphans or ghosts:
        lines = ["RECONCILIATION OKX — divergence état local / exchange :"]
        if orphans:
            lines.append(f"ORPHAN (exchange sans suivi local) : {', '.join(orphans)}")
        if ghosts:
            lines.append(f"GHOST (local absent de l'exchange) : {', '.join(ghosts)}")
        lines.append("Aucune action automatique — vérifier manuellement.")
        msg = "\n".join(lines)
        log.warning(msg)
        if _tg:
            try: _tg.notify_error(msg)
            except Exception: pass
    else:
        log.info(f"Reconciliation OK : {len(local)} position(s) exchange alignée(s)")

def engine_step(state: dict, sym_data: dict, current_bar_ts: pd.Timestamp):
    """
    Traite la barre `current_bar_ts` :
      1. Exécute les pending_entries (ouverture)
      2. Vérifie les exits (SL / TP / time_stop)
      3. Calcule skip_entries + drawdown
      4. Détection signaux BB Squeeze
    Modifie `state` en place. Retourne (trades_closed, signals_new).
    """
    _sync_live_equity(state)
    equity           = state["equity"]
    peak_equity      = state["peak_equity"]
    open_positions   = state["open_positions"]
    pending_entries  = state["pending_entries"]
    cooldown_tracker = state["cooldown_tracker"]
    ts_str           = str(current_bar_ts)

    # Day tracking
    today = str(current_bar_ts)[:10]
    if today != state["current_day"]:
        if state["current_day"] and _tg:
            _tg.notify_daily_summary(
                equity,
                equity - state["day_start_equity"],
                state["total_trades"],
                state["total_wins"],
                len(open_positions),
            )
        state["current_day"]      = today
        state["day_start_equity"] = equity
    day_start_equity = state["day_start_equity"]

    # ── Suivi mensuel (circuit-breaker mensuel) ──────────────────────────────
    this_month = current_bar_ts.strftime("%Y-%m")
    if this_month != state.get("current_month", ""):
        state["current_month"]      = this_month
        state["month_start_equity"] = equity
    month_start_equity = state.get("month_start_equity", equity)

    trades_closed = []
    signals_new   = []

    # ── 1. Exécuter les pending_entries ──────────────────────────────────────
    # Garde anti-staleness (16/07) : une panne infra prolongée laissant des
    # pending_entries en attente ne doit jamais les faire exécuter au réveil
    # au prix courant sur un contexte de marché obsolète.
    MAX_PENDING_AGE_H = 3.0
    for pk in list(pending_entries.keys()):
        if len(open_positions) >= MAX_POS:
            break
        p  = pending_entries.pop(pk)
        sd = sym_data.get(p["sym"])
        if sd is None:
            continue
        _sig_ts = p.get("signal_ts")
        _age_h = ((current_bar_ts - pd.Timestamp(_sig_ts)).total_seconds() / 3600
                  if _sig_ts else float("inf"))
        if _age_h > MAX_PENDING_AGE_H:
            log.warning(f"PÉRIMÉ {p['sym']} {p.get('pattern','?')} — signal vieux de "
                        f"{_age_h:.1f}h (max {MAX_PENDING_AGE_H}h), annulé sans exécution")
            if _tg:
                try:
                    _tg.notify_error(f"Signal {p['sym']} {p.get('pattern','?')} annulé "
                                     f"(périmé, {_age_h:.1f}h — probable coupure infra)")
                except Exception:
                    pass
            continue
        bar = sd["ts_to_pos"].get(current_bar_ts)
        if bar is None:
            # Remettre en attente pour la prochaine barre
            pending_entries[pk] = p
            continue

        # Revalidation : si les paramètres ont changé depuis la détection, annuler
        if p.get("pattern") == "D":
            # LONGs uniquement vérifiés contre whitelist (SHORTs peuvent venir de bear_mode élargi)
            if p.get("side") == "long" and p["sym"] not in (PATTERN_D_WHITELIST | PATTERN_D_BULL_EXTRA):
                log.warning(f"ANNULÉ {p['sym']} LONG D (hors whitelist — paramètres mis à jour)")
                continue
            if p.get("score", 0) < SCORE_MIN_D:
                log.warning(f"ANNULÉ {p['sym']} (score={p.get('score')} < SCORE_MIN_D={SCORE_MIN_D})")
                continue

        # En mode reel inverse (compte OKX EEA), seul un sous-ensemble de symboles
        # a un contrat -USD-SWAP disponible. On annule la position AVANT de
        # l'ouvrir (paper ou reel) plutot que de creer une position paper
        # fantome qu'on ne pourra jamais executer reellement.
        if (_trader is not None and getattr(_trader, "contract_type", "linear") == "inverse"
                and p["sym"] not in INVERSE_AVAILABLE_SYMS):
            log.warning(f"ANNULÉ {p['sym']} — pas de contrat -USD-SWAP disponible "
                        f"sur ce compte (mode inverse, univers restreint)")
            continue

        open_px     = float(sd["open"][bar])
        side        = p["side"]
        entry_price = (open_px * (1 + SLIPPAGE) if side == "long"
                       else open_px * (1 - SLIPPAGE))
        atr_now = float(sd["atr14"][bar])
        if math.isnan(atr_now) or atr_now <= 0:
            atr_now = entry_price * 0.015
        # Paramètres SL/TP selon le pattern (R = mean reversion, C/D = trend)
        if p.get("pattern") == "R":
            sl_pct = max(ATR_SL_MIN_R, min(ATR_SL_MAX_R, ATR_SL_MULT_R * atr_now / (entry_price + 1e-10)))
            tp_pct = sl_pct * RR_RATIO_R
        elif p.get("pattern") == "MOM":
            sl_pct = max(ATR_SL_MIN_C, min(ATR_SL_MAX_C, ATR_SL_MULT * atr_now / (entry_price + 1e-10)))
            tp_pct = sl_pct * RR_RATIO_MOM   # RR=3 (vs 4 pour C/D) : pullback = cible plus courte
        else:
            sl_pct = max(ATR_SL_MIN_C, min(ATR_SL_MAX_C, ATR_SL_MULT * atr_now / (entry_price + 1e-10)))
            tp_pct = sl_pct * RR_RATIO
        sl = entry_price * (1 - sl_pct) if side == "long" else entry_price * (1 + sl_pct)
        tp = entry_price * (1 + tp_pct) if side == "long" else entry_price * (1 - tp_pct)

        pos_key = p["sym"] + p.get("pattern", "C") + ts_str
        open_positions[pos_key] = {
            "sym":         p["sym"],
            "side":        side,
            "pattern":     p.get("pattern", "C"),
            "entry_ts":    ts_str,
            "entry_price": entry_price,
            "sl":          sl,
            "tp":          tp,
            "initial_sl":  sl,    # pour trailing SL breakeven
            "be_armed":    False, # True une fois breakeven déclenché
            "margin":      p["margin"],
            "leverage":    p["leverage"],
            "score":       p["score"],
        }
        log.info(f"OPEN  {p['sym']:12s} {side:5s} | px={entry_price:.5g} "
                 f"sl={sl:.5g} tp={tp:.5g} | marge={p['margin']:.0f}€ ×{p['leverage']}")

        # Ordre réel OKX (si trader connecté ET compte financé)
        if _trader is not None and _okx_account_funded:
            _exec_t0 = time.time()
            try:
                okx_result = _trader.place_order_hybrid(
                    p["sym"], side,
                    margin_usdt=p["margin"],
                    leverage=p["leverage"],
                    sl_pct=sl_pct,
                    tp_pct=tp_pct,
                    score=p["score"],
                )
                _latency_ms = (time.time() - _exec_t0) * 1000
                if okx_result:
                    open_positions[pos_key]["okx_ord_id"]    = okx_result["ord_id"]
                    open_positions[pos_key]["okx_contracts"] = okx_result["n_contracts"]
                    open_positions[pos_key]["okx_inst_id"]   = okx_result["inst_id"]
                    open_positions[pos_key]["entry_ts_ms"]   = int(_exec_t0 * 1000)
                    _lim_ok  = okx_result.get("maker_fill", False)
                    _lim_try = okx_result.get("limit_attempted", False)
                    log.info(
                        f"OKX ORDER OK  ordId={okx_result['ord_id']}"
                        f"  {okx_result['n_contracts']}c  entry~{okx_result['est_entry']:.5g}"
                        f"  lat={_latency_ms:.0f}ms"
                        + ("  [MAKER ✓]" if _lim_ok else "  [limit→market]" if _lim_try else "")
                    )
                    # Snap pour closure de thread
                    _sym_snap    = p["sym"]
                    _side_snap   = side
                    _patt_snap   = p.get("pattern", "C")
                    _score_snap  = p["score"]
                    _lev_snap    = p["leverage"]
                    _expected_px = open_px
                    _est_entry   = okx_result["est_entry"]
                    _ord_id      = okx_result["ord_id"]
                    _inst_id     = okx_result["inst_id"]
                    _known_fill  = okx_result.get("fill_px")   # déjà connu si limit filled

                    def _log_exec(fill, lat, expected, est,
                                  sym_, side_, patt, score_, lev_, lim_try, lim_ok):
                        if fill:
                            direction = 1 if side_ == "long" else -1
                            slip_bps  = (fill - expected) / (expected + 1e-10) * 10000 * direction
                        else:
                            slip_bps = None
                        _write_exec_metric({
                            "ts":              datetime.now().isoformat(),
                            "sym":             sym_,
                            "side":            side_,
                            "pattern":         patt,
                            "score":           score_,
                            "leverage":        lev_,
                            "expected_px":     round(expected, 6),
                            "est_entry_px":    round(est, 6),
                            "actual_fill_px":  round(fill, 6) if fill else "",
                            "slippage_bps":    round(slip_bps, 2) if slip_bps is not None else "",
                            "latency_ms":      round(lat, 1),
                            "fill_ok":         fill is not None,
                            "limit_attempted": lim_try,
                            "maker_fill":      lim_ok,
                        })
                        if fill:
                            log.info(
                                f"EXEC {sym_} fill={fill:.5g}"
                                f" slip={slip_bps:+.1f}bps lat={lat:.0f}ms"
                                + (" [maker]" if lim_ok else "")
                            )
                        else:
                            log.warning(f"EXEC {sym_} fill non obtenu dans délai")

                    if _known_fill is not None:
                        # Limit order rempli — on a déjà le fill, log direct
                        _log_exec(_known_fill, _latency_ms, _expected_px, _est_entry,
                                  _sym_snap, _side_snap, _patt_snap, _score_snap,
                                  _lev_snap, _lim_try, _lim_ok)
                    else:
                        # Market order — fetch fill en arrière-plan
                        def _async_fill(ord_id, inst_id, expected, est, lat,
                                        sym_, side_, patt, score_, lev_, lim_try, lim_ok):
                            fill = _trader.get_fill_price(ord_id, inst_id, max_wait_ms=4000)
                            _log_exec(fill, lat, expected, est,
                                      sym_, side_, patt, score_, lev_, lim_try, lim_ok)
                        threading.Thread(
                            target=_async_fill,
                            args=(_ord_id, _inst_id, _expected_px, _est_entry, _latency_ms,
                                  _sym_snap, _side_snap, _patt_snap, _score_snap, _lev_snap,
                                  _lim_try, _lim_ok),
                            daemon=True,
                        ).start()
                else:
                    log.error(f"OKX ORDER FAILED {p['sym']} — position paper uniquement")
            except Exception as e:
                log.error(f"OKX place_order_hybrid exception: {e}")

        if _tg:
            _tg.notify_open(p["sym"], side, entry_price, sl, tp,
                            p["margin"], p["leverage"])

    # ── 2. Vérifier les exits ─────────────────────────────────────────────────
    to_remove = []
    for pos_key, pos in list(open_positions.items()):
        sd = sym_data.get(pos["sym"])
        if sd is None:
            continue
        bar = sd["ts_to_pos"].get(current_bar_ts)
        if bar is None:
            continue

        hi = float(sd["high"][bar])
        lo = float(sd["low"][bar])
        cl = float(sd["close"][bar])
        if math.isnan(hi) or math.isnan(lo) or math.isnan(cl):
            continue
        entry  = pos["entry_price"]
        sl, tp = pos["sl"], pos["tp"]
        side   = pos["side"]

        exit_price = exit_reason = None
        if side == "long":
            if hi >= tp:  exit_price, exit_reason = tp, "take_profit"
            elif lo <= sl: exit_price, exit_reason = sl, "stop_loss"
        else:
            if lo <= tp:  exit_price, exit_reason = tp, "take_profit"
            elif hi >= sl: exit_price, exit_reason = sl, "stop_loss"

        entry_ts = pd.Timestamp(pos["entry_ts"])
        elapsed_h = (current_bar_ts - entry_ts).total_seconds() / 3600
        if exit_price is None and elapsed_h >= TIME_STOP_H:
            exit_price, exit_reason = cl, "time_stop"

        # Clôture forcée si la position ne respecte plus les règles actuelles
        # (ex: paramètres changés depuis l'entrée — trade hors whitelist ou score périmé)
        if exit_price is None and pos.get("pattern") == "D":
            invalid = False
            if pos["sym"] not in PATTERN_D_WHITELIST:
                invalid = True
                log.warning(f"FORCE-CLOSE {pos['sym']} hors whitelist Pattern D (params mis à jour)")
            elif pos.get("score", 99) < SCORE_MIN_D:
                invalid = True
                log.warning(f"FORCE-CLOSE {pos['sym']} score={pos.get('score')} < {SCORE_MIN_D} (params mis à jour)")
            if invalid:
                exit_price, exit_reason = cl, "param_update"

        if exit_price is not None:
            # Fermeture OKX si connecté :
            # - time_stop et param_update → fermeture manuelle (OKX n'a pas ces règles)
            # - stop_loss / take_profit   → OKX a déjà fermé automatiquement via algo attaché
            if _trader is not None and _okx_account_funded and exit_reason in ("time_stop", "param_update"):
                try:
                    _trader.close_position(pos["sym"], side)
                except Exception as e:
                    log.error(f"OKX close_position exception: {e}")

            exit_price = (exit_price * (1 - EXIT_SLIPPAGE) if side == "long"
                          else exit_price * (1 + EXIT_SLIPPAGE))
            side_mult = 1 if side == "long" else -1
            notional  = pos["margin"] * pos["leverage"]
            raw_pnl   = (exit_price - entry) / (entry + 1e-10) * side_mult * notional
            fees      = notional * COMMISSION * 2
            net_pnl   = raw_pnl - fees
            equity   += net_pnl
            peak_equity = max(peak_equity, equity)

            state["total_trades"] += 1
            state["total_pnl"]    += net_pnl
            if net_pnl > 0:
                state["total_wins"] += 1
                state["consec_losses"] = 0
                state["consec_pause_until"] = ""
            else:
                new_cl = state.get("consec_losses", 0) + 1
                state["consec_losses"] = new_cl
                if new_cl >= 5:
                    pause_ts = current_bar_ts + pd.Timedelta(hours=48)
                    state["consec_pause_until"] = str(pause_ts)
                    log.warning(
                        f"CIRCUIT-BREAKER CONSEC: {new_cl} pertes d'affilée "
                        f"→ nouvelles entrées suspendues jusqu'à {pause_ts.strftime('%Y-%m-%d %Hh')}"
                    )

            # Funding cost (async — ne bloque pas la boucle principale)
            funding_paid = 0.0
            if _trader is not None and pos.get("okx_inst_id") and pos.get("entry_ts_ms"):
                try:
                    _from_ms = int(pos["entry_ts_ms"])
                    _to_ms   = int(time.time() * 1000)
                    funding_paid = _trader.get_funding_paid(pos["okx_inst_id"], _from_ms, _to_ms)
                    if funding_paid != 0.0:
                        log.info(f"FUNDING COST {pos['sym']} : {funding_paid:+.4f} USDT sur {elapsed_h:.0f}h")
                except Exception as e:
                    log.debug(f"get_funding_paid: {e}")

            trade_row = {
                "exit_ts":    str(current_bar_ts),
                "sym":        pos["sym"],
                "side":       side,
                "pattern":    pos.get("pattern", ""),
                "entry_ts":   pos["entry_ts"],
                "entry_px":   entry,
                "exit_px":    exit_price,
                "reason":     exit_reason,
                "margin":     pos["margin"],
                "leverage":   pos["leverage"],
                "score":      pos.get("score", 0),
                "pnl":        round(net_pnl, 2),
                "equity_after": round(equity, 2),
                "funding_paid": round(funding_paid, 4) if funding_paid else "",
            }
            trades_closed.append(trade_row)
            _write_trade(trade_row)
            to_remove.append(pos_key)
            emoji = "✓" if net_pnl > 0 else "✗"
            log.info(f"CLOSE {pos['sym']:12s} {side:5s} | {exit_reason:11s} | "
                     f"pnl={net_pnl:+.2f}€ {emoji} | equity={equity:.2f}€")
            if _tg:
                _tg.notify_close(pos["sym"], side, exit_reason, net_pnl, equity)


    for k in to_remove:
        del open_positions[k]

    # ── 3. Gardes d'entrée ────────────────────────────────────────────────────
    state["equity"]      = equity
    state["peak_equity"] = peak_equity

    day_pnl_pct  = (equity - day_start_equity) / (day_start_equity + 1e-10)
    skip_entries = day_pnl_pct <= -DAILY_LOSS_CAP
    drawdown     = (peak_equity - equity) / (peak_equity + 1e-10)

    # Circuit-breaker : équité trop faible pour trader (≤ EQUITY_FLOOR)
    if equity <= EQUITY_FLOOR:
        log.error(f"CIRCUIT-BREAKER : équité {equity:.2f}€ ≤ {EQUITY_FLOOR}€ — toutes entrées bloquées")
        return trades_closed, signals_new

    # Pas d'entrée en session asiatique 00h-07h UTC (WR 19% sur backtest 4 mois)
    if current_bar_ts.hour < 7:
        log.debug(f"SESSION ASIATIQUE ({current_bar_ts.hour}h UTC) — scan écourté, pas d'entrée possible")
        return trades_closed, signals_new
    if skip_entries:
        log.info(f"  skip_entries=True (day_pnl={day_pnl_pct:.1%})")
        return trades_closed, signals_new
    if len(open_positions) + len(pending_entries) >= MAX_POS:
        return trades_closed, signals_new
    if drawdown > 0.40:
        log.info(f"  skip drawdown={drawdown:.1%}")
        return trades_closed, signals_new

    # ── 4. BTC direction filter : momentum 24H/48H ───────────────────────────
    # Bloque les LONGS si BTC est en crash soutenu (-3%/24H confirmé sur 48H)
    # Bloque les SHORTS si BTC est en bull soutenu (+3%/24H confirmé sur 48H)
    # Utilise 24H/48H au lieu de 4H/8H → ne déclenche PAS sur de simples
    # pullbacks intra-journaliers (-1.5%/4H) qui existent même en bull market
    btc_4h_bull: bool | None = None
    _btc_sd_dir = sym_data.get("BTC-USDT")
    if _btc_sd_dir is not None:
        try:
            _b = len(_btc_sd_dir["ts_index"]) - 1
            _cl_now = float(_btc_sd_dir["close"][_b])
            _cl_24h = float(_btc_sd_dir["close"][max(0, _b - 24)])
            _cl_48h = float(_btc_sd_dir["close"][max(0, _b - 48)])
            if _cl_24h > 0 and _cl_48h > 0:
                _m24h = (_cl_now - _cl_24h) / _cl_24h
                _m48h = (_cl_now - _cl_48h) / _cl_48h
                if _m24h < -0.03 and _m48h < -0.04:
                    # Crash soutenu : -3%/24H confirmé -4%/48H → LONGS interdits
                    btc_4h_bull = False
                    log.info(f"DIR-FILTER CRASH: BTC 24H={_m24h*100:+.1f}% 48H={_m48h*100:+.1f}% → longs bloqués")
                elif _m24h > 0.03 and _m48h > 0.04:
                    # Bull soutenu : +3%/24H confirmé +4%/48H → SHORTS interdits
                    btc_4h_bull = True
                    log.info(f"DIR-FILTER BULL: BTC 24H={_m24h*100:+.1f}% 48H={_m48h*100:+.1f}% → shorts bloqués")
        except Exception as _e:
            log.debug(f"DIR-FILTER indisponible ({_e}) — btc_4h_bull inchangé")

    # BTC 1H alignment : micro-trend BTC sur les 3 dernières heures
    # "bull" → +0.3%/3H → SHORTS interdits  |  "bear" → -0.3%/3H → LONGS interdits
    btc_1h_bias: "str | None" = None
    if _btc_sd_dir is not None:
        try:
            _b = len(_btc_sd_dir["ts_index"]) - 1
            if _b >= 3:
                _cl_now = float(_btc_sd_dir["close"][_b])
                _cl_3h  = float(_btc_sd_dir["close"][_b - 3])
                if _cl_3h > 0 and not (math.isnan(_cl_now) or math.isnan(_cl_3h)):
                    _m3h = (_cl_now - _cl_3h) / _cl_3h
                    if _m3h > 0.003:
                        btc_1h_bias = "bull"
                        log.info(f"BTC-1H-BIAS BULL: +{_m3h*100:.2f}% sur 3H → shorts bloqués")
                    elif _m3h < -0.003:
                        btc_1h_bias = "bear"
                        log.info(f"BTC-1H-BIAS BEAR: {_m3h*100:.2f}% sur 3H → longs bloqués")
        except Exception as _e:
            log.debug(f"BTC-1H-BIAS indisponible ({_e}) — btc_1h_bias inchangé")

    # ── 4c. Détection régime marché ───────────────────────────────────────────
    # CHAOS  : ATR BTC > 2× médiane → volatilité extrême → tout bloquer
    # RANGE  : ADX BTC < 18 ET BBW étroit → marché latéral → Pattern R only
    # BEAR   : BTC 4H baissier (EMA20_4H < EMA50_4H + ADX>20) → risk ÷3
    # TREND  : sinon → Pattern C + D normal
    chaos_regime   = False
    ranging_regime = False
    bear_macro     = False
    bull_macro     = False
    btc_mom_24h    = 0.0
    btc_adx        = 0.0
    btc_sd = sym_data.get("BTC-USDT")
    if btc_sd is not None:
        try:
            btc_bar    = len(btc_sd["ts_index"]) - 1
            btc_atr    = float(btc_sd["atr14"][btc_bar])
            btc_cl     = float(btc_sd["close"][btc_bar])
            btc_adx    = float(btc_sd["adx"][btc_bar])
            btc_bbw    = float(btc_sd["bbw"][btc_bar])
            btc_e20_4h = float(btc_sd["ema20_4h"][btc_bar])
            btc_e50_4h = float(btc_sd["ema50_4h"][btc_bar])
            cl_24h_ago = float(btc_sd["close"][max(0, btc_bar - 24)])
            if cl_24h_ago > 0:
                btc_mom_24h = (btc_cl - cl_24h_ago) / cl_24h_ago
            if btc_cl > 0 and not math.isnan(btc_atr):
                btc_atr_pct = btc_atr / btc_cl
                past_atrs = [
                    float(btc_sd["atr14"][max(0, btc_bar - i)]) /
                    (float(btc_sd["close"][max(0, btc_bar - i)]) + 1e-10)
                    for i in range(1, 31)
                ]
                med_atr_pct = sorted(past_atrs)[15]
                atr_ratio   = btc_atr_pct / (med_atr_pct + 1e-10)
                if atr_ratio > 2.0:
                    chaos_regime = True
                    log.warning(
                        f"RÉGIME CHAOS: BTC ATR {btc_atr_pct*100:.2f}% "
                        f"= {atr_ratio:.1f}× médiane — toutes entrées bloquées"
                    )
                elif not math.isnan(btc_adx) and not math.isnan(btc_bbw) and btc_adx < ADX_RANGE_MAX:
                    past_bbws = [float(btc_sd["bbw"][max(0, btc_bar - i)]) for i in range(1, 31)]
                    med_bbw   = sorted(past_bbws)[15]
                    if btc_bbw <= med_bbw * 1.2:
                        ranging_regime = True
                        log.info(
                            f"RÉGIME RANGE: BTC ADX={btc_adx:.1f} BBW={btc_bbw:.4f} "
                            f"— Pattern R actif, C+D suspendus"
                        )
            # Bear macro : BTC 4H EMA20 sous EMA50 ET ADX assez fort (vraie tendance baissière)
            if (not math.isnan(btc_e20_4h) and not math.isnan(btc_e50_4h)
                    and not math.isnan(btc_adx)
                    and btc_e20_4h < btc_e50_4h and btc_adx > 20):
                bear_macro = True
                log.info(
                    f"RÉGIME BEAR MACRO: BTC EMA20_4H={btc_e20_4h:.0f} < EMA50_4H={btc_e50_4h:.0f} "
                    f"ADX={btc_adx:.1f} — Pattern D SELL élargi à tous actifs"
                )
            # Bull macro : inverse symétrique — gate pour Pattern MOM + relaxation C/D
            elif (not math.isnan(btc_e20_4h) and not math.isnan(btc_e50_4h)
                    and btc_e20_4h > btc_e50_4h):
                bull_macro = True
                log.info(
                    f"RÉGIME BULL MACRO: BTC EMA20_4H={btc_e20_4h:.0f} > EMA50_4H={btc_e50_4h:.0f} "
                    f"— Pattern MOM actif, score C/D relâché de 3pts"
                )
        except Exception as _e:
            log.debug(f"Détection régime (chaos/range/bear/bull macro) indisponible ({_e}) — régime neutre par défaut")

    # BTC 24H crash guard
    btc_24h_crash = btc_mom_24h < BTC_CRASH_THRESH if btc_mom_24h != 0.0 else False

    # Mode Survie : BTC >12% sous pic 30j ET sous EMA50_4H → bloque toutes nouvelles entrées
    survive_mode = False
    if btc_sd is not None and not math.isnan(btc_e50_4h):
        btc_peak_30d = max(
            float(btc_sd["close"][max(0, btc_bar - _d * 24)])
            for _d in range(31)
        )
        if btc_peak_30d > 0:
            btc_dd_30d = (btc_peak_30d - btc_cl) / btc_peak_30d
            if btc_dd_30d > SURVIVE_DD and btc_cl < btc_e50_4h:
                survive_mode = True

    # Market Panic directionnel : 80%+ altcoins bougent >2.5%/4H dans même sens
    market_panic_down = False  # panique baissière → bloque C/D LONG
    market_panic_up   = False  # panique haussière (FOMO) → bloque C/D SHORT
    _panic_rets = []
    for _ps, _psd in sym_data.items():
        if _ps == "BTC-USDT": continue
        _pb = len(_psd["ts_index"]) - 1
        if _pb < 4: continue
        _pc_now = float(_psd["close"][_pb])
        _pc_4h  = float(_psd["close"][max(0, _pb - 4)])
        if _pc_4h > 0 and not math.isnan(_pc_now):
            _panic_rets.append((_pc_now - _pc_4h) / _pc_4h)
    if len(_panic_rets) >= 5:
        _n_down = sum(1 for r in _panic_rets if r < -PANIC_VEL)
        _n_up   = sum(1 for r in _panic_rets if r >  PANIC_VEL)
        if _n_down / len(_panic_rets) >= PANIC_THRESH:
            market_panic_down = True
            log.warning(f"MARKET PANIC DOWN: {_n_down}/{len(_panic_rets)} altcoins <-{PANIC_VEL*100:.1f}%/4H → LONGS C/D bloqués")
        if _n_up   / len(_panic_rets) >= PANIC_THRESH:
            market_panic_up = True
            log.warning(f"MARKET PANIC UP (FOMO): {_n_up}/{len(_panic_rets)} altcoins >+{PANIC_VEL*100:.1f}%/4H → SHORTS C/D bloqués")

    if survive_mode:
        if btc_sd is not None:
            log.warning(
                f"BEAR MODE ACTIF: BTC DD={btc_dd_30d*100:.1f}% depuis pic 30j "
                f"(BTC={btc_cl:.0f} pic={btc_peak_30d:.0f}) ET sous EMA50_4H={btc_e50_4h:.0f} "
                f"→ LONGS bloqués, SHORTS autorisés (marge ×{BEAR_MARGIN_SCALE})"
            )

    # NearATH : BTC <3% sous son pic 90j → C/D SHORTS bloqués (bull fort)
    # WFO insight : en bull fort (BTC à ATH), les shorts C/D échouent systématiquement
    btc_near_ath = False
    if btc_sd is not None:
        btc_peak_90d = max(
            float(btc_sd["close"][max(0, btc_bar - _d * 24)])
            for _d in range(91)
        )
        if btc_peak_90d > 0:
            _dd_90 = (btc_peak_90d - btc_cl) / btc_peak_90d
            if _dd_90 < NEAR_ATH_THRESH:
                btc_near_ath = True
                log.info(f"NEAR-ATH: BTC={btc_cl:.0f} à {_dd_90*100:.1f}% sous pic 90j={btc_peak_90d:.0f} → SHORTS C/D bloqués")

    # ── 4b. Sentiment & News ──────────────────────────────────────────────────
    sentiment_scale = 1.0
    if _get_sentiment is not None:
        try:
            analyzer = _get_sentiment()
            sent     = analyzer.composite()
            sentiment_scale = analyzer.position_scale()
            if sent["block_all"]:
                log.warning(f"Sentiment: BLOCK ALL — {sent['reasons']}")
                return trades_closed, signals_new
            if sent["block_longs"]:
                log.info(f"Sentiment: block_longs actif — {sent['reasons']}")
            if sent["reasons"]:
                log.info(f"Sentiment score={sent['score']:+.2f} scale=×{sentiment_scale:.2f}"
                         f" | {' | '.join(sent['reasons'])}")
        except Exception as e:
            log.warning(f"Sentiment indisponible ({e}) — scale=1.0")
            sentiment_scale = 1.0

    # Bloquer toutes nouvelles entrées si régime chaos
    if chaos_regime:
        return trades_closed, signals_new

    # Circuit-breaker : 5 pertes consécutives → pause 48h
    _pause_str = state.get("consec_pause_until", "")
    if _pause_str:
        try:
            _pause_until = pd.Timestamp(_pause_str)
            if current_bar_ts < _pause_until:
                log.info(
                    f"CIRCUIT-BREAKER CONSEC: pause jusqu'à {_pause_until.strftime('%Y-%m-%d %Hh')} "
                    f"({state.get('consec_losses', 0)} pertes d'affilée)"
                )
                return trades_closed, signals_new
        except Exception:
            pass

    # ── 5. Détection signaux — régime adaptatif ───────────────────────────────
    # TREND  → Pattern C + D   (ADX élevé, volatilité normale)
    # RANGE  → Pattern R       (ADX faible, marché latéral)
    # CHAOS  → rien            (bloqué ci-dessus)
    dd_scale         = max(0.5, 1.0 - drawdown * 2.5)

    # Bear mode : survive_mode → shorts autorisés, marge réduite
    bear_mode  = survive_mode
    bear_scale = BEAR_MARGIN_SCALE if bear_mode else 1.0

    # Circuit-breaker mensuel : mois down >15% → size réduite
    monthly_pnl_pct  = (equity - month_start_equity) / (month_start_equity + 1e-10)
    monthly_cb_scale = MONTHLY_CB_SCALE if monthly_pnl_pct < -MONTHLY_CB_PCT else 1.0
    if monthly_pnl_pct < -MONTHLY_CB_PCT:
        log.info(f"MONTHLY-CB: mois {this_month} à {monthly_pnl_pct*100:.1f}% → size ×{MONTHLY_CB_SCALE}")

    # ── Paramètres adaptatifs : 3 régimes + direction filter ─────────────────
    # BEAR   : EMA croisée + ADX → protection maximale, risque ÷3
    # NORMAL : Tendance neutre → paramètres standards
    # DIR    : filtre direction actif → score +8 (seulement signaux forts)
    if bear_macro:
        active_risk_pct = RISK_PCT * 0.30    # 25% → 7.5% : protection max
        active_max_pos  = 3                  # 8 → 3 positions max
        active_score_c  = SCORE_MIN + 15     # 65 → 80
        active_score_d  = SCORE_MIN_D + 8    # 74 → 82
    else:
        active_risk_pct = RISK_PCT
        active_max_pos  = MAX_POS
        active_score_c  = (SCORE_MIN - 3) if bull_macro else SCORE_MIN   # -3pts validé IS+OOS en bull
        active_score_d  = SCORE_MIN_D
    # Direction filter : le blocage directionnel seul suffit, pas de pénalité score
    # btc_4h_bull=False → zéro LONG (scores inchangés pour les shorts)
    # btc_4h_bull=True  → zéro SHORT (scores inchangés pour les longs)
    active_leverage_base = BASE_LEVERAGE
    active_leverage_high = HIGH_LEVERAGE
    active_max_margin    = MAX_MARGIN_RATIO

    # ATR normalization : position size réduite si volatilité actuelle > médiane 30 barres
    # → positions plus petites en période agitée, normales en période stable
    def _atr_norm(sd: dict, bar: int) -> float:
        try:
            atr_cur  = float(sd["atr14"][bar])
            cl_cur   = float(sd["close"][bar])
            if math.isnan(atr_cur) or math.isnan(cl_cur) or cl_cur <= 0:
                return 1.0
            atr_pct  = atr_cur / cl_cur
            atrs     = [float(sd["atr14"][max(0, bar-i)]) / (float(sd["close"][max(0, bar-i)]) + 1e-10)
                        for i in range(1, 31)]
            med_atr  = sorted(atrs)[15]
            return max(0.5, min(2.0, med_atr / (atr_pct + 1e-10)))
        except Exception:
            return 1.0

    combined_scale   = bear_scale * monthly_cb_scale
    margin_per_trade = equity * active_risk_pct * dd_scale * combined_scale  # ajusté par ATR norm par signal
    total_margin     = (sum(p["margin"] for p in pending_entries.values()) +
                        sum(p["margin"] for p in open_positions.values()))
    max_margin_allowed = equity * active_max_margin

    # ── Microstructure : Funding Rate z-score (adaptatif 30j) ────────────────
    # micro_adj > 0 = bullish bias ; < 0 = bearish bias
    # Appliqué : BUY score += micro_adj   /   SELL score -= micro_adj
    # Fetch 90 dernières fundings → z-score → trigger si extrême relatif (|z| > 2)
    micro_adj = 0
    try:
        _fund_history = _fetch_live_funding_history(n=90)
        if len(_fund_history) >= 20:
            _fr  = float(_fund_history[-1])
            _mu  = sum(_fund_history) / len(_fund_history)
            _var = sum((x - _mu)**2 for x in _fund_history) / len(_fund_history)
            _sig = _var ** 0.5
            if _sig > 1e-10:
                _z = (_fr - _mu) / _sig
                if _z > 2.0:
                    micro_adj -= FUNDING_SCORE_ADJ
                    log.info(f"FUNDING BEAR: z={_z:.1f} fr={_fr*100:+.4f}%/8h → micro_adj={micro_adj:+d}")
                elif _z < -2.0:
                    micro_adj += FUNDING_SCORE_ADJ
                    log.info(f"FUNDING BULL: z={_z:.1f} fr={_fr*100:+.4f}%/8h → micro_adj={micro_adj:+d}")
    except Exception:
        pass

    # Symboles déjà en position (toutes patterns confondues)
    syms_with_pos = {pos["sym"] for pos in open_positions.values()}

    # ── Pattern C — BB Squeeze (uniquement en régime TREND) ──────────────────
    candidates = []
    if not ranging_regime:
        for sym, sd in sym_data.items():
            if sym == "BTC-USDT":
                continue
            if sym in PATTERN_C_BLACKLIST:
                continue
            if sym + "C" in pending_entries or sym + "D" in pending_entries:
                continue
            if sym in syms_with_pos:
                continue
            bar = sd["ts_to_pos"].get(current_bar_ts)
            if bar is None or bar < 250:
                continue
            adx_val = float(sd["adx"][bar])
            if math.isnan(adx_val):
                continue
            ck = sym + "C"
            # Comparaison en timestamp (pas en indice de barre) — en live la
            # fenêtre glissante de 350 barres garde le même indice à chaque cycle,
            # ce qui rendait le cooldown permanent. Le timestamp avance lui toujours.
            if cooldown_tracker.get(ck, "") > str(current_bar_ts):
                continue
            action = check_pattern_c(sd, bar, adx_val)
            if action is None:
                continue
            if btc_4h_bull is not None:
                if action == "BUY"  and not btc_4h_bull:
                    continue
                if action == "SELL" and btc_4h_bull:
                    continue
            if btc_1h_bias == "bear" and action == "BUY":
                continue
            if btc_1h_bias == "bull" and action == "SELL":
                continue
            # 24H crash guard : bloque les LONGS C si BTC en chute libre
            if action == "BUY" and btc_24h_crash:
                continue
            # Bear mode : LONGS bloqués, SHORTS autorisés avec score renforcé
            if bear_mode and action == "BUY":
                continue
            # Market Panic directionnel : panique DOWN→bloque LONGS, UP→bloque SHORTS
            if action == "BUY"  and market_panic_down: continue
            if action == "SELL" and market_panic_up:   continue
            # NearATH : BTC proche de son ATH → C SHORTS bloqués
            if action == "SELL" and btc_near_ath: continue
            _raw_sc = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
            score = min(100, max(0, _raw_sc + (micro_adj if action == "BUY" else -micro_adj)))
            if score < active_score_c:
                continue
            # Leverage : bear → base uniquement, bull fort + score élevé → boost
            if bear_mode:
                lev = active_leverage_base
            elif btc_near_ath and score >= BULL_LEV_SCORE and adx_val > 25:
                lev = BULL_LEV_MAX
            else:
                lev = active_leverage_high if (adx_val > 30 and score >= 75) else active_leverage_base
            candidates.append({"sym": sym, "ck": ck, "bar": bar,
                                "action": action, "score": score,
                                "adx": adx_val, "leverage": lev, "pattern": "C"})

    candidates.sort(key=lambda c: (c["score"], c["adx"]), reverse=True)

    for c in candidates:
        if len(open_positions) + len(pending_entries) >= active_max_pos:
            break
        if total_margin + margin_per_trade > max_margin_allowed:
            break
        side        = "long" if c["action"] == "BUY" else "short"
        atr_factor  = _atr_norm(sym_data[c["sym"]], c["bar"])
        margin_atr  = equity * active_risk_pct * dd_scale * combined_scale * atr_factor * sentiment_scale * _score_size_mult(c["score"], "C")
        pending_entries[c["sym"] + "C"] = {
            "sym":      c["sym"],
            "side":     side,
            "pattern":  "C",
            "signal_ts": str(current_bar_ts),
            "margin":   margin_atr,
            "leverage": c["leverage"],
            "score":    c["score"],
            "adx":      c["adx"],
        }
        cooldown_tracker[c["ck"]] = str(current_bar_ts + pd.Timedelta(hours=COOLDOWN_BARS))
        total_margin += margin_atr
        signals_new.append(c)
        log.info(f"SIGNAL-C {c['sym']:12s} {side:5s} | score={c['score']} "
                 f"adx={c['adx']:.1f} lev=×{c['leverage']} marge={margin_atr:.0f}€"
                 f" atr_factor={atr_factor:.2f} | entry prévu à la prochaine bougie")
        if _tg:
            _tg.notify_signal(c["sym"], side, c["score"], c["adx"],
                              c["leverage"], margin_per_trade)

    # ── 6. Pattern D — EMA Trend Follow (uniquement en régime TREND) ────────
    # En bear macro, Pattern D SELL élargi à tous les actifs (pas seulement 5)
    # Diagnostic révèle 89.7% des signaux D bloqués par whitelist en bear market
    margin_d = equity * RISK_PCT_D * dd_scale

    candidates_d = []
    if not ranging_regime:
        for sym, sd in sym_data.items():
            if sym == "BTC-USDT":
                continue
            # En bear macro : tous les actifs (SELL uniquement)
            # En bull fort (non-bear_macro) : whitelist élargie (10 assets)
            # En neutre : whitelist stricte (5 assets)
            if bear_mode:
                # Bear mode : tous les actifs éligibles D (SELL only)
                pass  # pas de restriction d'univers
            elif not bear_macro:
                _d_universe = PATTERN_D_WHITELIST | PATTERN_D_BULL_EXTRA
                if sym not in _d_universe:
                    continue
            if sym in syms_with_pos:
                continue
            if sym + "C" in pending_entries or sym + "D" in pending_entries:
                continue
            bar = sd["ts_to_pos"].get(current_bar_ts)
            if bar is None or bar < 150:
                continue
            adx_val = float(sd["adx"][bar])
            if math.isnan(adx_val) or adx_val < ADX_MIN_D:
                continue
            ck = sym + "D"
            if cooldown_tracker.get(ck, "") > str(current_bar_ts):
                continue
            action = check_pattern_d(sd, bar, adx_val)
            if action is None:
                continue
            # En bear macro : BUY bloqué (ne pas aller long contre la tendance macro)
            if bear_macro and action == "BUY":
                continue
            if btc_4h_bull is not None:
                if action == "BUY"  and not btc_4h_bull:
                    continue
                if action == "SELL" and btc_4h_bull:
                    continue
            if btc_1h_bias == "bear" and action == "BUY":
                continue
            if btc_1h_bias == "bull" and action == "SELL":
                continue
            # 24H crash guard : bloque les LONGS D si BTC en chute libre
            if action == "BUY" and btc_24h_crash:
                continue
            # Bear mode : LONGs D bloqués, SHORTs autorisés
            if bear_mode and action == "BUY":
                continue
            # Bear choppy : D shorts bloqués si BTC ADX trop faible (pas de tendance nette)
            if action == "SELL" and bear_mode and btc_adx < BTC_ADX_MIN_BEAR_D:
                continue
            # Market Panic directionnel
            if action == "BUY"  and market_panic_down: continue
            if action == "SELL" and market_panic_up:   continue
            # NearATH : BTC proche de son ATH → D SHORTS bloqués
            if action == "SELL" and btc_near_ath: continue
            _raw_sc = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
            score = min(100, max(0, _raw_sc + (micro_adj if action == "BUY" else -micro_adj)))
            if score < active_score_d:
                continue
            # Score plus strict pour non-whitelist D en bear
            if (bear_mode and action == "SELL"
                    and sym not in PATTERN_D_WHITELIST
                    and score < BEAR_D_NONWL_SCORE_MIN):
                continue
            lev = active_leverage_base if bear_mode else (active_leverage_high if (adx_val > 30 and score >= SCORE_MIN_D) else active_leverage_base)
            candidates_d.append({"sym": sym, "ck": ck, "bar": bar,
                                  "action": action, "score": score,
                                  "adx": adx_val, "leverage": lev, "pattern": "D"})

    candidates_d.sort(key=lambda c: (c["score"], c["adx"]), reverse=True)

    # Compteur non-whitelist bear D déjà ouverts ou en attente
    _bear_nonwl_d_count = (
        sum(1 for p in open_positions.values()
            if p.get("pattern") == "D" and p.get("side") == "short"
            and p.get("sym") not in PATTERN_D_WHITELIST)
        + sum(1 for pe in pending_entries.values()
              if pe.get("pattern") == "D" and pe.get("side") == "short"
              and pe.get("sym") not in PATTERN_D_WHITELIST)
    )

    for c in candidates_d:
        if len(open_positions) + len(pending_entries) >= active_max_pos:
            break
        if total_margin + margin_d > max_margin_allowed:
            break
        side          = "long" if c["action"] == "BUY" else "short"
        # Garde corrélation : max MAX_BEAR_D_NONWL_POS non-whitelist shorts simultanés
        if (side == "short" and bear_mode
                and c["sym"] not in PATTERN_D_WHITELIST
                and _bear_nonwl_d_count >= MAX_BEAR_D_NONWL_POS):
            continue
        atr_factor_d  = _atr_norm(sym_data[c["sym"]], c["bar"])
        margin_d_atr  = equity * RISK_PCT_D * dd_scale * combined_scale * atr_factor_d * sentiment_scale * _score_size_mult(c["score"], "D")
        pending_entries[c["sym"] + "D"] = {
            "sym":      c["sym"],
            "side":     side,
            "pattern":  "D",
            "signal_ts": str(current_bar_ts),
            "margin":   margin_d_atr,
            "leverage": c["leverage"],
            "score":    c["score"],
            "adx":      c["adx"],
        }
        cooldown_tracker[c["ck"]] = str(current_bar_ts + pd.Timedelta(hours=COOLDOWN_BARS))
        if side == "short" and bear_mode and c["sym"] not in PATTERN_D_WHITELIST:
            _bear_nonwl_d_count += 1
        total_margin += margin_d_atr
        signals_new.append(c)
        log.info(f"SIGNAL-D {c['sym']:12s} {side:5s} | score={c['score']} "
                 f"adx={c['adx']:.1f} lev=×{c['leverage']} marge={margin_d:.0f}€ | "
                 f"entry prévu à la prochaine bougie")
        if _tg:
            _tg.notify_signal(c["sym"], side, c["score"], c["adx"],
                              c["leverage"], margin_d_atr)

    # ── 6b. Pattern MOM — Bull Pullback ──────────────────────────────────────
    # Actif SEULEMENT en bull_macro (BTC EMA20_4H > EMA50_4H)
    # Symétrique à Pattern S : cible les dips RSI 40-50 en tendance haussière confirmée
    # Validé IS+OOS : PF=1.41, WR=38.5%, ratio ×2.26, Kelly=11.2% (audit 06/07)
    if bull_macro and not chaos_regime and not survive_mode and not bear_mode:
        _mom_count = (
            sum(1 for p in open_positions.values() if p.get("pattern") == "MOM")
            + sum(1 for pe in pending_entries.values() if pe.get("pattern") == "MOM")
        )

        if _mom_count < MAX_POS_MOM:
            candidates_mom = []
            for sym, sd in sym_data.items():
                if sym == "BTC-USDT":
                    continue
                if sym in syms_with_pos:
                    continue
                if sym + "MOM" in pending_entries:
                    continue
                bar = sd["ts_to_pos"].get(current_bar_ts)
                if bar is None or bar < 60:
                    continue
                ck = sym + "MOM"
                if cooldown_tracker.get(ck, "") > str(current_bar_ts):
                    continue
                adx_val = float(sd["adx"][bar])
                if math.isnan(adx_val):
                    continue
                action = check_pattern_mom(sd, bar, adx_val)
                if action is None:
                    continue
                if market_panic_down:
                    continue
                if btc_1h_bias == "bear":
                    continue
                if btc_24h_crash:
                    continue
                _raw_sc = int(sd["buy_sc"][bar])
                score_m = min(100, max(0, _raw_sc + micro_adj))
                if score_m < SCORE_MIN_MOM:
                    continue
                candidates_mom.append({"sym": sym, "ck": ck, "bar": bar,
                                       "action": "BUY", "score": score_m,
                                       "adx": adx_val, "leverage": BASE_LEVERAGE_MOM,
                                       "pattern": "MOM"})

            candidates_mom.sort(key=lambda c: c["score"], reverse=True)

            for c in candidates_mom[:MAX_POS_MOM - _mom_count]:
                if len(open_positions) + len(pending_entries) >= active_max_pos:
                    break
                if total_margin + equity * RISK_PCT_MOM > max_margin_allowed:
                    break
                atr_factor_m = _atr_norm(sym_data[c["sym"]], c["bar"])
                margin_m = equity * RISK_PCT_MOM * dd_scale * combined_scale * atr_factor_m * sentiment_scale
                pending_entries[c["sym"] + "MOM"] = {
                    "sym":      c["sym"],
                    "side":     "long",
                    "pattern":  "MOM",
                    "signal_ts": str(current_bar_ts),
                    "margin":   margin_m,
                    "leverage": BASE_LEVERAGE_MOM,
                    "score":    c["score"],
                    "adx":      c["adx"],
                }
                cooldown_tracker[c["ck"]] = str(
                    current_bar_ts + pd.Timedelta(hours=COOLDOWN_BARS_MOM)
                )
                total_margin += margin_m
                signals_new.append(c)
                log.info(
                    f"SIGNAL-MOM {c['sym']:12s} long | score={c['score']} "
                    f"adx={c['adx']:.1f} lev=×{BASE_LEVERAGE_MOM} marge={margin_m:.0f}€ "
                    f"(bull_macro gate) | entry prévu à la prochaine bougie"
                )
                if _tg:
                    _tg.notify_signal(c["sym"], "long", c["score"], c["adx"],
                                      BASE_LEVERAGE_MOM, margin_m)

    # ── 7. Pattern S — Short Continuation Bear Trend ─────────────────────────
    # Actif SEULEMENT en bear_macro (BTC EMA20_4H < EMA50_4H + ADX > 22)
    # Bypass intentionnel du filtre 1H bias : S cible les failed rallies 1H en bear
    # Budget isolé S_MARGIN_CAP — ne touche pas au capital composé C/D
    if bear_macro and not chaos_regime and not survive_mode:
        _s_exposure = sum(
            p["margin"] for p in open_positions.values() if p.get("pattern") == "S"
        ) + sum(
            pe["margin"] for pe in pending_entries.values() if pe.get("pattern") == "S"
        )
        _s_budget = S_MARGIN_CAP - _s_exposure

        if _s_budget > 0:
            candidates_s = []
            for sym, sd in sym_data.items():
                if sym == "BTC-USDT":
                    continue
                if sym in PATTERN_S_BLACKLIST:
                    continue
                if sym in syms_with_pos:
                    continue
                if sym + "S" in pending_entries:
                    continue
                bar = sd["ts_to_pos"].get(current_bar_ts)
                if bar is None or bar < 52:
                    continue
                ck = sym + "S"
                if cooldown_tracker.get(ck, "") > str(current_bar_ts):
                    continue
                # Gate individuel : asset lui-même doit être baissier 4H
                ema20_4h_s = float(sd["ema20_4h"][bar])
                ema50_4h_s = float(sd["ema50_4h"][bar])
                if math.isnan(ema20_4h_s) or math.isnan(ema50_4h_s):
                    continue
                if ema20_4h_s > ema50_4h_s:   # asset en bull 4H → pas de short S
                    continue
                adx_val = float(sd["adx"][bar])
                if math.isnan(adx_val):
                    continue
                action = check_pattern_s(sd, bar, adx_val)
                if action is None:
                    continue
                if market_panic_up:
                    continue
                if btc_near_ath:
                    continue
                _raw_sc = int(sd["sell_sc"][bar])
                score_s = min(100, max(0, _raw_sc - micro_adj))
                if score_s < SCORE_MIN_S:
                    continue
                candidates_s.append({"sym": sym, "ck": ck, "bar": bar,
                                      "score": score_s, "adx": adx_val})

            candidates_s.sort(key=lambda c: c["score"], reverse=True)

            for c in candidates_s[:1]:   # max 1 position S (budget 200€ = 1 trade)
                margin_s = min(_s_budget, S_MARGIN_CAP)
                pending_entries[c["sym"] + "S"] = {
                    "sym":      c["sym"],
                    "side":     "short",
                    "pattern":  "S",
                    "signal_ts": str(current_bar_ts),
                    "margin":   margin_s,
                    "leverage": BASE_LEVERAGE_S,
                    "score":    c["score"],
                    "adx":      c["adx"],
                }
                cooldown_tracker[c["ck"]] = str(
                    current_bar_ts + pd.Timedelta(hours=COOLDOWN_BARS_S)
                )
                signals_new.append(c)
                log.info(
                    f"SIGNAL-S {c['sym']:12s} short | score={c['score']} "
                    f"adx={c['adx']:.1f} lev=×{BASE_LEVERAGE_S} marge={margin_s:.0f}€ "
                    f"(budget S isolé) | entry prévu à la prochaine bougie"
                )
                if _tg:
                    _tg.notify_signal(c["sym"], "short", c["score"], c["adx"],
                                      BASE_LEVERAGE_S, margin_s)

    # ── 8. Pattern R — Mean Reversion (uniquement en régime RANGE) ───────────
    if ranging_regime:
        margin_r = equity * RISK_PCT_R * dd_scale
        candidates_r = []
        for sym, sd in sym_data.items():
            if sym == "BTC-USDT":
                continue
            if sym in syms_with_pos:
                continue
            if sym + "R" in pending_entries:
                continue
            bar = sd["ts_to_pos"].get(current_bar_ts)
            if bar is None or bar < 30:
                continue
            ck = sym + "R"
            if cooldown_tracker.get(ck, "") > str(current_bar_ts):
                continue
            # Mode Survie : bloque toutes entrées R également
            if survive_mode:
                continue
            action = check_pattern_r(sd, bar)
            if action is None:
                continue
            _raw_sc = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
            score = min(100, max(0, _raw_sc + (micro_adj if action == "BUY" else -micro_adj)))
            if score < SCORE_MIN_R:
                continue
            candidates_r.append({"sym": sym, "ck": ck, "bar": bar,
                                  "action": action, "score": score,
                                  "adx": float(sd["adx"][bar]), "leverage": BASE_LEVERAGE_R,
                                  "pattern": "R"})

        candidates_r.sort(key=lambda c: c["score"], reverse=True)

        for c in candidates_r:
            if len(open_positions) + len(pending_entries) >= active_max_pos:
                break
            if total_margin + margin_r > max_margin_allowed:
                break
            side         = "long" if c["action"] == "BUY" else "short"
            atr_factor_r = _atr_norm(sym_data[c["sym"]], c["bar"])
            margin_r_atr = equity * RISK_PCT_R * dd_scale * atr_factor_r * sentiment_scale
            pending_entries[c["sym"] + "R"] = {
                "sym":      c["sym"],
                "side":     side,
                "pattern":  "R",
                "signal_ts": str(current_bar_ts),
                "margin":   margin_r_atr,
                "leverage": c["leverage"],
                "score":    c["score"],
            }
            cooldown_tracker[c["ck"]] = str(current_bar_ts + pd.Timedelta(hours=COOLDOWN_BARS_R))
            total_margin += margin_r_atr
            signals_new.append(c)
            log.info(f"SIGNAL-R {c['sym']:12s} {side:5s} | score={c['score']} "
                     f"régime=RANGE lev=×{c['leverage']} marge={margin_r_atr:.0f}€ | "
                     f"entry prévu à la prochaine bougie")
            if _tg:
                _tg.notify_signal(c["sym"], side, c["score"], c.get("adx", 0),
                                  c["leverage"], margin_r_atr)

    # ── 8. Pattern V — Volume Surge (indépendant du régime) ─────────────────────
    # En bear_mode : SELL uniquement (volume institutionnel de vente = signal fort en bear)
    if True:  # V actif même en bear_mode (SELL only)
        margin_v = equity * RISK_PCT_V * dd_scale * combined_scale
        candidates_v = []
        for sym, sd in sym_data.items():
            if sym == "BTC-USDT":
                continue
            if sym in syms_with_pos:
                continue
            if sym + "V" in pending_entries:
                continue
            bar = sd["ts_to_pos"].get(current_bar_ts)
            if bar is None or bar < 30:
                continue
            ck = sym + "V"
            if cooldown_tracker.get(ck, "") > str(current_bar_ts):
                continue
            action = check_pattern_v(sd, bar)
            if action is None:
                continue
            if action == "BUY"  and bear_mode:         continue  # bear : no longs V
            if action == "BUY"  and market_panic_down: continue
            if action == "SELL" and market_panic_up:   continue
            if action == "SELL" and btc_near_ath:      continue
            _raw_sc = int(sd["buy_sc"][bar]) if action == "BUY" else int(sd["sell_sc"][bar])
            score = min(100, max(0, _raw_sc + (micro_adj if action == "BUY" else -micro_adj)))
            if score < SCORE_MIN_V:
                continue
            candidates_v.append({"sym": sym, "ck": ck, "bar": bar,
                                  "action": action, "score": score,
                                  "adx": float(sd["adx"][bar]), "leverage": BASE_LEVERAGE_V,
                                  "pattern": "V"})

        candidates_v.sort(key=lambda c: c["score"], reverse=True)

        for c in candidates_v:
            if len(open_positions) + len(pending_entries) >= active_max_pos:
                break
            if total_margin + margin_v > max_margin_allowed:
                break
            side = "long" if c["action"] == "BUY" else "short"
            pending_entries[c["sym"] + "V"] = {
                "sym":      c["sym"],
                "side":     side,
                "pattern":  "V",
                "signal_ts": str(current_bar_ts),
                "margin":   margin_v,
                "leverage": c["leverage"],
                "score":    c["score"],
            }
            cooldown_tracker[c["ck"]] = str(current_bar_ts + pd.Timedelta(hours=COOLDOWN_BARS_V))
            total_margin += margin_v
            signals_new.append(c)
            log.info(f"SIGNAL-V {c['sym']:12s} {side:5s} | score={c['score']} "
                     f"vol-surge lev=×{c['leverage']} marge={margin_v:.0f}€ | "
                     f"entry prévu à la prochaine bougie")
            if _tg:
                _tg.notify_signal(c["sym"], side, c["score"], c.get("adx", 0),
                                  c["leverage"], margin_v)

    state["open_positions"]   = open_positions
    state["pending_entries"]  = pending_entries
    state["cooldown_tracker"] = cooldown_tracker

    # ── Diagnostic : pourquoi aucun signal ? ─────────────────────────────────
    if not signals_new:
        _diag = []

        # Blockers globaux
        if current_bar_ts.hour < 7:
            _diag.append("SESSION ASIATIQUE (00-07h UTC)")
        if chaos_regime:
            _diag.append("CHAOS (ATR×2)")
        if survive_mode:
            _diag.append("SURVIVE MODE (DD>12% sous EMA50)")
        if bear_macro:
            _diag.append(f"BEAR MACRO → C longs bloqués")
        if btc_near_ath:
            _diag.append(f"NEAR-ATH (BTC à {((float(btc_sd['close'][len(btc_sd['ts_index'])-1]) - float(btc_sd['close'][max(0,len(btc_sd['ts_index'])-1-90*24)]))/max(1e-10,float(btc_sd['close'][max(0,len(btc_sd['ts_index'])-1-90*24)]))*100):.1f}% sous pic 90j) → shorts bloqués")
        if btc_1h_bias == "bull":
            _diag.append("1H BIAS BULL → shorts bloqués")
        elif btc_1h_bias == "bear":
            _diag.append("1H BIAS BEAR → longs bloqués")
        if ranging_regime:
            _diag.append("RANGE (ADX<22, BB étroit) → C+D suspendus, R actif")

        # Diagnostic Pattern C : chercher le(s) signal(s) le plus proche
        _c_best = []
        for _sym, _sd in sym_data.items():
            if _sym == "BTC-USDT" or _sym in PATTERN_C_BLACKLIST:
                continue
            _b = _sd["ts_to_pos"].get(current_bar_ts)
            if _b is None or _b < 250:
                continue
            _adx = float(_sd["adx"][_b])
            if math.isnan(_adx):
                continue
            # Vérifier les conditions C une par une pour trouver le blocage
            try:
                import backtest_v33 as _bt_diag
                _cl  = float(_sd["close"][_b])
                _bbw = float(_sd["bbw"][_b])
                _bbq = float(_sd["bbw_q15"][_b])
                _vr  = float(_sd["vol_ratio"][_b])
                _bbu = float(_sd["bb_upper"][_b])
                _bbl = float(_sd["bb_lower"][_b])
                _e20 = float(_sd["ema20_4h"][_b])
                _e50 = float(_sd["ema50_4h"][_b])
                _asset_bull = _e20 > _e50
                _squeeze_ok = _bbw <= _bbq
                _vol_ok     = _vr >= VOL_RATIO_C
                _breach_ok  = (_cl > _bbu and _asset_bull) or (_cl < _bbl and not _asset_bull)
                _sc_raw     = int(_sd["buy_sc"][_b]) if _asset_bull else int(_sd["sell_sc"][_b])
                _sc         = min(100, max(0, _sc_raw + (micro_adj if _asset_bull else -micro_adj)))
                _sc_ok      = _sc >= active_score_c
                if not _squeeze_ok:
                    _reason = f"squeeze NON (BBW={_bbw:.4f}>{_bbq:.4f})"
                elif not _vol_ok:
                    _reason = f"vol NON ({_vr:.2f}<{VOL_RATIO_C})"
                elif not _breach_ok:
                    _reason = f"pas de breach BB"
                elif not _sc_ok:
                    _reason = f"score {_sc}<{active_score_c}"
                else:
                    _reason = "OK mais bloqué par filtre global"
                _c_best.append((_sym.replace("-USDT",""), _reason, _sc))
            except Exception:
                pass

        if _c_best:
            # Trier par score décroissant, afficher top 3
            _c_best.sort(key=lambda x: x[2], reverse=True)
            _c_lines = "  ".join([f"{s}({r})" for s, r, _ in _c_best[:3]])
            _diag.append(f"C longs — top 3 : {_c_lines}")

        if _diag:
            log.info("SCAN: 0 signal  |  " + "  |  ".join(_diag))
        else:
            log.info("SCAN: 0 signal  |  Aucun setup détecté (scores insuffisants)")

    # ── Intelligence adaptative : readiness pré-signal ────────────────────────
    # Calcule la maturité de chaque setup (0-100%) et alerte via Telegram
    # avant qu'il se déclenche — sans changer les paramètres d'entrée.
    if _tg and not signals_new and current_bar_ts.hour >= 7 and not chaos_regime:
        _ready_alerts = state.setdefault("readiness_alerts", {})
        _cur_str      = str(current_bar_ts)
        _READY_THR    = 78   # % minimum pour alerte pré-signal
        _READY_COOL   = pd.Timedelta(hours=4)  # silence 4h entre 2 alertes même symbole

        for _sym, _sd in sym_data.items():
            if _sym == "BTC-USDT":
                continue
            _b = _sd["ts_to_pos"].get(current_bar_ts)
            if _b is None or _b < 50:
                continue
            try:
                _adx  = float(_sd["adx"][_b])
                _rsi  = float(_sd["rsi14"][_b])
                _e9   = float(_sd["ema9"][_b]); _e21 = float(_sd["ema21"][_b])
                _e50  = float(_sd["ema50"][_b])
                _e20_4h = float(_sd["ema20_4h"][_b]); _e50_4h = float(_sd["ema50_4h"][_b])
                _bbw  = float(_sd["bbw"][_b]); _bbq = float(_sd["bbw_q15"][_b])
                _vr   = float(_sd["vol_ratio"][_b])
                _bsc  = int(_sd["buy_sc"][_b]); _ssc = int(_sd["sell_sc"][_b])
                if any(math.isnan(v) for v in [_adx, _rsi, _e9, _e21, _e50, _bbw, _bbq, _vr]):
                    continue
                _asset_bull = _e20_4h > _e50_4h

                # ── Readiness Pattern C (BB squeeze approach) ─────────────────
                if not bear_macro and _sym not in PATTERN_C_BLACKLIST:
                    _side_c = "long" if _asset_bull else "short"
                    _sc_c   = min(100, max(0, _bsc + micro_adj if _asset_bull else _ssc - micro_adj))
                    # squeeze_pct : 1.0 si déjà en squeeze, baisse linéairement au-delà
                    _sq_pct = 1.0 if _bbw <= _bbq else max(0.0, 1.0 - (_bbw - _bbq) / max(1e-9, _bbq))
                    _vr_pct = min(1.0, _vr / VOL_RATIO_C)
                    _sc_pct = min(1.0, _sc_c / active_score_c)
                    _rc = int(100 * (_sq_pct * _vr_pct * _sc_pct) ** (1/3))
                    _ck = f"{_sym}_C"
                    _last = _ready_alerts.get(_ck, "")
                    _can_alert = (not _last or
                                  current_bar_ts - pd.Timestamp(_last) >= _READY_COOL)
                    if _rc >= _READY_THR and _can_alert:
                        _detail = (f"BBW {_bbw:.4f} vs seuil {_bbq:.4f} | "
                                   f"Vol {_vr:.2f}×/{VOL_RATIO_C}× | Score {_sc_c}")
                        _tg.notify_readiness(_sym, "C", _rc, _detail, _side_c)
                        _ready_alerts[_ck] = _cur_str
                        log.info(f"READINESS-C {_sym} {_side_c} {_rc}% — {_detail}")

                # ── Readiness Pattern D (RSI zone approach) ───────────────────
                _d_univ = (PATTERN_D_WHITELIST | PATTERN_D_BULL_EXTRA
                           if not bear_macro else set(sym_data.keys()))
                if _sym in _d_univ and _asset_bull and not btc_near_ath:
                    # RSI proximité fenêtre [62, 65]
                    if _rsi < 62:
                        _rsi_pct = max(0.0, _rsi / 62.0)
                    elif _rsi <= 65:
                        _rsi_pct = 1.0
                    else:
                        _rsi_pct = max(0.0, 1.0 - (_rsi - 65) / 10.0)
                    # ADX montant
                    _adx_prev = float(_sd["adx"][_b - 1]) if _b > 0 else _adx
                    _adx_pct  = 1.0 if _adx > _adx_prev else 0.6
                    _sc_d     = min(100, max(0, _bsc + micro_adj))
                    _sc_d_pct = min(1.0, _sc_d / SCORE_MIN_D)
                    _rd = int(100 * (_rsi_pct * _adx_pct * _sc_d_pct) ** (1/3))
                    _ck = f"{_sym}_D"
                    _last = _ready_alerts.get(_ck, "")
                    _can_alert = (not _last or
                                  current_bar_ts - pd.Timestamp(_last) >= _READY_COOL)
                    if _rd >= _READY_THR and _can_alert:
                        _detail = (f"RSI {_rsi:.1f} (cible 62-65) | "
                                   f"ADX {_adx:.1f}{'↑' if _adx > _adx_prev else '↔'} | "
                                   f"Score {_sc_d}")
                        _tg.notify_readiness(_sym, "D", _rd, _detail, "long")
                        _ready_alerts[_ck] = _cur_str
                        log.info(f"READINESS-D {_sym} long {_rd}% — {_detail}")

            except Exception as _ex:
                log.debug(f"Readiness {_sym}: {_ex}")

        state["readiness_alerts"] = _ready_alerts

    return trades_closed, signals_new

# ── Dashboard ────────────────────────────────────────────────────────────────
def print_dashboard(state: dict, trades_closed: list, signals_new: list,
                    bar_ts: pd.Timestamp):
    equity   = state["equity"]
    peak     = state["peak_equity"]
    ret_pct  = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    dd_pct   = (peak - equity) / peak * 100 if peak > 0 else 0
    n_open   = len(state["open_positions"])
    n_pend   = len(state["pending_entries"])
    total_t  = state["total_trades"]
    wr_pct   = state["total_wins"] / total_t * 100 if total_t > 0 else 0

    ret_color = "green" if ret_pct >= 0 else "red"
    console.print(Panel(
        f"[bold]Barre traitée :[/bold] {bar_ts}\n"
        f"[bold]Équité        :[/bold] [bold {ret_color}]€{equity:,.2f}[/bold {ret_color}]  "
        f"[{ret_color}]{ret_pct:+.1f}%[/{ret_color}]  "
        f"MaxDD [yellow]{dd_pct:.1f}%[/yellow]\n"
        f"[bold]Positions     :[/bold] {n_open} ouvertes · {n_pend} en attente\n"
        f"[bold]Historique    :[/bold] {total_t} trades · WR {wr_pct:.0f}% · PnL total €{state['total_pnl']:+,.2f}",
        title="[bold cyan]PRISM v33 — Live Paper Monitor[/bold cyan]",
        border_style="cyan",
    ))

    if trades_closed:
        t = Table(box=box.SIMPLE_HEAD, title="Trades fermés cette barre")
        t.add_column("Symbole");  t.add_column("Side")
        t.add_column("Raison");   t.add_column("PnL", justify="right")
        t.add_column("Equity après", justify="right")
        for tr in trades_closed:
            col = "green" if tr["pnl"] > 0 else "red"
            t.add_row(tr["sym"], tr["side"], tr["reason"],
                      f"[{col}]€{tr['pnl']:+.2f}[/{col}]",
                      f"€{tr['equity_after']:,.2f}")
        console.print(t)

    if signals_new:
        s = Table(box=box.SIMPLE_HEAD, title="Nouveaux signaux (exécution prochaine barre)")
        s.add_column("Symbole"); s.add_column("Pat."); s.add_column("Direction")
        s.add_column("Score", justify="right"); s.add_column("ADX", justify="right")
        s.add_column("Levier", justify="right")
        for sig in signals_new:
            col = "green" if sig["action"] == "BUY" else "red"
            s.add_row(sig["sym"],
                      sig.get("pattern", "C"),
                      f"[{col}]{sig['action']}[/{col}]",
                      str(sig["score"]), f"{sig['adx']:.1f}", f"×{sig['leverage']}")
        console.print(s)

    if state["open_positions"]:
        p = Table(box=box.SIMPLE_HEAD, title="Positions ouvertes")
        p.add_column("Symbole"); p.add_column("Side"); p.add_column("Entrée")
        p.add_column("SL", justify="right"); p.add_column("TP", justify="right")
        p.add_column("Marge", justify="right"); p.add_column("Levier", justify="right")
        for pos in state["open_positions"].values():
            col = "green" if pos["side"] == "long" else "red"
            p.add_row(pos["sym"],
                      f"[{col}]{pos['side']}[/{col}]",
                      f"{pos['entry_price']:.5g}",
                      f"{pos['sl']:.5g}", f"{pos['tp']:.5g}",
                      f"€{pos['margin']:.0f}", f"×{pos['leverage']}")
        console.print(p)

# ── Boucle principale ────────────────────────────────────────────────────────
def run_once():
    """Télécharge les données, traite la dernière barre complète, sauvegarde."""
    def _download() -> dict:
        out = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(fetch_live, s): s for s in SYMBOLS}
            for fut in as_completed(futs):
                sym = futs[fut]
                df  = fut.result()
                if df is not None and len(df) >= 50:
                    out[sym] = prepare(sym, df)
        return out

    console.print("[dim]Téléchargement données OKX...[/dim]", end="")
    sym_data = _download()
    # Résilience réseau (10/07) : un blip transitoire ne doit pas coûter une
    # heure de scan — retry 2× à 120s avant d'abandonner le tick.
    for _retry in range(2):
        if sym_data:
            break
        log.warning(f"0 symbole chargé — retry {_retry + 1}/2 dans 120s (blip réseau ?)")
        time.sleep(120)
        sym_data = _download()

    n_loaded = len(sym_data)
    console.print(f"\r[green]{n_loaded} symboles chargés[/green]       ")
    log.info(f"Données : {n_loaded}/{len(SYMBOLS)} symboles")

    if "BTC-USDT" not in sym_data:
        # Réseau partiellement down : on ne cherche pas de nouveaux signaux,
        # mais on vérifie quand même les positions ouvertes (time_stop)
        if n_loaded == 0:
            console.print("[red]Aucune donnée disponible — scan ignoré.[/red]")
            log.error("Scan ignoré : 0 symboles chargés")
            return
        console.print("[yellow]BTC-USDT absent — scan partiel, vérification positions uniquement.[/yellow]")
        log.warning(f"Scan partiel : {n_loaded}/{len(SYMBOLS)} symboles, pas de nouveaux signaux")
        # Utilise le timestamp de n'importe quel symbole disponible comme référence
        ref_sym = next(iter(sym_data))
        current_bar_ts = sym_data[ref_sym]["ts_index"][-1]
        state = load_state()
        trades_closed, signals_new = engine_step(state, sym_data, current_bar_ts)
        save_state(state)
        return

    # Dernière barre complète = avant-dernière entrée de BTC (la dernière est en cours)
    btc_ts = sym_data["BTC-USDT"]["ts_index"]
    # La dernière barre retournée par history-candles est la plus récente complète
    current_bar_ts = btc_ts[-1]

    state = load_state()
    trades_closed, signals_new = engine_step(state, sym_data, current_bar_ts)
    save_state(state)
    save_scan_record(current_bar_ts, n_loaded, trades_closed, signals_new, state)
    print_dashboard(state, trades_closed, signals_new, current_bar_ts)

    # ── Notification Telegram horaire + changement de régime ─────────────────
    if _tg:
        try:
            _eq    = state["equity"]
            _open  = len(state.get("open_positions", {}))
            _pend  = len(state.get("pending_entries", {}))
            # Calcul régime
            _btc_sd = sym_data.get("BTC-USDT")
            _regime = "BULL"
            _bear_s = False
            _btc_cl = 0.0
            _btc_adx = 0.0
            if _btc_sd is not None:
                _bb = len(_btc_sd["ts_index"]) - 1
                _e20 = float(_btc_sd["ema20_4h"][_bb])
                _e50 = float(_btc_sd["ema50_4h"][_bb])
                _btc_adx = float(_btc_sd["adx"][_bb])
                _btc_cl  = float(_btc_sd["close"][_bb])
                _bbw_btc = float(_btc_sd["bbw"][_bb])
                _bbq_btc = float(_btc_sd["bbw_q15"][_bb])
                _atr_btc = float(_btc_sd.get("atr", [float("nan")])[_bb]) if "atr" in _btc_sd else float("nan")
                if not (math.isnan(_e20) or math.isnan(_e50) or math.isnan(_btc_adx)):
                    if _e20 < _e50 and _btc_adx > 20:
                        _regime = "BEAR"
                        _bear_s = True
                    elif _btc_adx < ADX_RANGE_MAX and not math.isnan(_bbw_btc) and _bbw_btc <= _bbq_btc:
                        _regime = "RANGE"
                    elif not math.isnan(_atr_btc) and _atr_btc > 2 * _btc_cl * 0.02:
                        _regime = "CHAOS"

            # Détection changement de régime
            _last_regime = state.get("last_regime", "")
            if _last_regime and _last_regime != _regime:
                try:
                    _tg.notify_regime_change(_last_regime, _regime, _btc_cl, _btc_adx)
                    log.info(f"RÉGIME CHANGÉ : {_last_regime} → {_regime}")
                except Exception as _re:
                    log.warning(f"notify_regime_change failed: {_re}")
            state["last_regime"] = _regime

            # Notif horaire
            _nxt = current_bar_ts + pd.Timedelta(hours=1)
            _nxt_str = _nxt.strftime("%H:%M")
            _tg.notify_scan(
                equity        = _eq,
                regime        = _regime,
                n_open        = _open,
                n_pending     = _pend,
                n_signals     = len(signals_new),
                n_closed      = len(trades_closed),
                next_scan_str = _nxt_str,
                bear_s_active = _bear_s,
            )
        except Exception as _e:
            log.warning(f"notify_scan failed: {_e}")

    console.print(f"\n[dim]Logs : {LOG_DIR}[/dim]")
    console.print(f"[dim]State: {STATE_FILE}[/dim]")

def run_backfill():
    """Rejoue toutes les barres manquées depuis le dernier scan enregistré."""
    # Trouver la dernière barre traitée
    last_bar_ts = None
    if SCAN_HISTORY_FILE.exists():
        try:
            with open(SCAN_HISTORY_FILE) as f:
                history = json.load(f)
            if history:
                last_bar_ts = pd.Timestamp(history[0]["bar_ts"])
        except Exception:
            pass

    if last_bar_ts is None:
        console.print("[yellow]Aucun historique — exécution d'un scan normal.[/yellow]")
        run_once()
        return

    console.print(f"[cyan]Dernière barre traitée : {last_bar_ts}[/cyan]")
    console.print("[dim]Téléchargement données OKX...[/dim]", end="")

    sym_data = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch_live, s): s for s in SYMBOLS}
        for fut in as_completed(futs):
            sym = futs[fut]
            df  = fut.result()
            if df is not None and len(df) >= 50:
                sym_data[sym] = prepare(sym, df)

    n_loaded = len(sym_data)
    console.print(f"\r[green]{n_loaded} symboles chargés[/green]       ")

    if "BTC-USDT" not in sym_data:
        if n_loaded == 0:
            console.print("[red]Aucune donnée disponible — scan ignoré.[/red]")
            return
        console.print("[yellow]BTC-USDT absent — scan partiel, positions uniquement.[/yellow]")
        log.warning(f"Scan partiel : {n_loaded}/{len(SYMBOLS)} symboles")
        ref_sym = next(iter(sym_data))
        current_bar_ts = sym_data[ref_sym]["ts_index"][-1]
        state = load_state()
        engine_step(state, sym_data, current_bar_ts)
        save_state(state)
        return

    # Toutes les barres disponibles après la dernière traitée (sauf la dernière = en cours)
    btc_ts    = sym_data["BTC-USDT"]["ts_index"]
    missed    = [ts for ts in btc_ts if ts > last_bar_ts]
    if not missed:
        console.print("[yellow]Aucune barre manquée.[/yellow]")
        return
    # Exclure la dernière barre (potentiellement en cours de formation)
    missed = missed[:-1]
    if not missed:
        console.print("[yellow]Aucune barre complète manquée.[/yellow]")
        return

    console.print(f"[bold]{len(missed)} barres à rattraper :[/bold] "
                  f"{missed[0]} → {missed[-1]}")

    state = load_state()
    for bar_ts in missed:
        trades_closed, signals_new = engine_step(state, sym_data, bar_ts)
        save_state(state)
        save_scan_record(bar_ts, n_loaded, trades_closed, signals_new, state)

        tc_str  = f"  {len(trades_closed)} trade(s) fermé(s)" if trades_closed else ""
        sig_str = f"  {len(signals_new)} signal(s)" if signals_new else ""
        result  = (tc_str + sig_str).strip() or "aucun signal"
        console.print(f"  [dim]{bar_ts}[/dim]  →  {result}")

    console.print(f"\n[green]Backfill terminé — {len(missed)} barres traitées.[/green]")
    print_dashboard(state, [], [], missed[-1])


def _seconds_to_next_hour(offset_min: int = 3) -> float:
    """Secondes jusqu'à HH:(offset_min)."""
    now  = datetime.now()
    nxt  = now.replace(minute=offset_min, second=0, microsecond=0)
    if now.minute >= offset_min:
        nxt += timedelta(hours=1)
    return (nxt - now).total_seconds()

def run_loop():
    """Boucle infinie : exécute à HH:03 chaque heure."""
    console.print("[bold cyan]PRISM v33 Live Monitor démarré — Ctrl+C pour quitter[/bold cyan]")
    log.info("Live monitor démarré")
    _init_trader()
    _boot_state = load_state()
    save_state(_boot_state)          # marque last_run_ts dès le démarrage
    reconcile_positions(_boot_state)  # parité état local / exchange au boot
    if _tg:
        _tg.notify_bot_start(_boot_state["equity"])
    while True:
        wait = _seconds_to_next_hour(offset_min=3)
        nxt  = datetime.now() + timedelta(seconds=wait)
        console.print(f"[dim]Prochaine exécution : {nxt.strftime('%H:%M:%S')} "
                      f"(dans {wait/60:.1f} min)[/dim]")
        try:
            time.sleep(max(0, wait))
        except KeyboardInterrupt:
            console.print("\n[yellow]Arrêt demandé.[/yellow]")
            log.info("Live monitor arrêté manuellement")
            break
        except Exception as e:
            log.warning(f"Sleep interrompu : {e} — continuation immédiate")
        try:
            run_once()
            reconcile_positions(load_state())  # parité après chaque tick
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.exception(f"Erreur run_once: {e}")
            console.print(f"[red]Erreur : {e}[/red]")
            if _tg:
                _tg.notify_error(str(e))

def show_status():
    state = load_state()
    equity  = state["equity"]
    ret_pct = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    total_t = state["total_trades"]
    wr_pct  = state["total_wins"] / total_t * 100 if total_t > 0 else 0
    console.print(Panel(
        f"Equity      : €{equity:,.2f}  ({ret_pct:+.1f}%)\n"
        f"PnL total   : €{state['total_pnl']:+,.2f}\n"
        f"Trades      : {total_t}  |  WR {wr_pct:.0f}%\n"
        f"Positions   : {len(state['open_positions'])} open · {len(state['pending_entries'])} pending\n"
        f"Démarré le  : {state.get('started_at','?')}\n"
        f"Dernier run : {state.get('last_run_ts','jamais')}",
        title="[bold cyan]PRISM v33 — État courant[/bold cyan]",
    ))

# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once",     action="store_true", help="Exécute une seule fois")
    parser.add_argument("--status",   action="store_true", help="Affiche l'état et quitte")
    parser.add_argument("--reset",    action="store_true", help="Remet le state à zéro")
    parser.add_argument("--backfill", action="store_true", help="Rattrape les barres manquées")
    args = parser.parse_args()

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        console.print("[yellow]State réinitialisé.[/yellow]")
        sys.exit(0)

    if args.status:
        show_status()
        sys.exit(0)

    if args.backfill:
        run_backfill()
    elif args.once:
        run_once()
    else:
        run_loop()
