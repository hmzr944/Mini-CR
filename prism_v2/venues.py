"""VenueAdapter — abstraction minimale multi-venues, lecture seule.

Objectif : permettre la famille CROSS_VENUE sans construire une usine a
integrations. On ne connecte que des venues REELLEMENT joignables et
documentees depuis cet environnement.

Constat de faisabilite (teste, date du jour) :
    OKX          joignable (REST + WebSocket)
    Hyperliquid  joignable (REST POST /info)
    Binance      HTTP 451 depuis cet environnement (restriction geographique)
    Bybit        HTTP 403 depuis cet environnement

Les venues injoignables ne sont PAS simulees : elles sont declarees
indisponibles, et la famille CROSS_VENUE le dit.

REGLE CENTRALE DE CETTE FAMILLE
On ne compare JAMAIS deux tickers affiches. On compare un prix REELLEMENT
EXECUTABLE a l'achat sur une venue et un prix REELLEMENT EXECUTABLE a la
vente sur l'autre, pour une TAILLE DONNEE, en marchant chaque carnet. Un
ecart affiche mais non executable n'est pas une opportunite.
"""
from __future__ import annotations

import abc
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .core_types import Quality, utc_now_iso

USER_AGENT = "prism-v2/2.0 (research; public-endpoints-only)"


class VenueUnavailable(RuntimeError):
    """Venue injoignable ou refusant l'acces depuis cet environnement."""


@dataclass(frozen=True)
class VenueLevel:
    price: float
    notional_usd: float


@dataclass
class VenueQuote:
    """Carnet normalise d'une venue, pour comparaison economique uniquement.

    La normalisation sert a COMPARER, jamais a fusionner : chaque venue garde
    ses frais, sa mecanique et ses contraintes propres.
    """

    venue: str
    symbol: str
    ts_ms: int
    local_recv_ts_ms: int
    bids: List[VenueLevel]
    asks: List[VenueLevel]
    #: Frais taker de la venue, en bps. Qualite ASSUMED tant qu'ils ne sont
    #: pas lus sur un compte authentifie.
    taker_fee_bps: float
    fee_quality: Quality = Quality.ASSUMED
    fee_source: str = ""
    settle_ccy: str = ""
    #: Contraintes de capital : peut-on reellement etre des deux cotes ?
    requires_prefunded_capital: bool = True
    transfer_latency_note: str = ""

    @property
    def transport_delay_ms(self) -> int:
        return self.local_recv_ts_ms - self.ts_ms

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price + self.asks[0].price) / 2.0

    def fillable(self, side: str, notional_usd: float) -> Optional[Dict[str, Any]]:
        """Prix moyen REELLEMENT obtenable pour `notional_usd`, en marchant
        le carnet. None si la profondeur ne suffit pas — on ne fabrique pas
        de liquidite absente."""
        levels = self.asks if side == "ask" else self.bids
        remaining, cost, filled, used = notional_usd, 0.0, 0.0, 0
        for lv in levels:
            if remaining <= 1e-12:
                break
            take = min(remaining, lv.notional_usd)
            cost += lv.price * take
            filled += take
            remaining -= take
            used += 1
        if filled <= 0 or remaining > 1e-9:
            return None
        return {"vwap": cost / filled, "filled_usd": filled,
                "levels": used, "depth_usd": sum(l.notional_usd for l in levels)}

    def to_dict(self) -> Dict[str, Any]:
        return {"venue": self.venue, "symbol": self.symbol, "ts_ms": self.ts_ms,
                "transport_delay_ms": self.transport_delay_ms,
                "best_bid": self.best_bid, "best_ask": self.best_ask, "mid": self.mid,
                "taker_fee_bps": self.taker_fee_bps,
                "fee_quality": self.fee_quality.value, "fee_source": self.fee_source,
                "settle_ccy": self.settle_ccy,
                "requires_prefunded_capital": self.requires_prefunded_capital}


class VenueAdapter(abc.ABC):
    name: str
    available: bool = True
    unavailable_reason: str = ""

    @abc.abstractmethod
    def quote(self, symbol: str, depth: int = 20) -> VenueQuote:
        ...

    def health(self) -> Dict[str, Any]:
        return {"venue": self.name, "available": self.available,
                "reason": self.unavailable_reason}


def _http_json(url: str, body: Optional[Dict[str, Any]] = None,
               timeout: float = 15.0) -> Dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class HyperliquidAdapter(VenueAdapter):
    """Hyperliquid — perpetuels marges en USDC, tailles en unites de base.

    Frais publics du palier de base : taker 0.045 %, maker 0.015 %.
    Marques ASSUMED : non verifies sur un compte.
    """

    name = "HYPERLIQUID"
    TAKER_FEE_BPS = 4.5
    MAKER_FEE_BPS = 1.5
    INFO_URL = "https://api.hyperliquid.xyz/info"

    def quote(self, symbol: str, depth: int = 20) -> VenueQuote:
        try:
            t0 = int(time.time() * 1000)
            data = _http_json(self.INFO_URL, {"type": "l2Book", "coin": symbol})
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            self.available = False
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            raise VenueUnavailable(f"{self.name}: {exc}") from exc
        recv = int(time.time() * 1000)
        levels = data.get("levels") or []
        if len(levels) < 2:
            raise VenueUnavailable(f"{self.name}: carnet incomplet pour {symbol}")

        def build(rows: Sequence[Dict[str, Any]]) -> List[VenueLevel]:
            out: List[VenueLevel] = []
            for r in rows[:depth]:
                try:
                    px, sz = float(r["px"]), float(r["sz"])
                except (KeyError, TypeError, ValueError):
                    continue
                if px > 0 and sz > 0:
                    # Taille en unites de base -> notionnel USD = px * sz.
                    out.append(VenueLevel(px, px * sz))
            return out

        bids = sorted(build(levels[0]), key=lambda l: -l.price)
        asks = sorted(build(levels[1]), key=lambda l: l.price)
        return VenueQuote(
            venue=self.name, symbol=symbol,
            ts_ms=int(data.get("time") or recv), local_recv_ts_ms=recv,
            bids=bids, asks=asks, taker_fee_bps=self.TAKER_FEE_BPS,
            fee_quality=Quality.ASSUMED,
            fee_source="bareme public Hyperliquid, palier de base (non authentifie)",
            settle_ccy="USDC", requires_prefunded_capital=True,
            transfer_latency_note=(
                "capital pre-positionne obligatoire : un transfert inter-venues "
                "prend des minutes, bien au-dela de la duree de vie d'un ecart"))


class OKXVenueAdapter(VenueAdapter):
    """OKX vu comme une venue parmi d'autres, pour la comparaison cross-venue.

    Les calculs internes a OKX continuent de passer par InstrumentSpec : cet
    adaptateur ne sert QU'A la comparaison inter-venues.
    """

    name = "OKX"
    TAKER_FEE_BPS = 5.0

    def __init__(self, client: Any, registry: Any):
        self.client = client
        self.registry = registry

    def quote(self, symbol: str, depth: int = 20) -> VenueQuote:
        from .contracts import usd_notional
        spec = self.registry.require(symbol)
        try:
            obs = self.client.orderbook(spec, depth=max(depth, 20))
        except Exception as exc:
            self.available = False
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
            raise VenueUnavailable(f"{self.name}: {exc}") from exc
        recv = int(time.time() * 1000)
        row = (obs.payload or [{}])[0]

        def build(rows) -> List[VenueLevel]:
            out: List[VenueLevel] = []
            for r in rows[:depth]:
                try:
                    px, sz = float(r[0]), float(r[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if px > 0 and sz > 0:
                    out.append(VenueLevel(px, usd_notional(spec, sz, px)))
            return out

        return VenueQuote(
            venue=self.name, symbol=symbol,
            ts_ms=int(row.get("ts") or recv), local_recv_ts_ms=recv,
            bids=sorted(build(row.get("bids", [])), key=lambda l: -l.price),
            asks=sorted(build(row.get("asks", [])), key=lambda l: l.price),
            taker_fee_bps=self.TAKER_FEE_BPS, fee_quality=Quality.ASSUMED,
            fee_source="bareme public OKX Lv1 (non authentifie)",
            settle_ccy=spec.settle_ccy, requires_prefunded_capital=True,
            transfer_latency_note="capital pre-positionne obligatoire")


@dataclass
class VenueRegistry:
    """Venues disponibles + journal des indisponibilites, jamais masquees."""

    adapters: Dict[str, VenueAdapter] = field(default_factory=dict)
    unavailable: Dict[str, str] = field(default_factory=dict)

    def register(self, adapter: VenueAdapter) -> "VenueRegistry":
        self.adapters[adapter.name] = adapter
        return self

    def mark_unavailable(self, name: str, reason: str) -> None:
        self.unavailable[name] = reason

    def quotes(self, mapping: Dict[str, str], depth: int = 20) -> Dict[str, VenueQuote]:
        """mapping: {nom_venue: symbole}. Une venue qui echoue est journalisee,
        elle n'interrompt pas les autres."""
        out: Dict[str, VenueQuote] = {}
        for venue, symbol in mapping.items():
            adapter = self.adapters.get(venue)
            if adapter is None:
                self.unavailable[venue] = "adaptateur non enregistre"
                continue
            try:
                out[venue] = adapter.quote(symbol, depth=depth)
            except VenueUnavailable as exc:
                self.unavailable[venue] = str(exc)
        return out

    def health(self) -> Dict[str, Any]:
        return {"available": sorted(self.adapters),
                "unavailable": dict(sorted(self.unavailable.items())),
                "checked_at": utc_now_iso()}
