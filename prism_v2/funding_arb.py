#!/usr/bin/env python3
"""FUNDING ARB — le differentiel de funding inter-venues persiste-t-il ?

Regle GELEE dans prism_v2/FUNDING_ARB_PROTOCOL.md avant toute analyse.

LA QUESTION N'EST PAS « le differentiel existe-t-il ». Il existe : mediane
0,3 %/an, p90 23 %/an, extremes au-dela de 200 %/an. La question est de
savoir s'il PERSISTE apres etre devenu observable. Un differentiel est un
signal avance, pas une rente : s'il se referme en deux heures, on encaisse
quelques bps de funding en payant ~39 bps d'aller-retour.

MECANIQUE DES PAIEMENTS, QUI EST TOUT LE SUJET.
Hyperliquid paie toutes les heures ; OKX toutes les 8 heures. Une position
detenue 8 h ne touche PAS forcement un paiement OKX : cela depend de l'heure
d'entree par rapport a l'horodatage de funding. Compter « 8 h de taux OKX »
au lieu des paiements reellement horodates surestimerait le revenu d'un
facteur pouvant aller jusqu'a l'infini (position fermee avant tout paiement).
Ce module somme les paiements REELS, par horodatage.

SENS DE LA POSITION.
    f > 0  =>  les longs paient les shorts (convention des deux venues)
    d(t) = f_okx(t) - f_hl(t),  par heure
    d > 0 => short OKX (encaisse f_okx), long HL (paie f_hl)  => gagne d
    d < 0 => l'inverse                                        => gagne -d
Dans les deux cas on gagne |d| SI le differentiel tient.

ANTI-LOOK-AHEAD STRUCTUREL. Le signal a t n'utilise que des paiements
horodates <= t. L'entree est a t+1h, jamais a t. Le revenu ne compte que les
paiements horodates strictement apres l'entree.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HOUR_MS = 3_600_000
YEAR_HOURS = 24 * 365

# Couts. Les frais sont OBSERVED (baremes publies) ; le demi-spread est
# ASSUMED et le reste — il n'est jamais traite comme nul.
OKX_TAKER_BPS = 5.0
HL_TAKER_BPS = 4.5
DEFAULT_HALF_SPREAD_BPS = 5.0   # ASSUMED, par traversee
N_CROSSINGS = 4                  # entree 2 jambes + sortie 2 jambes

THRESHOLDS_APR = (0.20, 0.50, 1.00, 2.00)
HORIZONS_H = (8, 24, 72, 168)
LEVERAGE_GRID = (1.0, 2.0, 3.0, 5.0)


def round_trip_cost_bps(half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS) -> float:
    """Cout total d'un aller-retour, en bps du NOTIONNEL (pas du capital)."""
    if half_spread_bps < 0.0:
        raise ValueError("demi-spread negatif")
    fees = 2.0 * (OKX_TAKER_BPS + HL_TAKER_BPS)
    return fees + N_CROSSINGS * half_spread_bps


@dataclass(frozen=True)
class Payment:
    ts: int
    rate: float


def infer_period_h(timestamps: Sequence[int]) -> float:
    """Periode de funding REELLE, deduite des horodatages.

    OKX n'a pas une cadence unique : 8 h sur la plupart des instruments,
    mais 4 h et 1 h sur les actifs volatils. Coder 8 h en dur divisait le
    taux par 8 la ou il fallait diviser par 1, gonflant le signal jusqu'a
    un facteur 8 — c'est ce qui produisait un differentiel lu a +3879 %/an
    sur SOPH. La mediane des ecarts est robuste aux paiements manquants.
    """
    if len(timestamps) < 2:
        raise ValueError("pas assez de paiements pour deduire la periode")
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:]) if b > a]
    if not gaps:
        raise ValueError("horodatages non strictement croissants")
    return statistics.median(gaps) / HOUR_MS


class VenueFunding:
    """Serie de paiements de funding, interrogeable causalement."""

    def __init__(self, payments: Sequence[Tuple[int, float]],
                 period_h: Optional[float] = None):
        pays = sorted(payments)
        self._ts = [p[0] for p in pays]
        self._rate = [p[1] for p in pays]
        # Deduite par defaut : aucune cadence n'est supposee.
        self.period_h = (period_h if period_h is not None
                         else infer_period_h(self._ts))

    def __len__(self) -> int:
        return len(self._ts)

    def last_rate_per_hour_at(self, ts: int) -> Optional[float]:
        """Dernier taux PAYE a ou avant ts, ramene a l'heure.

        Renvoie None s'il n'existe aucun paiement anterieur : c'est un refus,
        pas un zero. Un zero ferait croire a un differentiel nul.
        """
        i = bisect_right(self._ts, ts)
        if i == 0:
            return None
        return self._rate[i - 1] / self.period_h

    def sum_between(self, t0: int, t1: int) -> float:
        """Somme des taux REELLEMENT payes dans ]t0, t1].

        C'est la seule definition honnete du revenu : un paiement compte s'il
        tombe dans la fenetre, pas au prorata du temps passe.
        """
        lo = bisect_right(self._ts, t0)
        hi = bisect_right(self._ts, t1)
        return sum(self._rate[lo:hi])

    def span(self) -> Tuple[int, int]:
        return (self._ts[0], self._ts[-1]) if self._ts else (0, 0)


@dataclass(frozen=True)
class Trade:
    symbol: str
    signal_ts: int
    entry_ts: int
    exit_ts: int
    signal_apr: float
    direction: int
    gross_bps: float      # revenu de funding, bps du notionnel
    cost_bps: float
    net_bps_notional: float
    oi_usd: float
    vol24_usd: float

    def net_bps_capital(self, leverage: float) -> float:
        """Capital = 2 x notionnel / levier (les deux jambes sont margees)."""
        if leverage <= 0:
            raise ValueError("levier <= 0")
        return self.net_bps_notional * leverage / 2.0

    def bps_per_day(self, leverage: float) -> float:
        hours = (self.exit_ts - self.entry_ts) / HOUR_MS
        if hours <= 0:
            raise ValueError("duree nulle")
        return self.net_bps_capital(leverage) / (hours / 24.0)


class Asset:
    def __init__(self, symbol: str, raw: dict):
        self.symbol = symbol
        self.hl = VenueFunding(raw["hl"])
        self.okx = VenueFunding(raw["okx"])
        self.oi_usd = float(raw["meta"]["oi_usd"])
        self.vol24_usd = float(raw["meta"]["vol24_usd"])

    def signal_apr_at(self, ts: int) -> Optional[float]:
        """d(t) annualise. None si une des deux venues n'a pas d'historique."""
        a = self.okx.last_rate_per_hour_at(ts)
        b = self.hl.last_rate_per_hour_at(ts)
        if a is None or b is None:
            return None
        return (a - b) * YEAR_HOURS

    def simulate(self, ts: int, horizon_h: int,
                 half_spread_bps: float) -> Optional[Trade]:
        sig = self.signal_apr_at(ts)
        if sig is None:
            return None
        entry = ts + HOUR_MS                      # t+1h, strictement apres
        exit_ = entry + horizon_h * HOUR_MS
        hl_end = self.hl.span()[1]
        okx_end = self.okx.span()[1]
        if exit_ > min(hl_end, okx_end):
            return None                            # fenetre incomplete
        direction = 1 if sig > 0 else -1
        gross = direction * (self.okx.sum_between(entry, exit_)
                             - self.hl.sum_between(entry, exit_))
        gross_bps = gross * 10_000.0
        cost = round_trip_cost_bps(half_spread_bps)
        return Trade(self.symbol, ts, entry, exit_, sig, direction,
                     gross_bps, cost, gross_bps - cost,
                     self.oi_usd, self.vol24_usd)


def load_assets(path: Path) -> Dict[str, Asset]:
    raw = json.loads(path.read_text())
    out = {}
    for sym, d in raw.items():
        if "error" in d or not d.get("hl") or not d.get("okx"):
            continue
        out[sym] = Asset(sym, d)
    return out


def run_config(assets: Dict[str, Asset], t0: int, t1: int,
               threshold_apr: float, horizon_h: int,
               half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS,
               step_h: int = 1) -> List[Trade]:
    """Applique la regle gelee sur ]t0, t1]."""
    trades = []
    for asset in assets.values():
        ts = t0
        while ts <= t1:
            sig = asset.signal_apr_at(ts)
            if sig is not None and abs(sig) >= threshold_apr:
                tr = asset.simulate(ts, horizon_h, half_spread_bps)
                if tr is not None:
                    trades.append(tr)
            ts += step_h * HOUR_MS
    return trades


def non_overlapping(trades: Sequence[Trade]) -> List[Trade]:
    """Un seul trade a la fois par actif : sinon les observations se recouvrent
    et toute statistique de dispersion est fausse."""
    by_sym: Dict[str, List[Trade]] = {}
    for t in trades:
        by_sym.setdefault(t.symbol, []).append(t)
    kept = []
    for sym, lst in by_sym.items():
        lst.sort(key=lambda t: t.entry_ts)
        free_at = -1
        for t in lst:
            if t.entry_ts >= free_at:
                kept.append(t)
                free_at = t.exit_ts
    return kept


def summarise(trades: Sequence[Trade], leverage: float = 1.0) -> dict:
    if not trades:
        return {"n": 0}
    net = [t.net_bps_notional for t in trades]
    gross = [t.gross_bps for t in trades]
    per_day = [t.bps_per_day(leverage) for t in trades]
    mean = statistics.fmean(net)
    sd = statistics.stdev(net) if len(net) > 1 else float("nan")
    t_stat = (mean / (sd / math.sqrt(len(net)))
              if len(net) > 1 and sd > 0 else float("nan"))
    return {
        "n": len(trades),
        "n_actifs": len({t.symbol for t in trades}),
        "gross_bps_moy": round(statistics.fmean(gross), 2),
        "cout_bps": round(trades[0].cost_bps, 2),
        "net_bps_moy": round(mean, 2),
        "net_bps_median": round(statistics.median(net), 2),
        "t_stat": round(t_stat, 2) if t_stat == t_stat else None,
        "part_positive": round(sum(1 for x in net if x > 0) / len(net), 3),
        "bps_par_jour_moy": round(statistics.fmean(per_day), 2),
        "bps_par_jour_median": round(statistics.median(per_day), 2),
        "levier": leverage,
    }
