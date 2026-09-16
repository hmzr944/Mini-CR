"""Observatoire de marche longue duree — instantanes compacts, pas de flux brut.

PROBLEME RESOLU. Le collecteur d'evenements ecrit chaque message WebSocket :
~34 Mo par tranche de 7 minutes, soit ~1,8 Go sur 6 heures. Ce volume n'est ni
relisable d'un bloc ni analysable. Mais la mesure dont le projet a besoin — la
FRACTION DE REVERSION apres un deplacement — n'a pas besoin de chaque update :
elle a besoin d'une serie reguliere d'etats de carnet EXACTS.

METHODE. On maintient le carnet incremental en memoire (meme chainage
prevSeqId/seqId que `l2book.py`, meme FAIL CLOSED sur trou de sequence) et on
n'ECRIT qu'un instantane des `snapshot_ms` : les `depth_levels` meilleurs
niveaux de chaque cote, plus l'agregat des trades survenus depuis l'instantane
precedent.

CE QUE CELA PRESERVE
  - l'exactitude du carnet a l'instant ecrit (reconstruit, pas echantillonne) ;
  - la causalite : chaque instantane porte son horodatage exchange ;
  - la detection des trous de sequence, qui invalident l'instantane.

CE QUE CELA PERD, ET QUI EST DIT
  - les mouvements entre deux instantanes ne sont pas observables : la
    resolution temporelle est bornee par `snapshot_ms` et `is_resolvable()`
    doit etre consulte avant toute mesure sous cet intervalle ;
  - la profondeur au-dela de `depth_levels` : un notionnel qui l'epuise donne
    un cout UNKNOWN, jamais extrapole (meme regle que `orderbook.walk`).

Un instantane dont le carnet est invalide est ecrit avec `ok: false` et SANS
niveaux : on n'ecrit jamais un carnet perime en le faisant passer pour frais.
"""
from __future__ import annotations

import gzip
import json
import zlib
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .instruments import InstrumentSpec
from .l2book import L2BookSet
from .wsclient import connect_okx_public
from .core_types import utc_now_iso

DEFAULT_OBS_DIR = Path(__file__).parent / "data" / "observatory"

#: Cadence d'ecriture. 250 ms est en dessous de la cadence de publication du
#: canal `books` (~100 ms observee) : on n'invente donc aucun etat, on
#: sous-echantillonne un flux plus rapide.
DEFAULT_SNAPSHOT_MS = 250

#: Niveaux conserves de chaque cote. 10 niveaux couvrent les notionnels de
#: sonde du projet (10 a 5 000 USD) sur les instruments suivis ; au-dela le
#: cout devient UNKNOWN plutot qu'extrapole.
DEFAULT_DEPTH_LEVELS = 10


@dataclass
class ObservatoryStats:
    started_at: str = ""
    finished_at: str = ""
    #: Rafraichi periodiquement pendant la collecte. Un heartbeat qui cesse
    #: d'avancer signale une panne, qu'un fichier simplement muet ne dirait pas.
    heartbeat_at: str = ""
    elapsed_s: float = 0.0
    snapshots_written: int = 0
    invalid_snapshots: int = 0
    messages: int = 0
    trades: int = 0
    reconnects: int = 0
    sequence_gaps: int = 0
    bytes_written: int = 0
    by_instrument: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class MarketObservatory:
    """Collecte longue duree. Ecrit du JSONL gzippe, un objet par instantane."""

    def __init__(self, out_dir: Path = DEFAULT_OBS_DIR,
                 snapshot_ms: int = DEFAULT_SNAPSHOT_MS,
                 depth_levels: int = DEFAULT_DEPTH_LEVELS,
                 max_backoff_s: float = 30.0,
                 flush_every_s: float = 30.0):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_ms = int(snapshot_ms)
        self.depth_levels = int(depth_levels)
        self.max_backoff_s = max_backoff_s
        self.flush_every_s = float(flush_every_s)
        self._stop = False

    def request_stop(self, *_: Any) -> None:
        self._stop = True

    def _args(self, instruments: Sequence[InstrumentSpec]) -> List[Dict[str, str]]:
        args: List[Dict[str, str]] = []
        for spec in instruments:
            args.append({"channel": "books", "instId": spec.inst_id})
            args.append({"channel": "trades", "instId": spec.inst_id})
        args.append({"channel": "liquidation-orders", "instType": "SWAP"})
        return args

    def run(self, instruments: Sequence[InstrumentSpec], duration_s: float,
            out_file: Optional[Path] = None) -> tuple[Path, ObservatoryStats]:
        if duration_s <= 0:
            raise ValueError("duration_s doit etre > 0")
        if not instruments:
            raise ValueError("aucun instrument")
        for spec in instruments:
            spec.validate()          # FAIL CLOSED avant toute souscription

        path = Path(out_file) if out_file else (
            self.out_dir /
            f"obs_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl.gz")
        stats = ObservatoryStats(started_at=utc_now_iso())
        books = L2BookSet({s.inst_id: s for s in instruments})
        # Agregats de trades depuis le dernier instantane, par instrument.
        trades: Dict[str, Dict[str, float]] = {}
        liquidations: Dict[str, List[Dict[str, Any]]] = {}

        def fresh_trade_bucket() -> Dict[str, float]:
            return {"n": 0, "buy_usd": 0.0, "sell_usd": 0.0,
                    "last_px": 0.0, "first_px": 0.0}

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                pass

        deadline = time.monotonic() + duration_s
        next_snapshot = time.monotonic() + self.snapshot_ms / 1000.0
        next_flush = time.monotonic() + self.flush_every_s
        backoff = 1.0
        client = None
        try:
            with gzip.open(path, "at", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "_meta": True, "started_at": stats.started_at,
                    "snapshot_ms": self.snapshot_ms,
                    "depth_levels": self.depth_levels,
                    "instruments": [s.to_dict() for s in instruments],
                }, ensure_ascii=False) + "\n")
                while time.monotonic() < deadline and not self._stop:
                    try:
                        if client is None or not client.is_connected:
                            client = connect_okx_public(timeout=20.0)
                            client.send_json({"op": "subscribe",
                                              "args": self._args(instruments)})
                            backoff = 1.0
                        msg = client.recv()
                        if msg is not None:
                            stats.messages += 1
                            self._ingest(msg, books, trades, liquidations,
                                         fresh_trade_bucket, stats)
                    except Exception as exc:                  # noqa: BLE001
                        # On NE saute PAS l'ecriture d'instantanes : pendant une
                        # tempete de reconnexions, `continue` ici laissait le
                        # fichier muet sans que rien ne le signale. Les carnets
                        # deviennent invalides et sont ecrits comme tels.
                        stats.errors.append(f"{type(exc).__name__}: {exc}"[:200])
                        # Compter ICI, et non a la reconnexion : le chemin
                        # d'erreur remet `client` a None, si bien qu'un test
                        # `client is not None` au moment de reconnecter ne se
                        # declenchait jamais. Le compteur affichait 0 alors que
                        # des deconnexions avaient eu lieu.
                        if client is not None:
                            stats.reconnects += 1
                        client = None
                        time.sleep(min(backoff, self.max_backoff_s))
                        backoff = min(backoff * 2, self.max_backoff_s)

                    now = time.monotonic()
                    if now >= next_snapshot:
                        next_snapshot = now + self.snapshot_ms / 1000.0
                        n = self._write_snapshots(fh, instruments, books, trades,
                                                  liquidations, fresh_trade_bucket,
                                                  stats)
                        stats.snapshots_written += n
                        # Un flux gzip non vide n'atteint le disque qu'a la
                        # fermeture. Sans point de synchronisation periodique,
                        # une collecte de six heures interrompue a la cinquieme
                        # serait integralement perdue. On paie quelques octets
                        # de surcout toutes les `flush_every_s` secondes.
                        if now >= next_flush:
                            next_flush = now + self.flush_every_s
                            try:
                                fh.flush()
                            except (OSError, ValueError):
                                pass
                            stats.heartbeat_at = utc_now_iso()
                            stats.elapsed_s = round(
                                duration_s - (deadline - now), 1)
                            try:
                                (path.parent / (path.name + ".stats.json")
                                 ).write_text(json.dumps(stats.to_dict(), indent=1,
                                                         ensure_ascii=False),
                                              encoding="utf-8")
                            except OSError:
                                pass
        finally:
            stats.finished_at = utc_now_iso()
            try:
                stats.bytes_written = path.stat().st_size
            except OSError:
                pass
            side = books.stats()
            stats.sequence_gaps = sum(
                v.get("sequence_gaps", 0) for v in side.values()
                if isinstance(v, dict))
            (path.parent / (path.name + ".stats.json")).write_text(
                json.dumps(stats.to_dict(), indent=1, ensure_ascii=False),
                encoding="utf-8")
        return path, stats

    # ── ingestion ─────────────────────────────────────────────────────────
    def _ingest(self, msg: Any, books: L2BookSet, trades: Dict[str, Dict[str, float]],
                liquidations: Dict[str, List[Dict[str, Any]]],
                fresh: Any, stats: ObservatoryStats) -> None:
        payload = msg.payload
        if "event" in payload:
            if payload.get("event") == "error":
                stats.errors.append(str(payload)[:200])
            return
        arg = payload.get("arg") or {}
        channel = arg.get("channel", "")
        rows = payload.get("data") or []
        if channel == "books":
            for row in rows:
                books.apply_event({
                    "inst_id": row.get("instId") or arg.get("instId") or "",
                    "action": payload.get("action"), "data": row,
                    "local_recv_ts_ms": msg.local_recv_ts_ms})
        elif channel == "trades":
            for row in rows:
                inst = row.get("instId") or arg.get("instId") or ""
                try:
                    px, sz = float(row["px"]), float(row["sz"])
                except (KeyError, TypeError, ValueError):
                    continue
                b = trades.setdefault(inst, fresh())
                b["n"] += 1
                stats.trades += 1
                usd = px * sz
                if row.get("side") == "buy":
                    b["buy_usd"] += usd
                else:
                    b["sell_usd"] += usd
                if not b["first_px"]:
                    b["first_px"] = px
                b["last_px"] = px
        elif channel == "liquidation-orders":
            for row in rows:
                for det in row.get("details") or []:
                    inst = row.get("instId") or det.get("instId") or ""
                    liquidations.setdefault(inst, []).append({
                        "px": det.get("bkPx"), "sz": det.get("sz"),
                        "side": det.get("side"), "ts": det.get("ts")})

    # ── ecriture ──────────────────────────────────────────────────────────
    def _write_snapshots(self, fh: Any, instruments: Sequence[InstrumentSpec],
                         books: L2BookSet, trades: Dict[str, Dict[str, float]],
                         liquidations: Dict[str, List[Dict[str, Any]]],
                         fresh: Any, stats: ObservatoryStats) -> int:
        written = 0
        k = self.depth_levels
        for spec in instruments:
            inst = spec.inst_id
            l2 = books.books.get(inst) if hasattr(books, "books") else None
            tb = trades.pop(inst, None)
            liq = liquidations.pop(inst, None)
            rec: Dict[str, Any] = {"i": inst}
            if l2 is None or not l2.valid or l2.ts_ms is None:
                rec["ok"] = False
                rec["why"] = (l2.invalid_reason if l2 is not None
                              else "aucun carnet")
                stats.invalid_snapshots += 1
            else:
                bids = sorted(l2.bids.items(), key=lambda kv: -float(kv[0]))[:k]
                asks = sorted(l2.asks.items(), key=lambda kv: float(kv[0]))[:k]
                if not bids or not asks:
                    rec["ok"] = False
                    rec["why"] = f"carnet incomplet (bids={len(bids)} asks={len(asks)})"
                    stats.invalid_snapshots += 1
                else:
                    rec["ok"] = True
                    rec["ts"] = l2.ts_ms
                    rec["seq"] = l2.seq_id
                    rec["recv"] = l2.local_recv_ts_ms
                    rec["b"] = [[float(p), float(s)] for p, s in bids]
                    rec["a"] = [[float(p), float(s)] for p, s in asks]
                    stats.by_instrument[inst] = stats.by_instrument.get(inst, 0) + 1
                    written += 1
            if tb and tb["n"]:
                rec["t"] = {"n": int(tb["n"]), "bu": round(tb["buy_usd"], 2),
                            "su": round(tb["sell_usd"], 2),
                            "fp": tb["first_px"], "lp": tb["last_px"]}
            if liq:
                rec["liq"] = liq[:10]
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        return written


def load_snapshots(path: Path, inst_id: Optional[str] = None,
                   max_records: Optional[int] = None
                   ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Relit un fichier d'observatoire. Retourne (meta, instantanes VALIDES).

    Les instantanes invalides sont ECARTES ici : un carnet marque `ok: false`
    ne doit jamais devenir une observation. Leur nombre reste lisible dans le
    fichier `.stats.json` ecrit a cote.

    LECTURE TOLERANTE. Un fichier encore en cours d'ecriture, ou dont le
    processus a ete interrompu, n'a pas de marqueur de fin de flux. Plutot que
    de perdre l'integralite de la collecte, on rend ce qui a ete lu et on le
    SIGNALE dans `meta["truncated"]`. Une ligne partielle en queue est
    ignoree, jamais devinee.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    meta: Dict[str, Any] = {}
    out: List[Dict[str, Any]] = []
    truncated = False
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            while True:
                try:
                    line = fh.readline()
                except (EOFError, OSError, zlib.error) as exc:
                    truncated = True
                    meta["truncation_reason"] = f"{type(exc).__name__}: {exc}"[:120]
                    break
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue          # ligne partielle en queue : ignoree
                if rec.get("_meta"):
                    meta.update(rec)
                    continue
                if inst_id is not None and rec.get("i") != inst_id:
                    continue
                if not rec.get("ok"):
                    continue
                out.append(rec)
                if max_records is not None and len(out) >= max_records:
                    break
    except (EOFError, OSError, zlib.error) as exc:
        truncated = True
        meta["truncation_reason"] = f"{type(exc).__name__}: {exc}"[:120]
    meta["truncated"] = truncated
    meta["n_loaded"] = len(out)
    out.sort(key=lambda r: r.get("ts") or 0)
    return meta, out
