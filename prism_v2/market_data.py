"""Acces marche public OKX. Aucune cle, aucun endpoint prive.

Separation stricte : ce module RAPPORTE le marche, il ne l'interprete pas.
Aucune logique d'opportunite ici. Toute donnee sortante est encapsulee dans
une Observation qui porte timestamp, exchange, instrument et source.

Dependance : urllib (stdlib). Un User-Agent explicite est requis par
certains proxys sortants.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Provenance, utc_now_iso
from .instruments import Instrument, InstrumentRegistry

OKX_BASE = "https://www.okx.com/api/v5"
USER_AGENT = "prism-v2/2.0 (research; public-endpoints-only)"


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Observation:
    """Enveloppe obligatoire de toute donnee de marche.

    Conserve : timestamp, exchange, instrument, source. Sans cela, une mesure
    n'est pas reproductible et n'a pas sa place dans le ledger.
    """

    ts_utc: str                      # horodatage exchange si fourni, sinon local
    exchange: str
    inst_id: Optional[str]
    source: str                      # endpoint exact
    payload: Any
    provenance: Provenance
    received_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_utc": self.ts_utc,
            "exchange": self.exchange,
            "inst_id": self.inst_id,
            "source": self.source,
            "received_at": self.received_at,
            "provenance": self.provenance.to_dict(),
            "payload": self.payload,
        }


class OKXPublicClient:
    """Client HTTP minimal, lecture seule, endpoints publics uniquement.

    Il n'expose aucune methode d'ordre : il n'y a pas de chemin de code de
    ce client vers une operation de trading.
    """

    def __init__(self, base_url: str = OKX_BASE, timeout: float = 20.0,
                 max_retries: int = 3, pause: float = 0.15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.pause = pause
        self.request_count = 0

    # ---- transport -----------------------------------------------------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.load(resp)
                self.request_count += 1
                if body.get("code") not in ("0", 0):
                    raise MarketDataError(f"OKX code={body.get('code')} msg={body.get('msg')!r} url={url}")
                time.sleep(self.pause)
                return body
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_err = exc
                if attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise MarketDataError(f"echec apres {self.max_retries} tentatives: {url} ({last_err})")

    def _observe(self, path: str, body: Dict[str, Any], inst_id: Optional[str],
                 ts_utc: Optional[str] = None) -> Observation:
        fetched = utc_now_iso()
        return Observation(
            ts_utc=ts_utc or fetched,
            exchange="OKX",
            inst_id=inst_id,
            source=path,
            payload=body.get("data"),
            provenance=Provenance(exchange="OKX", endpoint=path, fetched_at=fetched,
                                  inst_id=inst_id),
        )

    # ---- endpoints -----------------------------------------------------------
    def server_time(self) -> Observation:
        return self._observe("/public/time", self._get("/public/time"), None)

    def instruments(self, inst_type: str = "SWAP") -> Observation:
        path = "/public/instruments"
        body = self._get(path, {"instType": inst_type})
        obs = self._observe(path, body, None)
        object.__setattr__(obs, "provenance",
                           Provenance(exchange="OKX", endpoint=path,
                                      fetched_at=obs.provenance.fetched_at,
                                      extra={"instType": inst_type}))
        return obs

    def load_registry(self, inst_types: Optional[List[str]] = None) -> InstrumentRegistry:
        """Construit un Registry a partir de l'exchange, maintenant."""
        registry = InstrumentRegistry()
        for it in (inst_types or ["SWAP", "SPOT"]):
            obs = self.instruments(it)
            registry.merge(InstrumentRegistry.from_okx_payload(obs.payload or [],
                                                               provenance=obs.provenance))
        return registry

    def ticker(self, instrument: Instrument) -> Observation:
        path = "/market/ticker"
        body = self._get(path, {"instId": instrument.inst_id})
        rows = body.get("data") or []
        ts = rows[0].get("ts") if rows else None
        from .core_types import ms_to_iso
        return self._observe(path, body, instrument.inst_id,
                             ts_utc=ms_to_iso(ts) if ts else None)

    def orderbook(self, instrument: Instrument, depth: int = 50) -> Observation:
        """Carnet L2 agrege. depth<=400 sur /market/books."""
        path = "/market/books"
        body = self._get(path, {"instId": instrument.inst_id, "sz": str(depth)})
        rows = body.get("data") or []
        ts = rows[0].get("ts") if rows else None
        from .core_types import ms_to_iso
        return self._observe(path, body, instrument.inst_id,
                             ts_utc=ms_to_iso(ts) if ts else None)

    def candles(self, instrument: Instrument, bar: str = "1m", limit: int = 100) -> Observation:
        path = "/market/candles"
        body = self._get(path, {"instId": instrument.inst_id, "bar": bar, "limit": str(limit)})
        return self._observe(path, body, instrument.inst_id)

    def funding_rate(self, instrument: Instrument) -> Observation:
        """Funding courant. N'a de sens que pour un SWAP — on refuse le spot
        explicitement plutot que de retourner une valeur trompeuse."""
        if not instrument.inst_type.is_swap:
            raise MarketDataError(f"{instrument.inst_id} n'est pas un swap : pas de funding")
        path = "/public/funding-rate"
        body = self._get(path, {"instId": instrument.inst_id})
        return self._observe(path, body, instrument.inst_id)

    def liquidation_orders(self, instrument: Instrument, limit: int = 100) -> Observation:
        """Liquidations publiques recentes pour la famille de l'instrument."""
        path = "/public/liquidation-orders"
        params = {"instType": "SWAP", "state": "filled",
                  "uly": instrument.family or f"{instrument.base}-{instrument.quote}",
                  "limit": str(limit)}
        body = self._get(path, params)
        return self._observe(path, body, instrument.inst_id)
