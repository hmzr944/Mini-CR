"""RESET DE REPRESENTATION : le risque est-il dilue, ou seulement deplace ?

CE QUI CHANGE. Toutes les mesures precedentes definissaient une opportunite
comme un DIFFERENTIEL entre deux instruments, capture en detenant une paire
COUVERTE. Couvrir achete la stationnarite du residu au prix du flux lui-meme :
le funding brut vaut 10 a 25 fois le differentiel (16,55 contre 0,7 a 12,8).

Ici l'unite devient un PANIER d'expositions NON COUVERTES, court sur les noms
a funding eleve, long sur ceux a funding bas. Le risque de prix n'est pas ote
par une jambe opposee appariee ; il est dilue transversalement.

  cout : 2 traversees par nom au lieu de 4
  flux : le NIVEAU du funding, pas un differentiel
  risque : non couvert, dilue sur N noms

LA QUESTION QUI TUE. Le funding est-il la remuneration d'un service, ou la
compensation d'un risque de prix ? Si le PnL de prix du panier annule le
funding encaisse, le reset est mort. C'est exactement ce qui est mesure ici :
le PnL TOTAL (funding + prix), pas le funding seul.

GARDE-FOUS CONSERVES :
  - causalite : on classe sur le funding DEJA PAYE a t, on encaisse de t a t+N ;
  - aucune selection de periode : toutes les periodes sont utilisees ;
  - aucune selection de nom : le classement est mecanique, pas choisi ;
  - le PnL de prix est celui REELLEMENT observe, jamais suppose nul ;
  - coussin de survie mesure sur le panier, pas sur un nom ;
  - Benjamini-Hochberg sur toutes les cellules.
"""
import json, math, os, statistics as st, time
from pathlib import Path
from prism_v2.funding_feed import _http_json, OKX_BASE
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
os.makedirs(SCAN, exist_ok=True)
NOMS = ["TSLA","NVDA","INTC","MU","MSTR","QQQ","SOXL","MRVL","CRCL","NBIS",
        "AAOI","CRWV","SNDK","SAMSUNG","SKHYNIX","XAU","XAG","CL","BZ",
        "SPCX","ZHIPU","UNITREE","LITE","SNXX","KORU","SKHY",
        "BTC","ETH","SOL","XRP","DOGE","ADA","LTC","BCH","LINK","ETC","DOT","FIL"]
TAKER = 5.0
CACHE_C, CACHE_F = Path(SCAN)/"basket_c.json", Path(SCAN)/"basket_f.json"


def candles(inst, pages=25):
    rows, after = {}, None
    for _ in range(pages):
        u=f"{OKX_BASE}/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after: u+=f"&after={after}"
        try: d=_http_json(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: rows[int(r[0])]=float(r[4])
            except (ValueError,IndexError): continue
        nb=min(int(r[0]) for r in d)
        if after and nb>=after: break
        after=nb
    return rows


def funding(inst, pages=10):
    out, after = {}, None
    for _ in range(pages):
        u=f"{OKX_BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after: u+=f"&after={after}"
        try: d=_http_json(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: out[int(r["fundingTime"])]=float(r.get("realizedRate") or r["fundingRate"])
            except (KeyError,TypeError,ValueError): continue
        nb=min(int(r["fundingTime"]) for r in d)
        if after and nb>=after: break
        after=nb
        if len(d)<100: break
        time.sleep(0.02)
    return out


if CACHE_C.exists() and CACHE_F.exists():
    C={k:{int(a):b for a,b in v.items()} for k,v in json.load(open(CACHE_C)).items()}
    F={k:{int(a):b for a,b in v.items()} for k,v in json.load(open(CACHE_F)).items()}
else:
    C, F = {}, {}
    for n in NOMS:
        c=candles(f"{n}-USDT-SWAP")
        f=funding(f"{n}-USDT-SWAP")
        if len(c)>=1500 and len(f)>=100: C[n], F[n] = c, f
    json.dump({k:{str(a):b for a,b in v.items()} for k,v in C.items()}, open(CACHE_C,"w"))
    json.dump({k:{str(a):b for a,b in v.items()} for k,v in F.items()}, open(CACHE_F,"w"))

noms = sorted(C)
print(f"{len(noms)} instruments avec prix ET funding exploitables")
times = sorted(set.intersection(*[set(F[n]) for n in noms]))
print(f"{len(times)} periodes de funding communes "
      f"({(times[-1]-times[0])/86_400_000:.0f} jours)\n")

tick={t["instId"]:t for t in (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or [])}
HS={}
for n in noms:
    t=tick.get(f"{n}-USDT-SWAP")
    try:
        bid,ask=float(t["bidPx"]),float(t["askPx"]); HS[n]=(ask-bid)/(ask+bid)*10_000.0
    except (TypeError,ValueError,KeyError): HS[n]=None
noms=[n for n in noms if HS[n] is not None]
COUT_NOM = {n: 2*TAKER + 2*HS[n] for n in noms}   # 2 traversees, pas 4
print(f"cout median par nom (2 traversees) : {st.median(list(COUT_NOM.values())):.1f} bps\n")


def prix_a(n, t):
    c=C[n]; k=t - t % 3_600_000
    for d in (0, -3_600_000, 3_600_000, -7_200_000):
        if k+d in c: return c[k+d]
    return None


print(f"{'K':>3}{'N per.':>8}{'jours':>7}{'entrees':>9}{'funding':>10}{'prix':>10}"
      f"{'cout':>8}{'TOTAL bps':>11}{'par jour':>10}{'t':>8}")
print("-"*84)
res, pv = [], {}
for K in (3, 5, 8, 12):
    if 2*K > len(noms): continue
    for N in (1, 3, 6, 12):
        fu, px, tot, jours = [], [], [], []
        last=-1
        for i,t in enumerate(times):
            if i<=last or i+N>=len(times): continue
            # CLASSEMENT sur le funding DEJA PAYE a t
            cls=sorted(noms, key=lambda n: -F[n][t])
            courts, longs = cls[:K], cls[-K:]
            gf=gp=gc=0.0; ok=True
            for n, sgn in [(x,-1.0) for x in courts]+[(x,1.0) for x in longs]:
                p0, p1 = prix_a(n, t), prix_a(n, times[i+N])
                if not p0 or not p1: ok=False; break
                # court encaisse le funding positif ; long le paie
                gf += -sgn*sum(F[n][times[j]] for j in range(i+1,i+1+N))*10_000.0
                gp += sgn*(p1/p0-1.0)*10_000.0
                gc += COUT_NOM[n]
            if not ok: continue
            m=2*K
            fu.append(gf/m); px.append(gp/m); tot.append((gf+gp-gc)/m)
            jours.append((times[i+N]-t)/86_400_000); last=i+N
        if len(tot)<12: continue
        dj=st.fmean(jours)
        tt,p = t_test_one_sided(tot) if len(tot)>=30 else (float("nan"),1.0)
        pv[f"K{K}/N{N}"]=p
        res.append((K,N,st.fmean(tot)/dj,st.fmean(fu)/dj,st.fmean(px)/dj,dj,len(tot)))
        print(f"{K:>3}{N:>8}{dj:>7.1f}{len(tot):>9}{st.fmean(fu):>10.2f}"
              f"{st.fmean(px):>10.2f}{st.fmean([COUT_NOM[n] for n in noms]):>8.1f}"
              f"{st.fmean(tot):>11.2f}{st.fmean(tot)/dj:>10.2f}{tt:>8.2f}")

print(f"\n{'='*84}")
if res:
    fu_moy=st.fmean([r[3] for r in res]); px_moy=st.fmean([r[4] for r in res])
    print(f"funding encaisse moyen : {fu_moy:>8.2f} bps/jour de notionnel")
    print(f"PnL de PRIX moyen      : {px_moy:>8.2f} bps/jour de notionnel")
    ecart = max(r[4] for r in res) - min(r[4] for r in res)
    print(f"AMPLITUDE du terme de prix entre cellules : {ecart:.0f} bps/jour")
    print(f"  Moyenner ce terme n'a pas de sens : il oscille d'un facteur "
          f"{ecart/max(abs(fu_moy),1e-9):.0f} fois le funding. Ce n'est pas une")
    print(f"  esperance, c'est l'episode de marche de la fenetre.")
    pos=[r for r in res if r[2]>0]
    surv=benjamini_hochberg(pv,q=0.10)
    print(f"\ncellules a TOTAL positif : {len(pos)}/{len(res)}")
    print(f"survivants Benjamini-Hochberg q=0,10 : {sum(surv.values())}/{len(surv)}")
    b=max(res,key=lambda r:r[2])
    print(f"meilleure cellule : K={b[0]} N={b[1]} -> {b[2]:.2f} bps/jour de NOTIONNEL")
    print(f"\nKILL CONDITION : le prix annule le funding -> reset mort")
json.dump(res, open(os.path.join(SCAN,"basket.json"),"w"))
