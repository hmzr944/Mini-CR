"""Replay d'evenements — mesure de la decroissance par latence, SANS look-ahead.

Trois modes, jamais confondus (le mode est porte jusqu'au ledger) :

    BAR_BACKTEST  : bougies OHLC. Ne mesure RIEN de microstructurel.
    EVENT_REPLAY  : evenements collectes (L2 + trades + liquidations).
    LIVE_PAPER    : carnet temps reel, execution simulee.

GARANTIE ANTI-LOOK-AHEAD, structurelle et non declarative :
`EventTimeline.book_at(t)` ne retourne QUE le dernier carnet d'horodatage
<= t. Un `ReplayCursor` empeche en outre de consulter un instant anterieur a
celui deja atteint. Toute demande de donnee future leve `LookAheadViolation`.

LIMITE CONNUE ET ASSUMEE : le canal WebSocket `books5` ne publie que 5
niveaux. Il suffit au spread, a la fraicheur et a la decroissance par latence,
PAS a la capacite profonde. La capacite profonde exige le REST /market/books
(jusqu'a 400 niveaux), qui n'est pas un flux d'evenements. Les deux sources
sont donc complementaires et jamais melangees dans une meme mesure.
"""
from __future__ import annotations

import enum
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .core_types import Direction, Provenance, ms_to_iso
from .instruments import InstrumentSpec
from .orderbook import EmptyBook, Level, OrderBook

#: Grille de latence par defaut, en millisecondes. Ce sont des POINTS
#: D'OBSERVATION, pas des parametres : les faire varier ne change aucun
#: resultat, cela ajoute seulement des points de mesure.
DEFAULT_LATENCY_GRID_MS = (0, 10, 25, 50, 100, 250, 500, 1_000, 2_000, 5_000)


class MeasurementMode(str, enum.Enum):
    BAR_BACKTEST = "BAR_BACKTEST"
    EVENT_REPLAY = "EVENT_REPLAY"
    LIVE_PAPER = "LIVE_PAPER"


class LookAheadViolation(RuntimeError):
    """Une donnee posterieure a l'instant de decision a ete demandee."""


def _book_from_ws_event(event: Dict[str, Any], spec: InstrumentSpec) -> Optional[OrderBook]:
    """Construit un OrderBook depuis un evenement books5 collecte."""
    if event.get("inst_id") != spec.inst_id:
        return None
    data = event.get("data") or {}
    raw_bids, raw_asks = data.get("bids") or [], data.get("asks") or []
    if not raw_bids or not raw_asks:
        return None
    from .contracts import usd_notional

    def build(rows: Sequence[Sequence[str]]) -> List[Level]:
        out: List[Level] = []
        for r in rows:
            try:
                px, sz = float(r[0]), float(r[1])
            except (TypeError, ValueError, IndexError):
                continue
            if px > 0 and sz > 0:
                out.append(Level(px, sz, usd_notional(spec, sz, px)))
        return out

    bids = sorted(build(raw_bids), key=lambda l: -l.price)
    asks = sorted(build(raw_asks), key=lambda l: l.price)
    if not bids or not asks:
        return None
    ts_ms = event.get("exchange_ts_ms")
    return OrderBook(
        instrument=spec, bids=bids, asks=asks,
        ts_utc=ms_to_iso(ts_ms) if ts_ms else "",
        provenance=Provenance("OKX", "ws:books5", ms_to_iso(ts_ms) if ts_ms else "",
                              spec.inst_id),
        seq_id=str(event.get("seq_id")) if event.get("seq_id") is not None else None,
        raw_depth=max(len(bids), len(asks)), ts_ms=ts_ms,
        local_recv_ts_ms=event.get("local_recv_ts_ms"))


@dataclass
class EventTimeline:
    """Suite ordonnee de carnets pour UN instrument. Immuable apres construction."""

    instrument: InstrumentSpec
    _ts: List[int] = field(default_factory=list)
    _books: List[OrderBook] = field(default_factory=list)
    skipped: int = 0

    @classmethod
    def from_events(cls, spec: InstrumentSpec, events: Sequence[Dict[str, Any]],
                    channel: str = "books5") -> "EventTimeline":
        tl = cls(instrument=spec)
        pairs: List[tuple[int, OrderBook]] = []
        for ev in events:
            if ev.get("channel") != channel or ev.get("inst_id") != spec.inst_id:
                continue
            book = _book_from_ws_event(ev, spec)
            if book is None or book.ts_ms is None:
                tl.skipped += 1
                continue
            pairs.append((book.ts_ms, book))
        pairs.sort(key=lambda p: p[0])
        tl._ts = [p[0] for p in pairs]
        tl._books = [p[1] for p in pairs]
        return tl

    def __len__(self) -> int:
        return len(self._books)

    @property
    def first_ts_ms(self) -> Optional[int]:
        return self._ts[0] if self._ts else None

    @property
    def last_ts_ms(self) -> Optional[int]:
        return self._ts[-1] if self._ts else None

    def covers(self, ts_ms: int) -> bool:
        return bool(self._ts) and self._ts[0] <= ts_ms <= self._ts[-1]

    def book_at(self, ts_ms: int) -> Optional[OrderBook]:
        """Dernier carnet d'horodatage <= ts_ms.

        C'est ICI que l'absence de look-ahead est structurelle : la fonction
        ne peut pas retourner un carnet futur, quelle que soit l'intention de
        l'appelant.
        """
        if not self._ts:
            return None
        idx = bisect_right(self._ts, ts_ms) - 1
        if idx < 0:
            return None
        return self._books[idx]

    def update_interval_ms(self) -> Optional[float]:
        """Intervalle median entre deux publications de carnet.

        Borne la RESOLUTION TEMPORELLE des mesures : une decroissance mesuree
        sur un delta inferieur a cet intervalle compare le carnet a lui-meme
        et vaut mecaniquement 0. Ce 0 est un ARTEFACT, pas une observation.
        """
        if len(self._ts) < 2:
            return None
        gaps = sorted(b - a for a, b in zip(self._ts, self._ts[1:]))
        n = len(gaps)
        return gaps[n // 2] if n % 2 else (gaps[n // 2 - 1] + gaps[n // 2]) / 2

    def is_resolvable(self, delta_ms: int) -> bool:
        """Un delta est mesurable s'il depasse l'intervalle de publication."""
        interval = self.update_interval_ms()
        return interval is not None and delta_ms >= interval

    def age_at(self, ts_ms: int) -> Optional[int]:
        book = self.book_at(ts_ms)
        if book is None or book.ts_ms is None:
            return None
        return ts_ms - book.ts_ms


class ReplayCursor:
    """Curseur monotone. Interdit de revenir en arriere ou de lire le futur."""

    def __init__(self, timeline: EventTimeline, start_ts_ms: int):
        self.timeline = timeline
        self._now = start_ts_ms

    @property
    def now_ms(self) -> int:
        return self._now

    def advance_to(self, ts_ms: int) -> None:
        if ts_ms < self._now:
            raise LookAheadViolation(
                f"curseur monotone : {ts_ms} < instant courant {self._now}")
        self._now = ts_ms

    def current_book(self) -> Optional[OrderBook]:
        return self.timeline.book_at(self._now)

    def book_at_or_before(self, ts_ms: int) -> Optional[OrderBook]:
        if ts_ms > self._now:
            raise LookAheadViolation(
                f"lecture du futur interdite : {ts_ms} > instant courant {self._now}")
        return self.timeline.book_at(ts_ms)


@dataclass(frozen=True)
class LatencyPoint:
    delta_ms: int
    book_ts_ms: Optional[int]
    book_age_ms: Optional[int]
    available: bool
    exec_vwap: Optional[float]
    mid: Optional[float]
    cost_bps: Optional[float]              # cout de traversee depuis le mid a T0+delta
    depth_usd: Optional[float]
    depth_survival_ratio: Optional[float]  # profondeur a T0+delta / profondeur a T0
    decay_bps: Optional[float]             # cout a T0+delta moins cout a T0
    mid_drift_bps: Optional[float]         # derive du mid depuis T0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def latency_decay_curve(timeline: EventTimeline, event_ts_ms: int,
                        direction: Direction, notional_usd: float,
                        grid_ms: Sequence[int] = DEFAULT_LATENCY_GRID_MS,
                        ) -> List[LatencyPoint]:
    """Mesure comment le cout d'execution evolue apres un evenement.

    Pour chaque delta, on lit le carnet REELLEMENT disponible a T0+delta et on
    calcule le cout de traversee du meme notionnel. La difference avec delta=0
    est la DECROISSANCE PAR LATENCE : ce que coute le fait d'arriver en retard.

    Aucun point n'est interpole ni simule. Si le carnet manque a un delta,
    le point est marque `available=False` — il reste UNKNOWN, pas zero.
    """
    side = direction.taker_side
    base_book = timeline.book_at(event_ts_ms)
    base_cost: Optional[float] = None
    base_depth: Optional[float] = None
    base_mid: Optional[float] = None
    if base_book is not None:
        try:
            base_cost = base_book.market_impact_bps(side, notional_usd)
            base_depth = base_book.depth(side)
            base_mid = base_book.mid
        except (EmptyBook, ValueError):
            base_cost = base_depth = base_mid = None

    interval = timeline.update_interval_ms()
    points: List[LatencyPoint] = []
    for delta in sorted(grid_ms):
        target = event_ts_ms + delta
        # Sous la cadence de publication, comparer T0 et T0+delta revient a
        # comparer un carnet a lui-meme : le resultat serait un 0 artificiel.
        # On retourne UNKNOWN plutot qu'un zero trompeur.
        if delta > 0 and interval is not None and delta < interval:
            points.append(LatencyPoint(
                delta_ms=delta, book_ts_ms=None, book_age_ms=None, available=False,
                exec_vwap=None, mid=None, cost_bps=None, depth_usd=None,
                depth_survival_ratio=None, decay_bps=None, mid_drift_bps=None,
                note=(f"SOUS-RESOLU : delta {delta}ms < cadence de publication "
                      f"{interval:.0f}ms — non mesurable, UNKNOWN (pas zero)")))
            continue
        if not timeline.covers(target):
            points.append(LatencyPoint(
                delta_ms=delta, book_ts_ms=None, book_age_ms=None, available=False,
                exec_vwap=None, mid=None, cost_bps=None, depth_usd=None,
                depth_survival_ratio=None, decay_bps=None, mid_drift_bps=None,
                note="hors de la fenetre collectee — UNKNOWN, non extrapole"))
            continue
        book = timeline.book_at(target)
        if book is None:
            points.append(LatencyPoint(delta, None, None, False, None, None, None,
                                       None, None, None, None,
                                       "aucun carnet a cet instant"))
            continue
        try:
            walk = book.walk(side, notional_usd)
            cost = book.market_impact_bps(side, notional_usd)
            depth = book.depth(side)
            mid = book.mid
        except (EmptyBook, ValueError) as exc:
            points.append(LatencyPoint(delta, book.ts_ms, None, False, None, None,
                                       None, None, None, None, None, str(exc)))
            continue
        points.append(LatencyPoint(
            delta_ms=delta, book_ts_ms=book.ts_ms,
            book_age_ms=target - book.ts_ms if book.ts_ms else None,
            available=not walk.exhausted, exec_vwap=walk.vwap, mid=mid,
            cost_bps=cost, depth_usd=depth,
            depth_survival_ratio=(depth / base_depth) if base_depth else None,
            decay_bps=(cost - base_cost) if (cost is not None and base_cost is not None) else None,
            mid_drift_bps=((mid - base_mid) / base_mid * 10_000.0) if base_mid else None,
            note="" if not walk.exhausted else
                 f"profondeur epuisee: rempli {walk.fill_ratio:.0%}"))
    return points


@dataclass(frozen=True)
class CausalCapture:
    """Capture CAUSALE : decision et sortie n'utilisent que le passe.

    A opposer a la mesure ex-post de l'adaptateur M2, qui utilise
    l'information future et ne constitue qu'une borne superieure.
    """

    event_ts_ms: int
    decision_ts_ms: int           # T0 + latence supposee
    exit_ts_ms: int               # T0 + latence + duree de detention
    direction: str
    entry_px: Optional[float]
    exit_px: Optional[float]
    gross_bps: Optional[float]
    entry_cost_bps: Optional[float]
    exit_cost_bps: Optional[float]
    net_before_fees_bps: Optional[float]
    resolved: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def causal_capture(timeline: EventTimeline, event_ts_ms: int, direction: Direction,
                   notional_usd: float, latency_ms: int, hold_ms: int) -> CausalCapture:
    """Simule une capture realisable : entree a T0+latence, sortie a T0+latence+hold.

    Le prix d'entree est le VWAP d'execution du carnet disponible a l'instant
    d'arrivee de l'ordre, pas le prix de l'evenement. Le prix de sortie est
    celui du carnet a l'instant de sortie. Aucune information posterieure a
    chacun de ces deux instants n'est utilisee.
    """
    entry_ts = event_ts_ms + latency_ms
    exit_ts = entry_ts + hold_ms
    if not timeline.covers(entry_ts) or not timeline.covers(exit_ts):
        return CausalCapture(event_ts_ms, entry_ts, exit_ts, direction.value,
                             None, None, None, None, None, None, False,
                             "fenetre collectee ne couvre pas entree et/ou sortie")
    entry_book = timeline.book_at(entry_ts)
    exit_book = timeline.book_at(exit_ts)
    if entry_book is None or exit_book is None:
        return CausalCapture(event_ts_ms, entry_ts, exit_ts, direction.value,
                             None, None, None, None, None, None, False,
                             "carnet manquant a l'entree ou a la sortie")
    entry_side = direction.taker_side
    exit_side = "bid" if direction is Direction.LONG else "ask"
    try:
        e_walk = entry_book.walk(entry_side, notional_usd)
        x_walk = exit_book.walk(exit_side, notional_usd)
        e_cost = entry_book.market_impact_bps(entry_side, notional_usd)
        x_cost = exit_book.market_impact_bps(exit_side, notional_usd)
    except (EmptyBook, ValueError) as exc:
        return CausalCapture(event_ts_ms, entry_ts, exit_ts, direction.value,
                             None, None, None, None, None, None, False, str(exc))
    if e_walk.vwap is None or x_walk.vwap is None:
        return CausalCapture(event_ts_ms, entry_ts, exit_ts, direction.value,
                             None, None, None, None, None, None, False,
                             "aucune liquidite exploitable")
    if e_walk.exhausted or x_walk.exhausted:
        return CausalCapture(event_ts_ms, entry_ts, exit_ts, direction.value,
                             e_walk.vwap, x_walk.vwap, None, e_cost, x_cost, None, False,
                             f"profondeur insuffisante (entree {e_walk.fill_ratio:.0%}, "
                             f"sortie {x_walk.fill_ratio:.0%})")
    # Mouvement brut entre mid d'entree et mid de sortie, dans le sens pris.
    sign = 1.0 if direction is Direction.LONG else -1.0
    gross = sign * (exit_book.mid - entry_book.mid) / entry_book.mid * 10_000.0
    total_cost = (e_cost or 0.0) + (x_cost or 0.0)
    return CausalCapture(event_ts_ms, entry_ts, exit_ts, direction.value,
                         e_walk.vwap, x_walk.vwap, gross, e_cost, x_cost,
                         gross - total_cost, True, "")
