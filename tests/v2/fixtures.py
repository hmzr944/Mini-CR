"""Fixtures deterministes — aucun appel reseau dans les tests unitaires."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.core_types import Provenance
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.orderbook import OrderBook

FETCHED = "2026-09-16T00:00:00Z"

BTC_INVERSE = InstrumentSpec(
    inst_id="BTC-USD-SWAP", exchange="OKX", inst_type=InstrumentType.SWAP_INVERSE,
    ct_type="inverse", base="BTC", quote="USD", settle_ccy="BTC",
    ct_val=100.0, ct_val_ccy="USD", ct_mult=1.0, tick_size=0.1, lot_size=0.1,
    min_size=0.1, state="live", lever=100.0, family="BTC-USD", fetched_at=FETCHED)

BTC_LINEAR = InstrumentSpec(
    inst_id="BTC-USDT-SWAP", exchange="OKX", inst_type=InstrumentType.SWAP_LINEAR,
    ct_type="linear", base="BTC", quote="USDT", settle_ccy="USDT",
    ct_val=0.01, ct_val_ccy="BTC", ct_mult=1.0, tick_size=0.1, lot_size=0.01,
    min_size=0.01, state="live", lever=100.0, family="BTC-USDT", fetched_at=FETCHED)

PROV = Provenance(exchange="OKX", endpoint="/market/books", fetched_at=FETCHED,
                  inst_id="BTC-USD-SWAP")


def okx_book_payload(bids: List[List[str]], asks: List[List[str]],
                     ts: str = "1789549475551") -> List[Dict[str, Any]]:
    return [{"bids": bids, "asks": asks, "ts": ts, "seqId": 42}]


def simple_inverse_book() -> OrderBook:
    """Carnet inverse simple et calculable a la main.

    ctVal=100 USD -> chaque contrat vaut 100 USD de notionnel.
    asks: 100.0 x 10 contrats = 1000 USD ; 101.0 x 20 = 2000 USD ; 102.0 x 30 = 3000 USD
    bids:  99.0 x 10 = 1000 USD ; 98.0 x 20 = 2000 USD
    mid = 99.5
    """
    return OrderBook.from_okx(
        BTC_INVERSE,
        okx_book_payload(bids=[["99.0", "10"], ["98.0", "20"]],
                         asks=[["100.0", "10"], ["101.0", "20"], ["102.0", "30"]]),
        PROV)


def thin_inverse_book() -> OrderBook:
    """Carnet volontairement mince : 1 contrat = 100 USD de chaque cote."""
    return OrderBook.from_okx(
        BTC_INVERSE, okx_book_payload(bids=[["99.0", "1"]], asks=[["100.0", "1"]]), PROV)
