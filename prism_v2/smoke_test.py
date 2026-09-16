#!/usr/bin/env python3
"""Smoke test V2 — chaine complete sur donnees OKX publiques reelles.

    INSTRUMENTS -> COLLECTE WS -> QUALITE -> SPREAD -> CAPACITE
    -> LATENCE (mesuree) -> OPPORTUNITES -> EXPECTED NET CAPTURE
    -> RISQUE -> EXECUTION PAPER -> RECONCILIATION -> LEDGER
    -> FAILURE MEMORY -> EDGE HEALTH

AUCUN ordre reel. AUCUNE cle. Endpoints publics uniquement.

Usage :
  python3 -m prism_v2.smoke_test [--duration 60] [--instruments 5] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2 import __version__
from prism_v2.capacity import capacity_curve, max_notional_without_exhaustion
from prism_v2.core_types import Direction, Provenance, utc_now_iso
from prism_v2.costs import (
    ExecutionStyle, build_breakdown, fees_assumed_public, funding_not_applicable,
    latency_from_replay, legacy_v33_assumption,
)
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.edge_health import EdgeHealth, describe
from prism_v2.execution import PaperExecutor
from prism_v2.failure_memory import FailureMemory
from prism_v2.instruments import InstrumentType
from prism_v2.ledger import CaptureLedger
from prism_v2.market_data import MarketDataError, OKXPublicClient
from prism_v2.opportunities.dislocation import LiquidationDislocationOpportunity
from prism_v2.opportunity import Candidate, DetectionStatus, MarketContext, OpportunityRegistry
from prism_v2.orderbook import OrderBook
from prism_v2.quality import QualityPolicy, assess_book
from prism_v2.reconciliation import reconcile
from prism_v2.replay import (
    DEFAULT_LATENCY_GRID_MS, EventTimeline, MeasurementMode, causal_capture,
    latency_decay_curve,
)
from prism_v2.risk import RiskGate, RiskLimits
from prism_v2.ws_collector import EventCollector, load_events

#: Notionnel de sondage. Ordre de grandeur d'un petit capital, pas un reglage.
PROBE_NOTIONAL_USD = 1_000.0
#: Nombre de points T0 echantillonnes pour la courbe de latence. Plus il y en
#: a, plus le N est grand : ce n'est pas un parametre economique.
LATENCY_SAMPLES = 40


def _hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _fmt(v: Optional[float], nd: int = 4) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}"


def run(duration_s: float = 60.0, max_instruments: int = 5,
        json_out: Optional[Path] = None) -> Dict[str, Any]:
    run_id = f"smoke-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    client = OKXPublicClient()
    ledger = CaptureLedger()
    executor = PaperExecutor()
    risk = RiskGate(RiskLimits())
    policy = QualityPolicy()
    report: Dict[str, Any] = {"run_id": run_id, "engine_version": __version__,
                              "started_at": utc_now_iso(), "execution_mode": "PAPER"}

    # ── 1. INSTRUMENTS ─────────────────────────────────────────────────────
    _hr("1. INSTRUMENT REGISTRY")
    registry = client.load_registry(["SWAP"])
    inverse, excluded = registry.executable_universe(InstrumentType.SWAP_INVERSE)
    print(f"SWAP connus: {len(registry)} | INVERSE executables: {len(inverse)} "
          f"| exclus: {len(excluded)}")
    print("  " + ", ".join(s.inst_id.replace("-USD-SWAP", "") for s in inverse))
    for e in excluded:
        print(f"  EXCLU {e['inst_id']}: {e['reason']}")
    probes = inverse[:max_instruments]
    report["instruments"] = {"n_swap": len(registry),
                             "inverse": [s.to_dict() for s in inverse],
                             "excluded": excluded}
    if not probes:
        raise SystemExit("aucun instrument inverse executable")

    # ── 2. COLLECTE WEBSOCKET ──────────────────────────────────────────────
    _hr(f"2. COLLECTE WEBSOCKET ({duration_s:g}s, {len(probes)} instruments)")
    collector = EventCollector()
    ev_path, cstats = collector.collect(probes, duration_s=duration_s)
    events = load_events(ev_path)
    print(f"evenements: {cstats.events} | canaux: {dict(cstats.by_channel)}")
    print(f"reconnexions: {cstats.reconnects} | doublons: {cstats.duplicates} "
          f"| regressions: {cstats.regressions}")
    td = cstats.transport_delay_summary()
    if td:
        print(f"delai transport exchange->local: min={td['min_ms']}ms "
              f"median={td['median_ms']}ms p90={td['p90_ms']}ms max={td['max_ms']}ms "
              f"(N={td['n']})")
        print("  NB: delai de TRANSPORT, soumis a la derive d'horloge. "
              "Ce n'est PAS la latence aller-retour d'un ordre.")
    report["collection"] = {**cstats.to_dict(), "file": str(ev_path)}

    timelines = {s.inst_id: EventTimeline.from_events(s, events) for s in probes}
    print(f"\n{'instrument':<16}{'carnets':>9}{'cadence_ms':>13}"
          f"{'resolution':>34}")
    for s in probes:
        tl = timelines[s.inst_id]
        iv = tl.update_interval_ms()
        print(f"  {s.inst_id:<16}{len(tl):>7}{_fmt(iv, 0):>13}"
              f"{('deltas < ' + _fmt(iv, 0) + 'ms non mesurables'):>34}")
    print("\nLa cadence de publication de books5 BORNE la resolution : sous cet")
    print("intervalle, une mesure de latence comparerait un carnet a lui-meme.")

    # ── 3. LATENCE MESUREE ─────────────────────────────────────────────────
    _hr("3. DECROISSANCE PAR LATENCE (mesuree sur evenements collectes)")
    latency_report: Dict[str, Any] = {}
    latency_by_inst: Dict[str, Dict[int, float]] = {}
    drift_rows: List[Any] = []
    print(f"{'instrument':<16}{'N':>5}" +
          "".join(f"{d:>9}ms" for d in DEFAULT_LATENCY_GRID_MS[:8]))
    print("-" * 94)
    for s in probes:
        tl = timelines[s.inst_id]
        if len(tl) < 10:
            print(f"{s.inst_id:<16}  echantillon insuffisant ({len(tl)} carnets)")
            continue
        span = (tl.last_ts_ms or 0) - (tl.first_ts_ms or 0)
        if span <= 6_000:
            print(f"{s.inst_id:<16}  fenetre trop courte ({span}ms)")
            continue
        starts = [(tl.first_ts_ms or 0) + int(i * (span - 5_000) / max(1, LATENCY_SAMPLES - 1))
                  for i in range(LATENCY_SAMPLES)]
        acc: Dict[int, List[float]] = {d: [] for d in DEFAULT_LATENCY_GRID_MS}
        surv: Dict[int, List[float]] = {d: [] for d in DEFAULT_LATENCY_GRID_MS}
        drift: Dict[int, List[float]] = {d: [] for d in DEFAULT_LATENCY_GRID_MS}
        n_used = 0
        for t0 in starts:
            pts = latency_decay_curve(tl, t0, Direction.LONG, PROBE_NOTIONAL_USD)
            if not any(p.decay_bps is not None for p in pts):
                continue
            n_used += 1
            for p in pts:
                if p.decay_bps is not None:
                    acc[p.delta_ms].append(p.decay_bps)
                if p.depth_survival_ratio is not None:
                    surv[p.delta_ms].append(p.depth_survival_ratio)
                if p.mid_drift_bps is not None:
                    # Pour un LONG, une derive NEGATIVE du mid est defavorable
                    # (le prix fuit a la hausse est favorable ; a la baisse non).
                    # On mesure la derive ADVERSE : -drift pour un long.
                    drift[p.delta_ms].append(-p.mid_drift_bps)

        def q(vals: List[float], frac: float) -> Optional[float]:
            if not vals:
                return None
            v = sorted(vals)
            return v[min(len(v) - 1, int(frac * (len(v) - 1)))]

        med = {d: q(acc[d], 0.5) for d in DEFAULT_LATENCY_GRID_MS}
        latency_by_inst[s.inst_id] = {d: m for d, m in med.items() if m is not None}
        print(f"{s.inst_id:<16}{n_used:>5}" +
              "".join(f"{_fmt(med[d], 3):>11}" for d in DEFAULT_LATENCY_GRID_MS[:8]))
        latency_report[s.inst_id] = {
            "n_samples": n_used,
            "book_cost_decay_bps": {
                str(d): {"p50": q(acc[d], 0.5), "p90": q(acc[d], 0.9),
                         "max": q(acc[d], 1.0), "n": len(acc[d])}
                for d in DEFAULT_LATENCY_GRID_MS},
            "adverse_mid_drift_bps": {
                str(d): {"p50": q(drift[d], 0.5), "p90": q(drift[d], 0.9),
                         "max": q(drift[d], 1.0), "n": len(drift[d])}
                for d in DEFAULT_LATENCY_GRID_MS},
            "depth_survival": {str(d): q(surv[d], 0.5) for d in DEFAULT_LATENCY_GRID_MS},
        }
        drift_rows.append((s.inst_id, n_used,
                           {d: (q(drift[d], 0.5), q(drift[d], 0.9)) for d in
                            DEFAULT_LATENCY_GRID_MS}))
    print("\n(mediane du cout de CARNET : a ce notionnel le sommet absorbe tout,")
    print(" donc la mediane est souvent nulle. La grandeur economique reelle")
    print(" pour un petit ordre est la DERIVE ADVERSE DU MID ci-dessous.)")
    print(f"\nDERIVE ADVERSE DU MID pendant delta (bps, p50 / p90) — N par instrument")
    print(f"{'instrument':<16}{'N':>4}" +
          "".join(f"{str(d) + 'ms':>14}" for d in (50, 100, 250, 500, 1000, 5000)))
    print("-" * 102)
    for inst_id, n_used, dd in drift_rows:
        cells = []
        for d in (50, 100, 250, 500, 1000, 5000):
            p50, p90 = dd.get(d, (None, None))
            cells.append(("n/r" if p50 is None else
                          f"{_fmt(p50, 2)}/{_fmt(p90, 2)}").rjust(14))
        print(f"{inst_id:<16}{n_used:>4}" + "".join(cells))
    print("\nLecture : derive ADVERSE = mouvement du mid CONTRE la position pendant")
    print("delta. p90 positif = dans 10% des cas, attendre delta coute au moins ca.")
    print("C'est le cout de latence qui compte pour un ordre de petite taille.")
    print("n/r = non resolu : delta sous la cadence de publication du flux.")
    report["latency_decay"] = latency_report

    # ── 4. CAPACITE (carnet REST profond) ──────────────────────────────────
    _hr("4. CAPACITE (REST /market/books, 400 niveaux)")
    books: Dict[str, OrderBook] = {}
    quality: Dict[str, Any] = {}
    capacity_report: Dict[str, Any] = {}
    print(f"{'instrument':<16}{'spread_bps':>12}{'prof_ask$':>14}{'cap_sondee$':>14}"
          f"{'age_ms':>9}{'qualite':>10}")
    print("-" * 78)
    for spec in probes:
        try:
            obs = client.orderbook(spec, depth=400)
            book = OrderBook.from_okx(spec, obs.payload, obs.provenance,
                                      local_recv_ts_ms=int(time.time() * 1000))
        except (MarketDataError, ValueError) as exc:
            print(f"{spec.inst_id:<16} carnet indisponible: {exc}")
            continue
        books[spec.inst_id] = book
        q = assess_book(book, spec, now_ms=int(time.time() * 1000), policy=policy)
        quality[spec.inst_id] = q.to_dict()
        curve = capacity_curve(book, "ask")
        cap = max_notional_without_exhaustion(curve)
        print(f"{spec.inst_id:<16}{book.spread_bps:>12.4f}{book.ask_depth():>14,.0f}"
              f"{(cap or 0):>14,.0f}{(q.book_age_ms or 0):>9}{q.verdict.value:>10}")
        capacity_report[spec.inst_id] = {
            "spread_bps": book.spread_bps, "ask_depth_usd": book.ask_depth(),
            "bid_depth_usd": book.bid_depth(), "max_probed_absorbed_usd": cap,
            "curve": [p.to_dict() for p in curve]}
    report["capacity"] = capacity_report
    report["data_quality"] = quality

    # ── 5. OPPORTUNITES ────────────────────────────────────────────────────
    _hr("5. DETECTION")
    opp_registry = OpportunityRegistry().register(LiquidationDislocationOpportunity())
    print(f"branchees: {opp_registry.names()}")
    detections: Dict[str, Any] = {}
    candidates: List[Candidate] = []
    for spec in probes:
        liq = candles = None
        try:
            liq = client.liquidation_orders(spec, limit=100).payload
        except MarketDataError:
            pass
        try:
            candles = client.candles(spec, bar="1m", limit=100).payload
        except MarketDataError:
            pass
        ctx = MarketContext(instrument=spec, book=books.get(spec.inst_id),
                            liquidations=liq, candles=candles)
        for name, res in opp_registry.detect_all(ctx).items():
            detections.setdefault(spec.inst_id, {})[name] = res.to_dict()
            print(f"  {spec.inst_id:<16}{name:<30}{res.status.value:<20}"
                  f"{len(res.candidates)} cand." +
                  (f" — {res.reason[:48]}" if res.reason else ""))
            candidates.extend(res.candidates)
    report["detections"] = detections

    # ── 6-9. ECONOMIE -> RISQUE -> PAPER -> LEDGER ─────────────────────────
    _hr("6. ECONOMIE -> 7. RISQUE -> 8. EXECUTION PAPER -> 9. LEDGER")
    counters = {"ACCEPTED": 0, "REJECTED": 0, "UNRESOLVED": 0}
    print(f"\n{'instrument':<16}{'type':<26}{'brut':>9}{'couts':>9}{'net':>9}"
          f"{'statut':>12}  motif")
    print("-" * 118)

    def process(cand: Candidate, mode: str, style: ExecutionStyle,
                latency_component=None, note: str = "") -> None:
        # Le carnet est RE-TELECHARGE a l'instant de la decision. Reutiliser
        # celui de l'etape 4 (vieux de plusieurs minutes a ce stade) ferait
        # decider sur une donnee perimee — le kill switch STALE_BOOK le
        # refuserait, a juste titre. Un systeme reel rafraichit avant de decider.
        try:
            fresh = client.orderbook(cand.instrument, depth=400)
            book = OrderBook.from_okx(cand.instrument, fresh.payload,
                                      fresh.provenance,
                                      local_recv_ts_ms=int(time.time() * 1000))
            books[cand.instrument.inst_id] = book
        except (MarketDataError, ValueError):
            book = books.get(cand.instrument.inst_id)
        if book is None:
            return
        side = cand.direction.taker_side
        q = assess_book(book, cand.instrument, now_ms=int(time.time() * 1000),
                        policy=policy)
        costs = build_breakdown(book, side, PROBE_NOTIONAL_USD, style=style,
                                strict_fees=False, latency=latency_component)
        costs.funding = funding_not_applicable("horizon de mesure intra-periode")
        rd = risk.evaluate(cand.instrument, PROBE_NOTIONAL_USD, quality=q,
                           capacity_usd=cand.capacity_usd,
                           reference_price=book.mid, registry_spec=cand.instrument)
        ev = evaluate(cand, costs, required_notional_usd=None, quality=q,
                      risk_decision=rd)
        counters[ev.status.value] += 1
        fill = rt = None
        if ev.status is CaptureStatus.ACCEPTED and rd.approved_notional_usd > 0:
            fill = executor.submit(cand.instrument, cand.direction, book,
                                   rd.approved_notional_usd)
            rt = executor.round_trip(cand.instrument, cand.direction, book, book,
                                     rd.approved_notional_usd)
        rec = reconcile(cand, ev, fill, rt)
        ledger.record(cand, ev, costs=costs, fill=fill, round_trip=rt,
                      reconciliation=rec, run_id=run_id, notes=note,
                      measurement_mode=mode, data_quality=q.to_dict(),
                      risk=rd.to_dict(),
                      market_state={"mid": book.mid, "spread_bps": book.spread_bps,
                                    "ask_depth_usd": book.ask_depth()},
                      registry_version=registry.provenance.fetched_at
                      if registry.provenance else None)
        motif = (ev.rejection_reason or ev.blocked_by or rec.detail or "")[:52]
        print(f"{cand.instrument.inst_id:<16}{cand.opportunity_type[:25]:<26}"
              f"{cand.gross_capture_bps:>9.3f}"
              f"{(ev.total_cost_bps if ev.total_cost_bps is not None else float('nan')):>9.3f}"
              f"{(ev.expected_net_capture_bps if ev.expected_net_capture_bps is not None else float('nan')):>9.3f}"
              f"{ev.status.value:>12}  {motif}")

    for cand in candidates:
        process(cand, MeasurementMode.EVENT_REPLAY.value, ExecutionStyle.TAKER,
                note="mesure ex-post M2 — borne superieure, non executable par construction")

    # ── 8bis. MESURE : plancher de cout aller-retour ───────────────────────
    # DISTINCTION STRUCTURANTE. Ci-dessus, une DECISION : elle traverse
    # qualite -> economie -> capacite -> risque, et ne s'execute que si tout
    # passe. Ici, une MESURE : caracteriser le cout de traversee d'un
    # instrument. Une mesure n'a pas besoin de connaitre le slippage reel pour
    # etre valide — elle a besoin de dire ce qu'elle EXCLUT.
    #
    # Ce que ce chiffre est : le plancher de cout d'un aller-retour immediat
    # sur le carnet observe (spread traverse deux fois + impact + frais ASSUMED).
    # Ce qu'il N'EST PAS : un cout d'execution reel. Il exclut par construction
    # la latence d'arrivee, la file d'attente et l'adverse selection. C'est donc
    # une BORNE INFERIEURE du cout, jamais une prevision.
    #
    # Le controle n'est PAS une opportunite : gross=0 par construction, il ne
    # peut donc jamais etre ACCEPTED, et un test garantit qu'aucun controle ne
    # peut se faire compter comme une opportunite.
    _hr("8bis. MESURE DU PLANCHER DE COUT ALLER-RETOUR (execution PAPER reelle)")
    print(f"{'instrument':<16}{'notionnel$':>12}{'contrats':>10}{'entree':>13}"
          f"{'sortie':>13}{'frais_bps':>11}{'AR_bps':>10}{'PnL_USD':>10}{'debouclee':>11}")
    print("-" * 106)
    floor_rows: List[Dict[str, Any]] = []
    for spec in probes:
        try:
            fresh = client.orderbook(spec, depth=400)
            book = OrderBook.from_okx(spec, fresh.payload, fresh.provenance,
                                      local_recv_ts_ms=int(time.time() * 1000))
        except (MarketDataError, ValueError) as exc:
            print(f"{spec.inst_id:<16} carnet indisponible: {exc}")
            continue
        q = assess_book(book, spec, now_ms=int(time.time() * 1000), policy=policy)
        if not q.is_usable:          # FAIL CLOSED s'applique aussi aux mesures
            print(f"{spec.inst_id:<16} donnee inutilisable: {q.verdict.value} "
                  f"{[i.value for i in q.issues]}")
            continue
        rd = risk.evaluate(spec, PROBE_NOTIONAL_USD, quality=q,
                           capacity_usd=min(book.bid_depth(), book.ask_depth()),
                           reference_price=book.mid, registry_spec=spec)
        if not rd.allowed or rd.approved_notional_usd <= 0:
            print(f"{spec.inst_id:<16} risque: {rd.reasons}")
            continue
        rt = executor.round_trip(spec, Direction.LONG, book, book,
                                 rd.approved_notional_usd)
        if rt is None:
            print(f"{spec.inst_id:<16} aller-retour impossible")
            continue
        med = latency_by_inst.get(spec.inst_id, {})
        lat = (latency_from_replay(med[100], 100, f"replay WS {spec.inst_id}",
                                   n=LATENCY_SAMPLES) if 100 in med else None)
        ctrl = Candidate(
            ts_utc=utc_now_iso(), instrument=spec,
            opportunity_type="EXECUTION_CONTROL_PAPER", direction=Direction.LONG,
            gross_capture_bps=0.0,
            capacity_usd=min(book.bid_depth(), book.ask_depth()),
            provenance=Provenance("OKX", "/market/books", utc_now_iso(), spec.inst_id),
            metadata={"is_control": True, "not_an_opportunity": True,
                      "is_cost_lower_bound": True,
                      "excludes": ["latence d'arrivee", "file d'attente",
                                   "adverse selection", "slippage reel"],
                      "purpose": "plancher de cout aller-retour sur carnet observe",
                      "latency_measured_bps": med.get(100)})
        costs = build_breakdown(book, "ask", rd.approved_notional_usd,
                                style=ExecutionStyle.TAKER, strict_fees=False,
                                latency=lat)
        costs.funding = funding_not_applicable("aller-retour instantane")
        ev = evaluate(ctrl, costs, quality=q, risk_decision=rd)
        rec = reconcile(ctrl, ev, rt.entry, rt)
        ledger.record(ctrl, ev, costs=costs, fill=rt.entry, round_trip=rt,
                      reconciliation=rec, run_id=run_id,
                      measurement_mode=MeasurementMode.LIVE_PAPER.value,
                      data_quality=q.to_dict(), risk=rd.to_dict(),
                      market_state={"mid": book.mid, "spread_bps": book.spread_bps},
                      registry_version=registry.provenance.fetched_at
                      if registry.provenance else None,
                      notes="MESURE (pas une decision) : plancher de cout "
                            "aller-retour. Exclut latence, file d'attente et "
                            "adverse selection — borne INFERIEURE du cout reel.")
        counters[ev.status.value] += 1
        print(f"{spec.inst_id:<16}{rt.entry.filled_notional_usd:>12,.0f}"
              f"{rt.entry.contracts:>10g}{rt.entry.exec_price:>13g}"
              f"{rt.exit.exec_price:>13g}{rt.total_fee_bps:>11.3f}"
              f"{rt.realized_bps:>10.3f}{rt.realized_pnl_usd:>10.4f}"
              f"{str(rt.fully_closed):>11}")
        floor_rows.append({"inst_id": spec.inst_id,
                           "notional_usd": rt.entry.filled_notional_usd,
                           "contracts": rt.entry.contracts,
                           "entry_px": rt.entry.exec_price,
                           "exit_px": rt.exit.exec_price,
                           "fee_bps": rt.total_fee_bps,
                           "round_trip_bps": rt.realized_bps,
                           "realized_pnl_usd": rt.realized_pnl_usd,
                           "realized_pnl_settle_ccy": rt.realized_pnl_settle_ccy,
                           "settle_ccy": rt.settle_ccy,
                           "fully_closed": rt.fully_closed,
                           "latency_measured_bps_at_100ms": med.get(100)})
    print("\nBORNE INFERIEURE du cout : exclut latence d'arrivee, file d'attente")
    print("et adverse selection. Le cout reel sera SUPERIEUR, jamais inferieur.")
    report["cost_floor_paper"] = floor_rows

    # ── 10. MEMOIRE ET SANTE ───────────────────────────────────────────────
    _hr("10. LEDGER / FAILURE MEMORY / EDGE HEALTH")
    rows = ledger.read_all()
    summary = ledger.summary()
    fm = FailureMemory.from_records(rows)
    eh = EdgeHealth.from_records(rows)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print("\nFAILURE MEMORY:")
    print(json.dumps(fm.to_dict()["counts"], indent=1, ensure_ascii=False))
    print(json.dumps(fm.data_vs_economics(), indent=1, ensure_ascii=False))
    print("\nEDGE HEALTH:")
    print(json.dumps(eh.report()["evidence"], indent=1, ensure_ascii=False))
    report["ledger_summary"] = summary
    report["failure_memory"] = fm.to_dict()
    report["edge_health"] = eh.report()
    report["counters"] = counters

    legacy = legacy_v33_assumption()
    print(f"\nHypothese heritee v33 : {legacy.value_bps} bps [{legacy.quality.value}] "
          "— citee, jamais appliquee")
    report["finished_at"] = utc_now_iso()
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                             default=str), encoding="utf-8")
        print(f"\nrapport JSON -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke test PRISM V2 (PAPER uniquement)")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--instruments", type=int, default=5)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    run(duration_s=a.duration, max_instruments=a.instruments, json_out=a.json)


if __name__ == "__main__":
    main()
