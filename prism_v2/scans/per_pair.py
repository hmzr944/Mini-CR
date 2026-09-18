"""L'objectif est un PRODUIT PAR PAIRE. Le mesurer paire par paire.

LE DEFAUT CORRIGE ICI. R = L(alpha) x (r - c/T). J'ai mesure alpha POOLE sur
16 paires (buffer_alpha.py ligne 98) et r POOLE, puis compare les deux
moyennes. Or le pooling detruit exactement la quantite optimisee : une paire
peut avoir un gros flux ET un residu stationnaire, et aucune des deux moyennes
ne le montrera. C'est une limite de REPRESENTATION, pas de marche.

Ici chaque paire porte son propre alpha, son propre flux CAUSALEMENT capte,
son propre cout, et son propre R.

MULTIPLICITE. Retenir la meilleure de N paires est une selection. Benjamini-
Hochberg est applique sur le flux capte, et alpha est rendu avec son
erreur-type ; une paire dont alpha n'est pas estimable est ECARTEE, pas devinee.
"""
import json, math, os, statistics as st, time
from pathlib import Path
from prism_v2.funding_feed import _http_json, OKX_BASE
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
NC = [("SKHYNIX","SAMSUNG"),("SKHYNIX","MU"),("SKHYNIX","SOXL"),("SAMSUNG","MU"),
      ("MU","SOXL"),("NVDA","MRVL"),("NVDA","SOXL"),("MRVL","MU"),("INTC","MU"),
      ("TSLA","NVDA"),("MSTR","CRCL"),("AAOI","NBIS"),("CRWV","NBIS"),
      ("QQQ","SOXL"),("XAU","XAG"),("BZ","CL")]
CARRY = ["ADA","BCH","BTC","DOGE","DOT","ETC","ETH","FIL","LINK","LTC","SOL","XRP"]
DUREES = [1,2,3,5,7,10,14]
Q, IMR_DEF, TAKER, HORIZON, RUINE = 0.95, 0.04, 5.0, 60.0, 0.05
CACHE = Path(SCAN)/"candles_nc.json"


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


NC_NAMES = sorted({x for p in NC for x in p})
if CACHE.exists():
    C = {k: {int(a): b for a, b in v.items()} for k, v in json.load(open(CACHE)).items()}
else:
    C = {n: candles(f"{n}-USDT-SWAP") for n in NC_NAMES}
    json.dump({k: {str(a): b for a, b in v.items()} for k, v in C.items()}, open(CACHE,"w"))
RAW = json.load(open(Path("prism_v2/data/candles_1h.json")))["data"]
for b in CARRY:
    C[f"{b}_inv"] = {t: c for t,h,l,c in RAW[f"{b}-USD-SWAP"]}
    C[f"{b}_lin"] = {t: c for t,h,l,c in RAW[f"{b}-USDT-SWAP"]}

F = {}
for n in NC_NAMES: F[n] = funding(f"{n}-USDT-SWAP")
for b in CARRY:
    F[f"{b}_inv"] = funding(f"{b}-USD-SWAP"); F[f"{b}_lin"] = funding(f"{b}-USDT-SWAP")
tick = {t["instId"]: t for t in (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or [])}


def hs(inst):
    t = tick.get(inst)
    try:
        bid, ask = float(t["bidPx"]), float(t["askPx"])
        return (ask-bid)/(ask+bid)*10_000.0
    except (TypeError, ValueError, KeyError): return None


def z_ruine(T):
    """Quantile cohérent avec la ruine du programme, pas d'une periode."""
    n = HORIZON/T
    q = (1.0-RUINE)**(1.0/n)
    # Cornish-Fisher inutile ici : approximation rationnelle suffisante
    p = 1.0-q
    t = math.sqrt(-2.0*math.log(p))
    return t - (2.515517+0.802853*t+0.010328*t*t)/(1+1.432788*t+0.189269*t*t+0.001308*t**3)


def alpha_pair(resid):
    """alpha et son erreur-type, sur CETTE paire seule."""
    pts = []
    for T in DUREES:
        w = T*24
        if w >= len(resid)-10: continue
        out = []
        for i in range(len(resid)-w):
            c, lo = 0.0, 0.0
            for j in range(i, i+w):
                c += resid[j]
                if c < lo: lo = c
            out.append(-lo)
        if not out: continue
        out.sort()
        pts.append((T, out[min(len(out)-1, int(Q*len(out)))]))
    if len(pts) < 4: return None
    xs=[math.log(p[0]) for p in pts]; ys=[math.log(max(p[1],1e-9)) for p in pts]
    mx,my=st.fmean(xs),st.fmean(ys); sxx=sum((x-mx)**2 for x in xs)
    if sxx<=0: return None
    a=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sxx
    r=[y-(my+a*(x-mx)) for x,y in zip(xs,ys)]
    se=math.sqrt(sum(v*v for v in r)/max(1,len(pts)-2)/sxx)
    return a, se, {T:q for T,q in pts}


CAND = [(f"{a}/{b}","corr",a,b) for a,b in NC] + \
       [(f"{b} inv/lin","meme",f"{b}_inv",f"{b}_lin") for b in CARRY]
res, pv = [], {}
for nom, typ, ka, kb in CAND:
    ca, cb = C.get(ka), C.get(kb)
    if not ca or not cb: continue
    g = sorted(set(ca) & set(cb))
    if len(g) < 1500: continue
    ra=[math.log(ca[y]/ca[x]) for x,y in zip(g,g[1:])]
    rb=[math.log(cb[y]/cb[x]) for x,y in zip(g,g[1:])]
    if typ=="meme":
        beta=1.0
    else:
        mb=st.fmean(rb); ma=st.fmean(ra)
        vb=st.pvariance(rb)*len(rb)/(len(rb)-1)
        if vb<=0: continue
        beta=sum((x-ma)*(y-mb) for x,y in zip(ra,rb))/(len(ra)-1)/vb
    resid=[x-beta*y for x,y in zip(ra,rb)]
    A = alpha_pair(resid)
    if A is None: continue
    alpha, se_a, coussins = A
    fa, fb = F.get(ka,{}), F.get(kb,{})
    ts = sorted(set(fa)&set(fb))
    per = {y:(y-x)/3_600_000 for x,y in zip(ts,ts[1:]) if 0.5<=(y-x)/3_600_000<=12.0}
    if len(per) < 100: continue
    S = {t:(fa[t]-beta*fb[t])*10_000.0 for t in per}
    times = sorted(S)
    ia = f"{ka.replace('_inv','-USD-SWAP').replace('_lin','-USDT-SWAP')}" if typ=="meme" else f"{ka}-USDT-SWAP"
    ib = f"{kb.replace('_inv','-USD-SWAP').replace('_lin','-USDT-SWAP')}" if typ=="meme" else f"{kb}-USDT-SWAP"
    ha, hb = hs(ia), hs(ib)
    if ha is None or hb is None: continue
    cout = 4*TAKER + 2*(ha+hb)
    best=None
    for T in DUREES:
        if T not in coussins: continue
        N = max(1, int(round(T*24/st.median(list(per.values())))))
        gains, last = [], -1
        for i,t in enumerate(times):
            if i<=last or i+N>=len(times): continue
            sgn = 1.0 if S[t]>0 else -1.0
            gains.append(sgn*sum(S[times[j]] for j in range(i+1,i+1+N)) - cout)
            last=i+N
        if len(gains)<12: continue
        r_j = st.fmean(gains)/T + cout/T
        net = st.fmean(gains)/T
        # coussin a T, requantile pour la ruine du programme
        cous = coussins[T]/1.645*z_ruine(T)
        L = 1.0/(IMR_DEF + cous)
        R = L*net
        tt,p = t_test_one_sided(gains) if len(gains)>=30 else (float("nan"),1.0)
        if best is None or R>best["R"]:
            best={"T":T,"R":R,"L":L,"r":r_j,"net":net,"cous":cous,"n":len(gains),"t":tt,"p":p}
    if best is None: continue
    pv[nom]=best["p"]
    res.append((nom,typ,alpha,se_a,best))

res.sort(key=lambda x:-x[4]["R"])
print(f"{len(res)} paires evaluees PAR PAIRE (alpha propre, flux causal propre)\n")
print(f"{'paire':<20}{'type':<6}{'alpha':>8}{'+/-':>7}{'T':>4}{'coussin':>9}"
      f"{'levier':>8}{'flux r':>9}{'net':>8}{'R bps/j':>10}{'EUR/j':>8}{'n':>5}")
print("-"*100)
for nom,typ,a,se,b in res[:20]:
    print(f"{nom:<20}{typ:<6}{a:>8.3f}{se:>7.3f}{b['T']:>4}{b['cous']*100:>8.2f}%"
          f"{b['L']:>8.1f}{b['r']:>9.2f}{b['net']:>8.2f}{b['R']:>10.1f}"
          f"{b['R']/10_000*1000:>8.2f}{b['n']:>5}")

surv = benjamini_hochberg(pv, q=0.10) if pv else {}
pos=[x for x in res if x[4]["R"]>0]
print(f"\npaires a R positif : {len(pos)}/{len(res)}")
print(f"survivants Benjamini-Hochberg q=0,10 : {sum(surv.values())}/{len(surv)}")
if res:
    b=res[0]
    print(f"\nMEILLEURE PAIRE : {b[0]} -> {b[4]['R']:.1f} bps/jour = "
          f"{b[4]['R']/10_000*1000:.2f} EUR/jour  (BH : "
          f"{'SURVIT' if surv.get(b[0]) else 'ne survit pas'})")
    print(f"mecanisme poole precedent : 33,3 bps/jour")
    print(f"CRITERE D'ABANDON : meilleur R <= 40 bps/jour -> le pooling ne masquait rien")
    print(f"  -> R = {b[4]['R']:.1f} : "
          f"{'PIste POURSUIVIE' if b[4]['R']>40 else 'ABANDONNEE, representation innocentee'}")
json.dump([[n,t,a,s,{k:v for k,v in b.items()}] for n,t,a,s,b in res],
          open(os.path.join(SCAN,"per_pair.json"),"w"))
