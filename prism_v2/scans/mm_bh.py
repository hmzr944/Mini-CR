"""Le balayage maker passe-t-il la correction de multiplicite et le crible de bon sens ?

13 instruments x 4 horizons = 52 tests. Retenir le meilleur sans correction,
c'est retenir du bruit environ une fois sur deux. On applique donc Benjamini-
Hochberg du projet, et on fait passer chaque survivant par prism_v2.sanity.
"""
import pickle, statistics as st, sys
from bisect import bisect_left, bisect_right

from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

import os as _os
#: Repertoire de travail des mesures. Les fichiers intermediaires (panneau,
#: bande) n'ont pas leur place dans le depot : ils se recalculent.
SCRATCH = _os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
_os.makedirs(SCRATCH, exist_ok=True)



D = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))
quotes, trades = D["quotes"], D["trades"]
MAKER_BPS, HORIZONS, MIN_FILLS = 2.0, [1, 5, 30, 300], 50

nets, hs_by = {}, {}
for inst in sorted(trades):
    q, tr = quotes.get(inst), trades[inst]
    if not q or len(tr) < MIN_FILLS:
        continue
    ts_q = [x[0] for x in q]
    per = {h: [] for h in HORIZONS}
    hs_all = []
    for t, px, usd, side in tr:
        i = bisect_left(ts_q, t) - 1
        if i < 0:
            continue
        b, a = q[i][1], q[i][2]
        if not (a > b > 0):
            continue
        mid = (b + a) / 2.0
        sgn = 1.0 if side == "buy" else -1.0
        hs = abs((a if side == "buy" else b) - mid) / mid * 10_000.0
        vals = {}
        for h in HORIZONS:
            j = bisect_right(ts_q, t + h * 1000) - 1
            if j < 0 or ts_q[j] <= t:
                vals = None
                break
            m2 = (q[j][1] + q[j][2]) / 2.0
            vals[h] = hs - sgn * (m2 - mid) / mid * 10_000.0 - MAKER_BPS
        if vals is None:
            continue
        hs_all.append(hs)
        for h in HORIZONS:
            per[h].append(vals[h])
    if len(hs_all) < MIN_FILLS:
        continue
    hs_by[inst] = st.fmean(hs_all)
    for h in HORIZONS:
        nets[f"{inst}@{h}s"] = per[h]

pv, stats = {}, {}
for k, v in nets.items():
    t, p = t_test_one_sided(v)
    pv[k] = p
    stats[k] = (st.fmean(v), t, len(v))

surv = benjamini_hochberg(pv, q=0.10)
print(f"{len(pv)} tests (instrument x horizon), BH a q=0,10\n")
print(f"{'test':<26}{'net bps/fill':>14}{'t':>8}{'p':>10}{'n':>8}  BH")
print("-" * 74)
for k in sorted(pv, key=lambda k: pv[k]):
    m, t, n = stats[k]
    print(f"{k:<26}{m:>14.2f}{t:>8.2f}{pv[k]:>10.4f}{n:>8}  "
          f"{'SURVIT' if surv[k] else '-'}")

s = [k for k in surv if surv[k]]
print(f"\nsurvivants BH : {len(s)}/{len(pv)}")
print(f"\n{'instrument':<18}{'demi-spread bps':>17}{'selection adverse 1s':>22}")
print("-" * 57)
for inst in sorted(hs_by):
    m1 = st.fmean(nets[f"{inst}@1s"])
    print(f"{inst:<18}{hs_by[inst]:>17.2f}{hs_by[inst]-m1-MAKER_BPS:>22.2f}")
n_worse = sum(1 for inst in hs_by
              if hs_by[inst] - st.fmean(nets[f"{inst}@1s"]) - MAKER_BPS > hs_by[inst])
print(f"\nA 1 seconde, la selection adverse depasse le demi-spread sur "
      f"{n_worse}/{len(hs_by)} instruments.")
