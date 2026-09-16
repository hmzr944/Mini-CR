#!/usr/bin/env python3
"""EDGE_HUNT — exploration active de plusieurs familles d'inefficiences.

Ne trade pas aveuglement. Il :
  1. collecte le flux reel (carnets, trades, liquidations) ;
  2. rejoue ce flux de maniere CAUSALE a travers un MarketStateTracker ;
  3. fait passer 9 familles de detecteurs sur chaque etat ;
  4. encadre l'economie de chaque candidate (mode DISCOVERY) ;
  5. mesure quelles familles survivent a chaque couche de friction ;
  6. dimensionne, arbitre (Capital Router), execute en PAPER ;
  7. enregistre conditions et issues dans la Discovery Memory ;
  8. ajuste la priorite de RECHERCHE par famille.

Le scan est CAUSAL : a chaque instant de scan, le tracker ne contient que
des evenements d'horodatage anterieur. Aucun detecteur ne peut voir le futur.

AUCUN ordre reel. SystemMode reste DISCOVERY ou PAPER.

Usage :
  python3 -m prism_v2.edge_hunt [--duration 180] [--instruments 6] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2 import __version__
from prism_v2.contracts import usd_notional
from prism_v2.core_types import Direction, utc_now_iso
from prism_v2.costs import (
    ExecutionStyle, build_breakdown, funding_not_applicable, latency_not_applicable,
    slippage_excluded_for_paper_validation,
)
from prism_v2.detectors.cross_market import USD_EQUIVALENT_QUOTES
from prism_v2.detectors import (
    AggressiveFlowDetector, BookImbalanceDetector, CrossMarketDetector,
    CrossVenueDetector, DepthWithdrawalDetector, ForcedFlowDetector,
    FundingBasisDetector, ShortHorizonReversionDetector, SpreadDislocationDetector,
)
from prism_v2.discovery import DiscoveryEngine, Family
from prism_v2.discovery_economics import DiscoveryVerdict, discover
from prism_v2.discovery_memory import DiscoveryMemory, Observation
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.edge_health import EdgeHealth, describe
from prism_v2.execution import PaperExecutor
from prism_v2.failure_memory import FailureMemory
from prism_v2.instruments import InstrumentType
from prism_v2.l2book import L2BookSet
from prism_v2.ledger import CaptureLedger
from prism_v2.market_state import ForcedFlowEvent, MarketStateTracker, TradePrint
from prism_v2.market_data import MarketDataError, OKXPublicClient
from prism_v2.modes import EvaluationMode, ModeGate, SystemMode


def latency_component_for(inst_id: str):
    """Latence non applicable pour un aller-retour instantane simule.

    Le simulateur entre et sort sur le MEME carnet : aucun temps ne s'ecoule,
    donc aucune decroissance par latence ne s'applique. C'est aussi pourquoi
    le resultat est une borne superieure : un ordre reel, lui, arrive plus tard.
    """
    return latency_not_applicable(
        "aller-retour simule sur un carnet unique : aucun temps ne s'ecoule. "
        "Un ordre reel arriverait plus tard et subirait une decroissance.")
from prism_v2.orderbook import OrderBook
from prism_v2.quality import QualityPolicy, assess_book
from prism_v2.reconciliation import reconcile
from prism_v2.replay import MeasurementMode
from prism_v2.risk import RiskGate, RiskLimits
from prism_v2.router import CapitalRouter, RoutingDecision
from prism_v2.sizing import SizingLimits, recommend_size
from prism_v2.venues import HyperliquidAdapter, OKXVenueAdapter, VenueRegistry
from prism_v2.ws_collector import EventCollector, load_events

PROBE_NOTIONAL_USD = 1_000.0
#: Capital de reference du router. Ordre de grandeur d'un petit compte.
CAPITAL_USD = 100.0
#: Pas de scan dans le temps des evenements rejoues.
SCAN_STEP_MS = 500
#: Echantillons cross-venue (REST, deux venues quasi simultanees).
CROSS_VENUE_SAMPLES = 8


def _hr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def _f(v: Optional[float], nd: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}"


def build_engine() -> DiscoveryEngine:
    """Les 9 familles. Ajouter une famille ne modifie aucun module du noyau."""
    return DiscoveryEngine([
        CrossMarketDetector(), CrossVenueDetector(), FundingBasisDetector(),
        ForcedFlowDetector(), BookImbalanceDetector(), DepthWithdrawalDetector(),
        AggressiveFlowDetector(), ShortHorizonReversionDetector(),
        SpreadDislocationDetector(),
    ])


def _feed(tracker: MarketStateTracker, books: "L2BookSet",
          events: List[Dict[str, Any]], specs: Dict[str, Any],
          upto_ms: int, cursor: int) -> int:
    """Injecte dans le tracker tous les evenements <= upto_ms. CAUSAL.

    Les carnets passent par L2BookSet : le canal `books` est INCREMENTIEL,
    et traiter un update comme un carnet complet produirait un carnet
    tronque dont tous les couts seraient faux. Un carnet invalide (trou de
    sequence) n'est jamais transmis au tracker.
    """
    while cursor < len(events):
        ev = events[cursor]
        ts = ev.get("exchange_ts_ms")
        if ts is None or ts > upto_ms:
            if ts is None:
                cursor += 1
                continue
            break
        cursor += 1
        iid = ev.get("inst_id")
        spec = specs.get(iid)
        if spec is None:
            continue
        ch, data = ev.get("channel"), ev.get("data") or {}
        try:
            if ch in ("books", "books5"):
                if books.apply_event(ev):
                    book = books.book(iid)
                    if book is not None:
                        tracker.on_book(book)
            elif ch == "trades":
                px, sz = float(data["px"]), float(data["sz"])
                tracker.on_trade(iid, TradePrint(
                    ts, px, sz, usd_notional(spec, sz, px),
                    data.get("side") == "buy"))
            elif ch == "liquidation-orders":
                for det in data.get("details", []) or []:
                    px, sz = float(det["bkPx"]), float(det.get("sz", 0) or 0)
                    tracker.on_forced_flow(iid, ForcedFlowEvent(
                        int(det.get("ts") or ts), px, sz,
                        usd_notional(spec, sz, px), det.get("side", "")))
        except (KeyError, TypeError, ValueError):
            continue
    return cursor


from prism_v2.core_types import Provenance as _P
_PROV = _P("OKX", "ws:books", "", None)


def run(duration_s: float = 180.0, max_instruments: int = 6,
        json_out: Optional[Path] = None) -> Dict[str, Any]:
    run_id = f"hunt-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    client = OKXPublicClient()
    gate = ModeGate()
    engine = build_engine()
    ledger = CaptureLedger()
    memory = DiscoveryMemory()
    executor = PaperExecutor()
    risk = RiskGate(RiskLimits(max_notional_usd=CAPITAL_USD))
    router = CapitalRouter(SizingLimits(available_capital_usd=CAPITAL_USD,
                                        max_notional_usd=CAPITAL_USD))
    policy = QualityPolicy()
    report: Dict[str, Any] = {"run_id": run_id, "engine_version": __version__,
                              "started_at": utc_now_iso()}

    # ── 1. INSTRUMENTS + VENUES ────────────────────────────────────────────
    _hr("1. INSTRUMENTS ET VENUES")
    registry = client.load_registry(["SWAP", "SPOT"])
    inverse, _ = registry.executable_universe(InstrumentType.SWAP_INVERSE)
    gate.declare("instrument_registry_live", True, "OKX /public/instruments")
    gate.declare("data_quality_gate", True, "prism_v2/quality.py")
    gate.transition(SystemMode.PAPER)

    bases = [s.base for s in inverse[:max_instruments]]
    probes = []
    for b in bases:
        for t in (InstrumentType.SWAP_INVERSE, InstrumentType.SWAP_LINEAR,
                  InstrumentType.SPOT):
            # On ne retient qu'un instrument cote en equivalent USD : comparer
            # un BTC-AED a un BTC-USD-SWAP produirait un ecart de change, pas
            # une inefficience. registry.resolve() rend le PREMIER instrument
            # du type, qui peut etre libelle en EUR ou AED.
            picked = None
            for cand_spec in registry.by_type(t):
                if cand_spec.base != b:
                    continue
                quote = (cand_spec.quote or cand_spec.settle_ccy or "").upper()
                if quote in USD_EQUIVALENT_QUOTES:
                    picked = cand_spec
                    break
            if picked is not None:
                probes.append(picked)
    specs = {s.inst_id: s for s in probes}
    print(f"sous-jacents: {bases}")
    print(f"instruments suivis: {len(probes)} "
          f"(inverse+lineaire+spot pour comparaison cross-market)")

    venues = VenueRegistry()
    venues.register(OKXVenueAdapter(client, registry))
    venues.register(HyperliquidAdapter())
    venues.mark_unavailable("BINANCE", "HTTP 451 depuis cet environnement "
                                       "(restriction geographique)")
    venues.mark_unavailable("BYBIT", "HTTP 403 depuis cet environnement")
    print(f"venues: {venues.health()}")
    report["instruments"] = {"bases": bases, "tracked": sorted(specs)}
    report["venues"] = venues.health()
    report["mode"] = gate.to_dict()

    # ── 2. COLLECTE ────────────────────────────────────────────────────────
    _hr(f"2. COLLECTE ({duration_s:g}s)")
    collector = EventCollector(channels=("books", "trades"))
    ev_path, cstats = collector.collect(probes, duration_s=duration_s)
    events = load_events(ev_path)
    td = cstats.transport_delay_summary()
    print(f"evenements: {cstats.events} | canaux: {dict(cstats.by_channel)} "
          f"| reconnexions: {cstats.reconnects}")
    if td:
        print(f"delai transport: median {td['median_ms']}ms p90 {td['p90_ms']}ms "
              f"(N={td['n']})")
    report["collection"] = {**cstats.to_dict(), "file": str(ev_path)}

    # Funding pour la famille FUNDING_BASIS, sur l'instId REELLEMENT execute.
    tracker = MarketStateTracker()
    for s in probes:
        if not s.inst_type.is_swap:
            continue
        try:
            rows = client.funding_rate(s).payload or []
            if rows:
                tracker.on_funding(s.inst_id, float(rows[0]["fundingRate"]))
        except (MarketDataError, KeyError, TypeError, ValueError):
            pass
    print(f"funding lu pour {len(tracker.funding)} swaps "
          "(chacun sur son propre instId — aucun melange)")

    # ── 3. SCAN CAUSAL ─────────────────────────────────────────────────────
    _hr("3. SCAN CAUSAL — 9 FAMILLES")
    ts_list = [e["exchange_ts_ms"] for e in events if e.get("exchange_ts_ms")]
    if not ts_list:
        raise SystemExit("aucun evenement horodate collecte")
    t_start, t_end = min(ts_list), max(ts_list)
    l2 = L2BookSet(specs=specs)
    cursor, scans, all_candidates = 0, 0, []
    scan_ts = t_start + 5_000        # laisse l'historique se remplir
    t_scan0 = time.perf_counter()
    while scan_ts <= t_end:
        cursor = _feed(tracker, l2, events, specs, scan_ts, cursor)
        states = tracker.all_states(now_ms=scan_ts)
        if states:
            scans += 1
            for out in engine.scan(states):
                all_candidates.extend(out.candidates)
        scan_ts += SCAN_STEP_MS
    scan_wall = time.perf_counter() - t_scan0
    print(f"{scans} instants de scan sur {(t_end - t_start)/1000:.0f}s de flux "
          f"({scan_wall:.1f}s de calcul)")
    print(f"candidates brutes: {len(all_candidates)}")
    l2stats = l2.stats()
    gaps = sum(v["sequence_gaps"] for v in l2stats.values())
    print(f"carnets incrementiels: {len(l2stats)} suivis, "
          f"{sum(v['updates'] for v in l2stats.values())} updates chainees, "
          f"{gaps} trou(s) de sequence, "
          f"checksum disponible={any(v['checksum_available'] for v in l2stats.values())}")
    report["l2_books"] = l2stats

    # ── 4. CROSS-VENUE (REST, echantillons simultanes) ─────────────────────
    _hr("4. CROSS-VENUE (prix EXECUTABLES, pas des tickers)")
    cv_det = CrossVenueDetector()
    cv_candidates: List[Any] = []
    hl_symbols = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "DOGE": "DOGE"}
    cv_rows: List[Dict[str, Any]] = []
    for base in [b for b in bases if b in hl_symbols][:3]:
        okx_spec = registry.resolve(base, InstrumentType.SWAP_LINEAR) or \
            registry.resolve(base, InstrumentType.SWAP_INVERSE)
        if okx_spec is None:
            continue
        for _ in range(CROSS_VENUE_SAMPLES):
            quotes = venues.quotes({"OKX": okx_spec.inst_id,
                                    "HYPERLIQUID": hl_symbols[base]}, depth=30)
            if len(quotes) < 2:
                break
            book = None
            try:
                obs = client.orderbook(okx_spec, depth=50)
                book = OrderBook.from_okx(okx_spec, obs.payload, obs.provenance,
                                          local_recv_ts_ms=int(time.time() * 1000))
            except (MarketDataError, ValueError):
                break
            tracker.on_book(book)
            st = tracker.state(okx_spec.inst_id, now_ms=int(time.time() * 1000))
            if st is None:
                break
            st.venue_quotes = quotes
            out = cv_det.detect(st)
            engine.stats[Family.CROSS_VENUE].states_examined += 1
            engine.stats[Family.CROSS_VENUE].detections += 1
            engine.stats[Family.CROSS_VENUE].candidates += len(out.candidates)
            for c in out.candidates:
                engine.stats[Family.CROSS_VENUE].gross_bps.append(c.gross_capture_bps)
            engine.stats[Family.CROSS_VENUE].last_reason = out.reason
            cv_candidates.extend(out.candidates)
            okx_q, hl_q = quotes["OKX"], quotes["HYPERLIQUID"]
            ob, oa = okx_q.fillable("bid", PROBE_NOTIONAL_USD), okx_q.fillable("ask", PROBE_NOTIONAL_USD)
            hb, ha = hl_q.fillable("bid", PROBE_NOTIONAL_USD), hl_q.fillable("ask", PROBE_NOTIONAL_USD)
            if all((ob, oa, hb, ha)):
                cv_rows.append({
                    "base": base, "okx_symbol": okx_spec.inst_id,
                    "okx_bid": ob["vwap"], "okx_ask": oa["vwap"],
                    "hl_bid": hb["vwap"], "hl_ask": ha["vwap"],
                    "buy_okx_sell_hl_bps": (hb["vwap"] - oa["vwap"]) / oa["vwap"] * 1e4,
                    "buy_hl_sell_okx_bps": (ob["vwap"] - ha["vwap"]) / ha["vwap"] * 1e4,
                    "fee_floor_bps": okx_q.taker_fee_bps + hl_q.taker_fee_bps,
                    "okx_delay_ms": okx_q.transport_delay_ms,
                    "hl_delay_ms": hl_q.transport_delay_ms})
    if cv_rows:
        print(f"{'base':<6}{'sens':<22}{'ecart_bps':>11}{'plancher_frais':>16}"
              f"{'delai_max_ms':>14}")
        print("-" * 72)
        for r in cv_rows:
            for label, v in (("acheter OKX/vendre HL", r["buy_okx_sell_hl_bps"]),
                             ("acheter HL/vendre OKX", r["buy_hl_sell_okx_bps"])):
                print(f"{r['base']:<6}{label:<22}{v:>11.3f}{r['fee_floor_bps']:>16.1f}"
                      f"{max(r['okx_delay_ms'], r['hl_delay_ms']):>14}")
    else:
        print("aucun echantillon cross-venue exploitable")
    all_candidates.extend(cv_candidates)
    report["cross_venue_samples"] = cv_rows

    # ── 5. ECONOMIE (mode DISCOVERY, par bornes) ───────────────────────────
    _hr("5. ECONOMIE PAR BORNES — quelles familles survivent aux frictions ?")
    books_now: Dict[str, OrderBook] = dict(tracker.books)
    verdicts: Counter = Counter()
    by_family: Dict[str, Counter] = {}
    survivors: List[Any] = []
    needs_measurement: List[Any] = []
    skipped_quality = 0
    for cand in all_candidates:
        book = books_now.get(cand.instrument.inst_id)
        if book is None:
            continue
        # FAIL CLOSED : aucune economie sur un carnet dont on ne garantit pas
        # l'integrite (cote vide, croise, metadonnees incoherentes).
        # En replay, la fraicheur se juge sur l'horloge du replay (voir plus
        # bas). Ici on ne verifie que l'INTEGRITE : cote vide, carnet croise,
        # metadonnees incoherentes.
        q0 = assess_book(book, cand.instrument,
                         now_ms=book.ts_ms if book.ts_ms is not None
                         else int(time.time() * 1000),
                         policy=QualityPolicy(max_book_age_ms=10 ** 9))
        if not q0.is_usable:
            skipped_quality += 1
            continue
        try:
            costs = build_breakdown(book, cand.direction.taker_side,
                                    PROBE_NOTIONAL_USD, strict_fees=True)
        except Exception:
            skipped_quality += 1
            continue
        res = discover(cand, costs, book)
        verdicts[res.verdict.value] += 1
        by_family.setdefault(cand.family, Counter())[res.verdict.value] += 1
        engine.record_verdict(Family(cand.family), res.verdict.value)
        st = tracker.state(cand.instrument.inst_id)
        memory.record(Observation(
            family=cand.family, inst_id=cand.instrument.inst_id,
            venue="OKX", verdict=res.verdict.value,
            gross_bps=cand.gross_capture_bps, net_bps=res.net_optimistic_bps,
            realized_bps=None,
            spread_bps=(st.spread_bps if st else None),
            realized_vol_bps=(st.realized_volatility_bps() if st else None),
            depth_usd=cand.capacity_usd, size_usd=PROBE_NOTIONAL_USD,
            latency_ms=None, execution_mode=cand.required_execution,
            hour_utc=time.gmtime().tm_hour, ts_utc=cand.ts_utc))
        if res.verdict is DiscoveryVerdict.SURVIVES_ALL_BOUNDS:
            survivors.append((cand, res, book, costs))
        elif res.verdict is DiscoveryVerdict.NEEDS_MEASUREMENT:
            needs_measurement.append((cand, res))

    print(f"{'famille':<26}{'cand.':>7}{'survit':>8}{'a mesurer':>11}"
          f"{'mort':>7}{'sans edge':>12}")
    print("-" * 74)
    for fam in sorted(by_family):
        c = by_family[fam]
        print(f"{fam:<26}{sum(c.values()):>7}"
              f"{c.get('SURVIVES_ALL_BOUNDS', 0):>8}"
              f"{c.get('NEEDS_MEASUREMENT', 0):>11}"
              f"{c.get('DEAD_EVEN_AT_BEST', 0):>7}{c.get('NO_RAW_EDGE', 0):>12}")
    print(f"\nTOTAL: {dict(verdicts.most_common())}")
    if skipped_quality:
        print(f"candidates ecartees pour qualite de carnet: {skipped_quality}")
    report["discovery_verdicts"] = dict(verdicts)
    report["verdicts_by_family"] = {k: dict(v) for k, v in by_family.items()}

    # ── 6. VALIDATION + ROUTAGE + PAPER ────────────────────────────────────
    _hr("6. VALIDATION CAUSALE -> ROUTER -> EXECUTION PAPER")
    # Mode CAPTURE_VALIDATION : on valide la capture par simulation causale.
    # Le slippage reel est EXCLU explicitement (et non suppose nul) : tout
    # resultat obtenu ici est une BORNE SUPERIEURE de la capture. Le mode
    # EXECUTION, lui, refuserait cette exclusion.
    validation_pool = survivors or [
        (c, r, books_now.get(c.instrument.inst_id), None)
        for c, r in needs_measurement[:50]
        if books_now.get(c.instrument.inst_id) is not None]
    measurement_modes: Dict[str, str] = {}
    scored: List[tuple] = []
    for cand, res, book, _ in validation_pool[:50]:
        side = cand.direction.taker_side
        # En EVENT_REPLAY, "maintenant" est l'horloge du REPLAY : un carnet
        # rejoue est frais RELATIVEMENT a l'instant ou il a ete capture.
        # Le juger contre time.time() rejetterait tout l'echantillon pour une
        # raison qui n'a aucun sens en replay.
        replay_now = book.ts_ms if book.ts_ms is not None else int(time.time() * 1000)
        q = assess_book(book, cand.instrument, now_ms=replay_now, policy=policy)
        # En EVENT_REPLAY, "maintenant" est l'horloge du REPLAY, pas l'horloge
        # murale : un carnet rejoue est frais RELATIVEMENT a l'instant ou il a
        # ete capture. Juger sa fraicheur contre time.time() rejetterait tout
        # l'echantillon pour une raison qui n'a pas de sens en replay.
        med_lat = latency_component_for(cand.instrument.inst_id)
        costs = build_breakdown(book, side, PROBE_NOTIONAL_USD,
                                style=ExecutionStyle.TAKER, strict_fees=False,
                                slippage=slippage_excluded_for_paper_validation(),
                                latency=med_lat)
        costs.funding = funding_not_applicable("horizon court")
        fixed = sum(c.value_bps for c in (costs.fees, costs.spread)
                    if c.value_bps is not None)
        sz = recommend_size(cand.instrument, book, side, cand.gross_capture_bps,
                            fixed, SizingLimits(available_capital_usd=CAPITAL_USD,
                                                max_notional_usd=CAPITAL_USD))
        rd = risk.evaluate(cand.instrument, sz.recommended_notional_usd or 1.0,
                           quality=q, capacity_usd=cand.capacity_usd,
                           reference_price=book.mid, registry_spec=cand.instrument)
        ev = evaluate(cand, costs, quality=q, risk_decision=rd,
                      mode=EvaluationMode.CAPTURE_VALIDATION)
        measurement_modes[cand.candidate_id] = MeasurementMode.EVENT_REPLAY.value
        scored.append((cand, ev, sz))

    val_reasons = Counter()
    for _c, _ev, _sz in scored:
        if _ev.status is CaptureStatus.ACCEPTED:
            val_reasons["ACCEPTED"] += 1
        elif _ev.blocked_by:
            val_reasons[str(_ev.blocked_by)[:120]] += 1
        elif _ev.rejection_reason:
            val_reasons["NET<=0" if "<= 0" in _ev.rejection_reason
                        else "REJECTED"] += 1
        else:
            val_reasons[f"UNRESOLVED:{','.join(_ev.unresolved_components[:2])}"] += 1
    print(f"issues de validation causale: {dict(val_reasons.most_common())}")
    report["validation_reasons"] = dict(val_reasons)

    routed = router.route(scored)
    rsum = router.summary(routed)
    print(f"candidates soumises au router: {len(scored)}")
    print(json.dumps(rsum, indent=1, ensure_ascii=False))
    allocated = [r for r in routed if r.decision is RoutingDecision.ALLOCATE]
    if allocated:
        print(f"\n{'famille':<26}{'instrument':<16}{'alloue$':>10}{'net_bps':>10}"
              f"{'contrainte':<24}")
        for r in allocated[:12]:
            print(f"{r.candidate.family:<26}{r.candidate.instrument.inst_id:<16}"
                  f"{r.allocated_usd:>10.2f}"
                  f"{_f(r.evaluation.expected_net_capture_bps):>10}"
                  f"  {(r.sizing.binding_constraint if r.sizing else '-'):<24}")
    report["routing"] = {"summary": rsum, "rows": [r.to_dict() for r in routed[:60]]}

    n_paper = 0
    for r in allocated:
        book = books_now.get(r.candidate.instrument.inst_id)
        if book is None or r.allocated_usd <= 0:
            continue
        rt = executor.round_trip(r.candidate.instrument, r.candidate.direction,
                                 book, book, r.allocated_usd)
        if rt is None:
            continue
        n_paper += 1
        rec = reconcile(r.candidate, r.evaluation, rt.entry, rt)
        ledger.record(r.candidate, r.evaluation, costs=None, fill=rt.entry,
                      round_trip=rt, reconciliation=rec, run_id=run_id,
                      measurement_mode=measurement_modes.get(
                          r.candidate.candidate_id,
                          MeasurementMode.EVENT_REPLAY.value),
                      notes=(f"famille {r.candidate.family}; router rang {r.rank}; "
                             "slippage reel EXCLU (borne superieure de capture)"))
    print(f"\nallers-retours PAPER executes: {n_paper}")

    # ── 7. PRIORITES + MEMOIRE ─────────────────────────────────────────────
    _hr("7. PRIORITE DE RECHERCHE / MEMOIRE / SANTE")
    prio = engine.update_priorities()
    print("priorite de recherche par famille (l'allocation reste economique) :")
    for f, p in prio.items():
        print(f"  {f:<26}{p:>6.3f}")
    fm = FailureMemory.from_records(ledger.read_all())
    eh = EdgeHealth.from_records(ledger.read_all())
    mem = memory.report(min_n=5)
    print(f"\nmemoire de decouverte: {mem['n_observations']} observations, "
          f"lisible={mem['readable']}")
    if mem["favourable_conditions"]:
        print("conditions les plus favorables (N suffisant) :")
        for c in mem["favourable_conditions"][:5]:
            print(f"  {c['dimension']:<14}{c['bucket']:<24}"
                  f"survie {c['survival_rate']:.0%} (N={c['n_conclusive']})")
    print(f"\nedge health: {eh.report()['evidence']['statement']}")
    report["discovery_report"] = engine.report()
    report["discovery_memory"] = mem
    report["failure_memory"] = fm.to_dict()
    report["edge_health"] = eh.report()
    report["ledger_summary"] = ledger.summary()
    report["paper_round_trips"] = n_paper
    report["finished_at"] = utc_now_iso()

    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                             default=str), encoding="utf-8")
        print(f"\nrapport -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="EDGE_HUNT (PAPER uniquement)")
    ap.add_argument("--duration", type=float, default=180.0)
    ap.add_argument("--instruments", type=int, default=6)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    run(duration_s=a.duration, max_instruments=a.instruments, json_out=a.json)


if __name__ == "__main__":
    main()
