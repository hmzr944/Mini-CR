"""Etape 2 de OBJECTIF.md : le remplissage passif est-il atteignable ?

Lit tout ce que tools/collect_touch_forward.py a accumule et rend, par jambe,
le taux de remplissage et le markout. Ne conclut que si l'echantillon suffit ;
sinon il dit combien il manque.

    python3 -m prism_v2.scans.fill_measure
"""
from __future__ import annotations

import json
import statistics as st
from collections import defaultdict
from pathlib import Path

from prism_v2.fill_model import (MIN_FILL_RATE, MIN_WINDOWS, Snapshot,
                                 measure, verdict)

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "prism_v2" / "data" / "touch_forward"
HORIZON_S = 240.0     #: 4 h serait ideal ; les fenetres durent 8 min, donc 4 min
MARKOUT_S = 30.0


def load() -> tuple[list[Snapshot], int]:
    snaps, files = [], sorted(STORE.glob("*.jsonl"))
    for f in files:
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                snaps.append(Snapshot(ts=d["ts"], inst=d["i"], bid=d["bid"],
                                      bid_sz=d["bidSz"], ask=d["ask"],
                                      ask_sz=d["askSz"], agg=d.get("agg") or []))
            except Exception:
                continue
    return snaps, len(files)


def main() -> None:
    snaps, n_files = load()
    if not snaps:
        print("Aucune donnée de touch. Lancer tools/collect_touch_forward.py")
        print("(la routine « collecte forward du touch » le fait 4x/jour).")
        return
    insts = sorted({s.inst for s in snaps})
    span_h = (max(s.ts for s in snaps) - min(s.ts for s in snaps)) / 3_600_000
    print(f"{len(snaps):,} relevés, {n_files} fenêtres, {len(insts)} jambes, "
          f"{span_h:.1f} h d'étalement\n")

    # demi-spread median par jambe, mesure sur les memes releves
    hs = defaultdict(list)
    for s in snaps:
        if s.ask > s.bid > 0:
            hs[s.inst].append(1e4 * (s.ask - s.bid) / 2 / s.mid)

    print(f"{'jambe':18s} {'côté':5s} {'fen.':>5s} {'essais':>7s} {'remplis':>8s} "
          f"{'taux':>6s} {'délai méd.':>11s} {'markout':>9s} {'demi-spr':>9s}  verdict")
    print("-" * 118)
    any_conclusive = False
    for inst in insts:
        half = st.median(hs[inst]) if hs[inst] else float("nan")
        for side in ("bid", "ask"):
            st_ = measure(snaps, inst, side=side,
                          horizon_s=HORIZON_S, markout_s=MARKOUT_S)
            v = verdict(st_, half)
            any_conclusive |= not v.startswith("INSUFFISANT")
            d = f"{st_.median_seconds_to_fill:.0f}s" if st_.median_seconds_to_fill else "—"
            m = f"{st_.mean_markout_bps:+.2f}" if st_.mean_markout_bps is not None else "—"
            print(f"{inst:18s} {side:5s} {st_.n_windows:5d} {st_.n_attempts:7d} "
                  f"{st_.n_filled:8d} {100*st_.fill_rate:5.0f}% {d:>11s} {m:>9s} "
                  f"{half:9.2f}  {v}")

    if not any_conclusive:
        print(f"\nRien n'est conclu, et c'est normal.")
        print(f"Il faut {MIN_WINDOWS} fenêtres DISTINCTES par jambe — pas 200 relevés.")
        print(f"Deux essais espacés de 3 s dans la même fenêtre voient la même")
        print(f"trajectoire : ce sont des copies d'une observation. À 4 fenêtres")
        print(f"par jour, compter environ {MIN_WINDOWS/4:.0f} jours.")
        print(f"\nSeuils déclarés : remplissage ≥ {MIN_FILL_RATE:.0%} ET")
        print(f"sélection adverse ≤ demi-spread encaissé.")


if __name__ == "__main__":
    main()
