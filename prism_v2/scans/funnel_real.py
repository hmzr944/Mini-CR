"""Entonnoir des familles jusqu'au PnL net, sur carnets REELS rejoues.

    python3 -m prism_v2.scans.funnel_real <feed.jsonl>

Protocole gele dans prism_v2/PROTOCOLE_ENTONNOIR_REEL.md AVANT mesure.

Repond aux cinq questions qui manquaient :
  1. combien de candidats par famille ?
  2. combien rejetes parce que l'edge brut est insuffisant ?
  3. combien bloques par un cout INCONNU (fenetre, profondeur) ?
  4. combien restent positifs apres execution simulee realiste ?
  5. quel PnL net par jour et par euro immobilise ?
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

from prism_v2.core_types import Provenance
from prism_v2.discovery import DetectionStatus
from prism_v2.instruments import parse_okx_instrument
from prism_v2.market_state import MarketState, TradePrint
from prism_v2.orderbook import OrderBook
from prism_v2.replay import EventTimeline, causal_capture

ROOT = Path(__file__).resolve().parents[2]

TAKER_FEE_BPS = 5.0
FEES_ROUNDTRIP_BPS = 2 * TAKER_FEE_BPS
NOTIONALS = (100.0, 1_000.0, 10_000.0)
LATENCIES_MS = (200, 1_000, 3_000)
HOLDS_S = (30, 120, 300)          # amendement 1 : 5 s non mesurable
MIN_RESOLVED = 30
MIN_T = 3.0


def load_feed(path: Path, specs: dict):
    """(timelines par instrument, etats de marche ordonnes)."""
    rows = defaultdict(list)
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            rows[d["i"]].append(d)
    prov = Provenance(exchange="OKX", endpoint="/market/books",
                      fetched_at="2026-09-21T00:00:00Z", inst_id="")
    timelines, states = {}, []
    for inst, rs in rows.items():
        raw = specs.get(inst)
        if not raw:
            continue
        spec = parse_okx_instrument(raw, prov)
        rs.sort(key=lambda r: r["ts"])
        books = []
        for r in rs:
            payload = [{"bids": [[f"{p:.10f}", f"{q:.8f}"] for p, q in r["b"]],
                        "asks": [[f"{p:.10f}", f"{q:.8f}"] for p, q in r["a"]],
                        "ts": str(r["ts"]), "seqId": 0}]
            books.append((r["ts"], OrderBook.from_okx(spec, payload, prov)))
        tl = EventTimeline(instrument=spec)
        tl._ts = [b[0] for b in books]
        tl._books = [b[1] for b in books]
        timelines[inst] = tl
        for n, r in enumerate(rs):
            if n < 6:
                continue
            w = rs[max(0, n - 20):n + 1]
            states.append((inst, MarketState(
                instrument=spec, ts_ms=r["ts"], book=books[n][1],
                mid_history=[(x["ts"], (x["b"][0][0] + x["a"][0][0]) / 2) for x in w],
                depth_history=[(x["ts"], x["b"][0][0] * x["b"][0][1],
                                x["a"][0][0] * x["a"][0][1]) for x in w],
                spread_history=[(x["ts"], 1e4 * (x["a"][0][0] - x["b"][0][0])
                                 / ((x["a"][0][0] + x["b"][0][0]) / 2)) for x in w],
                trades=[TradePrint(ts_ms=int(a[0]), price=float(a[1]),
                                   size_native=float(a[2]),
                                   notional_usd=float(a[1]) * float(a[2]),
                                   is_buy=(a[3] == "buy"))
                        for a in (r.get("tr") or [])],
                forced_flow=[])))
    return timelines, states


def main() -> None:
    import urllib.parse
    import urllib.request
    feed = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "replay_feed.jsonl"
    raw = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://www.okx.com/api/v5/public/instruments?instType=SWAP",
        headers={"User-Agent": "prism/1.0"}), timeout=25))
    specs = {i["instId"]: i for i in raw["data"]}
    timelines, states = load_feed(feed, specs)
    span = (max(s.ts_ms for _, s in states) - min(s.ts_ms for _, s in states)) / 1000
    print(f"{len(states)} etats, {len(timelines)} instruments, "
          f"{span/60:.1f} min de fenetre\n")

    from prism_v2.scans.family_autopsy import detectors
    dets = detectors()

    # ── etape 1 : detection ────────────────────────────────────────────────
    cands = defaultdict(list)
    funnel = defaultdict(Counter)
    for inst, s in states:
        for d in dets:
            fam = d.family.value
            funnel[fam]["etats"] += 1
            try:
                out = d.detect(s)
            except Exception:
                funnel[fam]["erreur"] += 1
                continue
            if out.status is DetectionStatus.INSUFFICIENT_DATA:
                funnel[fam]["donnees_ko"] += 1
            elif not out.candidates:
                funnel[fam]["seuil_ko"] += 1
            else:
                funnel[fam]["candidats"] += len(out.candidates)
                for c in out.candidates:
                    cands[fam].append((inst, s.ts_ms, c))

    # ── etape 2 : capture causale, balayage declare ────────────────────────
    cells = []
    for fam, lst in cands.items():
        for notion in NOTIONALS:
            for lat in LATENCIES_MS:
                for hold in HOLDS_S:
                    nets, gross, impact, unres = [], [], [], Counter()
                    for inst, ts, c in lst:
                        cap = causal_capture(timelines[inst], ts, c.direction,
                                             notion, lat, hold * 1000)
                        if not cap.resolved:
                            unres[cap.reason[:42]] += 1
                            continue
                        gross.append(cap.gross_bps)
                        impact.append((cap.entry_cost_bps or 0.0)
                                      + (cap.exit_cost_bps or 0.0))
                        nets.append(cap.net_before_fees_bps - FEES_ROUNDTRIP_BPS)
                    if not nets:
                        cells.append(dict(fam=fam, notion=notion, lat=lat, hold=hold,
                                          n=0, mean=float("nan"), t=float("nan"),
                                          gross=float("nan"), impact=float("nan"),
                                          unres=sum(unres.values()),
                                          why=dict(unres.most_common(1))))
                        continue
                    m = st.fmean(nets)
                    sd = st.stdev(nets) if len(nets) > 1 else 0.0
                    t = m / (sd / math.sqrt(len(nets))) if sd > 0 else 0.0
                    cells.append(dict(fam=fam, notion=notion, lat=lat, hold=hold,
                                      n=len(nets), mean=m, t=t,
                                      gross=st.fmean(gross), impact=st.fmean(impact),
                                      unres=sum(unres.values()),
                                      why=dict(unres.most_common(1))))

    print(f"{'famille':24s} {'etats':>6s} {'donnees KO':>10s} {'seuil KO':>9s} "
          f"{'candidats':>10s}")
    print("-" * 64)
    for fam in sorted(funnel, key=lambda f: -funnel[f]["candidats"]):
        c = funnel[fam]
        print(f"{fam:24s} {c['etats']:6d} {c['donnees_ko']:10d} "
              f"{c['seuil_ko']:9d} {c['candidats']:10d}")

    print("\nDECOMPOSITION BRUT -> NET (latence 1000 ms ; voir amendement 2)")
    print(f"\n{'famille':24s} {'notion':>7s} {'hold':>5s} {'resolus':>8s} "
          f"{'non res.':>9s} {'BRUT':>8s} {'impact':>8s} {'frais':>7s} "
          f"{'NET':>9s} {'t':>7s}")
    print("-" * 100)
    for c in sorted(cells, key=lambda x: (x["fam"], x["notion"], x["hold"])):
        if c["lat"] != 1000:
            continue
        if not c["n"]:
            print(f"{c['fam']:24s} {c['notion']:7.0f} {c['hold']:5d} {0:8d} "
                  f"{c['unres']:9d}{'       —':>8s}{'       —':>8s}"
                  f"{'      —':>7s}{'        —':>9s}{'      —':>7s}")
            continue
        print(f"{c['fam']:24s} {c['notion']:7.0f} {c['hold']:5d} {c['n']:8d} "
              f"{c['unres']:9d} {c['gross']:8.3f} {-c['impact']:8.3f} "
              f"{-FEES_ROUNDTRIP_BPS:7.1f} {c['mean']:9.3f} {c['t']:7.2f}")

    ok = [c for c in cells if c["n"] >= MIN_RESOLVED and c["mean"] > 0 and c["t"] >= MIN_T]
    print(f"\ncellules retenues (net>0, t>={MIN_T}, >={MIN_RESOLVED} resolus) : "
          f"{len(ok)}/{len(cells)}")
    if ok:
        from prism_v2.long_test import benjamini_hochberg
        from prism_v2.long_test import t_test_one_sided  # noqa: F401
        pv = {f"{c['fam']}/{c['notion']:.0f}/{c['lat']}/{c['hold']}":
              0.5 * math.erfc(c["t"] / math.sqrt(2)) for c in cells if c["n"] > 1}
        surv = benjamini_hochberg(pv, q=0.10)
        print(f"survivants Benjamini-Hochberg q=0,10 sur {len(pv)} cellules : "
              f"{sum(surv.values())}")
        for c in sorted(ok, key=lambda x: -x["mean"])[:8]:
            print(f"  {c['fam']:24s} notion {c['notion']:.0f} lat {c['lat']} "
                  f"hold {c['hold']}s -> {c['mean']:+.3f} bps net, t={c['t']:.2f}, "
                  f"n={c['n']}")
    else:
        print("AUCUNE cellule ne franchit les trois criteres declares.")

    print("\nMOTIFS DE NON-RESOLUTION (cout INCONNU, jamais compte comme nul) :")
    agg = Counter()
    for c in cells:
        for k, v in (c["why"] or {}).items():
            agg[k] += v
    for k, v in agg.most_common(5):
        print(f"  {v:8d}  {k}")


if __name__ == "__main__":
    main()
