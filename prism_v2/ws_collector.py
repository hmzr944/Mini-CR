"""Collecteur d'evenements temps reel (WebSocket OKX public).

Enregistre au fil de l'eau, en JSONL, les evenements necessaires a la mesure
microstructurelle : carnets L2, trades, liquidations. Chaque evenement porte
DEUX horodatages — exchange_ts et local_recv_ts — sans lesquels aucune mesure
de latence n'est possible.

Ce collecteur ne decide rien et ne detecte rien. Il constitue l'echantillon
que le replay exploitera ensuite. C'est la reponse a l'impasse identifiee au
Prompt 2 : l'historique L2 n'existe pas, donc on le CONSTRUIT au lieu de
l'inventer.

Robustesse : reconnexion avec backoff, re-souscription, suivi de sequence,
detection de doublons et de regressions, journalisation des coupures.
"""
from __future__ import annotations

import json
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .core_types import utc_now_iso
from .instruments import InstrumentSpec
from .quality import SequenceTracker
from .wsclient import WebSocketClosed, WebSocketError, connect_okx_public

DEFAULT_EVENT_DIR = Path(__file__).parent / "data" / "events"


@dataclass
class CollectorStats:
    started_at: str
    ended_at: Optional[str] = None
    events: int = 0
    by_channel: Dict[str, int] = field(default_factory=dict)
    reconnects: int = 0
    subscribe_errors: List[str] = field(default_factory=list)
    transport_delays_ms: List[int] = field(default_factory=list)
    duplicates: int = 0
    regressions: int = 0
    disconnect_log: List[str] = field(default_factory=list)

    def transport_delay_summary(self) -> Optional[Dict[str, float]]:
        """min / median / max / n. Jamais la moyenne seule : la dispersion du
        delai compte autant que son centre."""
        if not self.transport_delays_ms:
            return None
        v = sorted(self.transport_delays_ms)
        n = len(v)
        return {"n": n, "min_ms": v[0], "max_ms": v[-1],
                "median_ms": v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2,
                "p90_ms": v[min(n - 1, int(n * 0.9))]}

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("transport_delays_ms", None)
        d["transport_delay"] = self.transport_delay_summary()
        d["by_channel"] = dict(sorted(self.by_channel.items()))
        d["disconnect_log"] = self.disconnect_log[:20]
        return d


class EventCollector:
    """Collecte books5 / trades / liquidation-orders et ecrit du JSONL."""

    def __init__(self, out_dir: Path = DEFAULT_EVENT_DIR,
                 channels: Sequence[str] = ("books5", "trades"),
                 include_liquidations: bool = True,
                 max_backoff_s: float = 30.0):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.channels = tuple(channels)
        self.include_liquidations = include_liquidations
        self.max_backoff_s = max_backoff_s
        self.sequences = SequenceTracker()
        self._stop = False

    def request_stop(self, *_: Any) -> None:
        self._stop = True

    def _subscription_args(self, instruments: Sequence[InstrumentSpec]) -> List[Dict[str, str]]:
        args: List[Dict[str, str]] = []
        for spec in instruments:
            for ch in self.channels:
                args.append({"channel": ch, "instId": spec.inst_id})
        if self.include_liquidations:
            args.append({"channel": "liquidation-orders", "instType": "SWAP"})
        return args

    def collect(self, instruments: Sequence[InstrumentSpec], duration_s: float,
                out_file: Optional[Path] = None) -> tuple[Path, CollectorStats]:
        """Collecte pendant `duration_s`. Interruptible, reprise sur coupure.

        Les instruments sont valides AVANT toute souscription : on ne collecte
        pas sur un instrument dont les metadonnees sont douteuses.
        """
        if duration_s <= 0:
            raise ValueError("duration_s doit etre > 0")
        if not instruments:
            raise ValueError("aucun instrument a collecter")
        for spec in instruments:
            spec.validate()

        path = Path(out_file) if out_file else (
            self.out_dir / f"events_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl")
        stats = CollectorStats(started_at=utc_now_iso())
        by_id = {s.inst_id: s for s in instruments}

        previous_handlers: Dict[int, Any] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[sig] = signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                pass

        deadline = time.monotonic() + duration_s
        backoff = 1.0
        client = None
        try:
            with open(path, "a", encoding="utf-8") as fh:
                while time.monotonic() < deadline and not self._stop:
                    try:
                        if client is None or not client.is_connected:
                            client = connect_okx_public(timeout=min(20.0, duration_s + 5))
                            client.send_json({"op": "subscribe",
                                              "args": self._subscription_args(instruments)})
                            backoff = 1.0
                        msg = client.recv()
                        if msg is None:
                            continue
                        payload = msg.payload
                        if "event" in payload:
                            if payload.get("event") == "error":
                                stats.subscribe_errors.append(str(payload)[:200])
                            continue
                        arg = payload.get("arg") or {}
                        channel = arg.get("channel", "?")
                        inst_id = arg.get("instId") or ""
                        rows = payload.get("data") or []
                        for row in rows:
                            rid = row.get("instId") or inst_id
                            seq = row.get("seqId")
                            if seq is not None:
                                try:
                                    issues = self.sequences.observe(f"{channel}:{rid}", int(seq))
                                except (TypeError, ValueError):
                                    issues = []
                            else:
                                issues = []
                            record = {
                                "channel": channel,
                                "inst_id": rid,
                                "exchange_ts_ms": _as_int(row.get("ts")),
                                "local_recv_ts_ms": msg.local_recv_ts_ms,
                                "seq_id": seq,
                                "quality_issues": [i.value for i in issues],
                                "data": row,
                            }
                            spec = by_id.get(rid)
                            if spec is not None:
                                record["inst_type"] = spec.inst_type.value
                                record["ct_type"] = spec.ct_type
                            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                            stats.events += 1
                            stats.by_channel[channel] = stats.by_channel.get(channel, 0) + 1
                            if record["exchange_ts_ms"] is not None:
                                stats.transport_delays_ms.append(
                                    msg.local_recv_ts_ms - record["exchange_ts_ms"])
                        fh.flush()
                    except (WebSocketClosed, WebSocketError, OSError) as exc:
                        stats.reconnects += 1
                        stats.disconnect_log.append(f"{utc_now_iso()} {type(exc).__name__}: {exc}")
                        if client is not None:
                            client.close()
                        client = None
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or self._stop:
                            break
                        time.sleep(min(backoff, self.max_backoff_s, remaining))
                        backoff = min(backoff * 2, self.max_backoff_s)
        finally:
            if client is not None:
                client.close()
            for sig, handler in previous_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError, TypeError):
                    pass
            stats.ended_at = utc_now_iso()
            stats.duplicates = self.sequences.duplicates
            stats.regressions = self.sequences.regressions
        return path, stats


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def load_events(path: Path, channel: Optional[str] = None,
                inst_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Charge les evenements collectes, tries par horodatage exchange."""
    p = Path(path)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if channel and ev.get("channel") != channel:
            continue
        if inst_id and ev.get("inst_id") != inst_id:
            continue
        out.append(ev)
    out.sort(key=lambda e: (e.get("exchange_ts_ms") or 0, e.get("local_recv_ts_ms") or 0))
    return out
