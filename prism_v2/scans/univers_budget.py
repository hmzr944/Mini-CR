"""Quel univers merite d'etre travaille ? L'economie doit justifier le choix.

LE PARAMETRE STRUCTUREL. En concurrence, le teneur de marche fixe le spread
pour couvrir la selection adverse. Le rapport

    mouvement disponible par jour  /  cout d'un aller-retour

mesure donc le BUDGET economique brut d'un instrument : combien de fois par
jour le prix parcourt la distance qu'il faut payer pour agir. Petit, aucune
strategie ne peut payer son propre cout. Grand, c'est une condition NECESSAIRE
— pas suffisante, car la part previsible du mouvement reste inconnue.

Ce n'est pas une strategie. C'est le choix de l'univers, fait par la mesure.

MESURE. Volatilite par l'estimateur de Parkinson sur le haut/bas 24 h :
sigma = ln(H/L) / (2 sqrt(ln 2)) — plus efficace que cloture a cloture et
disponible dans tous les tickers publics. Cout = 2 x (frais taker publics +
demi-spread observe). Aucun instrument sans volume reel n'est retenu : un
spread enorme sur un carnet vide n'est pas une opportunite.

PRISM n'a jamais regarde que 21 instruments d'une seule venue. L'univers
joignable depuis ce conteneur en compte environ 5 700 sur sept venues.
"""
import json, math, os, statistics as st, urllib.request
from collections import defaultdict

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
os.makedirs(SCAN, exist_ok=True)
K = 2*math.sqrt(math.log(2.0))          # Parkinson


def get(u, body=None, timeout=25):
    r = urllib.request.Request(
        u, data=json.dumps(body).encode() if body else None,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


#: Frais TAKER publics, niveau de base, sans reduction. Source : baremes
#: publics des venues. Marques ESTIME : je ne peux pas les mesurer sans compte.
#:
#: CORRECTION 2026-09-18. MEXC etait ici a 2.0 bps. C'etait FAUX. Le bareme
#: public MEXC (api/v3/exchangeInfo, champ takerCommission) renvoie 0.0005
#: soit 5.0 bps, identique a OKX, sur les quatre symboles interroges
#: (BTCUSDT, NEARUSDT, ARBUSDT, PONSUSDT). Le classement d'univers produit
#: par ce crible avant cette date sous-estimait donc le cout MEXC.
#: Le meme bareme donne makerCommission = 0 : le frais MAKER MEXC est nul.
FEES = {"OKX": 5.0, "Gate": 5.0, "MEXC": 5.0, "Bitget": 6.0,
        "Coinbase": 60.0, "Hyperliquid": 4.5}
#: Frais MAKER publics. MEXC mesure (makerCommission 0). Les autres restent
#: ESTIME : non interroges.
MAKER_FEES = {"MEXC": 0.0, "OKX": 2.0}
MIN_VOL_USD = 50_000.0                  # volume 24 h minimal pour etre retenu

rows = []   # (venue, symbole, demi_spread_bps, sigma_j_bps, vol_usd)


def add(venue, sym, bid, ask, hi, lo, vol):
    if not (ask > bid > 0 and hi >= lo > 0 and vol >= MIN_VOL_USD):
        return
    mid = (bid+ask)/2.0
    hs = (ask-bid)/(ask+bid)*10_000.0
    sig = math.log(hi/lo)/K*10_000.0 if hi > lo else 0.0
    if sig <= 0 or hs <= 0:
        return
    rows.append((venue, sym, hs, sig, vol))


# ---- OKX (swap + spot) ----
for it, tag in (("SWAP","OKX"), ("SPOT","OKX")):
    for t in (get(f"https://www.okx.com/api/v5/market/tickers?instType={it}").get("data") or []):
        try:
            add(tag, t["instId"], float(t["bidPx"]), float(t["askPx"]),
                float(t["high24h"]), float(t["low24h"]),
                float(t.get("volCcy24h") or 0)*float(t.get("last") or 0)
                if it=="SWAP" else float(t.get("volCcy24h") or 0))
        except (KeyError, ValueError, TypeError): continue

# ---- Gate perpetuels ----
for t in get("https://api.gateio.ws/api/v4/futures/usdt/tickers"):
    try:
        last=float(t["last"]); 
        add("Gate", t["contract"], float(t["highest_bid"]), float(t["lowest_ask"]),
            float(t["high_24h"]), float(t["low_24h"]), float(t.get("volume_24h_usd") or 0))
    except (KeyError, ValueError, TypeError): continue

# ---- MEXC spot : bookTicker + 24hr separement ----
try:
    bt={x["symbol"]:x for x in get("https://api.mexc.com/api/v3/ticker/bookTicker")}
    for t in get("https://api.mexc.com/api/v3/ticker/24hr"):
        b=bt.get(t["symbol"])
        if not b: continue
        try:
            add("MEXC", t["symbol"], float(b["bidPrice"]), float(b["askPrice"]),
                float(t["highPrice"]), float(t["lowPrice"]), float(t.get("quoteVolume") or 0))
        except (KeyError, ValueError, TypeError): continue
except Exception as e: print("MEXC:", str(e)[:60])

# ---- Bitget perpetuels ----
try:
    for t in (get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES").get("data") or []):
        add("Bitget", t["symbol"], float(t["bidPr"]), float(t["askPr"]),
            float(t["high24h"]), float(t["low24h"]), float(t.get("usdtVolume") or 0))
except Exception as e: print("Bitget:", str(e)[:60])

# ---- Hyperliquid ----
try:
    m=get("https://api.hyperliquid.xyz/info", {"type":"metaAndAssetCtxs"})
    for u,c in zip(m[0]["universe"], m[1]):
        if u.get("isDelisted"): continue
        px=c.get("impactPxs")
        if not (px and len(px)==2): continue
        mid=float(c["markPx"]); pr=float(c.get("prevDayPx") or mid)
        # pas de haut/bas 24 h : on utilise |variation| comme borne INFERIEURE
        rng=abs(math.log(mid/pr)) if pr>0 else 0.0
        if rng<=0: continue
        hs=(float(px[1])-float(px[0]))/(float(px[1])+float(px[0]))*10_000.0
        vol=float(c.get("dayNtlVlm") or 0)
        if hs>0 and vol>=MIN_VOL_USD:
            rows.append(("Hyperliquid", u["name"], hs, rng/K*10_000.0, vol))
except Exception as e: print("HL:", str(e)[:60])

# ---- Coinbase (frais retail eleves : contre-exemple utile) ----
try:
    for p in get("https://api.exchange.coinbase.com/products")[:400]:
        pass   # stats par produit = 1 appel chacun, trop lent ; venue ecartee
except Exception: pass

print(f"{len(rows)} instruments retenus (volume 24 h >= {MIN_VOL_USD:,.0f} USD)\n")
print(f"{'venue':<13}{'n':>6}{'frais':>7}{'demi-spr med':>14}{'sigma/j med':>13}"
      f"{'cout A/R med':>14}{'BUDGET sigma/cout':>19}")
print("-"*86)
par = defaultdict(list)
for v,s,hs,sig,vol in rows: par[v].append((hs,sig,vol,s))
resume=[]
for v in sorted(par, key=lambda x: -len(par[x])):
    L=par[v]; fee=FEES.get(v)
    if fee is None: continue
    hs_m=st.median([x[0] for x in L]); sg_m=st.median([x[1] for x in L])
    cout=[2*(fee+x[0]) for x in L]
    bud=sorted(x[1]/(2*(fee+x[0])) for x in L)
    resume.append((v,len(L),fee,hs_m,sg_m,st.median(cout),st.median(bud),bud))
    print(f"{v:<13}{len(L):>6}{fee:>7.1f}{hs_m:>14.2f}{sg_m:>13.0f}"
          f"{st.median(cout):>14.1f}{st.median(bud):>19.2f}")

print(f"\nBUDGET = mouvement quotidien / cout d'un aller-retour.")
print(f"C'est une condition NECESSAIRE, jamais suffisante : la part previsible")
print(f"du mouvement reste inconnue et n'est pas mesuree ici.\n")
ref=[r for r in resume if r[0]=="OKX"]
if ref:
    b0=ref[0][6]
    print(f"reference OKX : budget median {b0:.2f}")
    for v,n,fee,hs,sg,c,b,_ in sorted(resume,key=lambda r:-r[6]):
        print(f"  {v:<13}{b:>7.2f}   soit {b/b0:>5.2f}x OKX")
    print(f"\nmeilleur decile, toutes venues confondues :")
    tous=sorted([x[1]/(2*(FEES[v]+x[0])) for v in par if v in FEES for x in par[v]])
    n=len(tous)
    print(f"  median {tous[n//2]:.2f} | p90 {tous[int(.9*n)]:.2f} | "
          f"p99 {tous[int(.99*n)]:.2f} | max {tous[-1]:.2f}")
json.dump([[v,s,hs,sig,vol] for v,s,hs,sig,vol in rows], open(f"{SCAN}/univers.json","w"))
