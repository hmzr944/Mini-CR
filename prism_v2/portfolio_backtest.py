#!/usr/bin/env python3
"""BACKTEST du portefeuille a position continue, sur donnees reelles.

AUCUN LOOK-AHEAD. A l'heure t on ne connait que les prix et les paiements de
funding horodates <= t. La position decidee est portee de t a t+1, et c'est
le rendement de t+1 qui l'affecte.

Le funding Hyperliquid est horaire et les prix sont horaires : les deux
grilles coincident, ce qui evite toute interpolation.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.portfolio import (
    BookState, RiskConfig, SignalConfig, TargetConfig, TradingConfig,
    apply_step, expected_returns, move_toward_target, summarise_book,
    target_weights, volatilities,
)

HOUR_MS = 3_600_000


@dataclass
class Panel:
    """Grille horaire alignee : prix, rendements, funding, par actif."""
    hours: List[int]
    px: Dict[str, Dict[int, float]]
    fund: Dict[str, Dict[int, float]]

    @property
    def assets(self) -> List[str]:
        return sorted(self.px)

    def ret(self, asset: str, t: int) -> Optional[float]:
        a = self.px[asset].get(t - HOUR_MS)
        b = self.px[asset].get(t)
        if a is None or b is None or a <= 0:
            return None
        return b / a - 1.0


def build_panel(px_path: Path, fund_path: Path) -> Panel:
    px_raw = json.loads(px_path.read_text())
    fu_raw = json.loads(fund_path.read_text())
    px: Dict[str, Dict[int, float]] = {}
    fund: Dict[str, Dict[int, float]] = {}
    for sym, rows in px_raw.items():
        d = fu_raw.get(sym)
        if not d or "hl" not in d:
            continue
        px[sym] = {int(t): float(p) for t, p in rows}
        # Les horodatages de funding Hyperliquid portent une gigue de
        # quelques dizaines de millisecondes apres l'heure pile (30, 110,
        # 55 ms observes). Une recherche par cle exacte n'appariait que 9
        # paiements sur 1105 : le livre ne voyait aucun funding et ne tradait
        # jamais. On rabat donc chaque paiement sur SON heure — vers le bas,
        # car un paiement a H+55 ms appartient a l'heure H, pas a H+1.
        fund[sym] = {}
        for t, r in d["hl"]:
            hour = int(t) - int(t) % HOUR_MS
            fund[sym][hour] = float(r)
    if not px:
        raise ValueError("aucun actif commun prix/funding")
    lo = max(min(v) for v in px.values())
    hi = min(max(v) for v in px.values())
    hours = list(range(lo, hi + 1, HOUR_MS))
    return Panel(hours, px, fund)


def run_backtest(panel: Panel, t0: int, t1: int,
                 sig: SignalConfig, risk: RiskConfig,
                 tgt: TargetConfig, trade: TradingConfig,
                 capital_usd: float = 10_000.0) -> Tuple[BookState, dict]:
    book = BookState()
    warm = max(sig.lookback_h, risk.lookback_h) + 1
    hours = [h for h in panel.hours if t0 <= h <= t1]
    if len(hours) <= warm:
        raise ValueError("fenetre trop courte pour l'echauffement")

    for i, t in enumerate(hours):
        if i < warm:
            continue
        past = hours[max(0, i - risk.lookback_h): i]        # STRICTEMENT < t

        fwin: Dict[str, List[float]] = {}
        rwin: Dict[str, List[float]] = {}
        for a in panel.assets:
            fs = [panel.fund[a][h] for h in past[-sig.lookback_h:]
                  if h in panel.fund[a]]
            if len(fs) >= sig.lookback_h:
                fwin[a] = fs
            rs = [r for r in (panel.ret(a, h) for h in past) if r is not None]
            if rs:
                rwin[a] = rs

        mu = expected_returns(fwin, sig)
        vol = volatilities(rwin, risk)
        want = target_weights(mu, vol, tgt)
        new = move_toward_target(book.weights, want, trade)

        # Encaissement sur la periode a venir : t -> t+1.
        nxt = t + HOUR_MS
        rets = {a: r for a in panel.assets
                if (r := panel.ret(a, nxt)) is not None}
        funds = {a: panel.fund[a][nxt] for a in panel.assets
                 if nxt in panel.fund[a]}
        apply_step(book, new, rets, funds, trade)

    return book, summarise_book(book, len(hours) - warm, capital_usd)
