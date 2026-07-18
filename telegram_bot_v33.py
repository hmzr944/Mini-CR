#!/usr/bin/env python3
"""
PRISM v33.9 — Bot Telegram de suivi
====================================
Commandes :
  /status    — capital, positions, régime, prochain scan
  /trades    — 10 derniers trades
  /positions — positions et ordres en attente
  /journal   — stats du mois (WR, PnL, PF, par pattern)
  /regime    — régime BTC (bull/bear, ADX, EMA)
  /perf      — performance globale vs départ
  /config    — paramètres v33.9 actifs
  /monthly   — breakdown mois par mois (capital composé)
  /help      — aide

Lancement : python telegram_bot_v33.py
Docker    : inclus dans docker-compose.v33.yml (service prism-telegram)
"""

import csv
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ── Config ─────────────────────────────────────────────────────────────────────

DATA_DIR        = Path(os.environ.get("PRISM_DATA_DIR", str(Path(__file__).parent)))
CONFIG_FILE     = DATA_DIR / "telegram_config.json"
STATE_FILE      = DATA_DIR / "live_state_v33.json"
INITIAL_CAPITAL = 1000.0
POLL_TIMEOUT    = 30   # secondes pour long-polling Telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("prism_tg")


# ── Helpers config ─────────────────────────────────────────────────────────────

def _cfg() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}

def _token()   -> str: return _cfg().get("token", "").strip()
def _chat_id() -> str: return str(_cfg().get("chat_id", "")).strip()


# ── API Telegram ───────────────────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{_token()}/{method}",
            json=kwargs, timeout=POLL_TIMEOUT + 5,
        )
        return r.json()
    except Exception as e:
        log.error(f"Telegram API [{method}]: {e}")
        return {}


def send(text: str, chat_id: str = None):
    _api(
        "sendMessage",
        chat_id=chat_id or _chat_id(),
        text=text,
        parse_mode="HTML",
        link_preview_options={"is_disabled": True},
    )


# ── Lecture état live ──────────────────────────────────────────────────────────

def _state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text())
        for key in ("open_positions", "pending_entries", "cooldown_tracker"):
            val = d.get(key)
            if isinstance(val, str):
                try:
                    d[key] = eval(val)   # stocké comme repr() Python
                except Exception:
                    d[key] = {}
        return d
    except Exception:
        return {}


def _trades(months: int = 2) -> list[dict]:
    """Charge les trades des N derniers mois depuis les CSV live_logs."""
    rows = []
    now = datetime.now()
    for delta in range(months):
        mo = now.month - delta
        yr = now.year
        if mo <= 0:
            mo += 12
            yr -= 1
        path = DATA_DIR / "live_logs" / f"live_trades_{yr}{mo:02d}.csv"
        if path.exists():
            with open(path, newline="") as f:
                rows.extend(list(csv.DictReader(f)))
    return sorted(rows, key=lambda x: x.get("exit_ts", ""), reverse=True)


# ── Données OKX ───────────────────────────────────────────────────────────────

def _btc_regime() -> dict:
    """Récupère prix BTC, ADX 4H, EMA50 4H et calcule bear_mode."""
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": "BTC-USDT", "bar": "4H", "limit": 200},
            timeout=12,
        )
        data = r.json().get("data", [])
        if len(data) < 60:
            return {}

        df = pd.DataFrame(data, columns=["ts","o","h","l","c","v","a","b","c2"])
        for col in ("h","l","c"):
            df[col] = pd.to_numeric(df[col])
        df = df.sort_values("ts").reset_index(drop=True)

        price  = float(df["c"].iloc[-1])
        ema50  = float(df["c"].ewm(span=50, adjust=False).mean().iloc[-1])
        peak30 = float(df["c"].tail(180).max())   # ~30 jours en 4H

        # ADX 14 simplifié
        n  = 14
        tr = pd.concat([
            df["h"] - df["l"],
            (df["h"] - df["c"].shift()).abs(),
            (df["l"] - df["c"].shift()).abs(),
        ], axis=1).max(axis=1)
        p_dm = np.where((df["h"] - df["h"].shift()) > (df["l"].shift() - df["l"]),
                        np.maximum(df["h"] - df["h"].shift(), 0), 0)
        m_dm = np.where((df["l"].shift() - df["l"]) > (df["h"] - df["h"].shift()),
                        np.maximum(df["l"].shift() - df["l"], 0), 0)
        atr    = pd.Series(tr).ewm(span=n, adjust=False).mean()
        pdi    = 100 * pd.Series(p_dm).ewm(span=n, adjust=False).mean() / (atr + 1e-9)
        mdi    = 100 * pd.Series(m_dm).ewm(span=n, adjust=False).mean() / (atr + 1e-9)
        dx     = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-9)
        adx    = float(dx.ewm(span=n, adjust=False).mean().iloc[-1])

        bear   = (price < ema50) and (price < peak30 * 0.88)
        pct    = (price - peak30) / peak30 * 100

        return {
            "price"   : price,
            "adx"     : adx,
            "ema50"   : ema50,
            "peak30"  : peak30,
            "bear"    : bear,
            "pct_peak": pct,
        }
    except Exception as e:
        log.error(f"BTC regime: {e}")
        return {}


# ── Commandes ──────────────────────────────────────────────────────────────────

def cmd_status(chat_id: str):
    s   = _state()
    btc = _btc_regime()

    equity   = s.get("equity", INITIAL_CAPITAL)
    ret      = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    peak     = s.get("peak_equity", equity)
    dd       = (peak - equity) / peak * 100 if peak > 0 else 0
    n_open   = len(s.get("open_positions", {}))
    n_pend   = len(s.get("pending_entries", {}))
    n_trades = s.get("total_trades", 0)
    n_wins   = s.get("total_wins", 0)
    wr       = n_wins / n_trades * 100 if n_trades else 0
    total_pnl= s.get("total_pnl", 0)

    # Prochain scan (toutes les heures à HH:03)
    next_scan = ""
    last_run  = s.get("last_run_ts", "")
    if last_run:
        try:
            lr = datetime.fromisoformat(last_run)
            nxt = lr.replace(minute=3, second=0, microsecond=0)
            if nxt <= lr:
                nxt += timedelta(hours=1)
            mins_left = max(0, int((nxt - datetime.now()).total_seconds() / 60))
            next_scan = f"{nxt.strftime('%H:%M')} (dans {mins_left} min)"
        except Exception:
            pass

    # Régime
    regime_line = ""
    if btc:
        mode = "🐻 BEAR" if btc["bear"] else "🐂 BULL"
        regime_line = f"\nRégime : <b>{mode}</b>  ·  BTC ${btc['price']:,.0f}  ·  ADX {btc['adx']:.1f}"

    sign_ret = "+" if ret >= 0 else ""
    sign_pnl = "+" if total_pnl >= 0 else ""

    text = (
        f"📊 <b>PRISM v33.9 — Statut</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Capital : <b>€{equity:,.2f}</b>  ({sign_ret}{ret:.1f}%)\n"
        f"📉 MaxDD : {dd:.1f}%  ·  Pic €{peak:,.2f}\n"
        f"📌 Positions : {n_open} ouvertes · {n_pend} en attente\n"
        f"📋 Trades : {n_trades}  ·  WR {wr:.0f}%  ·  {sign_pnl}€{abs(total_pnl):.2f}\n"
    )
    if next_scan:
        text += f"⏱ Prochain scan : {next_scan}\n"
    if regime_line:
        text += regime_line

    send(text, chat_id)


def cmd_trades(chat_id: str):
    trades = _trades()[:10]
    if not trades:
        send("📋 Aucun trade enregistré pour l'instant.", chat_id)
        return

    REASON = {"take_profit": "TP ✅", "stop_loss": "SL ❌", "time_stop": "TS ⏱", "param_update": "UPD 🔄"}
    lines  = ["📋 <b>Derniers trades</b>\n━━━━━━━━━━━━━━━━━━━━"]

    for t in trades:
        sym   = t.get("sym", "?").replace("-USDT", "")
        side  = "↑" if t.get("side") == "long" else "↓"
        pnl   = float(t.get("pnl", 0))
        pat   = t.get("pattern", "?")
        score = t.get("score", "?")
        date  = str(t.get("exit_ts", ""))[:10]
        reason= REASON.get(t.get("reason", ""), t.get("reason", "?"))
        sign  = "+" if pnl >= 0 else ""
        lines.append(
            f"{date}  <b>{sym}</b> {side}  [{pat}] sc={score}  "
            f"<b>{sign}€{pnl:.2f}</b>  {reason}"
        )

    send("\n".join(lines), chat_id)


def cmd_positions(chat_id: str):
    s       = _state()
    open_p  = s.get("open_positions", {})
    pending = s.get("pending_entries", {})

    if not open_p and not pending:
        send(
            "📌 <b>Aucune position en cours</b>\n"
            "Le bot attend le prochain signal.",
            chat_id,
        )
        return

    lines = ["📌 <b>Positions en cours</b>\n━━━━━━━━━━━━━━━━━━━━"]

    for pos in open_p.values():
        sym   = pos.get("sym", "?").replace("-USDT", "")
        side  = "↑ LONG" if pos.get("side") == "long" else "↓ SHORT"
        entry = pos.get("entry_price", 0)
        sl    = pos.get("sl", 0)
        tp    = pos.get("tp", 0)
        margin= pos.get("margin", 0)
        lev   = pos.get("leverage", 1)
        pat   = pos.get("pattern", "?")
        lines.append(
            f"✅ <b>{sym}</b> {side}  [{pat}]\n"
            f"   Entrée {entry:.5g}  ·  SL {sl:.5g}  ·  TP {tp:.5g}\n"
            f"   Mise €{margin:.0f} × {lev}"
        )

    for p in pending.values():
        sym  = p.get("sym", "?").replace("-USDT", "")
        side = "↑ LONG" if p.get("side") == "long" else "↓ SHORT"
        sc   = p.get("score", "?")
        pat  = p.get("pattern", "?")
        lines.append(f"⏳ <b>{sym}</b> {side}  [{pat}] sc={sc}  — entrée à la prochaine bougie")

    send("\n".join(lines), chat_id)


def cmd_journal(chat_id: str):
    trades = _trades(months=1)
    if not trades:
        send("📅 Aucun trade ce mois-ci.", chat_id)
        return

    pnls      = [float(t.get("pnl", 0)) for t in trades]
    wins      = [p for p in pnls if p > 0]
    losses    = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_los = abs(sum(losses)) if losses else 0
    pf        = gross_win / gross_los if gross_los > 0 else 99.0
    wr        = len(wins) / len(pnls) * 100 if pnls else 0
    net       = sum(pnls)

    best  = max(trades, key=lambda t: float(t.get("pnl", 0)))
    worst = min(trades, key=lambda t: float(t.get("pnl", 0)))

    # Breakdown par pattern (utilise le champ pattern réel)
    by_pattern: dict[str, list] = {}
    for t in trades:
        pat = t.get("pattern", "?") or "?"
        by_pattern.setdefault(pat, []).append(float(t.get("pnl", 0)))

    month_name = datetime.now().strftime("%B %Y")
    sign = "+" if net >= 0 else ""

    pat_lines = ""
    for pat in sorted(by_pattern.keys()):
        ps  = by_pattern[pat]
        pw  = sum(1 for p in ps if p > 0)
        pp  = sum(ps)
        spn = "+" if pp >= 0 else ""
        pat_lines += f"\n  [{pat}] N={len(ps)} WR={pw/len(ps)*100:.0f}% PnL={spn}€{abs(pp):.1f}"

    text = (
        f"📅 <b>Journal — {month_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Trades : {len(trades)}  ·  WR {wr:.0f}%  ·  PF {pf:.2f}\n"
        f"PnL net : <b>{sign}€{net:.2f}</b>\n"
        f"Gains : €{gross_win:.2f}  ·  Pertes : €{gross_los:.2f}\n"
        f"🏆 Meilleur : {best.get('sym','?').replace('-USDT','')}  "
        f"+€{float(best.get('pnl',0)):.2f}\n"
        f"💀 Pire : {worst.get('sym','?').replace('-USDT','')}  "
        f"-€{abs(float(worst.get('pnl',0))):.2f}"
    )
    if pat_lines:
        text += f"\n━━━━━━━━━━━━━━━━━━━━\n<b>Par pattern :</b>{pat_lines}"
    send(text, chat_id)


def cmd_regime(chat_id: str):
    btc = _btc_regime()
    if not btc:
        send("❌ Impossible de récupérer les données BTC (OKX hors ligne?).", chat_id)
        return

    bear = btc["bear"]
    mode = "🐻 BEAR MODE" if bear else "🐂 BULL MODE"
    adx  = btc["adx"]

    if bear:
        if adx >= 28:
            signal_lines = "✅ D shorts autorisés (BTC ADX ≥ 28 → bear directionnel)\n❌ C longs bloqués"
        else:
            signal_lines = "❌ D shorts bloqués (BTC ADX < 28 → bear choppy)\n❌ C longs bloqués"
    else:
        signal_lines = "✅ C longs autorisés (squeeze + vol ≥ 1.9×)\n✅ D longs possibles (whitelist)"

    text = (
        f"🌡 <b>Régime marché</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Mode : <b>{mode}</b>\n"
        f"BTC prix   : <b>${btc['price']:,.0f}</b>\n"
        f"BTC ADX 4H : {adx:.1f}\n"
        f"EMA50 4H   : ${btc['ema50']:,.0f}\n"
        f"Pic 30j    : ${btc['peak30']:,.0f}  ({btc['pct_peak']:+.1f}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{signal_lines}"
    )
    send(text, chat_id)


def cmd_perf(chat_id: str):
    s = _state()
    trades = _trades(months=6)

    equity   = s.get("equity", INITIAL_CAPITAL)
    peak     = s.get("peak_equity", equity)
    dd       = (peak - equity) / peak * 100 if peak > 0 else 0
    ret      = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    started  = s.get("started_at", "")
    days     = 0
    if started:
        try:
            days = (datetime.now() - datetime.fromisoformat(started)).days
        except Exception:
            pass

    pnls      = [float(t.get("pnl", 0)) for t in trades]
    gross_win = sum(p for p in pnls if p > 0)
    gross_los = abs(sum(p for p in pnls if p <= 0)) or 1
    pf        = gross_win / gross_los
    wr        = sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else 0

    sign = "+" if ret >= 0 else ""
    text = (
        f"📈 <b>Performance PRISM v33.9</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Capital départ : €{INITIAL_CAPITAL:,.0f}\n"
        f"Capital actuel : <b>€{equity:,.2f}</b>  ({sign}{ret:.1f}%)\n"
        f"Pic capital    : €{peak:,.2f}\n"
        f"MaxDD          : {dd:.1f}%\n"
        f"PF global      : {pf:.2f}  ·  WR {wr:.0f}%\n"
        f"Durée          : {days} jours\n"
        f"Trades total   : {s.get('total_trades', len(trades))}\n"
        f"\n"
        f"<b>Backtest v33.9 (OOS 3M)</b>\n"
        f"Sim 3M : +290.9%  ·  1000€ → 3909€\n"
        f"Avr +41% · Mai +52% · Jun +82%\n"
        f"WR 61%  ·  PF 4.1  ·  31 trades"
    )
    send(text, chat_id)


def cmd_config(chat_id: str):
    """Affiche les paramètres v33.9 actifs."""
    text = (
        f"⚙️ <b>Config PRISM v33.9 (active)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Capital départ  : €{INITIAL_CAPITAL:,.0f}\n"
        f"RR_RATIO        : 4.0  (TP = 4× SL)\n"
        f"VOL_RATIO_C     : 1.9×  (filtre volume Pattern C)\n"
        f"RISK_PCT_D      : 16%  (sizing Pattern D)\n"
        f"S_MARGIN_CAP    : €200  (budget Pattern S)\n"
        f"Pattern V       : ❌ désactivé (SCORE_MIN=999)\n"
        f"ATR_SL_MULT     : 1.5\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Mode            : 🧪 Paper Trade (OKX Demo)\n"
        f"Scan            : toutes les heures (HH:03)\n"
        f"Symboles        : 26 perps USDT"
    )
    send(text, chat_id)


def cmd_monthly(chat_id: str):
    """Breakdown mensuel depuis les CSV de trades live."""
    trades_all = _trades(months=6)
    if not trades_all:
        send("📆 Pas encore de trades enregistrés.", chat_id)
        return

    # Grouper par mois
    by_month: dict[str, list] = {}
    for t in trades_all:
        exit_ts = t.get("exit_ts", "")
        if not exit_ts:
            continue
        try:
            mo = str(exit_ts)[:7]   # "2026-07"
        except Exception:
            continue
        by_month.setdefault(mo, []).append(t)

    if not by_month:
        send("📆 Pas encore de trades avec date de sortie.", chat_id)
        return

    s           = _state()
    month_start = s.get("month_start_equity", INITIAL_CAPITAL)
    lines       = ["📆 <b>Breakdown mensuel</b>\n━━━━━━━━━━━━━━━━━━━━"]

    for mo in sorted(by_month.keys()):
        ts = by_month[mo]
        pnl  = sum(float(t.get("pnl", 0)) for t in ts)
        wins = sum(1 for t in ts if float(t.get("pnl", 0)) > 0)
        wr   = wins / len(ts) * 100 if ts else 0
        sign = "+" if pnl >= 0 else ""
        lines.append(
            f"<b>{mo}</b>  N={len(ts)}  WR={wr:.0f}%  "
            f"PnL=<b>{sign}€{pnl:.1f}</b>"
        )

    # Mois en cours
    cur_mo = datetime.now().strftime("%Y-%m")
    if cur_mo not in by_month:
        eq    = s.get("equity", INITIAL_CAPITAL)
        cur_p = eq - month_start
        sign  = "+" if cur_p >= 0 else ""
        lines.append(f"<b>{cur_mo}</b>  (en cours)  PnL={sign}€{cur_p:.1f}")

    send("\n".join(lines), chat_id)


def cmd_help(chat_id: str):
    text = (
        f"🤖 <b>PRISM v33.9 — Commandes</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"/status    — Capital, positions, régime, prochain scan\n"
        f"/trades    — 10 derniers trades (avec pattern)\n"
        f"/positions — Positions et ordres en attente\n"
        f"/journal   — Stats du mois (WR, PnL, PF, par pattern)\n"
        f"/regime    — Régime BTC (bull/bear, ADX, signaux autorisés)\n"
        f"/perf      — Performance globale vs backtest\n"
        f"/config    — Paramètres v33.9 actifs\n"
        f"/monthly   — Breakdown PnL mois par mois\n"
        f"/help      — Cette aide\n"
        f"\n"
        f"<i>Bot paper trading · OKX Demo · v33.9\n"
        f"Alertes automatiques : entrées + sorties</i>"
    )
    send(text, chat_id)


# ── Push notifications (surveillance automatique des trades) ───────────────────

class _TradeWatcher:
    """Surveille live_state_v33.json et envoie des alertes push."""

    def __init__(self):
        self._known_open:    set = set()
        self._known_pending: set = set()
        self._last_total:    int = 0
        self._initialized:   bool = False

    def check(self, chat_id: str):
        s           = _state()
        open_p      = s.get("open_positions", {})
        pending     = s.get("pending_entries", {})
        total_tr    = s.get("total_trades", 0)
        total_wins  = s.get("total_wins", 0)

        open_keys    = set(open_p.keys())
        pending_keys = set(pending.keys())

        # Première lecture : initialiser silencieusement
        if not self._initialized:
            self._known_open    = open_keys
            self._known_pending = pending_keys
            self._last_total    = total_tr
            self._initialized   = True
            return

        # Nouvelles entrées en attente
        new_pending = pending_keys - self._known_pending
        for key in new_pending:
            p   = pending.get(key, {})
            sym = p.get("sym", key).replace("-USDT", "")
            sid = "↑ LONG" if p.get("side") == "long" else "↓ SHORT"
            pat = p.get("pattern", "?")
            sc  = p.get("score", "?")
            send(
                f"🔔 <b>Signal détecté</b>\n"
                f"<b>{sym}</b> {sid}  [{pat}]  sc={sc}\n"
                f"Entrée confirmée à la prochaine bougie.",
                chat_id,
            )

        # Positions ouvertes (pending → open)
        new_open = open_keys - self._known_open
        for key in new_open:
            pos   = open_p.get(key, {})
            sym   = pos.get("sym", key).replace("-USDT", "")
            sid   = "↑ LONG" if pos.get("side") == "long" else "↓ SHORT"
            pat   = pos.get("pattern", "?")
            entry = pos.get("entry_price", 0)
            sl    = pos.get("sl", 0)
            tp    = pos.get("tp", 0)
            margin= pos.get("margin", 0)
            lev   = pos.get("leverage", 1)
            send(
                f"✅ <b>Position ouverte</b>\n"
                f"<b>{sym}</b> {sid}  [{pat}]\n"
                f"Entrée {entry:.5g}  ·  SL {sl:.5g}  ·  TP {tp:.5g}\n"
                f"Mise €{margin:.0f} × {lev}",
                chat_id,
            )

        # Positions fermées : détecter via total_trades incrémenté
        closed = self._known_open - open_keys
        if closed and total_tr > self._last_total:
            # Récupérer le(s) dernier(s) trade(s) fermé(s)
            recent = _trades(months=1)[:len(closed)]
            REASON = {"take_profit": "TP ✅", "stop_loss": "SL ❌", "time_stop": "TS ⏱"}
            for t in recent:
                sym  = t.get("sym", "?").replace("-USDT", "")
                pnl  = float(t.get("pnl", 0))
                pat  = t.get("pattern", "?")
                why  = REASON.get(t.get("reason", ""), t.get("reason", "?"))
                sign = "+" if pnl >= 0 else ""
                icon = "💰" if pnl >= 0 else "💸"
                send(
                    f"{icon} <b>Trade fermé</b>  {sym}  [{pat}]\n"
                    f"PnL : <b>{sign}€{pnl:.2f}</b>  {why}",
                    chat_id,
                )

        self._known_open    = open_keys
        self._known_pending = pending_keys
        self._last_total    = total_tr


# ── Routing ─────────────────────────────────────────────────────────────────────

COMMANDS = {
    "/start"    : cmd_status,
    "/status"   : cmd_status,
    "/trades"   : cmd_trades,
    "/positions": cmd_positions,
    "/journal"  : cmd_journal,
    "/regime"   : cmd_regime,
    "/perf"     : cmd_perf,
    "/config"   : cmd_config,
    "/monthly"  : cmd_monthly,
    "/help"     : cmd_help,
}


# ── Boucle long-polling ─────────────────────────────────────────────────────────

def run():
    log.info("PRISM v33.9 Telegram Bot démarré (long-polling)")
    authorized = _chat_id()
    offset     = 0
    watcher    = _TradeWatcher()
    last_watch = 0.0   # timestamp dernière vérification trade

    # Message de démarrage
    s = _state()
    eq = s.get("equity", INITIAL_CAPITAL)
    ret = (eq - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    send(
        f"🟢 <b>Bot Telegram PRISM v33.9 connecté</b>\n"
        f"Capital : €{eq:,.2f} ({ret:+.1f}%)\n"
        f"Alertes push activées (trades + signaux)\n"
        f"Tape /help pour voir les commandes.",
        authorized,
    )

    while True:
        try:
            # Surveillance périodique des trades (toutes les 60s)
            now = time.time()
            if now - last_watch >= 60:
                try:
                    watcher.check(authorized)
                except Exception as e:
                    log.error(f"TradeWatcher: {e}")
                last_watch = now

            data = _api(
                "getUpdates",
                offset=offset,
                timeout=POLL_TIMEOUT,
                allowed_updates=["message"],
            )
            if not data.get("ok"):
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                msg     = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "").strip()

                if not text or chat_id != authorized:
                    continue

                cmd = text.split()[0].split("@")[0].lower()
                log.info(f"Commande : {cmd!r} from {chat_id}")

                handler = COMMANDS.get(cmd)
                if handler:
                    try:
                        handler(chat_id)
                    except Exception as e:
                        log.error(f"Erreur handler {cmd}: {e}")
                        send(f"❌ Erreur interne : {e}", chat_id)
                # Commande inconnue : silence (pas de spam)

        except Exception as e:
            log.error(f"Polling error: {e}")
            time.sleep(10)


if __name__ == "__main__":
    run()
