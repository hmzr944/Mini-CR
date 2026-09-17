#!/usr/bin/env python3
"""TEST DE VITESSE — le momentum plus lent conserve-t-il son alpha ?

Protocole : prism_v2/MOMENTUM_PROTOCOL.md, gele avant execution.

LA QUESTION EST UN RATIO. Le diagnostic a montre que le cout vaut
turnover x 6,54 bps, et que 94 % du turnover vient du signal. Un signal deux
fois plus lent coute environ deux fois moins. Il ne sert donc a rien de
comparer les alphas bruts : ce qui decide, c'est **l'alpha par unite de
turnover**. C'est la grandeur que ce module rapporte.

Aucun signal de carry ici : le funding OKX est plafonne a 92 jours par l'API,
et tester un signal lent sur 92 jours serait exactement l'erreur de puissance
qui a invalide le test precedent.
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

from prism_v2.long_test import (
    BAR_MS, BARS_PER_DAY, COST_BPS, LongPanel, benjamini_hochberg,
    t_test_one_sided,
)
from prism_v2.portfolio import (
    BookState, RiskConfig, TargetConfig, TradingConfig, apply_step,
    exposures, market_betas, move_toward_target, summarise_book,
    target_weights, volatilities,
)

# Fenetres figees par le protocole, en barres de 4 h.
SPEEDS: Dict[str, Tuple[str, int]] = {
    "M1_MOM_7j": ("mom", 42),
    "M2_MOM_30j": ("mom", 180),
    "M3_MOM_90j": ("mom", 540),
    "R1_REV_1j": ("rev", 6),
}
RISK_BARS = 180
GAMMA = 1.0
GROSS = 1.0
MAX_WEIGHT = 0.10
BAND_MULTIPLE = 2.0


def build_okx_panel(path: Path, min_bars: int = 800) -> LongPanel:
    """Panneau OKX. Le funding est charge mais restera NUL dans ce test :
    aucun signal de carry n'y est evalue, et le PnL de funding est rapporte
    separement pour qu'on voie qu'il ne porte pas le resultat."""
    raw = json.loads(path.read_text())
    px: Dict[str, Dict[int, float]] = {}
    fund: Dict[str, Dict[int, float]] = {}
    for sym, d in raw.items():
        if "error" in d or len(d.get("px") or []) < min_bars:
            continue
        px[sym] = {int(t): float(p) for t, p in d["px"]}
        buckets: Dict[int, float] = {}
        for t, r in d.get("fund") or []:
            b = int(t) - int(t) % BAR_MS
            buckets[b] = buckets.get(b, 0.0) + float(r)
        fund[sym] = buckets
    if not px:
        raise ValueError("panneau OKX vide")
    lo = min(min(v) for v in px.values())
    hi = max(max(v) for v in px.values())
    return LongPanel(list(range(lo, hi + 1, BAR_MS)), px, fund)


def _mu_for(kind: str, bars: int, hist: Sequence[float]) -> Optional[float]:
    if len(hist) < bars:
        return None
    m = statistics.fmean(list(hist)[-bars:])
    return m if kind == "mom" else -m


def run_speeds(panel: LongPanel, b0: int, b1: int,
               cost_bps: float = COST_BPS,
               names: Optional[Sequence[str]] = None) -> Dict[str, dict]:
    """Toutes les vitesses en un passage : meme etat de marche pour toutes."""
    names = list(names or SPEEDS.keys())
    warm = max(RISK_BARS, max(SPEEDS[n][1] for n in names)) + 1
    risk = RiskConfig(lookback_h=RISK_BARS)
    tgt = TargetConfig(gamma=GAMMA, gross_leverage=GROSS,
                       max_weight=MAX_WEIGHT, dollar_neutral=True,
                       beta_neutral=True)
    trade = TradingConfig(cost_bps=cost_bps, band_multiple=BAND_MULTIPLE)

    bars = [b for b in panel.bars if b0 <= b <= b1]
    if len(bars) <= warm + 20:
        raise ValueError("fenetre trop courte")

    books = {n: BookState() for n in names}
    per_bar: Dict[str, List[float]] = {n: [] for n in names}
    prev = {n: 0.0 for n in names}
    beta_exp: Dict[str, List[float]] = {n: [] for n in names}
    hist: Dict[str, List[float]] = {a: [] for a in panel.assets}

    for i, b in enumerate(bars):
        for a in panel.assets:
            r = panel.ret(a, b)
            if r is not None:
                hist[a].append(r)
        if i < warm:
            continue

        rwin = {a: hist[a][-RISK_BARS:] for a in panel.assets
                if len(hist[a]) >= 20}
        if len(rwin) < 10:
            continue
        vol = volatilities(rwin, risk)
        beta = market_betas(rwin)

        nxt = b + BAR_MS
        nrets = {a: r for a in panel.assets
                 if (r := panel.ret(a, nxt)) is not None}
        nfunds = {a: panel.fund[a].get(nxt, 0.0) for a in panel.assets}

        for n in names:
            kind, win = SPEEDS[n]
            mu = {}
            for a in panel.assets:
                v = _mu_for(kind, win, hist[a])
                if v is not None:
                    mu[a] = v
            if len(mu) > 1:
                avg = statistics.fmean(mu.values())
                mu = {a: v - avg for a, v in mu.items()}
            bk = books[n]
            want = target_weights(mu, vol, tgt, beta=beta)
            new = move_toward_target(bk.weights, want, trade)
            apply_step(bk, new, nrets, nfunds, trade)
            per_bar[n].append(bk.pnl_net - prev[n])
            prev[n] = bk.pnl_net
            e = exposures(bk.weights, beta)
            beta_exp[n].append(e.get("net_beta", 0.0))

    n_bars = len(bars) - warm
    hours = n_bars * 4
    out: Dict[str, dict] = {}
    for n in names:
        s = summarise_book(books[n], hours)
        t, p = t_test_one_sided(per_bar[n])
        turn_per_bar = books[n].turnover / max(1, n_bars)
        gross_bps = (books[n].pnl_price + books[n].pnl_funding) * 10_000.0
        out[n] = {
            **s, "t": t, "p": p,
            "turnover_par_barre": round(turn_per_bar, 4),
            "alpha_brut_bps": round(gross_bps, 1),
            # LA grandeur du test : combien d'alpha brut par unite de turnover.
            # Elle doit depasser le cout unitaire pour que le signal survive.
            "alpha_par_turnover_bps": round(
                gross_bps / books[n].turnover, 3) if books[n].turnover > 0 else None,
            "beta_residuel_moyen": round(
                statistics.fmean(beta_exp[n]), 5) if beta_exp[n] else None,
        }
    return out
