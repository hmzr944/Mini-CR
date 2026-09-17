#!/usr/bin/env python3
"""TEST LONG — les quatre signaux pre-enregistres, sur 833 jours.

Protocole : prism_v2/LONG_TEST_PROTOCOL.md, gele avant les donnees.

AGREGATION DU FUNDING SUR LA BARRE. Hyperliquid paie toutes les heures ; les
barres font 4 h. Le funding d'une barre est la SOMME des paiements dont
l'horodatage tombe dans la barre — jamais une moyenne, jamais un prorata. Un
paiement compte une fois et une seule, dans la barre ou il a eu lieu.

CAUSALITE. La decision prise a la cloture de la barre b n'utilise que les
barres <= b. La position est portee pendant la barre b+1 et encaisse le
rendement et le funding de b+1. Le decalage est structurel.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.portfolio import (
    BookState, RiskConfig, TargetConfig, TradingConfig, apply_step,
    move_toward_target, summarise_book, target_weights, volatilities,
)
from prism_v2.signals import AssetWindow, SIGNALS, compute_all

BAR_MS = 4 * 3_600_000
BARS_PER_DAY = 6
BARS_PER_YEAR = BARS_PER_DAY * 365

# Parametres figes par le protocole.
COST_BPS = 6.54
VOL_BARS = 42
GAMMA = 1.0
GROSS = 1.0
MAX_WEIGHT = 0.10
BAND_MULTIPLE = 2.0


@dataclass
class LongPanel:
    bars: List[int]
    px: Dict[str, Dict[int, float]]
    fund: Dict[str, Dict[int, float]]

    @property
    def assets(self) -> List[str]:
        return sorted(self.px)

    def ret(self, asset: str, b: int) -> Optional[float]:
        prev = self.px[asset].get(b - BAR_MS)
        cur = self.px[asset].get(b)
        if prev is None or cur is None or prev <= 0:
            return None
        return cur / prev - 1.0


def build_long_panel(path: Path, min_bars: int = 600) -> LongPanel:
    raw = json.loads(path.read_text())
    px: Dict[str, Dict[int, float]] = {}
    fund: Dict[str, Dict[int, float]] = {}
    for sym, d in raw.items():
        if "error" in d or len(d.get("px") or []) < min_bars:
            continue
        px[sym] = {int(t): float(p) for t, p in d["px"]}
        buckets: Dict[int, float] = {}
        for t, r in d.get("fund") or []:
            b = int(t) - int(t) % BAR_MS     # la barre qui CONTIENT le paiement
            buckets[b] = buckets.get(b, 0.0) + float(r)
        fund[sym] = buckets
    if not px:
        raise ValueError("panneau vide")
    lo = min(min(v) for v in px.values())
    hi = max(max(v) for v in px.values())
    bars = list(range(lo, hi + 1, BAR_MS))
    return LongPanel(bars, px, fund)


def run_signal(panel: LongPanel, signal_name: str, b0: int, b1: int,
               cost_bps: float = COST_BPS) -> Tuple[BookState, dict, List[float]]:
    """Execute un signal sur une fenetre. Rend aussi les PnL par barre,
    necessaires au test statistique."""
    book = BookState()
    risk = RiskConfig(lookback_h=VOL_BARS)
    tgt = TargetConfig(gamma=GAMMA, gross_leverage=GROSS,
                       max_weight=MAX_WEIGHT, dollar_neutral=True)
    trade = TradingConfig(cost_bps=cost_bps, band_multiple=BAND_MULTIPLE)

    bars = [b for b in panel.bars if b0 <= b <= b1]
    warm = max(VOL_BARS, 42) + 1
    if len(bars) <= warm + 10:
        raise ValueError("fenetre trop courte")

    per_bar: List[float] = []
    prev_net = 0.0
    for i, b in enumerate(bars):
        if i < warm:
            continue
        past = bars[max(0, i - VOL_BARS): i + 1]      # inclut b : deja cloture

        windows: Dict[str, AssetWindow] = {}
        rwin: Dict[str, List[float]] = {}
        for a in panel.assets:
            fs = [panel.fund[a].get(x, 0.0) for x in past]
            rs = [r for r in (panel.ret(a, x) for x in past) if r is not None]
            if len(rs) < 10:
                continue
            windows[a] = AssetWindow(funding=fs, returns=rs)
            rwin[a] = rs

        sigs = compute_all(windows)
        mu = sigs.get(signal_name, {})
        vol = volatilities(rwin, risk)
        want = target_weights(mu, vol, tgt)
        new = move_toward_target(book.weights, want, trade)

        nxt = b + BAR_MS
        rets = {a: r for a in panel.assets
                if (r := panel.ret(a, nxt)) is not None}
        funds = {a: panel.fund[a].get(nxt, 0.0) for a in panel.assets}
        apply_step(book, new, rets, funds, trade)
        per_bar.append(book.pnl_net - prev_net)
        prev_net = book.pnl_net

    hours = (len(bars) - warm) * 4
    return book, summarise_book(book, hours), per_bar


def t_test_one_sided(x: Sequence[float]) -> Tuple[float, float]:
    """t de Student et p unilaterale (H1 : moyenne > 0).

    Approximation normale : avec plusieurs milliers de barres, l'ecart a la
    loi de Student est negligeable. Une p-value de 0,5 signale une moyenne
    nulle ou negative, jamais une absence de test.
    """
    n = len(x)
    if n < 30:
        return float("nan"), 1.0
    m = statistics.fmean(x)
    sd = statistics.stdev(x)
    if sd <= 0:
        return float("nan"), 1.0
    t = m / (sd / math.sqrt(n))
    p = 0.5 * math.erfc(t / math.sqrt(2.0))
    return t, p


def benjamini_hochberg(pvals: Dict[str, float], q: float = 0.10) -> Dict[str, bool]:
    """BH : quels signaux survivent au controle du taux de fausses decouvertes.

    Sans cette correction, tester quatre signaux et retenir le meilleur donne
    environ une chance sur cinq de retenir du bruit.
    """
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    survivors = {k: False for k in pvals}
    k_max = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= q * i / m:
            k_max = i
    for i, (name, _) in enumerate(items, start=1):
        survivors[name] = i <= k_max
    return survivors


def sharpe_from_bars(per_bar: Sequence[float]) -> Optional[float]:
    if len(per_bar) < 2:
        return None
    sd = statistics.stdev(per_bar)
    if sd <= 0:
        return None
    return statistics.fmean(per_bar) / sd * math.sqrt(BARS_PER_YEAR)


def run_all_signals(panel: LongPanel, b0: int, b1: int,
                    cost_bps: float = COST_BPS,
                    names: Optional[Sequence[str]] = None
                    ) -> Dict[str, Tuple[BookState, dict, List[float]]]:
    """Les quatre signaux en UN seul passage.

    Les fenetres et les volatilites sont identiques pour tous : les calculer
    une fois par barre au lieu d'une fois par signal divise le cout par
    quatre et, surtout, garantit que les quatre livres voient EXACTEMENT le
    meme etat du marche. Une comparaison ou chaque signal aurait sa propre
    estimation du risque ne serait pas une comparaison.
    """
    names = list(names or SIGNALS.keys())
    risk = RiskConfig(lookback_h=VOL_BARS)
    tgt = TargetConfig(gamma=GAMMA, gross_leverage=GROSS,
                       max_weight=MAX_WEIGHT, dollar_neutral=True)
    trade = TradingConfig(cost_bps=cost_bps, band_multiple=BAND_MULTIPLE)

    bars = [b for b in panel.bars if b0 <= b <= b1]
    warm = VOL_BARS + 1
    if len(bars) <= warm + 10:
        raise ValueError("fenetre trop courte")

    books = {n: BookState() for n in names}
    per_bar: Dict[str, List[float]] = {n: [] for n in names}
    prev = {n: 0.0 for n in names}

    # Rendements pre-calcules : le panneau ne change pas pendant la passe.
    rets_by_bar: Dict[int, Dict[str, float]] = {}
    for b in bars:
        row = {}
        for a in panel.assets:
            r = panel.ret(a, b)
            if r is not None:
                row[a] = r
        rets_by_bar[b] = row

    hist: Dict[str, List[float]] = {a: [] for a in panel.assets}
    for i, b in enumerate(bars):
        for a, r in rets_by_bar[b].items():
            hist[a].append(r)

        if i < warm:
            continue
        past = bars[max(0, i - VOL_BARS): i + 1]
        windows: Dict[str, AssetWindow] = {}
        rwin: Dict[str, List[float]] = {}
        for a in panel.assets:
            rs = hist[a][-VOL_BARS:]
            if len(rs) < 10:
                continue
            fs = [panel.fund[a].get(x, 0.0) for x in past]
            windows[a] = AssetWindow(funding=fs, returns=rs)
            rwin[a] = rs

        sigs = compute_all(windows)
        vol = volatilities(rwin, risk)
        nxt = b + BAR_MS
        nrets = rets_by_bar.get(nxt) or {}
        nfunds = {a: panel.fund[a].get(nxt, 0.0) for a in panel.assets}

        for n in names:
            bk = books[n]
            want = target_weights(sigs.get(n, {}), vol, tgt)
            new = move_toward_target(bk.weights, want, trade)
            apply_step(bk, new, nrets, nfunds, trade)
            per_bar[n].append(bk.pnl_net - prev[n])
            prev[n] = bk.pnl_net

    hours = (len(bars) - warm) * 4
    return {n: (books[n], summarise_book(books[n], hours), per_bar[n])
            for n in names}
