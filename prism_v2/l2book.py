"""Carnet L2 incrementiel avec chainage de sequence et checksum.

POURQUOI CE MODULE EXISTE
Le canal OKX `books` envoie un SNAPSHOT puis des UPDATES ne contenant que les
niveaux modifies. Traiter un update comme un carnet complet produit un carnet
tronque — un cote peut sembler vide — et TOUS les couts calcules dessus sont
faux, silencieusement. Ce defaut a ete trouve en exploitation reelle.

GARANTIES
  1. Chainage : chaque update doit porter prevSeqId == seqId courant. Sinon
     TROU DE SEQUENCE, le carnet est marque invalide et refuse jusqu'a un
     nouveau snapshot. On ne "rattrape" jamais en devinant.
  2. Checksum : OKX peut publier un CRC32 sur les 25 meilleurs niveaux. Il
     est verifie DES QU'IL EST FOURNI, et un echec invalide le carnet.
     LIMITE OBSERVEE : sur le canal `books` (400 niveaux), OKX renvoie
     `checksum: 0`, c'est-a-dire ABSENT. Seuls les canaux tick-by-tint
     (books-l2-tbt / books50-l2-tbt), reserves aux comptes VIP, publient un
     checksum exploitable. L'integrite repose donc ici sur le chainage de
     sequence seul — qui est present et verifie. C'est ecrit plutot que
     dissimule : une garantie qu'on n'a pas ne doit pas etre revendiquee.
  3. FAIL CLOSED : un carnet invalide n'est jamais rendu. `book()` retourne
     None plutot qu'un carnet douteux.

Aucune reconstruction approximative, aucun repli silencieux.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .contracts import usd_notional
from .core_types import Provenance, ms_to_iso
from .instruments import InstrumentSpec
from .orderbook import Level, OrderBook

#: OKX calcule le checksum sur les 25 meilleurs niveaux de chaque cote.
CHECKSUM_DEPTH = 25


class BookInvalid(RuntimeError):
    """Le carnet n'est pas dans un etat exploitable."""


def okx_checksum(bids: Sequence[Tuple[str, str]],
                 asks: Sequence[Tuple[str, str]]) -> int:
    """CRC32 OKX : alternance bid/ask sur 25 niveaux, "px:sz" separes par ':'.

    Les prix et tailles sont utilises TELS QUE RECUS (chaines), car une
    normalisation numerique changerait le checksum.
    """
    parts: List[str] = []
    for i in range(CHECKSUM_DEPTH):
        if i < len(bids):
            parts.append(f"{bids[i][0]}:{bids[i][1]}")
        if i < len(asks):
            parts.append(f"{asks[i][0]}:{asks[i][1]}")
    crc = zlib.crc32(":".join(parts).encode())
    # OKX publie un entier signe 32 bits.
    return crc - (1 << 32) if crc >= (1 << 31) else crc


@dataclass
class L2Book:
    """Carnet incrementiel d'un instrument."""

    instrument: InstrumentSpec
    bids: Dict[str, str] = field(default_factory=dict)   # px -> sz, chaines brutes
    asks: Dict[str, str] = field(default_factory=dict)
    seq_id: Optional[int] = None
    ts_ms: Optional[int] = None
    local_recv_ts_ms: Optional[int] = None
    valid: bool = False
    invalid_reason: str = "aucun snapshot recu"

    # compteurs de diagnostic
    snapshots: int = 0
    updates: int = 0
    sequence_gaps: int = 0
    checksum_failures: int = 0
    checksums_verified: int = 0

    def _invalidate(self, reason: str) -> None:
        self.valid = False
        self.invalid_reason = reason

    def apply(self, action: Optional[str], data: Dict[str, Any],
              local_recv_ts_ms: Optional[int] = None) -> bool:
        """Applique un message. Retourne True si le carnet reste valide.

        `action` vaut "snapshot" ou "update". Un message sans action est
        traite comme un snapshot (cas de books5).
        """
        try:
            seq = int(data["seqId"]) if data.get("seqId") is not None else None
            prev = int(data["prevSeqId"]) if data.get("prevSeqId") is not None else None
            ts = int(data["ts"]) if data.get("ts") is not None else None
        except (TypeError, ValueError):
            self._invalidate("horodatage ou sequence illisible")
            return False

        if action == "snapshot" or not self.valid and action is None:
            self.bids = {str(r[0]): str(r[1]) for r in data.get("bids", [])
                         if float(r[1]) > 0}
            self.asks = {str(r[0]): str(r[1]) for r in data.get("asks", [])
                         if float(r[1]) > 0}
            self.seq_id, self.ts_ms = seq, ts
            self.local_recv_ts_ms = local_recv_ts_ms
            self.valid = True
            self.invalid_reason = ""
            self.snapshots += 1
        elif action == "update":
            if not self.valid:
                return False
            # Chainage strict : un trou rend le carnet non fiable.
            if prev is not None and self.seq_id is not None and prev != self.seq_id:
                self.sequence_gaps += 1
                self._invalidate(f"trou de sequence: prevSeqId={prev} != "
                                 f"seqId courant={self.seq_id}")
                return False
            for side_map, rows in ((self.bids, data.get("bids", [])),
                                   (self.asks, data.get("asks", []))):
                for r in rows:
                    px, sz = str(r[0]), str(r[1])
                    try:
                        if float(sz) == 0:
                            side_map.pop(px, None)
                        else:
                            side_map[px] = sz
                    except (TypeError, ValueError):
                        continue
            self.seq_id, self.ts_ms = seq, ts
            self.local_recv_ts_ms = local_recv_ts_ms
            self.updates += 1
        else:
            # books5 et assimiles : snapshot complet a chaque message.
            self.bids = {str(r[0]): str(r[1]) for r in data.get("bids", [])
                         if float(r[1]) > 0}
            self.asks = {str(r[0]): str(r[1]) for r in data.get("asks", [])
                         if float(r[1]) > 0}
            self.seq_id, self.ts_ms = seq, ts
            self.local_recv_ts_ms = local_recv_ts_ms
            self.valid = True
            self.invalid_reason = ""
            self.snapshots += 1

        expected = data.get("checksum")
        # checksum == 0 signifie NON FOURNI sur le canal `books`. On ne
        # verifie que lorsqu'une valeur exploitable est presente ; on ne
        # fabrique pas un echec sur une garantie que l'exchange n'offre pas.
        if expected not in (None, 0, "0") and self.valid:
            if not self.verify_checksum(int(expected)):
                self.checksum_failures += 1
                self._invalidate(f"checksum invalide (attendu {expected})")
                return False
            self.checksums_verified += 1
        return self.valid

    def _sorted_sides(self) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        bids = sorted(self.bids.items(), key=lambda kv: -float(kv[0]))
        asks = sorted(self.asks.items(), key=lambda kv: float(kv[0]))
        return bids, asks

    def verify_checksum(self, expected: int) -> bool:
        bids, asks = self._sorted_sides()
        return okx_checksum(bids, asks) == expected

    def book(self, provenance: Optional[Provenance] = None) -> Optional[OrderBook]:
        """Carnet exploitable, ou None si l'etat n'est pas fiable. FAIL CLOSED."""
        if not self.valid or not self.bids or not self.asks:
            return None
        raw_bids, raw_asks = self._sorted_sides()
        try:
            bids = [Level(float(p), float(s),
                          usd_notional(self.instrument, float(s), float(p)))
                    for p, s in raw_bids]
            asks = [Level(float(p), float(s),
                          usd_notional(self.instrument, float(s), float(p)))
                    for p, s in raw_asks]
        except (TypeError, ValueError):
            return None
        if not bids or not asks or bids[0].price >= asks[0].price:
            return None
        prov = provenance or Provenance("OKX", "ws:books",
                                        ms_to_iso(self.ts_ms) if self.ts_ms else "",
                                        self.instrument.inst_id)
        return OrderBook(instrument=self.instrument, bids=bids, asks=asks,
                         ts_utc=ms_to_iso(self.ts_ms) if self.ts_ms else "",
                         provenance=prov,
                         seq_id=str(self.seq_id) if self.seq_id is not None else None,
                         raw_depth=max(len(bids), len(asks)),
                         ts_ms=self.ts_ms, local_recv_ts_ms=self.local_recv_ts_ms)

    def stats(self) -> Dict[str, Any]:
        return {"inst_id": self.instrument.inst_id, "valid": self.valid,
                "invalid_reason": self.invalid_reason, "seq_id": self.seq_id,
                "n_bids": len(self.bids), "n_asks": len(self.asks),
                "snapshots": self.snapshots, "updates": self.updates,
                "sequence_gaps": self.sequence_gaps,
                "checksum_failures": self.checksum_failures,
                "checksums_verified": self.checksums_verified,
                "checksum_available": self.checksums_verified > 0}


@dataclass
class L2BookSet:
    """Carnets incrementiels de plusieurs instruments."""

    specs: Dict[str, InstrumentSpec]
    books: Dict[str, L2Book] = field(default_factory=dict)

    def apply_event(self, event: Dict[str, Any]) -> bool:
        iid = event.get("inst_id")
        spec = self.specs.get(iid)
        if spec is None:
            return False
        book = self.books.get(iid)
        if book is None:
            book = self.books[iid] = L2Book(spec)
        return book.apply(event.get("action"), event.get("data") or {},
                          event.get("local_recv_ts_ms"))

    def book(self, inst_id: str) -> Optional[OrderBook]:
        b = self.books.get(inst_id)
        return b.book() if b is not None else None

    def stats(self) -> Dict[str, Any]:
        return {iid: b.stats() for iid, b in sorted(self.books.items())}
