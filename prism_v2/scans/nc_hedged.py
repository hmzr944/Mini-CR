"""Le flux SKHYNIX est-il persistant, et surtout COUVRABLE ?

Deux questions separees, toutes deux decisives :

1. CONCENTRATION. Une moyenne de 16,55 bps/jour avec seulement 48 % de
   periodes positives se porte par des pointes, pas par une accumulation.
   La lecon KAITO : une pointe n'est pas un flux, on ne peut pas la detenir.

2. COUVERTURE. Encaisser un funding exige d'etre COURT le perpetuel, donc
   d'accepter le risque de prix. Le hedger par le sous-jacent est impossible
   (pas d'acces au KRX, pas d'emprunt). Reste a le hedger par un perpetuel
   CORRELE cote sur la meme venue : SAMSUNG, MU, SOXL. Ce qui se capture est
   alors le DIFFERENTIEL de funding, et ce qui reste en risque est le residu
   de la couverture.

Le PnL de prix d'une position nue n'est PAS compte comme un edge : sur cette
fenetre les actifs choisis ont baisse, ce qui est une propriete de la fenetre,
pas de la strategie.
"""
import json, math, os as _os, statistics as st
from prism_v2.scans import scan_dir as _scan_dir
_SCAN = _scan_dir()
_os.makedirs(_SCAN, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE

PAIRES = [("SKHYNIX", "SAMSUNG"), ("SKHYNIX", "MU"), ("SAMSUNG", "MU"),
          ("SKHYNIX", "SOXL"), ("MU", "SOXL"), ("NVDA", "SOXL"),
          ("NVDA", "MRVL"), ("BZ", "CL")]
ALL = sorted({x for p in PAIRES for x in p})


def candles(inst, pages=25):
    rows, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after:
            u += f"&after={after}"
        try:
            d = _http_json(u).get("data") or []
        except Exception:
            break
        if not d:
            break
        for r in d:
            try:
                rows[int(r[0])] = float(r[4])
            except (ValueError, IndexError):
                continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after:
            break
        after = nb
    return rows


def funding(inst, pages=10):
    out, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            u += f"&after={after}"
        try:
            d = _http_json(u).get("data") or []
        except Exception:
            break
        if not d:
            break
        for r in d:
            try:
                out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
        nb = min(int(r["fundingTime"]) for r in d)
        if after and nb >= after:
            break
        after = nb
        if len(d) < 100:
            break
    return out


F = {n: funding(f"{n}-USDT-SWAP") for n in ALL}
C = {n: candles(f"{n}-USDT-SWAP") for n in ALL}

print("1. CONCENTRATION du funding — une pointe n'est pas un flux\n")
print(f"{'actif':<10}{'n':>6}{'cumule bps':>12}{'% positif':>11}"
      f"{'part des 5 % plus grandes':>27}{'mediane bps/periode':>21}")
print("-" * 87)
for n in ALL:
    f = F[n]
    if len(f) < 60:
        continue
    v = sorted(f.values(), key=abs, reverse=True)
    tot = sum(f.values())
    k = max(1, len(v) // 20)
    part = sum(v[:k]) / tot if abs(tot) > 1e-12 else float("nan")
    print(f"{n:<10}{len(f):>6}{tot*10_000:>12.0f}"
          f"{sum(1 for x in f.values() if x > 0)/len(f):>10.0%}"
          f"{part:>26.0%}{st.median(list(f.values()))*10_000:>21.3f}")

print("\n\n2. COUVERTURE — correlation horaire et differentiel de funding\n")
print(f"{'paire':<20}{'correl h':>10}{'beta':>8}{'diff moy bps/j':>16}"
      f"{'% periodes >0':>15}{'p90 |resid| %':>15}")
print("-" * 84)
res = []
for a, b in PAIRES:
    ca, cb = C.get(a), C.get(b)
    if not ca or not cb:
        continue
    common = sorted(set(ca) & set(cb))
    if len(common) < 300:
        print(f"{a+'/'+b:<20}  chevauchement insuffisant ({len(common)})")
        continue
    ra = [math.log(ca[t2]/ca[t1]) for t1, t2 in zip(common, common[1:])]
    rb = [math.log(cb[t2]/cb[t1]) for t1, t2 in zip(common, common[1:])]
    ma, mb = st.fmean(ra), st.fmean(rb)
    sa, sb = st.stdev(ra), st.stdev(rb)
    cov = sum((x-ma)*(y-mb) for x, y in zip(ra, rb)) / (len(ra)-1)
    corr = cov/(sa*sb) if sa > 0 and sb > 0 else float("nan")
    beta = cov/(sb*sb) if sb > 0 else float("nan")
    resid = [x - beta*y for x, y in zip(ra, rb)]
    # differentiel de funding sur les periodes communes
    fa, fb = F.get(a, {}), F.get(b, {})
    ts = sorted(set(fa) & set(fb))
    if len(ts) < 60:
        continue
    per = {}
    for x, y in zip(ts, ts[1:]):
        ph = (y-x)/3_600_000
        if 0.5 <= ph <= 12.0:
            per[y] = ph
    if not per:
        continue
    diff = [(fa[t] - beta*fb[t]) * 24.0/ph * 10_000.0 for t, ph in per.items()]
    ab = sorted(abs(r) for r in resid)
    res.append((f"{a}/{b}", corr, beta, st.fmean(diff),
                sum(1 for d in diff if d > 0)/len(diff), ab[int(.9*len(ab))]*100))
    print(f"{a+'/'+b:<20}{corr:>10.3f}{beta:>8.3f}{st.fmean(diff):>16.2f}"
          f"{sum(1 for d in diff if d>0)/len(diff):>14.0%}"
          f"{ab[int(.9*len(ab))]*100:>14.2f}%")

print(f"\nseuil d'admission a la cible 20 EUR/jour : ~84 bps/jour de notionnel")
if res:
    best = max(res, key=lambda r: r[3])
    print(f"meilleur differentiel couvert : {best[0]} a {best[3]:.2f} bps/jour "
          f"({best[3]/84:.3f}x du seuil), correlation {best[1]:.3f}")
json.dump([list(r) for r in res], open(_os.path.join(_SCAN, 'hedged.json'),'w'))
