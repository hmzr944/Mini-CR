"""Allocation causale sur les paires MEME SOUS-JACENT, avec le coussin mesure.

Mon evaluation precedente de cette structure utilisait un flux ESTIME : la
moyenne des 12 paires multipliee par 2,5, facteur importe de la famille
non-crypto. Le mandat interdit de remplacer un inconnu par une hypothese. Ici
le flux est MESURE par la meme politique causale : a chaque periode, observer
le differentiel DEJA PAYE, allouer aux k plus grands, encaisser ce qui est
reellement paye de t a t+N.

Le coussin vient de la mesure alpha = 0,236 (meme sous-jacent), pas d'une
extrapolation.
"""
import json, math, os, statistics as st, time
from prism_v2.funding_feed import _http_json, OKX_BASE
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
BASES = ["ADA","BCH","BTC","DOGE","DOT","ETC","ETH","FIL","LINK","LTC","SOL","XRP"]
COUSSIN = {1:0.00247,2:0.00286,3:0.00310,5:0.00339,7:0.00369,
           10:0.00399,14:0.00441,20:0.00485,30:0.00569}
IMR, MUTU, TAKER = 0.04, 2.21, 5.0


def hist(inst, pages=12):
    out, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after: u += f"&after={after}"
        try: d = _http_json(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
            except (KeyError, TypeError, ValueError): continue
        nb = min(int(r["fundingTime"]) for r in d)
        if after and nb >= after: break
        after = nb
        if len(d) < 100: break
        time.sleep(0.02)
    return out


tick = {t["instId"]: t for t in (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or [])}
SERIE, COUT = {}, {}
for b in BASES:
    fi, fl = hist(f"{b}-USD-SWAP"), hist(f"{b}-USDT-SWAP")
    ts = sorted(set(fi) & set(fl))
    if len(ts) < 100: continue
    per = {y: (y-x)/3_600_000 for x, y in zip(ts, ts[1:]) if 0.5 <= (y-x)/3_600_000 <= 12.0}
    if len(per) < 100: continue
    # differentiel PAYE sur la periode, en bps de notionnel
    SERIE[b] = {t: (fi[t]-fl[t])*10_000.0 for t in per}
    hs = 0.0
    for suf in ("USD-SWAP","USDT-SWAP"):
        t = tick.get(f"{b}-{suf}")
        try:
            bid_px, ask_px = float(t["bidPx"]), float(t["askPx"])
            hs += (ask_px - bid_px) / (ask_px + bid_px) * 10_000
        except (TypeError, ValueError, KeyError): hs = None; break
    if hs is None: SERIE.pop(b); continue
    COUT[b] = 4*TAKER + 2*hs

keys = sorted(SERIE)
times = sorted(set.intersection(*[set(SERIE[k]) for k in keys]))
print(f"{len(keys)} paires meme sous-jacent | {len(times)} periodes communes "
      f"({(times[-1]-times[0])/86_400_000:.0f} jours)")
print(f"cout d'aller-retour median : {st.median(list(COUT.values())):.1f} bps\n")

print(f"{'k':>3}{'N per.':>8}{'entrees':>9}{'flux capte':>12}{'net notionnel':>15}"
      f"{'jours':>8}{'levier':>8}{'R capital bps/j':>17}{'t':>7}")
print("-"*88)
res, pv = [], {}
for k in (1,2,3,6,12):
    if k > len(keys): continue
    for N in (3,6,12,24,45,90):
        gains, last, jours = [], -1, []
        for i, t in enumerate(times):
            if i <= last or i+N >= len(times): continue
            top = sorted(keys, key=lambda kk: -abs(SERIE[kk][t]))[:k]
            g = 0.0
            for kk in top:
                sgn = 1.0 if SERIE[kk][t] > 0 else -1.0
                g += sgn*sum(SERIE[kk][times[j]] for j in range(i+1, i+1+N)) - COUT[kk]
            gains.append(g/k); jours.append((times[i+N]-t)/86_400_000); last = i+N
        if len(gains) < 15: continue
        dj = st.fmean(jours)
        cle = min(COUSSIN, key=lambda x: abs(x-dj))
        L = 1.0/(IMR + COUSSIN[cle]/MUTU)
        brut = st.fmean([g + st.fmean(list(COUT.values())) for g in gains])/dj
        net = st.fmean(gains)/dj
        tt, p = t_test_one_sided(gains) if len(gains) >= 30 else (float("nan"), 1.0)
        pv[f"k{k}/N{N}"] = p
        res.append((k, N, net*L, dj))
        print(f"{k:>3}{N:>8}{len(gains):>9}{brut:>12.2f}{net:>15.2f}{dj:>8.1f}"
              f"{L:>8.1f}{net*L:>17.1f}{tt:>7.2f}")

pos = [r for r in res if r[2] > 0]
print(f"\ncellules a net POSITIF : {len(pos)}/{len(res)}")
if pv:
    surv = benjamini_hochberg(pv, q=0.10)
    print(f"survivants Benjamini-Hochberg q=0,10 : {sum(surv.values())}/{len(pv)}")
if pos:
    b = max(pos, key=lambda r: r[2])
    print(f"meilleure cellule : k={b[0]} N={b[1]} ({b[3]:.0f} j) -> {b[2]:.1f} bps/jour "
          f"= {b[2]/10_000*1000:.2f} EUR/jour")
    print(f"mecanisme precedent (abandonne) : 33,3 bps/jour = 3,33 EUR/jour")
    print(f"  -> {'MIEUX' if b[2] > 33.3 else 'MOINS BIEN'} d'un facteur "
          f"{max(b[2],33.3)/min(b[2],33.3):.2f}")
print(f"cible : 272 bps/jour")
json.dump(res, open(os.path.join(SCAN,"carry_alloc.json"),"w"))
