"""Verdict final : la concession lue depuis les IMPRESSIONS SEULES.

La premiere lecture prenait le touch dans le carnet reconstruit. Le diagnostic
integre a tire : la concession ne croissait pas avec la profondeur (-0,10 a un
niveau, 1,00 a plus de dix), signe que le carnet lu etait deja post-trade. La
concession etait donc sous-estimee, et le verdict negatif portait un biais qui
pouvait masquer un positif.

Cette lecture-ci ne depend d'aucun ordonnancement entre canaux : un acheteur
remonte le carnet, donc sa premiere impression est la moins chere, et
VWAP - premiere impression est la concession qu'il a reellement payee.
"""
import math, os, pickle, statistics as st
from bisect import bisect_right
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
D = pickle.load(open(f"{SCAN}/concession2.pkl", "rb"))
Q, B = D["quotes"], D["bursts"]
MAKER, HOR = 2.0, [1_000, 5_000, 30_000, 300_000]
SEAUX = [(1,1,"1 impr."),(2,3,"2-3 impr."),(4,10,"4-10 impr."),(11,10**9,">10 impr.")]

print("Concession lue depuis les IMPRESSIONS (independante de l'ordonnancement)")
print("Seaux par NOMBRE D'IMPRESSIONS dans la rafale : un ordre fractionne en")
print("dix impressions revele un agresseur qui balaie, pas qui attend.\n")
print(f"{'seau':<12}{'n':>7}{'USD med':>10}{'conc carnet':>13}{'conc impr.':>12}"
      f"{'ecart px':>10}", end="")
for h in HOR: print(f"{'adv'+str(h//1000)+'s':>9}", end="")
print(f"{'PASSIF net':>12}{'t':>8}")
print("-"*116)
series, pv = {}, {}
for lo, hi, lab in SEAUX:
    cb, cp, sp_, usds = [], [], [], []
    adv = {h: [] for h in HOR}; net = {h: [] for h in HOR}
    for inst, rows in B.items():
        q = Q.get(inst)
        if not q: continue
        ts = [x[0] for x in q]
        for t, side, vwap, usd, niv, c, mid, c_p, span, npr in rows:
            if not (lo <= npr <= hi) or mid <= 0 or usd <= 0: continue
            sgn = 1.0 if side == "buy" else -1.0
            vals, ok = {}, True
            for h in HOR:
                j = bisect_right(ts, t + h) - 1
                if j < 0 or ts[j] <= t: ok = False; break
                vals[h] = sgn*((q[j][1]+q[j][2])/2.0 - mid)/mid*10_000.0
            if not ok: continue
            cb.append(c); cp.append(c_p); sp_.append(span); usds.append(usd)
            for h in HOR:
                adv[h].append(vals[h]); net[h].append(c_p - vals[h] - MAKER)
    if len(cp) < 30:
        print(f"{lab:<12}{len(cp):>7}   echantillon insuffisant"); continue
    for h in HOR:
        pv[f"{lab}@{h//1000}s"] = t_test_one_sided(net[h])[1]
        series[f"{lab}@{h//1000}s"] = net[h]
    best = max(HOR, key=lambda h: st.fmean(net[h]))
    print(f"{lab:<12}{len(cp):>7}{st.median(usds):>10,.0f}{st.fmean(cb):>13.2f}"
          f"{st.fmean(cp):>12.2f}{st.fmean(sp_):>10.2f}", end="")
    for h in HOR: print(f"{st.fmean(adv[h]):>9.2f}", end="")
    print(f"{st.fmean(net[best]):>12.2f}{t_test_one_sided(net[best])[0]:>8.2f}")

surv = benjamini_hochberg(pv, q=0.10) if pv else {}
pos = [(k, st.fmean(v)) for k, v in series.items() if st.fmean(v) > 0]
print(f"\ncellules a PASSIF net positif : {len(pos)}/{len(series)}")
print(f"survivants Benjamini-Hochberg q=0,10 : {sum(surv.values())}/{len(surv)}")
for k, m in sorted(pos, key=lambda x: -x[1])[:6]:
    print(f"  {k:<16}{m:>+8.2f} bps/rafale   {'SURVIT' if surv[k] else 'ne survit pas'}")
if not pos:
    print("  AUCUNE. Le cote passif rend plus qu'il ne recoit, meme avec la")
    print("  concession lue sans biais d'ordonnancement. FORME MORTE.")
