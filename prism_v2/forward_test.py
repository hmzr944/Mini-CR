#!/usr/bin/env python3
"""TEST FORWARD — le bot qui accumule des resultats reels, pas des refus.

Regle gelee : prism_v2/LIQUIDATION_PROTOCOL.md.

CE QUE CE MODULE FAIT, ET POURQUOI IL REMPLACE LE PRECEDENT. Le bot
precedent allait chercher le carnet a l'instant du choc pour trancher le
barreau LIQUIDITE. Cette question EST TRANCHEE : 520 releves ont montre un
demi-spread inchange pendant le choc (ratio 1,00) et une profondeur souvent
superieure. Le cout reel d'un aller-retour vaut 10 a 11 bps, domine par les
frais.

Il ne reste donc qu'une inconnue : LE RETOUR DE PRIX EST-IL REEL ? Ce module
ne mesure que cela. Chaque declenchement est enregistre avec son prix
d'entree ; trente minutes plus tard, son prix de sortie et son PnL net. Rien
d'autre.

POURQUOI CETTE EXPERIENCE PEUT TRANCHER VITE. A 12-19 declenchements par
heure sur l'univers suivi, l'echantillon double tous les deux jours. Il faut
2,3x l'echantillon exploratoire pour passer de t=1,31 a t=2 — quelques jours,
pas quatorze.

AUCUN ORDRE REEL. Le PnL est calcule sur des prix observes, avec les couts
mesures. C'est une mesure, pas une execution.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.liquidation_flow import (
    AMPLITUDE_THRESHOLD, HOLD_MINUTES, IMBALANCE_THRESHOLD, MINUTE_MS,
    ROUND_TRIP_FEE_BPS, Liquidation, aggregate_minutes, fetch_liquidations,
)

#: Demi-spread MESURE par instrument (bps). Mediane des 5 instruments
#: echantillonnes ; utilise comme defaut prudent pour les autres. Ce n'est
#: pas un zero deguise : il est mesure et il est facture deux fois.
DEFAULT_HALF_SPREAD_BPS = 0.50


@dataclass
class PendingTrade:
    """Un declenchement en attente de son resultat."""
    instrument: str
    minute_ms: int
    direction: int
    imbalance: float
    amplitude: float
    forced_total_usd: float
    entry_px: float
    entry_ts_ms: int
    exit_due_ms: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClosedTrade:
    instrument: str
    minute_ms: int
    direction: int
    imbalance: float
    amplitude: float
    entry_px: float
    exit_px: float
    gross_bps: float
    cost_bps: float
    net_bps: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute_outcome(pending: PendingTrade, exit_px: float,
                    half_spread_bps: float = DEFAULT_HALF_SPREAD_BPS
                    ) -> ClosedTrade:
    """Resultat d'un trade. Le cout est facture integralement.

    Les deux traversees paient frais ET demi-spread : entrer coute, sortir
    coute. Un module qui n'en facturerait qu'une doublerait l'edge apparent.
    """
    if pending.entry_px <= 0 or exit_px <= 0:
        raise ValueError("prix <= 0")
    if half_spread_bps < 0:
        raise ValueError("demi-spread negatif")
    move = (exit_px / pending.entry_px - 1.0) * 10_000.0
    gross = pending.direction * move
    cost = ROUND_TRIP_FEE_BPS + 2.0 * half_spread_bps
    return ClosedTrade(
        pending.instrument, pending.minute_ms, pending.direction,
        pending.imbalance, pending.amplitude, pending.entry_px, exit_px,
        gross, cost, gross - cost)


def summarise(trades: Sequence[ClosedTrade]) -> dict:
    """Statistiques courantes. Rend None plutot qu'un chiffre trompeur
    quand l'echantillon est trop petit pour qu'il signifie quelque chose."""
    if not trades:
        return {"n": 0}
    net = [t.net_bps for t in trades]
    gross = [t.gross_bps for t in trades]
    mean = statistics.fmean(net)
    sd = statistics.stdev(net) if len(net) > 1 else 0.0
    t_stat = (mean / (sd / math.sqrt(len(net)))) if len(net) > 1 and sd > 0 else None
    p = None
    if t_stat is not None:
        p = 0.5 * math.erfc(t_stat / math.sqrt(2.0))
    return {
        "n": len(trades),
        "n_instruments": len({t.instrument for t in trades}),
        "gross_bps_moyen": round(statistics.fmean(gross), 2),
        "cout_bps": round(trades[0].cost_bps, 2),
        "net_bps_moyen": round(mean, 2),
        "net_bps_median": round(statistics.median(net), 2),
        "ecart_type": round(sd, 2),
        "t": round(t_stat, 2) if t_stat is not None else None,
        "p_unilaterale": round(p, 4) if p is not None else None,
        "part_positive": round(sum(1 for x in net if x > 0) / len(net), 3),
        "significatif_t2": bool(t_stat is not None and t_stat >= 2.0),
    }


def economics(stats: dict, size_usd: float, hold_minutes: int,
              triggers_per_day: float) -> dict:
    """Traduit le resultat dans l'unite de l'objectif : bps/jour de capital.

    Le capital immobilise est celui des positions SIMULTANEES, pas celui de
    tous les trades du jour : c'est ce qui distingue une famille a rotation
    rapide d'une famille qui dort.
    """
    if stats.get("n", 0) == 0 or stats.get("net_bps_moyen") is None:
        return {}
    concurrent = triggers_per_day / 24.0 * (hold_minutes / 60.0)
    capital = max(size_usd, concurrent * size_usd)
    pnl_day = triggers_per_day * size_usd * stats["net_bps_moyen"] / 10_000.0
    return {
        "declenchements_par_jour": round(triggers_per_day, 1),
        "positions_simultanees": round(concurrent, 2),
        "capital_immobilise_usd": round(capital, 0),
        "pnl_par_jour_usd": round(pnl_day, 2),
        "bps_par_jour": round(pnl_day / capital * 10_000.0, 1),
        "ratio_objectif": round(pnl_day / capital * 10_000.0 / 63.28, 3),
    }


# ── Runner ───────────────────────────────────────────────────────────────────

OKX_BASE = "https://www.okx.com"


def mid_price(inst_id: str) -> Optional[float]:
    """Mid courant. None si le carnet est absent ou croise — jamais un
    prix fabrique."""
    import urllib.request
    url = f"{OKX_BASE}/api/v5/market/books?instId={inst_id}&sz=1"
    req = urllib.request.Request(url, headers={"User-Agent": "prism-v2"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except Exception:                                    # noqa: BLE001
        return None
    rows = d.get("data") or []
    if not rows or not rows[0].get("bids") or not rows[0].get("asks"):
        return None
    b = float(rows[0]["bids"][0][0])
    a = float(rows[0]["asks"][0][0])
    return (a + b) / 2.0 if a > b > 0 else None


class ForwardRunner:
    """Detecte, enregistre, et cloture apres l'horizon gele.

    Persiste tout : un resultat qui ne survit pas au redemarrage du
    conteneur n'existe pas.
    """

    def __init__(self, families: Dict[str, str], state_dir: Path,
                 size_usd: float = 500.0,
                 half_spreads: Optional[Dict[str, float]] = None):
        self.families = families
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.size_usd = size_usd
        self.half_spreads = half_spreads or {}
        self.history: Dict[str, List[Liquidation]] = {}
        self.refs: Dict[str, float] = {}
        self.pending: List[PendingTrade] = []
        self.closed: List[ClosedTrade] = []
        self._seen: Dict[str, set] = {}
        self._last_minute: Dict[str, int] = {}
        self._load()

    # ---- persistance ----------------------------------------------------
    def _load(self) -> None:
        pf = self.dir / "pending.json"
        cf = self.dir / "closed.jsonl"
        if pf.exists():
            try:
                self.pending = [PendingTrade(**x) for x in
                                json.loads(pf.read_text())]
            except Exception:                            # noqa: BLE001
                self.pending = []
        if cf.exists():
            for line in cf.read_text().splitlines():
                if line.strip():
                    try:
                        self.closed.append(ClosedTrade(**json.loads(line)))
                    except Exception:                    # noqa: BLE001
                        continue

    def _save_pending(self) -> None:
        (self.dir / "pending.json").write_text(
            json.dumps([p.to_dict() for p in self.pending]))

    def _append_closed(self, t: ClosedTrade) -> None:
        with (self.dir / "closed.jsonl").open("a") as f:
            f.write(json.dumps(t.to_dict()) + "\n")

    # ---- reference glissante -------------------------------------------
    def prime(self, history: Dict[str, List[Liquidation]]) -> None:
        """Amorce avec l'historique deja collecte : sans cela la reference
        exigerait 24 h avant le premier declenchement."""
        for k, v in history.items():
            self.history[k] = list(v)
            self._seen[k] = {(x.ts_ms, x.side, x.size, x.price) for x in v}

    def _reference(self, inst: str, now_ms: int) -> Optional[float]:
        from prism_v2.flow_feed import trailing_reference
        liqs = self.history.get(inst) or []
        return trailing_reference(liqs, now_ms)

    # ---- un tour --------------------------------------------------------
    def step(self) -> dict:
        now = int(time.time() * 1000)
        target = (now - now % MINUTE_MS) - MINUTE_MS
        new_triggers = 0
        scanned = 0

        for inst, fam in self.families.items():
            try:
                fresh = fetch_liquidations(fam, pages=2)
            except Exception:                            # noqa: BLE001
                continue
            seen = self._seen.setdefault(inst, set())
            hist = self.history.setdefault(inst, [])
            for x in fresh:
                k = (x.ts_ms, x.side, x.size, x.price)
                if k not in seen:
                    seen.add(k)
                    hist.append(x)
            hist.sort(key=lambda x: x.ts_ms)
            cutoff = now - 26 * 60 * MINUTE_MS
            self.history[inst] = [x for x in hist if x.ts_ms >= cutoff]

            ref = self._reference(inst, now)
            if not ref or ref <= 0:
                continue
            self.refs[inst] = ref

            prev = self._last_minute.get(inst)
            first = target if prev is None else max(prev + MINUTE_MS,
                                                    target - 30 * MINUTE_MS)
            self._last_minute[inst] = target
            flows = aggregate_minutes(self.history[inst])
            for minute in range(first, target + MINUTE_MS, MINUTE_MS):
                scanned += 1
                flow = flows.get(minute)
                if flow is None:
                    continue
                imb = flow.imbalance
                if imb is None:
                    continue
                amp = flow.total / ref
                if abs(imb) < IMBALANCE_THRESHOLD or amp < AMPLITUDE_THRESHOLD:
                    continue
                if any(p.instrument == inst and p.minute_ms == minute
                       for p in self.pending):
                    continue
                px = mid_price(inst)
                if px is None:
                    continue          # sans prix d'entree, pas de trade
                self.pending.append(PendingTrade(
                    inst, minute, 1 if imb > 0 else -1, imb, amp, flow.total,
                    px, now, minute + (HOLD_MINUTES + 1) * MINUTE_MS))
                new_triggers += 1

        closed_now = self._close_due(now)
        self._save_pending()
        return {"nouveaux": new_triggers, "clotures": closed_now,
                "en_attente": len(self.pending), "total_clos": len(self.closed),
                "minutes": scanned}

    def _close_due(self, now: int) -> int:
        still: List[PendingTrade] = []
        n = 0
        for p in self.pending:
            if now < p.exit_due_ms:
                still.append(p)
                continue
            px = mid_price(p.instrument)
            if px is None:
                still.append(p)          # on reessaie, on n'invente pas
                continue
            hs = self.half_spreads.get(p.instrument, DEFAULT_HALF_SPREAD_BPS)
            t = compute_outcome(p, px, hs)
            self.closed.append(t)
            self._append_closed(t)
            n += 1
        self.pending = still
        return n

    def report(self, triggers_per_day: Optional[float] = None) -> dict:
        s = summarise(self.closed)
        out = {"stats": s, "en_attente": len(self.pending)}
        if s.get("n", 0) >= 20:
            rate = triggers_per_day or (len(self.closed) + len(self.pending))
            out["economie"] = economics(s, self.size_usd, HOLD_MINUTES, rate)
        return out
