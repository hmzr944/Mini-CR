#!/usr/bin/env python3
"""FEED — flux de liquidation force en direct, et le carnet a l'instant du choc.

Endpoints PUBLICS uniquement. Aucune cle, aucun ordre.

CE QUE CE FEED EXISTE POUR MESURER. Le deplacement de prix cause par un flux
force est deja etabli (-81 bps sur une minute de vente forcee ample). Ce qui
ne l'est pas, c'est qu'il soit CAPTURABLE : le carnet est cense etre vide
precisement a cet instant. Aucune donnee historique ne repond a cela — OKX ne
rediffuse pas les carnets passes, et aucun fournisseur ne les reconstitue a la
milliseconde. Il faut donc etre present.

Le feed va chercher le carnet UNIQUEMENT quand la regle gelee declenche. Un
carnet coute un aller-retour reseau ; en collecter 40 par minute pour des
instruments qui ne declenchent pas serait du gaspillage, et surtout cela
ralentirait la collecte au moment precis ou la vitesse compte.

LA MEDIANE DE REFERENCE VIENT DE L'HISTORIQUE DEJA COLLECTE. Elle exige
1 440 minutes ; sans amorcage, le feed ne declencherait rien pendant ses
24 premieres heures. On la charge donc depuis le fichier compact du depot.
"""
from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.bot import FlowObservation
from prism_v2.core_types import Provenance, utc_now_iso
from prism_v2.instruments import InstrumentRegistry
from prism_v2.liquidation_flow import (
    MINUTE_MS, MIN_ACTIVE_MINUTES, TRAILING_MINUTES, Liquidation, MinuteFlow,
    aggregate_minutes, fetch_liquidations, load_compact,
)
from prism_v2.opp_liquidation import ForcedFlowState
from prism_v2.orderbook import OrderBook

OKX_BASE = "https://www.okx.com"


def trailing_reference(liqs: Sequence[Liquidation], now_ms: int,
                       minutes: int = TRAILING_MINUTES) -> Optional[float]:
    """Mediane du flux des minutes ACTIVES sur la fenetre precedente.

    Compter les minutes vides comme zero rendait cette mediane nulle sur
    toutes les series reelles — les liquidations sont eparses — et la regle
    ne se declenchait alors jamais. Voir `scan_instrument` pour le detail du
    defaut.

    Renvoie None si la fenetre n'est pas couverte : on ne qualifie pas
    d'« ample » un flux mesure contre trop peu d'histoire.
    """
    end = now_ms - now_ms % MINUTE_MS
    start = end - minutes * MINUTE_MS
    recent = [x for x in liqs if start <= x.ts_ms < end]
    if not recent:
        return None
    span = max(x.ts_ms for x in recent) - min(x.ts_ms for x in recent)
    if span < minutes * MINUTE_MS * 0.5:
        return None                      # couverture insuffisante
    flows = aggregate_minutes(recent)
    active = [f.total for f in flows.values() if f.total > 0]
    if len(active) < MIN_ACTIVE_MINUTES:
        return None          # trop peu d'observations pour une mediane stable
    return statistics.median(active)


@dataclass
class ForcedFlowFeed:
    """Collecte le flux force et, au declenchement seulement, le carnet."""

    registry: InstrumentRegistry
    families: Dict[str, str]                       # inst_id -> instFamily
    history: Dict[str, List[Liquidation]] = field(default_factory=dict)
    max_instruments: int = 40
    #: Nombre de minutes ecoulees a considerer comme « la minute courante ».
    #: On lit la DERNIERE minute COMPLETE : une minute en cours serait
    #: partielle et son amplitude sous-estimee.
    lag_minutes: int = 1
    #: Plafond de minutes rattrapees en un cycle. Au-dela, on a perdu le fil
    #: (redemarrage, panne reseau) et rejouer davantage donnerait l'illusion
    #: d'une surveillance continue qui n'a pas eu lieu.
    max_catchup_minutes: int = 30

    def __post_init__(self) -> None:
        self._books_fetched = 0
        self._triggers_seen = 0
        self._minutes_scanned = 0
        #: Derniere minute deja traitee, par instrument. Sans ce suivi, un
        #: cycle plus long qu'une minute saute les minutes intermediaires —
        #: et un flux force qui y serait tombe serait perdu sans trace.
        self._last_minute: Dict[str, int] = {}

    @classmethod
    def from_compact(cls, compact_path: Path, registry: InstrumentRegistry,
                     families: Dict[str, str], **kw) -> "ForcedFlowFeed":
        data = load_compact(Path(compact_path))
        hist = {k: v[0] for k, v in data.items()}
        return cls(registry=registry, families=families, history=hist, **kw)

    # ---- collecte -------------------------------------------------------
    def snapshot(self) -> List[FlowObservation]:
        now = int(time.time() * 1000)
        target_minute = (now - now % MINUTE_MS) - self.lag_minutes * MINUTE_MS
        out: List[FlowObservation] = []

        for inst_id in list(self.families)[: self.max_instruments]:
            spec = self.registry.get(inst_id)
            if spec is None:
                continue
            try:
                fresh = fetch_liquidations(self.families[inst_id], pages=3)
            except Exception:                        # noqa: BLE001
                continue
            hist = self.history.setdefault(inst_id, [])
            seen = {(x.ts_ms, x.side, x.size, x.price) for x in hist}
            for x in fresh:
                k = (x.ts_ms, x.side, x.size, x.price)
                if k not in seen:
                    hist.append(x)
                    seen.add(k)
            hist.sort(key=lambda x: x.ts_ms)
            # On ne garde que ce qui sert la reference : la memoire du feed
            # n'a aucune raison de croitre indefiniment.
            cutoff = now - (TRAILING_MINUTES + 60) * MINUTE_MS
            self.history[inst_id] = [x for x in hist if x.ts_ms >= cutoff]

            ref = trailing_reference(self.history[inst_id], now)
            if ref is None or ref <= 0:
                continue                 # reference absente ou nulle : on s'abstient

            # Toutes les minutes ecoulees depuis le dernier passage, et non
            # la seule derniere : un cycle dure plus d'une minute, donc ne
            # regarder que la minute courante en perdrait la plupart.
            previous = self._last_minute.get(inst_id)
            if previous is None:
                first = target_minute
            else:
                first = max(previous + MINUTE_MS,
                            target_minute - self.max_catchup_minutes * MINUTE_MS)
            self._last_minute[inst_id] = target_minute

            for minute in range(first, target_minute + MINUTE_MS, MINUTE_MS):
                self._minutes_scanned += 1
                minute_liqs = [x for x in self.history[inst_id]
                               if minute <= x.ts_ms < minute + MINUTE_MS]
                if not minute_liqs:
                    continue
                flow = aggregate_minutes(minute_liqs).get(minute)
                if flow is None:
                    continue
                imb = flow.imbalance
                if imb is None:
                    continue

                state = ForcedFlowState(inst_id, minute, imb,
                                        flow.total / ref, flow.total)
                book = None
                capacity = None
                if state.triggers():
                    self._triggers_seen += 1
                    # Le carnet n'est pertinent que pour la minute COURANTE :
                    # pour une minute rattrapee il serait posterieur au choc
                    # et donnerait une profondeur qui n'existait pas alors.
                    if minute == target_minute:
                        book = self._fetch_book(spec)
                        self._books_fetched += 1
                        if book is not None:
                            capacity = self._touch_depth_usd(book)
                out.append(FlowObservation(inst_id, spec, state, book, capacity))
        return out

    def _fetch_book(self, spec) -> Optional[OrderBook]:
        from prism_v2.funding_feed import okx_book, FeedError
        try:
            return okx_book(spec)
        except (FeedError, Exception):               # noqa: BLE001
            return None

    @staticmethod
    def _touch_depth_usd(book: OrderBook) -> Optional[float]:
        """Profondeur au meilleur prix, en USD, cote le moins profond.

        C'est la borne honnete de ce qu'on peut deployer sans marcher dans le
        carnet — et c'est exactement la grandeur qui decide si un deplacement
        de 81 bps est capturable ou seulement visible.
        """
        if not book.bids or not book.asks:
            return None
        return min(book.bids[0].usd_notional, book.asks[0].usd_notional)

    def stats(self) -> Dict[str, int]:
        return {"declenchements": self._triggers_seen,
                "carnets_collectes": self._books_fetched,
                "minutes_balayees": self._minutes_scanned,
                "instruments_suivis": len(self.history)}
