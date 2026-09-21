"""La structure mesuree paie-t-elle son cout ? Test economique direct.

CE QUI PRECEDE. M(2) ressort au-dessus de 1 dans les cinq etats (1,057 a
1,365), ce qui implique une autocorrelation rho = M(2) - 1 de l'ordre de 0,13.
Six cellules sur vingt-cinq ont un IC95 excluant 1, contre 1,2 attendues. Mais
les signes sont INCOHERENTS entre etats voisins a q=16 (0,785 / 0,813 / 1,415),
et deux artefacts connus produisent exactement une autocorrelation positive a
court horizon :
  - PRIX PERIMES : une heure sans transaction reporte la cloture precedente et
    fabrique de l'autocorrelation ;
  - BOOTSTRAP MAL SPECIFIE : je reechantillonne des fenetres comme si elles
    etaient independantes alors que 35 instruments correles bougent ensemble,
    ce qui gonfle la significativite.

PLUTOT QUE DE TRANCHER LA STATISTIQUE, ON TRANCHE L'ECONOMIE. Si la structure
existe, elle doit produire un PnL net positif apres cout reel. Sinon elle est
economiquement nulle, que l'artefact soit resolu ou non.

TEST. Strictement causal : a l'instant t on observe le mouvement des h heures
ECOULEES, on prend la position dans son sens, on la tient h heures, on paie
l'aller-retour reel de l'instrument une fois. Aucune selection de periode,
aucune selection d'instrument, tous les etats rapportes.

FILTRE DES PRIX PERIMES applique d'abord : un instrument dont plus de 5 % des
heures ont un rendement exactement nul est ECARTE, pas corrige.
"""
import json, math, os, statistics as st, time, urllib.request
from collections import defaultdict
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
FEES = {"OKX": 5.0, "MEXC": 2.0, "Bitget": 6.0}
HS = [1, 2, 4, 8, 16]
TRAIL, NB = 24, 5


def get(u, t=25):
    r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r, timeout=t).read())


def fetch(v, s):
    try:
        if v == "OKX":
            rows, after = {}, None
            for _ in range(14):
                u = f"https://www.okx.com/api/v5/market/history-candles?instId={s}&bar=1H&limit=100"
                if after: u += f"&after={after}"
                d = get(u).get("data") or []
                if not d: break
                for r in d: rows[int(r[0])] = float(r[4])
                nb = min(int(r[0]) for r in d)
                if after and nb >= after: break
                after = nb
            return rows
        if v == "Bitget":
            d = get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={s}"
                    f"&productType=USDT-FUTURES&granularity=1H&limit=1000").get("data") or []
            return {int(r[0]): float(r[4]) for r in d}
        d = get(f"https://api.mexc.com/api/v3/klines?symbol={s}&interval=60m&limit=1000")
        return {int(r[0]): float(r[4]) for r in d}
    except Exception:
        return {}


U = json.load(open(f"{SCAN}/univers.json"))
E = [(sig/(2*(FEES[v]+hs)), v, s, vol, hs) for v, s, hs, sig, vol in U
     if v in FEES and vol >= 2_000_000.0]
E.sort(key=lambda x: -x[0])
CHOIX = E[:12] + E[len(E)//2-6:len(E)//2+6] + E[-12:]

DATA, ecartes = [], []
for b, v, s, vol, hs in CHOIX:
    c = fetch(v, s)
    if len(c) < 400: continue
    ts = sorted(c); p = [c[t] for t in ts]
    r = [math.log(p[i+1]/p[i]) if p[i] > 0 and p[i+1] > 0 else 0.0
         for i in range(len(p)-1)]
    zero = sum(1 for x in r if x == 0.0)/len(r)
    if zero > 0.05:
        ecartes.append((s, zero)); continue
    DATA.append((v, s, r, 2*(FEES[v]+hs)))
    time.sleep(0.03)

print(f"{len(DATA)} instruments retenus | {len(ecartes)} ecartes pour prix perimes")
for s, z in sorted(ecartes, key=lambda x: -x[1])[:8]:
    print(f"    {s:<22}{z:.1%} d'heures a rendement exactement nul")
if len(DATA) < 10:
    print("\nECHANTILLON INSUFFISANT."); raise SystemExit

# --- rho reel apres filtre ---
allr = [x for _, _, r, _ in DATA for x in r]
lag1 = []
for _, _, r, _ in DATA:
    m = st.fmean(r)
    lag1.append(sum((r[i]-m)*(r[i+1]-m) for i in range(len(r)-1))
                / max(1e-18, sum((x-m)**2 for x in r)))
print(f"\nautocorrelation d'ordre 1, apres filtre : mediane {st.median(lag1):+.4f}"
      f" | moyenne {st.fmean(lag1):+.4f} | n={len(lag1)}")
print(f"sigma horaire mediane : {st.median([st.pstdev(r) for _,_,r,_ in DATA])*10_000:.0f} bps")
print(f"cout median aller-retour : {st.median([c for _,_,_,c in DATA]):.1f} bps\n")

LAB = ["vol tres basse","vol basse","vol mediane","vol haute","vol tres haute"]
print(f"{'horizon':>8}{'etat':<18}{'n':>8}{'brut bps':>11}{'cout':>8}"
      f"{'NET bps':>10}{'t':>8}")
print("-"*72)
pv, series = {}, {}
for h in HS:
    for k in range(NB):
        g = []
        for v, s, r, cout in DATA:
            etat = [None]*TRAIL + [st.pstdev(r[i-TRAIL:i]) for i in range(TRAIL, len(r))]
            val = [e for e in etat if e and e > 0]
            if len(val) < 200: continue
            sl = [sorted(val)[int(x*len(val))] for x in [(j+1)/NB for j in range(NB-1)]]
            i = TRAIL + h
            while i + h < len(r):
                e = etat[i]
                if e and e > 0 and sum(1 for x in sl if e > x) == k:
                    passe = sum(r[i-h:i])
                    if passe != 0.0:
                        sgn = 1.0 if passe > 0 else -1.0
                        g.append(sgn*sum(r[i:i+h])*10_000.0 - cout)
                    i += h
                else:
                    i += 1
        if len(g) < 60: continue
        cm = st.median([c for _, _, _, c in DATA])
        tt, p = t_test_one_sided(g)
        pv[f"h{h}/k{k}"] = p; series[f"h{h}/k{k}"] = g
        print(f"{h:>7}h{LAB[k]:<18}{len(g):>8}{st.fmean(g)+cm:>11.2f}{cm:>8.1f}"
              f"{st.fmean(g):>10.2f}{tt:>8.2f}")

pos = [(k, st.fmean(v)) for k, v in series.items() if st.fmean(v) > 0]
surv = benjamini_hochberg(pv, q=0.10) if pv else {}
print(f"\ncellules a NET positif : {len(pos)}/{len(series)}")
print(f"survivants Benjamini-Hochberg q=0,10 : {sum(surv.values())}/{len(surv)}")
for k, m in sorted(pos, key=lambda x: -x[1])[:6]:
    print(f"  {k:<12}{m:>+8.2f} bps   {'SURVIT' if surv.get(k) else 'ne survit pas'}")
if not pos:
    print("  AUCUNE. La structure mesuree ne paie pas son cout d'execution.")
json.dump({"pos": len(pos), "tot": len(series),
           "surv": sum(surv.values())}, open(f"{SCAN}/final_eco.json","w"))
