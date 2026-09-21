"""MarketState — etat de marche microstructurel, sur lequel les detecteurs raisonnent.

REGLE : aucune feature n'est un indicateur technique. Pas de RSI, EMA, MACD,
Bollinger, ADX, ni de score agrege. Toutes les grandeurs decrivent l'ETAT DU
CARNET ET DU FLUX a un instant, pas la forme passee des prix.

Distinction a ne jamais perdre : une FEATURE n'est pas un ALPHA. Ce module
mesure ; il ne predit rien et ne retourne aucune direction. C'est le Capture
Engine, apres replay causal, qui dit si une situation est capturable.

Toutes les conversions de taille passent par InstrumentSpec via contracts.py :
un niveau de carnet inverse et un niveau lineaire n'ont pas le meme notionnel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence
from collections import deque

from .instruments import InstrumentSpec
from .orderbook import EmptyBook, OrderBook

#: Tailles de sondage pour la courbe de cout, en USD. Grille d'OBSERVATION.
PROBE_SIZES_USD = (100.0, 1_000.0, 10_000.0, 100_000.0)


@dataclass(frozen=True)
class TradePrint:
    ts_ms: int
    price: float
    size_native: float
    notional_usd: float
    is_buy: bool          # True = l'agresseur a achete (a lifte l'ask)


@dataclass(frozen=True)
class ForcedFlowEvent:
    """Liquidation publique : un flux CONTRAINT, pas une opinion de marche."""

    ts_ms: int
    price: float
    size_native: float
    notional_usd: float
    #: side="sell" => une position LONGUE a ete liquidee => vente forcee.
    side: str

    @property
    def pushes_price_down(self) -> bool:
        return self.side == "sell"


@dataclass
class MarketState:
    """Photographie de l'etat de marche d'un instrument, a un instant."""

    instrument: InstrumentSpec
    ts_ms: int
    book: OrderBook

    # ── flux recent (fenetre glissante) ──────────────────────────────────
    trades: List[TradePrint] = field(default_factory=list)
    forced_flow: List[ForcedFlowEvent] = field(default_factory=list)
    mid_history: List[tuple] = field(default_factory=list)   # (ts_ms, mid)
    depth_history: List[tuple] = field(default_factory=list) # (ts_ms, bid_usd, ask_usd)
    #: (ts_ms, spread_bps). AJOUTE le 21/09/2026 : DepthWithdrawalDetector
    #: pretendait comparer le spread courant a son historique, mais aucune
    #: structure ne portait ce passe. Il substituait le spread COURANT a chaque
    #: observation passee, rendant sa porte vide et le detecteur muet pour
    #: toujours. Voir AUTOPSIE_FAMILLES.md.
    spread_history: List[tuple] = field(default_factory=list)

    # ── contexte externe ─────────────────────────────────────────────────
    funding_rate: Optional[float] = None
    funding_next_ts_ms: Optional[int] = None
    peers: Dict[str, "MarketState"] = field(default_factory=dict)   # autres instruments
    venue_quotes: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # autres venues

    window_ms: int = 60_000

    # ══ niveau 1 : prix ═══════════════════════════════════════════════════
    @property
    def mid(self) -> float:
        return self.book.mid

    @property
    def spread_bps(self) -> float:
        return self.book.spread_bps

    @property
    def microprice(self) -> Optional[float]:
        """Mid pondere par les tailles opposees au touch.

        Definition standard : (bid*ask_sz + ask*bid_sz)/(bid_sz+ask_sz).
        C'est une mesure de PRESSION du carnet, pas une prevision de prix.
        """
        try:
            b, a = self.book.bids[0], self.book.asks[0]
        except (IndexError, AttributeError):
            return None
        tot = b.notional_usd + a.notional_usd
        if tot <= 0:
            return None
        return (b.price * a.notional_usd + a.price * b.notional_usd) / tot

    @property
    def microprice_deviation_bps(self) -> Optional[float]:
        mp = self.microprice
        if mp is None or self.mid <= 0:
            return None
        return (mp - self.mid) / self.mid * 10_000.0

    # ══ niveau 2 : profondeur ═════════════════════════════════════════════
    def depth_usd(self, side: str, levels: Optional[int] = None) -> float:
        return self.book.depth(side, levels)

    @property
    def depth_imbalance(self) -> Optional[float]:
        """(bid - ask) / (bid + ask) sur la profondeur totale affichee.

        Dans [-1, 1]. MESURE de desequilibre, jamais un signal directionnel.
        """
        b, a = self.depth_usd("bid"), self.depth_usd("ask")
        if b + a <= 0:
            return None
        return (b - a) / (b + a)

    def book_slope_bps_per_usd(self, side: str, notional_usd: float = 100_000.0
                               ) -> Optional[float]:
        """Cout marginal de traversee par USD supplementaire consomme.

        Mesure la RAIDEUR du carnet : combien coute d'aller plus profond.
        """
        try:
            c = self.book.market_impact_bps(side, notional_usd)
        except (EmptyBook, ValueError):
            return None
        if c is None or notional_usd <= 0:
            return None
        return c / notional_usd

    def cost_curve(self, side: str,
                   sizes: Sequence[float] = PROBE_SIZES_USD) -> Dict[float, Optional[float]]:
        out: Dict[float, Optional[float]] = {}
        for s in sizes:
            try:
                out[s] = self.book.market_impact_bps(side, s)
            except (EmptyBook, ValueError):
                out[s] = None
        return out

    @property
    def executable_vs_mid_bps(self) -> Optional[float]:
        """Ecart entre le mid affiche et le prix REELLEMENT executable.

        Un mid peut etre trompeur quand le carnet est mince : cette grandeur
        dit de combien.
        """
        try:
            return self.book.market_impact_bps("ask", 1_000.0)
        except (EmptyBook, ValueError):
            return None

    # ══ niveau 3 : dynamique de profondeur ════════════════════════════════
    def depth_change_ratio(self, side: str, lookback_ms: int = 5_000) -> Optional[float]:
        """Profondeur actuelle / profondeur il y a `lookback_ms`.

        < 1 = retrait de liquidite. > 1 = reconstitution.
        Retourne None si l'historique ne couvre pas la fenetre : on ne
        compare pas a une valeur qu'on n'a pas.
        """
        if not self.depth_history:
            return None
        target = self.ts_ms - lookback_ms
        past = [h for h in self.depth_history if h[0] <= target]
        if not past:
            return None
        _, pb, pa = past[-1]
        prev = pb if side == "bid" else pa
        cur = self.depth_usd(side)
        if prev <= 0:
            return None
        return cur / prev

    def realized_displacement_bps(self, lookback_ms: int = 5_000) -> Optional[float]:
        """Deplacement net du mid sur la fenetre. Signe."""
        if not self.mid_history:
            return None
        target = self.ts_ms - lookback_ms
        past = [h for h in self.mid_history if h[0] <= target]
        if not past:
            return None
        prev = past[-1][1]
        if prev <= 0:
            return None
        return (self.mid - prev) / prev * 10_000.0

    def realized_volatility_bps(self, lookback_ms: int = 30_000) -> Optional[float]:
        """Ecart-type des variations de mid successives, en bps.

        Volatilite REALISEE courte, mesuree sur les observations disponibles.
        None si moins de 3 points : une variance sur 2 points n'a pas de sens.
        """
        pts = [m for m in self.mid_history if m[0] >= self.ts_ms - lookback_ms]
        if len(pts) < 3:
            return None
        rets = [(b[1] - a[1]) / a[1] * 10_000.0
                for a, b in zip(pts, pts[1:]) if a[1] > 0]
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var)

    # ══ niveau 4 : flux agressif ══════════════════════════════════════════
    def _recent_trades(self, lookback_ms: int) -> List[TradePrint]:
        return [t for t in self.trades if t.ts_ms >= self.ts_ms - lookback_ms]

    def aggressive_flow_usd(self, lookback_ms: int = 5_000) -> Dict[str, float]:
        recent = self._recent_trades(lookback_ms)
        buy = sum(t.notional_usd for t in recent if t.is_buy)
        sell = sum(t.notional_usd for t in recent if not t.is_buy)
        return {"buy_usd": buy, "sell_usd": sell, "net_usd": buy - sell,
                "total_usd": buy + sell, "n": len(recent)}

    def aggressive_imbalance(self, lookback_ms: int = 5_000) -> Optional[float]:
        f = self.aggressive_flow_usd(lookback_ms)
        if f["total_usd"] <= 0:
            return None
        return f["net_usd"] / f["total_usd"]

    def trade_intensity(self, lookback_ms: int = 5_000) -> float:
        """Impressions par seconde sur la fenetre."""
        n = len(self._recent_trades(lookback_ms))
        return n / (lookback_ms / 1000.0) if lookback_ms > 0 else 0.0

    def trade_to_book_ratio(self, lookback_ms: int = 5_000) -> Optional[float]:
        """Volume agressif rapporte a la profondeur affichee.

        Eleve = le flux consomme le carnet plus vite qu'il ne se reconstitue.
        """
        total = self.aggressive_flow_usd(lookback_ms)["total_usd"]
        depth = self.depth_usd("bid") + self.depth_usd("ask")
        if depth <= 0:
            return None
        return total / depth

    def recent_forced_flow(self, lookback_ms: int = 10_000) -> List[ForcedFlowEvent]:
        return [f for f in self.forced_flow if f.ts_ms >= self.ts_ms - lookback_ms]

    # ══ serialisation ═════════════════════════════════════════════════════
    def snapshot(self) -> Dict[str, Any]:
        """Etat resume, ecrit au ledger : sans lui, un rejet n'est pas analysable."""
        flow = self.aggressive_flow_usd()
        return {
            "inst_id": self.instrument.inst_id,
            "inst_type": self.instrument.inst_type.value,
            "ts_ms": self.ts_ms,
            "mid": self.mid,
            "spread_bps": self.spread_bps,
            "microprice_deviation_bps": self.microprice_deviation_bps,
            "depth_bid_usd": self.depth_usd("bid"),
            "depth_ask_usd": self.depth_usd("ask"),
            "depth_imbalance": self.depth_imbalance,
            "executable_vs_mid_bps": self.executable_vs_mid_bps,
            "depth_change_ratio_bid_5s": self.depth_change_ratio("bid"),
            "depth_change_ratio_ask_5s": self.depth_change_ratio("ask"),
            "displacement_bps_5s": self.realized_displacement_bps(),
            "realized_vol_bps_30s": self.realized_volatility_bps(),
            "aggressive_imbalance_5s": self.aggressive_imbalance(),
            "trade_intensity_5s": self.trade_intensity(),
            "trade_to_book_ratio_5s": self.trade_to_book_ratio(),
            "n_forced_flow_10s": len(self.recent_forced_flow()),
            "funding_rate": self.funding_rate,
            "book_seq_id": self.book.seq_id,
            "book_age_available": self.book.ts_ms is not None,
        }


@dataclass
class MarketStateTracker:
    """Maintient les fenetres glissantes par instrument, au fil des evenements.

    Ne decide rien. Ne detecte rien. Il tient l'etat a jour, c'est tout.
    """

    window_ms: int = 60_000
    max_points: int = 5_000
    books: Dict[str, OrderBook] = field(default_factory=dict)
    trades: Dict[str, Deque[TradePrint]] = field(default_factory=dict)
    forced: Dict[str, Deque[ForcedFlowEvent]] = field(default_factory=dict)
    mids: Dict[str, Deque[tuple]] = field(default_factory=dict)
    depths: Dict[str, Deque[tuple]] = field(default_factory=dict)
    spreads: Dict[str, Deque] = field(default_factory=dict)
    funding: Dict[str, float] = field(default_factory=dict)

    def _dq(self, store: Dict[str, Deque], key: str) -> Deque:
        if key not in store:
            store[key] = deque(maxlen=self.max_points)
        return store[key]

    def _prune(self, dq: Deque, now_ms: int, ts_getter) -> None:
        cutoff = now_ms - self.window_ms
        while dq and ts_getter(dq[0]) < cutoff:
            dq.popleft()

    def on_book(self, book: OrderBook) -> None:
        iid = book.instrument.inst_id
        self.books[iid] = book
        if book.ts_ms is None:
            return
        try:
            mid, bd, ad = book.mid, book.bid_depth(), book.ask_depth()
        except (EmptyBook, ValueError):
            return
        m = self._dq(self.mids, iid)
        d = self._dq(self.depths, iid)
        sp = self._dq(self.spreads, iid)
        m.append((book.ts_ms, mid))
        d.append((book.ts_ms, bd, ad))
        sp.append((book.ts_ms, book.spread_bps))
        self._prune(m, book.ts_ms, lambda x: x[0])
        self._prune(d, book.ts_ms, lambda x: x[0])
        self._prune(sp, book.ts_ms, lambda x: x[0])

    def on_trade(self, inst_id: str, trade: TradePrint) -> None:
        dq = self._dq(self.trades, inst_id)
        dq.append(trade)
        self._prune(dq, trade.ts_ms, lambda t: t.ts_ms)

    def on_forced_flow(self, inst_id: str, ev: ForcedFlowEvent) -> None:
        dq = self._dq(self.forced, inst_id)
        dq.append(ev)
        self._prune(dq, ev.ts_ms, lambda e: e.ts_ms)

    def on_funding(self, inst_id: str, rate: float) -> None:
        self.funding[inst_id] = rate

    def state(self, inst_id: str, now_ms: Optional[int] = None) -> Optional[MarketState]:
        book = self.books.get(inst_id)
        if book is None:
            return None
        ts = now_ms if now_ms is not None else (book.ts_ms or 0)
        return MarketState(
            instrument=book.instrument, ts_ms=ts, book=book,
            trades=list(self.trades.get(inst_id, [])),
            forced_flow=list(self.forced.get(inst_id, [])),
            mid_history=list(self.mids.get(inst_id, [])),
            depth_history=list(self.depths.get(inst_id, [])),
            spread_history=list(self.spreads.get(inst_id, [])),
            funding_rate=self.funding.get(inst_id), window_ms=self.window_ms)

    def all_states(self, now_ms: Optional[int] = None) -> Dict[str, MarketState]:
        out: Dict[str, MarketState] = {}
        for iid in self.books:
            st = self.state(iid, now_ms)
            if st is not None:
                out[iid] = st
        for st in out.values():
            st.peers = {k: v for k, v in out.items() if k != st.instrument.inst_id}
        return out
