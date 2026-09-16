"""Adaptateur M2 — dislocation associee aux liquidations.

OBJET EXACT : MESURER l'amplitude du mecanisme, pas le trader.

Ce n'est pas une strategie et ne doit pas le devenir. La question posee est
strictement :

    "Meme en supposant un timing PARFAIT, retrospectif et irrealisable,
     l'amplitude de la dislocation depasse-t-elle les couts reels ?"

C'est un test de FALSIFICATION a faible cout. Si la borne superieure la plus
genereuse ne couvre pas les couts, le mecanisme est mort et on economise des
semaines. Si elle les couvre, on n'a RIEN prouve : il restera a montrer
qu'une regle causale, sans information future, en capture une fraction.

`gross_capture_bps` est donc une BORNE SUPERIEURE EX-POST, calculee avec
information future (entree a l'extreme, sortie apres reversion). Elle est
marquee `backward_looking: True` et `upper_bound: True` dans les metadonnees
et dans le ledger. La confondre avec un edge serait exactement l'erreur que
V2 existe pour empecher.

AUCUN indicateur technique : pas de RSI, EMA, MACD, ADX, Bollinger ni score.
Les seules entrees sont l'horodatage et le prix des liquidations publiques,
et les bougies servant a mesurer un deplacement de prix autour de cet instant.

Si les donnees manquent -> INSUFFICIENT_DATA. Jamais de signal invente.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core_types import Direction, Provenance, ms_to_iso, utc_now_iso
from ..opportunity import (
    Candidate, DetectionResult, MarketContext, Opportunity,
)

OPPORTUNITY_TYPE = "M2_LIQUIDATION_DISLOCATION"


@dataclass(frozen=True)
class _Bar:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float


def _parse_candles(rows: Optional[List[List[str]]]) -> List[_Bar]:
    """OKX /market/candles : [ts, o, h, l, c, vol, ...], ordre decroissant."""
    out: List[_Bar] = []
    for r in rows or []:
        try:
            out.append(_Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])))
        except (ValueError, IndexError, TypeError):
            continue
    return sorted(out, key=lambda b: b.ts_ms)


def _flatten_liquidations(payload: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Aplatit la structure OKX {..., details:[...]} en liste d'evenements."""
    events: List[Dict[str, Any]] = []
    for row in payload or []:
        for d in row.get("details", []) or []:
            try:
                events.append({
                    "ts_ms": int(d.get("ts") or d.get("time")),
                    "bk_px": float(d["bkPx"]),
                    "sz": float(d.get("sz", 0.0) or 0.0),
                    "side": d.get("side", ""),        # "sell" = long liquide
                    "pos_side": d.get("posSide", ""),
                })
            except (TypeError, ValueError, KeyError):
                continue
    return sorted(events, key=lambda e: e["ts_ms"])


class LiquidationDislocationOpportunity(Opportunity):
    """Mesure l'amplitude ex-post des dislocations autour des liquidations."""

    name = OPPORTUNITY_TYPE
    requires = ("liquidations", "candles")

    def __init__(self, pre_bars: int = 5, post_bars: int = 5,
                 min_events: int = 1, bar_ms: int = 60_000):
        """
        pre_bars  : bougies avant l'evenement servant de reference de prix
        post_bars : bougies apres, dans lesquelles la reversion est mesuree
        min_events: nombre minimal de liquidations exploitables

        Ces valeurs sont des FENETRES D'OBSERVATION, pas des parametres
        optimises. Elles ne sont jamais ajustees pour ameliorer un resultat.
        """
        self.pre_bars = pre_bars
        self.post_bars = post_bars
        self.min_events = min_events
        self.bar_ms = bar_ms

    # ---- detection -----------------------------------------------------------
    def detect(self, ctx: MarketContext) -> DetectionResult:
        events = _flatten_liquidations(ctx.liquidations)
        bars = _parse_candles(ctx.candles)

        if not events:
            return DetectionResult.insufficient(
                "aucune liquidation exploitable dans le contexte", ["liquidations"])
        if len(bars) < self.pre_bars + self.post_bars + 1:
            return DetectionResult.insufficient(
                f"historique de bougies insuffisant: {len(bars)} bougies, "
                f"{self.pre_bars + self.post_bars + 1} requises", ["candles"])

        t_min, t_max = bars[0].ts_ms, bars[-1].ts_ms
        covered = [e for e in events if t_min <= e["ts_ms"] <= t_max]
        if len(covered) < self.min_events:
            return DetectionResult.insufficient(
                f"aucune liquidation dans la fenetre des bougies "
                f"({len(events)} liquidations vues, fenetre bougies "
                f"{ms_to_iso(t_min)} -> {ms_to_iso(t_max)}). "
                "La dislocation ne peut pas etre mesuree sans recouvrement temporel.",
                ["liquidations∩candles"])

        capacity = self._capacity(ctx)
        candidates: List[Candidate] = []
        for ev in covered:
            built = self._measure(ctx, ev, bars, capacity)
            if built is not None:
                candidates.append(built)

        if not candidates:
            return DetectionResult.insufficient(
                f"{len(covered)} liquidations recouvertes mais aucune mesurable "
                "(bougies manquantes de part et d'autre de l'evenement)",
                ["candles_around_event"])
        return DetectionResult.ok(candidates)

    # ---- mesure --------------------------------------------------------------
    def _measure(self, ctx: MarketContext, ev: Dict[str, Any], bars: List[_Bar],
                 capacity: Optional[float]) -> Optional[Candidate]:
        idx = self._locate(bars, ev["ts_ms"])
        if idx is None or idx < self.pre_bars or idx + self.post_bars >= len(bars):
            return None

        pre = bars[idx - self.pre_bars: idx]
        post = bars[idx + 1: idx + 1 + self.post_bars]
        if not pre or not post:
            return None

        ref_px = pre[-1].close                       # reference avant evenement
        event_bar = bars[idx]
        is_long_liquidation = ev["side"] == "sell"   # vente forcee -> prix pousse vers le bas

        if is_long_liquidation:
            extreme = min(event_bar.low, ev["bk_px"])
            direction = Direction.LONG                # la reversion remonte
            displacement_bps = (ref_px - extreme) / extreme * 10_000.0
            recovery_px = max(b.high for b in post)
            reversion_bps = (recovery_px - extreme) / extreme * 10_000.0
        else:
            extreme = max(event_bar.high, ev["bk_px"])
            direction = Direction.SHORT
            displacement_bps = (extreme - ref_px) / extreme * 10_000.0
            recovery_px = min(b.low for b in post)
            reversion_bps = (extreme - recovery_px) / extreme * 10_000.0

        if displacement_bps <= 0:
            # Pas de dislocation observable : l'evenement n'a pas ecarte le prix
            # de sa reference. On ne fabrique pas d'amplitude.
            return None

        return Candidate(
            ts_utc=ms_to_iso(ev["ts_ms"]),
            instrument=ctx.instrument,
            opportunity_type=OPPORTUNITY_TYPE,
            direction=direction,
            gross_capture_bps=reversion_bps,
            capacity_usd=capacity,
            confidence=None,                    # aucune definition objective disponible
            confidence_definition=None,
            provenance=Provenance(
                exchange="OKX", endpoint="/public/liquidation-orders + /market/candles",
                fetched_at=utc_now_iso(), inst_id=ctx.instrument.inst_id,
                extra={"liquidation_ts": ms_to_iso(ev["ts_ms"]),
                       "bk_px": ev["bk_px"], "sz_contracts": ev["sz"]}),
            metadata={
                # ── avertissements structurels, repris tels quels au ledger
                "backward_looking": True,
                "upper_bound": True,
                "uses_future_information": True,
                "interpretation": (
                    "BORNE SUPERIEURE EX-POST avec timing parfait (entree a "
                    "l'extreme, sortie au meilleur point de la fenetre post). "
                    "N'est PAS un edge et n'est PAS capturable en l'etat."),
                # ── mesures
                "displacement_bps": displacement_bps,
                "reversion_bps": reversion_bps,
                "ref_price": ref_px,
                "extreme_price": extreme,
                "recovery_price": recovery_px,
                "liquidation_break_price": ev["bk_px"],
                "liquidation_side": ev["side"],
                "liquidation_pos_side": ev["pos_side"],
                "liquidation_size_contracts": ev["sz"],
                "pre_bars": self.pre_bars, "post_bars": self.post_bars,
                "bar_interval_ms": self.bar_ms,
                "no_technical_indicators": True,
            },
        )

    def _locate(self, bars: List[_Bar], ts_ms: int) -> Optional[int]:
        """Index de la bougie contenant l'evenement."""
        for i, b in enumerate(bars):
            if b.ts_ms <= ts_ms < b.ts_ms + self.bar_ms:
                return i
        return None

    def _capacity(self, ctx: MarketContext) -> Optional[float]:
        """Capacite = profondeur reellement affichee du cote a consommer.

        None si le carnet est absent : on ne devine pas une capacite.
        """
        if ctx.book is None:
            return None
        try:
            return min(ctx.book.bid_depth(), ctx.book.ask_depth())
        except Exception:
            return None
