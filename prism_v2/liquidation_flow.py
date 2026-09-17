#!/usr/bin/env python3
"""FLUX DE LIQUIDATION FORCE — lecture d'un etat de marche, pas d'une prevision.

Regle gelee dans prism_v2/LIQUIDATION_PROTOCOL.md avant que les donnees de
test existent.

CE QUI CHANGE PAR RAPPORT A TOUT LE RESTE DU DEPOT. Les cinq familles
precedentes demandaient « ou va le prix ». Ici on demande « qui est en train
d'etre force de vendre ». Un ordre de liquidation n'est pas une opinion : le
moteur de la venue DOIT fermer la position quel que soit le prix. C'est une
contrainte mecanique, observable, qui ne peut pas se decolorer comme une
relation statistique.

LA MEDIANE EST GLISSANTE ET CAUSALE, ET C'EST LE POINT CRITIQUE. L'ampleur
d'un flux n'a de sens que rapportee a ce qui est normal POUR CET INSTRUMENT.
La mesure exploratoire utilisait la mediane de toute la periode — donc de
l'information future — ce qui rendait « ample » un flux que l'on n'aurait pas
su qualifier sur le moment. Ici la mediane ne porte que sur les 1 440 minutes
STRICTEMENT anterieures.

CE QUE LE SURDEPASSEMENT NE PROUVE PAS. Que le prix bouge de -81 bps pendant
une minute de vente forcee est mesure et solide. Que ce mouvement soit
CAPTURABLE ne l'est pas : le carnet est vide precisement a cet instant. Le
module mesure donc separement le deplacement et ce qu'un participant aurait
reellement pu encaisser.
"""
from __future__ import annotations

import statistics
from bisect import insort
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

MINUTE_MS = 60_000

# Parametres GELES par le protocole. Les modifier invalide le test.
TRAILING_MINUTES = 1_440          # 24 h de reference glissante
IMBALANCE_THRESHOLD = 0.50
AMPLITUDE_THRESHOLD = 5.0
HOLD_MINUTES = 30
ROUND_TRIP_FEE_BPS = 10.0         # taker OKX, 2 jambes

SIDE_SELL = "sell"                # le moteur vend : il ferme des LONGS
SIDE_BUY = "buy"                  # le moteur achete : il ferme des SHORTS


@dataclass(frozen=True)
class Liquidation:
    ts_ms: int
    side: str
    size: float
    price: float

    def notional(self) -> float:
        return self.size * self.price

    def __post_init__(self) -> None:
        if self.side not in (SIDE_SELL, SIDE_BUY):
            raise ValueError(f"side inconnu: {self.side}")
        if self.size < 0 or self.price <= 0:
            raise ValueError("taille negative ou prix <= 0")


@dataclass(frozen=True)
class MinuteFlow:
    """Flux force agrege sur une minute."""
    minute_ms: int
    forced_sell_usd: float
    forced_buy_usd: float

    @property
    def total(self) -> float:
        return self.forced_sell_usd + self.forced_buy_usd

    @property
    def imbalance(self) -> Optional[float]:
        """+1 = 100 % de vente forcee. None si aucun flux : l'absence de flux
        n'est pas un desequilibre nul, c'est une absence d'information."""
        t = self.total
        if t <= 0:
            return None
        return (self.forced_sell_usd - self.forced_buy_usd) / t


def aggregate_minutes(liqs: Iterable[Liquidation]) -> Dict[int, MinuteFlow]:
    acc: Dict[int, List[float]] = {}
    for x in liqs:
        m = x.ts_ms - x.ts_ms % MINUTE_MS
        cell = acc.setdefault(m, [0.0, 0.0])
        if x.side == SIDE_SELL:
            cell[0] += x.notional()
        else:
            cell[1] += x.notional()
    return {m: MinuteFlow(m, s, b) for m, (s, b) in acc.items()}


class TrailingMedian:
    """Mediane glissante causale sur une fenetre de minutes.

    N'expose une valeur que lorsque la fenetre est PLEINE. Renvoyer une
    mediane partielle reviendrait a qualifier d'« ample » un flux mesure
    contre trop peu d'histoire — c'est ainsi qu'on fabrique des
    declenchements au demarrage.
    """

    def __init__(self, window: int = TRAILING_MINUTES):
        if window < 1:
            raise ValueError("fenetre < 1")
        self.window = window
        self._q: Deque[float] = deque()
        self._sorted: List[float] = []

    def push(self, value: float) -> None:
        insort(self._sorted, value)
        self._q.append(value)
        if len(self._q) > self.window:
            old = self._q.popleft()
            i = self._sorted.index(old)
            self._sorted.pop(i)

    @property
    def ready(self) -> bool:
        return len(self._q) >= self.window

    def value(self) -> Optional[float]:
        if not self.ready:
            return None
        return statistics.median(self._sorted)


@dataclass(frozen=True)
class Trigger:
    """Un declenchement de la regle gelee."""
    instrument: str
    minute_ms: int
    imbalance: float
    amplitude: float
    #: +1 = on achete (on fade une vente forcee), -1 = on vend.
    direction: int
    forced_total_usd: float


def scan_instrument(instrument: str, flows: Dict[int, MinuteFlow],
                    minutes: Sequence[int]) -> List[Trigger]:
    """Applique la regle gelee, minute par minute, causalement.

    `minutes` doit etre la grille complete et triee : les minutes SANS flux
    comptent comme zero dans la mediane de reference, sans quoi la reference
    ne decrirait que les minutes agitees et rien ne paraitrait jamais ample.
    """
    med = TrailingMedian(TRAILING_MINUTES)
    out: List[Trigger] = []
    for m in minutes:
        flow = flows.get(m)
        total = flow.total if flow else 0.0
        reference = med.value()          # LU AVANT d'inserer la minute courante
        if flow is not None and reference is not None and reference > 0:
            imb = flow.imbalance
            if imb is not None:
                amp = total / reference
                if abs(imb) >= IMBALANCE_THRESHOLD and amp >= AMPLITUDE_THRESHOLD:
                    out.append(Trigger(instrument, m, imb, amp,
                                       1 if imb > 0 else -1, total))
        med.push(total)
        # La minute courante n'entre dans la reference qu'APRES avoir servi :
        # sinon un flux enorme se normaliserait par lui-meme.
    return out


@dataclass(frozen=True)
class Outcome:
    trigger: Trigger
    entry_px: float
    exit_px: float
    gross_bps: float
    cost_bps: float

    @property
    def net_bps(self) -> float:
        return self.gross_bps - self.cost_bps


def evaluate(trigger: Trigger, prices: Dict[int, float],
             half_spread_bps: float,
             hold_minutes: int = HOLD_MINUTES) -> Optional[Outcome]:
    """Resultat d'un declenchement. Entree a la CLOTURE de la minute du flux.

    Le demi-spread est facture DEUX fois (entree et sortie) en plus des frais :
    c'est le cout d'immediatete que paie celui qui traverse, et l'ignorer
    ferait passer un deplacement non capturable pour un gain.
    """
    if half_spread_bps < 0:
        raise ValueError("demi-spread negatif")
    entry = prices.get(trigger.minute_ms)
    exit_ = prices.get(trigger.minute_ms + hold_minutes * MINUTE_MS)
    if entry is None or exit_ is None or entry <= 0:
        return None
    move_bps = (exit_ / entry - 1.0) * 10_000.0
    gross = trigger.direction * move_bps
    cost = ROUND_TRIP_FEE_BPS + 2.0 * half_spread_bps
    return Outcome(trigger, entry, exit_, gross, cost)


# ── Collecte vers l'avant ────────────────────────────────────────────────────
#
# OKX ne conserve que 24 h de liquidations. La seule facon d'obtenir un jeu de
# test propre est donc de l'accumuler. C'est aussi la plus solide : des donnees
# qui n'existaient pas quand la regle a ete ecrite ne peuvent pas avoir ete
# adaptees a elle.

import json
import os
import time
import urllib.request
from pathlib import Path

OKX_BASE = "https://www.okx.com"
UA = "prism-v2/research (public endpoints only)"


def _get(url: str, tries: int = 4) -> dict:
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1.5 ** k)
    raise RuntimeError("inatteignable")


def fetch_liquidations(inst_family: str, pages: int = 25) -> List[Liquidation]:
    out: List[Liquidation] = []
    after = None
    prev = None
    for _ in range(pages):
        url = (f"{OKX_BASE}/api/v5/public/liquidation-orders?instType=SWAP"
               f"&instFamily={inst_family}&state=filled&limit=100")
        if after:
            url += f"&after={after}"
        rows = _get(url).get("data") or []
        det = rows[0].get("details") if rows else []
        if not det:
            break
        for x in det:
            try:
                out.append(Liquidation(int(x["ts"]), x["side"],
                                       float(x["sz"]), float(x["bkPx"])))
            except (KeyError, TypeError, ValueError):
                continue     # une ligne illisible est ecartee, jamais devinee
        nb = min(int(x["ts"]) for x in det)
        if prev is not None and nb >= prev:
            break
        prev = nb
        after = nb
    return out


def fetch_minute_prices(inst_id: str, pages: int = 5) -> Dict[int, float]:
    out: Dict[int, float] = {}
    after = None
    for _ in range(pages):
        url = (f"{OKX_BASE}/api/v5/market/history-candles"
               f"?instId={inst_id}&bar=1m&limit=300")
        if after:
            url += f"&after={after}"
        rows = _get(url).get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = float(r[4])
        nb = min(int(r[0]) for r in rows)
        if after and nb >= after:
            break
        after = nb
    return out


def append_snapshot(directory: Path, instrument: str, inst_family: str) -> dict:
    """Un passage de collecte. Ecrit en append, deduplique a la lecture.

    On ne reecrit jamais un fichier existant : une collecte qui ecrase est une
    collecte qu'on ne peut pas auditer.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    liqs = fetch_liquidations(inst_family)
    pxs = fetch_minute_prices(instrument)
    rec = {
        "collected_at_ms": int(time.time() * 1000),
        "instrument": instrument,
        "inst_family": inst_family,
        "liq": [[x.ts_ms, x.side, x.size, x.price] for x in liqs],
        "px": sorted(pxs.items()),
    }
    with (directory / f"{instrument}.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return {"instrument": instrument, "n_liq": len(liqs), "n_px": len(pxs)}


def load_accumulated(directory: Path, instrument: str
                     ) -> Tuple[List[Liquidation], Dict[int, float]]:
    """Relit toutes les passes et deduplique.

    Les passes se recouvrent volontairement : OKX ne garde que 24 h, donc une
    collecte toutes les 6 h laisse une marge de securite de 4x. Le
    recouvrement produit des doublons, que la deduplication par horodatage
    elimine — un meme evenement compte une fois.
    """
    path = Path(directory) / f"{instrument}.jsonl"
    if not path.exists():
        return [], {}
    seen = set()
    liqs: List[Liquidation] = []
    pxs: Dict[int, float] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue          # une ligne tronquee ne doit pas tuer la lecture
        for ts, side, sz, px in rec.get("liq") or []:
            key = (ts, side, sz, px)
            if key in seen:
                continue
            seen.add(key)
            try:
                liqs.append(Liquidation(int(ts), side, float(sz), float(px)))
            except ValueError:
                continue
        for t, p in rec.get("px") or []:
            pxs[int(t)] = float(p)
    liqs.sort(key=lambda x: x.ts_ms)
    return liqs, pxs


def compact(source_dir: Path, out_path: Path,
            instruments: Optional[Sequence[str]] = None) -> dict:
    """Deduplique les passes et ecrit un fichier compact, versionnable.

    Le conteneur de collecte est ephemere : sans cette etape, le jeu de test
    disparaitrait avec la session, et le holdout « impossible a contaminer »
    serait aussi impossible a relire. La compaction rend les donnees durables
    ET auditables — chaque liquidation y figure une fois, horodatee.

    Le recouvrement volontaire des passes (toutes les 6 h pour une fenetre de
    24 h) produit environ 4 doublons par evenement. La deduplication par
    (horodatage, sens, taille, prix) les ramene a un.
    """
    import gzip

    source_dir = Path(source_dir)
    names = list(instruments) if instruments else sorted(
        p.stem for p in source_dir.glob("*.jsonl"))
    payload: Dict[str, dict] = {}
    n_liq = n_px = 0
    for name in names:
        liqs, pxs = load_accumulated(source_dir, name)
        if not liqs:
            continue
        payload[name] = {
            "liq": [[x.ts_ms, x.side, x.size, x.price] for x in liqs],
            "px": sorted(pxs.items()),
        }
        n_liq += len(liqs)
        n_px += len(pxs)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt") as f:
        json.dump(payload, f)
    spans = []
    for d in payload.values():
        if d["liq"]:
            spans.append((d["liq"][0][0], d["liq"][-1][0]))
    return {
        "instruments": len(payload),
        "liquidations": n_liq,
        "minutes_prix": n_px,
        "octets": out_path.stat().st_size,
        "span_heures": round((max(s[1] for s in spans)
                              - min(s[0] for s in spans)) / 3_600_000, 2)
        if spans else 0.0,
    }


def load_compact(path: Path) -> Dict[str, Tuple[List[Liquidation], Dict[int, float]]]:
    import gzip
    with gzip.open(Path(path), "rt") as f:
        raw = json.load(f)
    out = {}
    for name, d in raw.items():
        liqs = [Liquidation(int(t), s, float(sz), float(p))
                for t, s, sz, p in d["liq"]]
        pxs = {int(t): float(p) for t, p in d["px"]}
        out[name] = (liqs, pxs)
    return out
