#!/usr/bin/env python3
"""Smoke test V2 — pipeline complet sur donnees OKX publiques reelles.

Enchaine : instruments -> carnets -> spreads -> capacite -> opportunites ->
Expected Net Capture -> execution PAPER -> reconciliation -> ledger.

AUCUN ordre reel. AUCUNE cle. Endpoints publics uniquement.

Usage :
  python3 -m prism_v2.smoke_test [--duration 30] [--instruments N] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2 import __version__
from prism_v2.capacity import capacity_curve, max_notional_without_exhaustion
from prism_v2.collector import L2Collector, load_snapshots, spread_statistics
from prism_v2.core_types import Direction, utc_now_iso
from prism_v2.costs import build_breakdown, legacy_v33_assumption
from prism_v2.economics import CaptureStatus, evaluate
from prism_v2.execution import PaperExecutor
from prism_v2.instruments import InstrumentType
from prism_v2.ledger import CaptureLedger
from prism_v2.market_data import MarketDataError, OKXPublicClient
from prism_v2.opportunities.dislocation import LiquidationDislocationOpportunity
from prism_v2.opportunity import (
    DetectionStatus, MarketContext, OpportunityRegistry,
)
from prism_v2.orderbook import OrderBook
from prism_v2.reconciliation import reconcile

#: Notionnel de reference du smoke test. Ordre de grandeur d'un petit capital,
#: pas un parametre optimise.
PROBE_NOTIONAL_USD = 1_000.0


def _hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def run(duration_s: float = 20.0, max_instruments: int = 5,
        json_out: Path | None = None) -> Dict[str, Any]:
    run_id = f"smoke-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    client = OKXPublicClient()
    report: Dict[str, Any] = {"run_id": run_id, "engine_version": __version__,
                              "started_at": utc_now_iso(), "execution_mode": "PAPER"}

    # ── 1. INSTRUMENTS ─────────────────────────────────────────────────────
    _hr("1. INSTRUMENT REGISTRY (source unique de verite)")
    registry = client.load_registry(["SWAP"])
    inverse, excluded = registry.executable_universe(InstrumentType.SWAP_INVERSE)
    linear, _ = registry.executable_universe(InstrumentType.SWAP_LINEAR)
    print(f"instruments SWAP connus : {len(registry)}")
    print(f"univers INVERSE executable : {len(inverse)}   exclus : {len(excluded)}")
    print(f"univers LINEAR executable  : {len(linear)}")
    print(f"\n{'instId':<16}{'ctVal':>7}{'ctMult':>8}{'settle':>8}{'lotSz':>8}"
          f"{'minSz':>8}{'tickSz':>10}{'lever':>7}")
    for s in inverse:
        print(f"{s.inst_id:<16}{s.ct_val:>7g}{s.ct_mult:>8g}{s.settle_ccy:>8}"
              f"{s.lot_size:>8g}{s.min_size:>8g}{s.tick_size:>10g}{s.lever:>7g}")
    for e in excluded:
        print(f"  EXCLU {e['inst_id']}: {e['reason']}")
    report["instruments"] = {
        "n_swap_known": len(registry),
        "inverse_universe": [s.to_dict() for s in inverse],
        "inverse_excluded": excluded,
        "n_linear": len(linear),
        "registry_fetched_at": registry.provenance.fetched_at if registry.provenance else None,
    }

    probes = inverse[:max_instruments]
    if not probes:
        raise SystemExit("aucun instrument inverse executable — smoke test impossible")
    reg_path = registry.save(Path(__file__).parent / "data" / f"instruments_{run_id}.json")
    print(f"\nregistry persiste -> {reg_path.relative_to(Path.cwd()) if reg_path.is_relative_to(Path.cwd()) else reg_path}")

    # ── 2. COLLECTE L2 ─────────────────────────────────────────────────────
    _hr(f"2. COLLECTE L2 ({duration_s:g}s sur {len(probes)} instruments)")
    collector = L2Collector(client, depth=50)
    snap_path, stats = collector.collect(probes, duration_s=duration_s, interval_s=3.0)
    snapshots = load_snapshots(snap_path)
    print(f"snapshots: {stats.snapshots}  cycles: {stats.cycles}  erreurs: {stats.errors}")
    print(f"fichier  : {snap_path.name}")
    report["collection"] = stats.to_dict()
    report["collection"]["file"] = str(snap_path)

    # ── 3. SPREADS OBSERVES ────────────────────────────────────────────────
    _hr("3. SPREADS OBSERVES (fenetre de collecte)")
    spreads = spread_statistics(snapshots)
    print(f"{'instId':<16}{'n':>5}{'min':>10}{'median':>10}{'mean':>10}{'max':>10}")
    for inst, st in sorted(spreads.items()):
        print(f"{inst:<16}{st['n']:>5.0f}{st['min_bps']:>10.4f}{st['median_bps']:>10.4f}"
              f"{st['mean_bps']:>10.4f}{st['max_bps']:>10.4f}")
    print("\nATTENTION: fenetre courte -> PLANCHER observe, pas une verite tous regimes.")
    report["spreads_bps"] = spreads

    # ── 4. CAPACITE ────────────────────────────────────────────────────────
    _hr("4. COURBE COUT / CAPACITE (cote ask, achat)")
    books: Dict[str, OrderBook] = {}
    capacity_report: Dict[str, Any] = {}
    for spec in probes:
        try:
            obs = client.orderbook(spec, depth=400)
            book = OrderBook.from_okx(spec, obs.payload, obs.provenance)
        except (MarketDataError, ValueError) as exc:
            print(f"{spec.inst_id}: carnet indisponible ({exc})")
            continue
        books[spec.inst_id] = book
        curve = capacity_curve(book, "ask")
        cap = max_notional_without_exhaustion(curve)
        print(f"\n{spec.inst_id}  mid={book.mid:g}  profondeur_ask=${book.ask_depth():,.0f}  "
              f"max_sonde_absorbe=${cap:,.0f}" if cap else f"\n{spec.inst_id}")
        print(f"  {'notionnel':>12}{'impact_bps':>12}{'aller-retour':>14}{'fill':>8}{'niveaux':>9}")
        for p in curve:
            if p.notional_usd not in (100.0, 1_000.0, 10_000.0, 100_000.0, 1_000_000.0):
                continue
            imp = "n/a" if p.impact_bps is None else f"{p.impact_bps:.4f}"
            tot = "n/a" if p.total_estimated_cost_bps is None else f"{p.total_estimated_cost_bps:.4f}"
            print(f"  ${p.notional_usd:>11,.0f}{imp:>12}{tot:>14}{p.fill_ratio:>7.0%}"
                  f"{p.levels_consumed:>9}")
        capacity_report[spec.inst_id] = {
            "mid": book.mid, "ask_depth_usd": book.ask_depth(),
            "bid_depth_usd": book.bid_depth(),
            "max_probed_absorbed_usd": cap,
            "curve": [p.to_dict() for p in curve],
        }
    report["capacity"] = capacity_report

    # ── 5. OPPORTUNITES ────────────────────────────────────────────────────
    _hr("5. DETECTION D'OPPORTUNITES")
    opp_registry = OpportunityRegistry().register(LiquidationDislocationOpportunity())
    print(f"opportunites branchees: {opp_registry.names()}")

    contexts: List[MarketContext] = []
    for spec in probes:
        book = books.get(spec.inst_id)
        liq = candles = None
        try:
            liq = client.liquidation_orders(spec, limit=100).payload
        except MarketDataError as exc:
            print(f"  {spec.inst_id}: liquidations indisponibles ({exc})")
        try:
            candles = client.candles(spec, bar="1m", limit=100).payload
        except MarketDataError as exc:
            print(f"  {spec.inst_id}: bougies indisponibles ({exc})")
        contexts.append(MarketContext(instrument=spec, book=book,
                                      liquidations=liq, candles=candles))

    detections: Dict[str, Any] = {}
    all_candidates = []
    for ctx in contexts:
        results = opp_registry.detect_all(ctx)
        detections[ctx.instrument.inst_id] = {k: v.to_dict() for k, v in results.items()}
        for name, res in results.items():
            flag = {DetectionStatus.OK: "OK",
                    DetectionStatus.INSUFFICIENT_DATA: "INSUFFICIENT_DATA",
                    DetectionStatus.ERROR: "ERROR"}[res.status]
            print(f"  {ctx.instrument.inst_id:<16} {name:<28} {flag:<20} "
                  f"{len(res.candidates)} candidat(s)"
                  + (f" — {res.reason[:60]}" if res.reason else ""))
            all_candidates.extend(res.candidates)
    report["detections"] = detections

    # ── 6-8. ECONOMIE, EXECUTION PAPER, LEDGER ─────────────────────────────
    _hr("6. EXPECTED NET CAPTURE -> 7. EXECUTION PAPER -> 8. LEDGER")
    ledger = CaptureLedger()
    executor = PaperExecutor()
    counters = {"ACCEPTED": 0, "REJECTED": 0, "UNRESOLVED": 0}
    unknown_counts: Dict[str, int] = {}
    rows: List[Dict[str, Any]] = []

    print(f"\n{'instrument':<16}{'brut_bps':>10}{'couts_bps':>11}{'net_bps':>10}"
          f"{'statut':>12}{'qualite':>10}  motif")
    print("-" * 110)

    for cand in all_candidates:
        book = books.get(cand.instrument.inst_id)
        if book is None:
            continue
        side = cand.direction.taker_side
        # Posture STRICTE : frais UNKNOWN tant que le tier du compte n'est pas lu.
        strict = build_breakdown(book, side, PROBE_NOTIONAL_USD,
                                 strict_fees=True, paper_slippage=False)
        ev_strict = evaluate(cand, strict, required_notional_usd=PROBE_NOTIONAL_USD)
        # Posture ASSUMED : bareme public Lv1 + slippage PAPER, clairement etiquetee.
        assumed = build_breakdown(book, side, PROBE_NOTIONAL_USD,
                                  strict_fees=False, paper_slippage=True)
        from prism_v2.costs import funding_not_applicable
        assumed.funding = funding_not_applicable(
            "horizon de mesure intra-periode (aucun reglement de funding traverse)")
        ev = evaluate(cand, assumed, required_notional_usd=PROBE_NOTIONAL_USD)

        counters[ev.status.value] += 1
        for c in ev_strict.unresolved_components:
            unknown_counts[c] = unknown_counts.get(c, 0) + 1

        fill = rt = None
        if ev.status is CaptureStatus.ACCEPTED:
            fill = executor.submit(cand.instrument, cand.direction, book, PROBE_NOTIONAL_USD)
            rt = executor.round_trip(cand.instrument, cand.direction, book, book,
                                     PROBE_NOTIONAL_USD)
        rec = reconcile(cand, ev, fill, rt)
        ledger.record(cand, ev, costs=assumed, fill=fill, round_trip=rt,
                      reconciliation=rec, run_id=run_id,
                      notes=("posture ASSUMED (bareme public Lv1). Posture STRICTE: "
                             f"{ev_strict.status.value}"
                             + (f", non resolus={ev_strict.unresolved_components}"
                                if ev_strict.unresolved_components else "")))

        motif = (ev.rejection_reason or rec.detail or "")[:46]
        print(f"{cand.instrument.inst_id:<16}{cand.gross_capture_bps:>10.3f}"
              f"{(ev.total_cost_bps if ev.total_cost_bps is not None else float('nan')):>11.3f}"
              f"{(ev.expected_net_capture_bps if ev.expected_net_capture_bps is not None else float('nan')):>10.3f}"
              f"{ev.status.value:>12}{ev.weakest_quality.value:>10}  {motif}")
        rows.append({"inst_id": cand.instrument.inst_id,
                     "gross_bps": cand.gross_capture_bps,
                     "status_assumed": ev.status.value,
                     "status_strict": ev_strict.status.value,
                     "net_bps": ev.expected_net_capture_bps,
                     "realized_bps": rt.realized_bps if rt else None})

    report["evaluations"] = {"counters": counters, "rows": rows,
                             "strict_unknown_counts": unknown_counts,
                             "probe_notional_usd": PROBE_NOTIONAL_USD}

    # ── 8bis. CONTROLE D'EXECUTION PAPER ───────────────────────────────────
    # Une opportunite rejetee ne s'execute pas — c'est le comportement voulu.
    # Ce controle execute donc un aller-retour PAPER sur chaque carnet reel,
    # independamment de toute opportunite, pour deux raisons :
    #   1. prouver que le PaperExecutor fonctionne sur donnees reelles ;
    #   2. MESURER le plancher de cout aller-retour reellement observe, par
    #      instrument — la grandeur que V33 postulait a 28 bps sans jamais
    #      la confronter a un carnet.
    # gross_capture_bps=0 par construction : ce n'est pas une opportunite.
    _hr("8bis. CONTROLE D'EXECUTION PAPER (plancher de cout aller-retour observe)")
    from prism_v2.core_types import Provenance
    from prism_v2.opportunity import Candidate as _Cand

    control_rows: List[Dict[str, Any]] = []
    print(f"{'instrument':<16}{'notionnel':>11}{'contrats':>10}{'entree':>12}{'sortie':>12}"
          f"{'frais_bps':>11}{'AR_reel_bps':>13}{'PnL_USD':>11}")
    print("-" * 96)
    for spec in probes:
        book = books.get(spec.inst_id)
        if book is None:
            continue
        rt = executor.round_trip(spec, Direction.LONG, book, book, PROBE_NOTIONAL_USD)
        if rt is None:
            print(f"{spec.inst_id:<16}  aller-retour impossible (carnet insuffisant)")
            continue
        ctrl = _Cand(ts_utc=utc_now_iso(), instrument=spec,
                     opportunity_type="EXECUTION_CONTROL_PAPER",
                     direction=Direction.LONG, gross_capture_bps=0.0,
                     capacity_usd=min(book.bid_depth(), book.ask_depth()),
                     provenance=Provenance("OKX", "/market/books", utc_now_iso(),
                                           spec.inst_id),
                     metadata={"is_control": True, "not_an_opportunity": True,
                               "purpose": ("mesure du plancher de cout aller-retour "
                                           "sur carnet reel ; gross=0 par construction"),
                               "book_ts": book.ts_utc, "seq_id": book.seq_id})
        costs_ctrl = build_breakdown(book, "ask", PROBE_NOTIONAL_USD,
                                     strict_fees=False, paper_slippage=True)
        costs_ctrl.funding = funding_not_applicable("aller-retour instantane")
        ev_ctrl = evaluate(ctrl, costs_ctrl)
        rec_ctrl = reconcile(ctrl, ev_ctrl, rt.entry, rt)
        ledger.record(ctrl, ev_ctrl, costs=costs_ctrl, fill=rt.entry, round_trip=rt,
                      reconciliation=rec_ctrl, run_id=run_id,
                      notes="CONTROLE d'execution PAPER — pas une opportunite detectee")
        print(f"{spec.inst_id:<16}${rt.entry.filled_notional_usd:>10,.0f}"
              f"{rt.entry.contracts:>10g}{rt.entry.exec_price:>12g}{rt.exit.exec_price:>12g}"
              f"{rt.total_fee_bps:>11.3f}{rt.realized_bps:>13.3f}{rt.realized_pnl_usd:>11.4f}")
        control_rows.append({"inst_id": spec.inst_id,
                             "filled_notional_usd": rt.entry.filled_notional_usd,
                             "contracts": rt.entry.contracts,
                             "entry_px": rt.entry.exec_price, "exit_px": rt.exit.exec_price,
                             "fee_bps": rt.total_fee_bps,
                             "round_trip_realized_bps": rt.realized_bps,
                             "realized_pnl_usd": rt.realized_pnl_usd,
                             "realized_pnl_settle_ccy": rt.realized_pnl_settle_ccy,
                             "settle_ccy": rt.settle_ccy})
    print("\nLecture : AR_reel_bps = cout d'un aller-retour immediat sur le carnet")
    print("observe (spread traverse deux fois + frais ASSUMED Lv1 taker). C'est un")
    print("PLANCHER : il ne contient ni latence, ni derive, ni adverse selection.")
    report["paper_execution_control"] = control_rows

    # ── 9. SYNTHESE ────────────────────────────────────────────────────────
    _hr("9. SYNTHESE LEDGER")
    summary = ledger.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    report["ledger_summary"] = summary

    legacy = legacy_v33_assumption()
    print(f"\nHypothese heritee v33 (citee, JAMAIS appliquee) : "
          f"{legacy.value_bps} bps [{legacy.quality.value}]")
    print(f"Composantes UNKNOWN en posture stricte : {unknown_counts or 'aucune'}")

    report["finished_at"] = utc_now_iso()
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str),
                                  encoding="utf-8")
        print(f"\nrapport JSON -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke test PRISM V2 (PAPER uniquement)")
    ap.add_argument("--duration", type=float, default=20.0, help="duree de collecte L2 (s)")
    ap.add_argument("--instruments", type=int, default=5, help="nombre d'instruments sondes")
    ap.add_argument("--json", type=Path, default=None, help="chemin du rapport JSON")
    a = ap.parse_args()
    run(duration_s=a.duration, max_instruments=a.instruments, json_out=a.json)


if __name__ == "__main__":
    main()
