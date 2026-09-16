"""Collecteur L2 controle. Duree et cadence configurables.

Ne bloque jamais le build : une duree courte suffit au smoke test, et la
meme commande peut ensuite tourner des heures pour constituer un echantillon
couvrant plusieurs regimes. Les snapshots sont ecrits en JSONL au fil de
l'eau, donc une collecte interrompue reste exploitable.

RAPPEL METHODOLOGIQUE : une fenetre courte donne un PLANCHER DE COUT OBSERVE
sur cette fenetre, jamais une verite sur tous les regimes. Chaque snapshot
porte son horodatage pour que la fenetre reelle soit toujours verifiable.
"""
from __future__ import annotations

import json
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .core_types import utc_now_iso
from .instruments import InstrumentSpec
from .market_data import MarketDataError, OKXPublicClient
from .orderbook import EmptyBook, OrderBook

DEFAULT_SNAPSHOT_DIR = Path(__file__).parent / "data" / "l2_snapshots"


@dataclass
class CollectionStats:
    started_at: str
    ended_at: Optional[str] = None
    snapshots: int = 0
    errors: int = 0
    cycles: int = 0
    per_instrument: Dict[str, int] = field(default_factory=dict)
    error_detail: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"started_at": self.started_at, "ended_at": self.ended_at,
                "cycles": self.cycles, "snapshots": self.snapshots,
                "errors": self.errors, "per_instrument": dict(sorted(self.per_instrument.items())),
                "error_detail": self.error_detail[:20]}


class L2Collector:
    def __init__(self, client: OKXPublicClient, out_dir: Path = DEFAULT_SNAPSHOT_DIR,
                 depth: int = 50, levels_stored: int = 25):
        self.client = client
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.depth = depth
        self.levels_stored = levels_stored
        self._stop = False

    def request_stop(self, *_: Any) -> None:
        self._stop = True

    def collect(self, instruments: Sequence[InstrumentSpec], duration_s: float,
                interval_s: float = 5.0,
                out_file: Optional[Path] = None) -> tuple[Path, CollectionStats]:
        """Collecte pendant `duration_s`, un cycle tous les `interval_s`.

        Interruptible proprement (SIGINT/SIGTERM) : les snapshots deja ecrits
        restent valides.
        """
        if duration_s <= 0:
            raise ValueError("duration_s doit etre > 0")
        if not instruments:
            raise ValueError("aucun instrument a collecter")

        path = Path(out_file) if out_file else (
            self.out_dir / f"l2_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl")
        stats = CollectionStats(started_at=utc_now_iso())

        previous: Dict[str, Any] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[str(sig)] = signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                pass  # pas dans le thread principal

        deadline = time.monotonic() + duration_s
        try:
            with open(path, "a", encoding="utf-8") as fh:
                while time.monotonic() < deadline and not self._stop:
                    stats.cycles += 1
                    for spec in instruments:
                        if self._stop or time.monotonic() >= deadline:
                            break
                        try:
                            obs = self.client.orderbook(spec, depth=self.depth)
                            book = OrderBook.from_okx(spec, obs.payload, obs.provenance)
                            fh.write(json.dumps(book.snapshot_dict(self.levels_stored),
                                                ensure_ascii=False, default=str) + "\n")
                            fh.flush()
                            stats.snapshots += 1
                            stats.per_instrument[spec.inst_id] = \
                                stats.per_instrument.get(spec.inst_id, 0) + 1
                        except (MarketDataError, EmptyBook, ValueError) as exc:
                            stats.errors += 1
                            stats.error_detail.append(f"{spec.inst_id}: {type(exc).__name__}: {exc}")
                    remaining = deadline - time.monotonic()
                    if remaining > 0 and not self._stop:
                        time.sleep(min(interval_s, remaining))
        finally:
            for sig_s, handler in previous.items():
                try:
                    signal.signal(int(sig_s), handler)
                except (ValueError, OSError, TypeError):
                    pass
            stats.ended_at = utc_now_iso()
        return path, stats


def load_snapshots(path: Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def spread_statistics(snapshots: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Statistiques de spread par instrument, sur la fenetre COLLECTEE.

    Pas de moyenne seule : min/median/max/n, pour que la dispersion et la
    taille d'echantillon restent visibles.
    """
    by_inst: Dict[str, List[float]] = {}
    for s in snapshots:
        if (v := s.get("spread_bps")) is not None:
            by_inst.setdefault(s["inst_id"], []).append(float(v))
    out: Dict[str, Dict[str, float]] = {}
    for inst, vals in by_inst.items():
        vals.sort()
        n = len(vals)
        out[inst] = {
            "n": n, "min_bps": vals[0], "max_bps": vals[-1],
            "median_bps": (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2),
            "mean_bps": sum(vals) / n,
        }
    return out
