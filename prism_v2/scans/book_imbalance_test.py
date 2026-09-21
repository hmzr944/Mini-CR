"""BOOK_IMBALANCE : information directionnelle, ou derive de marche ?

    python3 -m prism_v2.scans.book_imbalance_test <feed.jsonl>

Protocole gele dans prism_v2/PROTOCOLE_BOOK_IMBALANCE.md, ecrit AVANT la lecture
de la moindre ligne de donnees. Le detecteur est GELE au commit cd58455.

La fenetre est coupee en deux par le TEMPS. La premiere moitie sert au
diagnostic ; la seconde est lue UNE FOIS, a la fin. Rien n'y est regle.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from prism_v2.core_types import Direction
from prism_v2.directional_test import (Event, block_bootstrap_ci,
                                       permutation_pvalue,
                                       stratified_difference, verdict)
from prism_v2.discovery import DetectionStatus
from prism_v2.scans.funnel_real import load_feed

HORIZONS_S = (30, 120, 300)
STRATUM_MIN = 5.0
N_PERM = 5_000
N_BOOT = 5_000


def collect_events(states, timelines, horizon_s: int):
    """Declenchements du detecteur GELE, avec leur rendement NON signe."""
    from prism_v2.detectors.microstructure import BookImbalanceDetector
    det = BookImbalanceDetector()
    out = []
    for inst, s in states:
        try:
            res = det.detect(s)
        except Exception:
            continue
        if res.status is DetectionStatus.INSUFFICIENT_DATA or not res.candidates:
            continue
        tl = timelines[inst]
        exit_ts = s.ts_ms + horizon_s * 1000
        if not tl.covers(s.ts_ms) or not tl.covers(exit_ts):
            continue
        b0, b1 = tl.book_at(s.ts_ms), tl.book_at(exit_ts)
        if b0 is None or b1 is None or b0.mid <= 0:
            continue
        ret = (b1.mid - b0.mid) / b0.mid * 1e4        # NON signe, volontairement
        for c in res.candidates:
            out.append(Event(inst, s.ts_ms,
                             +1 if c.direction is Direction.LONG else -1, ret))
    return out


def analyse(events, label: str, n_perm: int, n_boot: int) -> None:
    r = stratified_difference(events, STRATUM_MIN)
    print(f"\n── {label} — {len(events)} declenchements ──")
    if r.delta_bps is None:
        print(f"  aucune strate exploitable "
              f"({r.n_strata_dropped} ecartees, un seul signe)")
        return
    print(f"  strates utilisees {r.n_strata_used}, ecartees {r.n_strata_dropped} "
          f"(un seul signe), evenements retenus {r.n_events_used}")
    print(f"  moyenne NAIVE du brut signe      : {r.naive_signed_bps:+8.4f} bps")
    print(f"  DIFFERENCE STRATIFIEE (Delta)    : {r.delta_bps:+8.4f} bps")
    print(f"  part attribuable a la derive     : "
          f"{r.drift_component_bps:+8.4f} bps")
    p = permutation_pvalue(events, STRATUM_MIN, n_perm=n_perm)
    ci = block_bootstrap_ci(events, STRATUM_MIN, n_boot=n_boot)
    print(f"  p de permutation ({n_perm} tirages) : {p:.4f}")
    print(f"  IC 90 % bootstrap par strates    : "
          f"{'[%+.4f ; %+.4f]' % ci if ci else 'indisponible'}")
    print(f"  VERDICT : {verdict(r.delta_bps, p, ci)}")


def main() -> None:
    feed = Path(sys.argv[1])
    raw = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://www.okx.com/api/v5/public/instruments?instType=SWAP",
        headers={"User-Agent": "prism/1.0"}), timeout=25))
    specs = {i["instId"]: i for i in raw["data"]}
    timelines, states = load_feed(feed, specs)
    ts = [s.ts_ms for _, s in states]
    cut = (min(ts) + max(ts)) // 2
    print(f"{len(states)} etats, {len(timelines)} instruments, "
          f"{(max(ts)-min(ts))/60000:.1f} min")
    print(f"coupure IS/OOS au milieu temporel : "
          f"IS {sum(t < cut for t in ts)} etats, OOS {sum(t >= cut for t in ts)}")
    print("\nDetecteur GELE. Aucun seuil n'est ajuste par ce script.")

    for h in HORIZONS_S:
        evs = collect_events(states, timelines, h)
        is_ = [e for e in evs if e.ts_ms < cut]
        oos = [e for e in evs if e.ts_ms >= cut]
        print(f"\n{'='*66}\nHORIZON {h} s")
        analyse(is_, f"IS (diagnostic) h={h}s", N_PERM // 5, N_BOOT // 5)
        analyse(oos, f"OOS (confirmatoire, lu une fois) h={h}s", N_PERM, N_BOOT)

    print(f"\n{'='*66}")
    print("Trois horizons testes sur l'OOS. Meme si un Delta ressortait positif et")
    print("significatif, cela etablirait une INFORMATION DIRECTIONNELLE, pas une")
    print("capture nette : frais, remplissage passif, selection adverse, capacite")
    print("et turnover restent entiers et explicitement differes.")


if __name__ == "__main__":
    main()
