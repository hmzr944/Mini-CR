"""Ecart executable spot <-> perp inverse, sur carnet CORRECTEMENT reconstruit.

Premiere version : `bids[0]` de chaque message incrementiel pris pour la
meilleure offre. Faux. Cette version rejoue le carnet.

Le PnL d'un aller-retour a deux jambes s'ecrit exactement :
    G_in  = (jambe_riche_bid - jambe_pauvre_ask) / mid      a l'entree
    G_out = ecart executable dans le sens INVERSE            a la sortie
    net   = G_in + G_out - frais
Les quatre demi-spreads sont donc payes. Aucun prix milieu n'entre dans le PnL.
"""
import pickle, statistics as st

import os as _os
#: Repertoire de travail des mesures. Les fichiers intermediaires (panneau,
#: bande) n'ont pas leur place dans le depot : ils se recalculent.
from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
_os.makedirs(SCRATCH, exist_ok=True)

from bisect import bisect_right


Q = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))["quotes"]
BASES = ("BTC", "ADA", "BCH", "DOGE", "DOT", "ETC")
MAX_STALE_MS = 2_000
TAKER, MAKER = 5.0, 2.0

print(f"{'paire':<8}{'n':>9}{'median':>9}{'p90':>8}{'p99':>8}{'max':>8}"
      f"{'>0':>8}{'>8bps':>8}{'>20bps':>8}{'USD au touch':>14}")
print("-" * 88)
series = {}
for base in BASES:
    qs, qp = Q.get(f"{base}-USD"), Q.get(f"{base}-USD-SWAP")
    if not qs or not qp:
        continue
    ts_s, ts_p = [x[0] for x in qs], [x[0] for x in qp]
    g = []
    for t in sorted({*ts_s, *ts_p}):
        i, j = bisect_right(ts_s, t) - 1, bisect_right(ts_p, t) - 1
        if i < 0 or j < 0:
            continue
        s, p = qs[i], qp[j]
        if t - s[0] > MAX_STALE_MS or t - p[0] > MAX_STALE_MS:
            continue
        mid = (s[1] + s[2] + p[1] + p[2]) / 4.0
        ga = (p[1] - s[2]) / mid * 10_000.0        # perp riche
        gb = (s[1] - p[2]) / mid * 10_000.0        # spot riche
        g.append((t, ga, gb, min(p[3], s[4]), min(s[3], p[4])))
    if len(g) < 100:
        continue
    series[base] = g
    best = sorted(max(x[1], x[2]) for x in g)
    n = len(best)
    cap = st.median([x[3] if x[1] >= x[2] else x[4] for x in g])
    print(f"{base:<8}{n:>9}{st.median(best):>9.2f}{best[int(.90*n)]:>8.2f}"
          f"{best[int(.99*n)]:>8.2f}{best[-1]:>8.2f}"
          f"{sum(1 for x in best if x>0)/n:>7.1%}"
          f"{sum(1 for x in best if x>8)/n:>7.2%}"
          f"{sum(1 for x in best if x>20)/n:>7.2%}{cap:>14,.0f}")

print(f"\nALLER-RETOUR : entree sur ecart positif, sortie au meilleur ecart inverse")
print(f"{'paire':<7}{'horizon':>8}{'entrees':>9}{'G_in':>8}{'G_out':>9}{'brut':>8}"
      f"{'net taker':>11}{'net maker':>11}{'%>0 taker':>11}")
print("-" * 82)
best_overall = None
for base, g in series.items():
    ts_all = [x[0] for x in g]
    for H in (1, 5, 30, 300):
        ins, outs, tots = [], [], []
        last_exit = -1
        for k, (t, ga, gb, ca, cb) in enumerate(g):
            if t <= last_exit:
                continue
            sens = 1 if ga >= gb else 2
            gin = ga if sens == 1 else gb
            if gin <= 0:
                continue
            hi = bisect_right(ts_all, t + H * 1000)
            gout, texit = None, None
            for m in range(k + 1, hi):
                cand = g[m][2] if sens == 1 else g[m][1]
                if gout is None or cand > gout:
                    gout, texit = cand, g[m][0]
            if gout is None:
                continue
            ins.append(gin); outs.append(gout); tots.append(gin + gout)
            last_exit = texit
        if len(ins) < 20:
            continue
        brut = st.fmean(tots)
        nt, nm = brut - 4 * TAKER, brut - 4 * MAKER
        if best_overall is None or brut > best_overall[0]:
            best_overall = (brut, base, H, len(ins))
        print(f"{base:<7}{H:>7}s{len(ins):>9}{st.fmean(ins):>8.2f}"
              f"{st.fmean(outs):>9.2f}{brut:>8.2f}{nt:>11.2f}{nm:>11.2f}"
              f"{sum(1 for x in tots if x>4*TAKER)/len(tots):>11.1%}")

if best_overall:
    b, base, H, n = best_overall
    print(f"\nMEILLEUR aller-retour BRUT : {b:+.2f} bps  ({base}, {H}s, {n} entrees)")
    print(f"  net en taker pur (4 x {TAKER} bps) : {b - 4*TAKER:+.2f} bps")
    print(f"  net en maker pur (4 x {MAKER} bps) : {b - 4*MAKER:+.2f} bps")
