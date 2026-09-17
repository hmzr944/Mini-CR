"""Causalite, version CORRIGEE : alignement sur l'horodatage REEL.

La premiere version datait chaque liquidation a l'instant ou mon sondage la
voyait. Or le delai de publication median est de 2 434 s : 77 % des
liquidations etaient vues plus de 30 s apres les faits. La fenetre « avant »
contenait donc de l'apres, et la conclusion etait sans valeur.

Ici chaque liquidation est datee par son champ `ts`, et l'on ne retient que
celles dont l'instant reel tombe DANS la fenetre de sondage des prix, avec
assez de marge de part et d'autre.
"""
import json, statistics as st, math
from bisect import bisect_left
from collections import defaultdict

P = '/tmp/claude-0/-home-user-Mini-CR/99f5412e-d46b-5c7f-a2b0-5167b0af1774/scratchpad/fwd/book_shock.jsonl'
rows = [json.loads(l) for l in open(P) if l.strip()]
by = defaultdict(list)
for r in rows:
    by[r["inst"]].append(r)

MARGIN_MS = 90_000
ev = []
for inst, rs in sorted(by.items()):
    rs.sort(key=lambda x: x["poll_ms"])
    ts_axis = [r["poll_ms"] for r in rs]
    mids = [r["mid"] for r in rs]
    lo, hi = ts_axis[0] + MARGIN_MS, ts_axis[-1] - MARGIN_MS

    # toutes les liquidations vues, datees par LEUR horodatage
    liqs = {}
    for r in rs:
        for ts, side, sz, px in r["new_liq"]:
            liqs[(ts, side, sz, px)] = (ts, side, sz * px)
    inwin = [v for v in liqs.values() if lo <= v[0] <= hi]
    if not inwin:
        print(f"{inst:<20} 0 liquidation dans la fenetre de prix")
        continue

    # agregation par seconde reelle
    buckets = defaultdict(lambda: [0.0, 0.0])
    for ts, side, notional in inwin:
        b = ts - ts % 1000
        if side == "sell":
            buckets[b][0] += notional
        else:
            buckets[b][1] += notional

    def mid_at(t):
        i = bisect_left(ts_axis, t)
        if i >= len(ts_axis):
            i = len(ts_axis) - 1
        return mids[i]

    n = 0
    for b, (s, bu) in buckets.items():
        tot = s + bu
        if tot <= 0:
            continue
        imb = (s - bu) / tot
        d = 1 if imb > 0 else -1
        p0 = mid_at(b)
        if p0 <= 0:
            continue
        back = [-d * (p0 / mid_at(b - w * 1000) - 1) * 10000 for w in (6, 30, 60)]
        fwd = [-d * (mid_at(b + w * 1000) / p0 - 1) * 10000 for w in (6, 30, 60)]
        ev.append((inst, imb, back, fwd))
        n += 1
    print(f"{inst:<20} {n} evenements dates dans la fenetre")

print(f"\nevenements retenus : {len(ev)}")
if len(ev) < 10:
    print("ECHANTILLON INSUFFISANT — aucune conclusion n'est tiree.")
    raise SystemExit


def tt(x):
    if len(x) < 10:
        return float("nan")
    sd = st.stdev(x)
    return st.fmean(x) / (sd / math.sqrt(len(x))) if sd > 0 else float("nan")


print("\nMouvement DANS LE SENS du flux force (horodatage reel)")
print(f"{'fenetre':<14}{'moyen bps':>12}{'t':>8}{'% positif':>11}")
print("-" * 45)
for k, w in enumerate((6, 30, 60)):
    v = [e[2][k] for e in ev]
    print(f"AVANT -{w:>3}s{'':<3}{st.fmean(v):>12.2f}{tt(v):>8.2f}"
          f"{100*sum(1 for x in v if x > 0)/len(v):>11.1f}")
print()
for k, w in enumerate((6, 30, 60)):
    v = [e[3][k] for e in ev]
    print(f"APRES +{w:>3}s{'':<3}{st.fmean(v):>12.2f}{tt(v):>8.2f}"
          f"{100*sum(1 for x in v if x > 0)/len(v):>11.1f}")
