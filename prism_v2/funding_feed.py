#!/usr/bin/env python3
"""FEED — collecte publique du funding et des carnets, deux venues.

Endpoints PUBLICS uniquement. Aucune cle, aucune authentification, aucun
ordre. Le feed ne fait que lire et normaliser ; il n'interprete rien.

LA CADENCE EST DEDUITE, JAMAIS SUPPOSEE. OKX expose `fundingTime` et
`nextFundingTime` : leur ecart donne la periode reelle de l'instrument. 90 des
142 instruments communs paient toutes les 4 h, les autres toutes les 8 h.
Supposer 8 h partout divisait le taux horaire par deux sur 63 % de l'univers
et fabriquait des differentiels spectaculaires inexistants. On lit la periode.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.bot import FundingObservation, MAX_BOOK_AGE_MS
from prism_v2.core_types import utc_now_iso
from prism_v2.core_types import Provenance
from prism_v2.instruments import InstrumentRegistry
from prism_v2.opp_funding import VenueFunding
from prism_v2.orderbook import OrderBook

USER_AGENT = "prism-v2/research (public endpoints only)"
OKX_BASE = "https://www.okx.com"
HL_INFO = "https://api.hyperliquid.xyz/info"
HOUR_MS = 3_600_000


class FeedError(RuntimeError):
    pass


def _http_json(url: str, body: Optional[Dict[str, Any]] = None,
               timeout: float = 25.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise FeedError(f"{url}: {type(exc).__name__}: {exc}") from exc


def okx_funding(inst_id: str) -> VenueFunding:
    """Funding OKX, avec la periode LUE sur l'instrument."""
    d = _http_json(f"{OKX_BASE}/api/v5/public/funding-rate?instId={inst_id}")
    rows = d.get("data") or []
    if not rows:
        raise FeedError(f"OKX: aucun funding pour {inst_id}")
    r = rows[0]
    try:
        ft, nft = int(r["fundingTime"]), int(r["nextFundingTime"])
        rate = float(r["fundingRate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedError(f"OKX: funding illisible pour {inst_id}") from exc
    period_h = (nft - ft) / HOUR_MS
    if period_h <= 0:
        raise FeedError(f"OKX: cadence non deductible pour {inst_id}")
    return VenueFunding("OKX", rate, period_h, ft)


def hyperliquid_universe() -> Dict[str, Dict[str, Any]]:
    """Univers Hyperliquid + funding horaire, en UN appel."""
    d = _http_json(HL_INFO, {"type": "metaAndAssetCtxs"})
    if not isinstance(d, list) or len(d) < 2:
        raise FeedError("Hyperliquid: reponse metaAndAssetCtxs inattendue")
    uni, ctx = d[0].get("universe") or [], d[1]
    now = int(time.time() * 1000)
    out: Dict[str, Dict[str, Any]] = {}
    for u, c in zip(uni, ctx):
        if u.get("isDelisted"):
            continue
        try:
            px = float(c["markPx"])
            out[u["name"]] = {
                "funding": VenueFunding("HYPERLIQUID", float(c["funding"]),
                                        1.0, now),
                "mark_px": px,
                "oi_usd": float(c.get("openInterest", 0.0)) * px,
                "vol24_usd": float(c.get("dayNtlVlm", 0.0)),
            }
        except (KeyError, TypeError, ValueError):
            continue          # un actif illisible est ignore, jamais devine
    if not out:
        raise FeedError("Hyperliquid: univers vide")
    return out


def okx_book(spec, depth: int = 20) -> OrderBook:
    path = f"/api/v5/market/books?instId={spec.inst_id}&sz={depth}"
    recv = int(time.time() * 1000)
    d = _http_json(f"{OKX_BASE}{path}")
    rows = d.get("data") or []
    if not rows:
        raise FeedError(f"OKX: carnet absent pour {spec.inst_id}")
    prov = Provenance(exchange="OKX", endpoint=path, fetched_at=utc_now_iso(),
                      inst_id=spec.inst_id)
    return OrderBook.from_okx(spec, rows, prov, local_recv_ts_ms=recv)


@dataclass
class CrossVenueFeed:
    """Collecte les actifs cotes sur OKX ET Hyperliquid.

    `max_symbols` borne le nombre d'appels par cycle. `book_for_top` limite
    la collecte de carnets aux candidates les plus ecartees : un carnet coute
    un aller-retour reseau, et 142 carnets par cycle seraient du gaspillage
    pour des candidates que l'economie rejettera de toute facon.
    """

    registry: Optional[InstrumentRegistry] = None
    max_symbols: int = 60
    book_for_top: int = 8
    min_abs_apr_for_book: float = 0.20

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = self._load_okx_registry()
        self._okx_ids = {
            spec.base: spec.inst_id
            for spec in self.registry.instruments.values()
            if spec.inst_id.endswith("-USDT-SWAP")
        }

    @staticmethod
    def _load_okx_registry() -> InstrumentRegistry:
        path = "/api/v5/public/instruments?instType=SWAP"
        d = _http_json(f"{OKX_BASE}{path}")
        rows = d.get("data") or []
        if not rows:
            raise FeedError("OKX: liste d'instruments vide")
        prov = Provenance(exchange="OKX", endpoint=path,
                          fetched_at=utc_now_iso())
        return InstrumentRegistry.from_okx_payload(rows, prov)

    def snapshot(self) -> List[FundingObservation]:
        hl = hyperliquid_universe()
        common = sorted(set(hl) & set(self._okx_ids))
        # On priorise par |funding HL| : c'est le seul signal disponible
        # AVANT d'avoir paye l'appel OKX. Ce n'est pas une selection sur le
        # resultat, c'est un ordre de visite.
        common.sort(key=lambda s: -abs(hl[s]["funding"].apr))
        common = common[: self.max_symbols]

        obs: List[FundingObservation] = []
        for sym in common:
            inst_id = self._okx_ids[sym]
            try:
                near = okx_funding(inst_id)
            except FeedError:
                continue
            spec = self.registry.get(inst_id)
            if spec is None:
                continue     # absent du registre : on ne suppose rien
            obs.append(FundingObservation(
                symbol=sym, spec=spec, near=near, far=hl[sym]["funding"],
                capacity_usd=hl[sym]["oi_usd"]))

        # Carnets seulement pour les ecarts les plus larges.
        obs.sort(key=lambda o: -abs(o.far.apr - o.near.apr))
        for o in obs[: self.book_for_top]:
            if abs(o.far.apr - o.near.apr) < self.min_abs_apr_for_book:
                break
            try:
                o.book = okx_book(o.spec)
            except FeedError:
                o.book = None          # absence de carnet => UNRESOLVED
        return obs
