"""La MACHINE complete : allocation causale + mutualisation du coussin.

Ce n'est plus un test de signal. C'est un test de POLITIQUE D'ALLOCATION.

Le differentiel de funding est OBSERVABLE AVANT d'entrer : ce n'est pas une
prediction, c'est un prix affiche. Allouer aux paires dont le differentiel
courant est le plus grand est donc une decision ex ante legitime, et non une
selection apres coup — a condition de decider sur le taux DEJA PAYE a t et
d'encaisser ce qui est paye de t a t+N. C'est ce qui est fait ici.

La machine testee :
  1. a chaque periode, observer le differentiel paye sur les 16 paires ;
  2. allouer a parts egales aux k plus grands, dans le sens qui encaisse ;
  3. detenir N periodes, encaisser ce qui est REELLEMENT paye ;
  4. payer l'aller-retour une fois par entree ;
  5. dimensionner le capital sur le coussin MUTUALISE du sous-portefeuille.

k et N sont balayes, et le resultat est rendu en entier : prendre le meilleur
couple serait une selection apres coup, et le mandat l'interdit.
"""
import json, math, os, statistics as st
from prism_v2.funding_feed import _http_json, OKX_BASE
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
PAIRES = [("SKHYNIX","SAMSUNG"),("SKHYNIX","MU"),("SKHYNIX","SOXL"),
          ("SAMSUNG","MU"),("MU","SOXL"),("NVDA","MRVL"),("NVDA","SOXL"),
          ("MRVL","MU"),("INTC","MU"),("TSLA","NVDA"),("MSTR","CRCL"),
          ("AAOI","NBIS"),("CRWV","NBIS"),("QQQ","SOXL"),("XAU","XAG"),
          ("BZ","CL")]
NAMES = sorted({x for p in PAIRES for x in p})
TAKER, IMR_2JAMBES, QUANTILE = 5.0, 0.04, 0.99


def candles(inst, pages=25):
    rows, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after: u += f"&after={after}"
        try: d = _http_json(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: rows[int(r[0])] = float(r[4])
            except (ValueError, IndexError): continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after: break
        after = nb
    return rows


def funding(inst, pages=10):
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
    return out


C = {n: candles(f"{n}-USDT-SWAP") for n in NAMES}
F = {n: funding(f"{n}-USDT-SWAP") for n in NAMES}
tick = {t["instId"]: t for t in (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or [])}
grid = sorted(set.intersection(*[set(C[n]) for n in NAMES if C[n]]))
R = {n: [math.log(C[n][b]/C[n][a]) for a, b in zip(grid, grid[1:])] for n in NAMES}

def hs(n):
    t = tick.get(f"{n}-USDT-SWAP")
    try:
        b, a = float(t["bidPx"]), float(t["askPx"])
        return (a-b)/(a+b)*10_000.0
    except (TypeError, ValueError, KeyError): return None

BETA, RESID, COUT, SERIE = {}, {}, {}, {}
for a, b in PAIRES:
    ra, rb = R[a], R[b]
    ma, mb = st.fmean(ra), st.fmean(rb)
    cov = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))/(len(ra)-1)
    vb = st.pvariance(rb)*len(rb)/(len(rb)-1)
    if vb <= 0: continue
    beta = cov/vb
    ha, hb = hs(a), hs(b)
    if ha is None or hb is None: continue
    fa, fb = F[a], F[b]
    ts = sorted(set(fa) & set(fb))
    per = {y: (y-x)/3_600_000 for x, y in zip(ts, ts[1:]) if 0.5 <= (y-x)/3_600_000 <= 12.0}
    if len(per) < 100: continue
    BETA[(a,b)] = beta
    RESID[(a,b)] = [x - beta*y for x, y in zip(ra, rb)]
    COUT[(a,b)] = 4*TAKER + 2*(ha+hb)
    # bps de notionnel REELLEMENT payes sur chaque periode (pas annualises)
    SERIE[(a,b)] = {t: (fa[t]-beta*fb[t])*10_000.0 for t in per}

keys = sorted(SERIE, key=lambda k: str(k))
times = sorted(set.intersection(*[set(SERIE[k]) for k in keys]))
print(f"{len(keys)} paires | {len(times)} periodes de funding communes "
      f"({(times[-1]-times[0])/86_400_000:.0f} jours)\n")


def coussin(sub, w_heures):
    """Coussin du sous-portefeuille equipondere, mesure."""
    n = len(RESID[sub[0]])
    port = [st.fmean([RESID[k][i] for k in sub]) for i in range(n)]
    out = []
    for i in range(len(port)-w_heures):
        c, lo = 0.0, 0.0
        for j in range(i, i+w_heures):
            c += port[j]; lo = min(lo, c)
        out.append(-lo)
    if not out: return None
    out.sort()
    return out[int(QUANTILE*len(out))-1]


print(f"{'k':>3}{'N per.':>8}{'entrees':>9}{'flux brut':>11}{'cout/entree':>13}"
      f"{'net notionnel':>15}{'levier':>8}{'net CAPITAL bps/j':>19}{'t':>7}")
print("-"*93)
resultats, pv = [], {}
for k in (1, 2, 4, 8, 16):
    if k > len(keys): continue
    for N in (1, 3, 6, 12):
        gains, last, jours = [], -1, []
        for i, t in enumerate(times):
            if i <= last or i+N >= len(times): continue
            # DECISION sur le taux DEJA PAYE a t
            top = sorted(keys, key=lambda kk: -abs(SERIE[kk][t]))[:k]
            g = 0.0
            for kk in top:
                sgn = 1.0 if SERIE[kk][t] > 0 else -1.0
                g += sgn*sum(SERIE[kk][times[j]] for j in range(i+1, i+1+N))
                g -= COUT[kk]
            gains.append(g/k)
            jours.append((times[i+N]-t)/86_400_000)
            last = i+N
        if len(gains) < 20: continue
        dj = st.fmean(jours)
        w = max(1, int(dj*24))
        cous = coussin(keys[:k] if k < len(keys) else keys, w)
        if cous is None or cous <= 0: continue
        cap = IMR_2JAMBES + cous
        L = 1.0/cap
        net_not = st.fmean(gains)/dj
        tt, p = t_test_one_sided(gains) if len(gains) >= 30 else (float("nan"), 1.0)
        brut = st.fmean([g + st.fmean([COUT[kk] for kk in keys]) for g in gains])/dj
        pv[f"k{k}/N{N}"] = p
        resultats.append((k, N, net_not*L))
        print(f"{k:>3}{N:>8}{len(gains):>9}{brut:>11.2f}"
              f"{st.fmean([COUT[kk] for kk in keys]):>13.1f}{net_not:>15.2f}"
              f"{L:>8.2f}{net_not*L:>19.2f}{tt:>7.2f}")

pos = [r for r in resultats if r[2] > 0]
print(f"\ncellules a net POSITIF sur le capital : {len(pos)}/{len(resultats)}")
if pv:
    surv = benjamini_hochberg(pv, q=0.10)
    print(f"survivants Benjamini-Hochberg q=0,10 : {sum(surv.values())}/{len(pv)}")
print(f"SEUIL QUI MORD : 272 bps/jour de capital")
if pos:
    b = max(pos, key=lambda r: r[2])
    print(f"meilleure cellule : k={b[0]} N={b[1]} -> {b[2]:.2f} bps/jour "
          f"({b[2]/272:.4f}x de l'objectif)")
json.dump(resultats, open(os.path.join(SCAN, "alloc.json"), "w"))
