"""M(q) avec fenetres DISJOINTES et intervalle bootstrap. Et sans fausse economie.

DEUX FAUTES CORRIGEES DANS MON PROPRE TEST.

1. CHEVAUCHEMENT. Les fenetres avancaient d'une heure avec q jusqu'a 32 : les
   8 122 « observations » d'un bucket valaient environ 250 fenetres reellement
   independantes. Sur un estimateur de MOMENT D'ORDRE 4 applique a des
   rendements a queues epaisses, cela ne determine rien — une poignee de
   mouvements domine. Aucune barre d'erreur n'etait rapportee, ce qui rendait
   la faute invisible. Ici : fenetres disjointes, et bootstrap par blocs.

2. FAUSSE TRADUCTION ECONOMIQUE. J'ecrivais amplitude = racine(|num - den|),
   qui vaut environ 0,8 fois la volatilite de la fenetre : ce n'est pas un
   edge, c'est la volatilite reformulee. Et M > 1 signifie TENDANCE : pour la
   monetiser il faut predire la direction, que ce test ne fournit pas. Une
   variance en exces n'est pas du PnL capturable. Aucune conversion en bps
   n'est donc faite ici : le test repond a « structure directionnelle,
   oui ou non », et rien de plus.

Ce que le test peut encore etablir : si M est indistinguable de 1 sur des
fenetres disjointes, aucune structure directionnelle n'est detectable a ces
horizons dans cette representation.
"""
import json, math, os, random, statistics as st, time, urllib.request
from collections import defaultdict

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
FEES = {"OKX": 5.0, "MEXC": 2.0, "Bitget": 6.0}
QS = [2, 4, 8, 16, 32]
TRAIL, NB, BOOT = 24, 5, 400
random.seed(20260918)


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

# fenetres DISJOINTES par horizon
SEG = {q: defaultdict(list) for q in QS}
n_ok = 0
for b, v, s, vol, hs in CHOIX:
    c = fetch(v, s)
    if len(c) < 400: continue
    ts = sorted(c); p = [c[t] for t in ts]
    r = [math.log(p[i+1]/p[i]) if p[i] > 0 and p[i+1] > 0 else 0.0
         for i in range(len(p)-1)]
    etat = [None]*TRAIL + [st.pstdev(r[i-TRAIL:i]) for i in range(TRAIL, len(r))]
    val = [e for e in etat if e and e > 0]
    if len(val) < 200: continue
    sl = [sorted(val)[int(x*len(val))] for x in [(k+1)/NB for k in range(NB-1)]]
    for q in QS:
        i = TRAIL
        while i + q < len(r):
            e = etat[i]
            if e and e > 0:
                k = sum(1 for x in sl if e > x)
                SEG[q][k].append(r[i:i+q])
                i += q                      # DISJOINT
            else:
                i += 1
    n_ok += 1
    time.sleep(0.03)

print(f"{n_ok} instruments, fenetres DISJOINTES\n")


def M_of(W):
    num = st.fmean([sum(w)**2 for w in W])
    den = st.fmean([sum(x*x for x in w) for w in W])
    return num/den if den > 0 else float("nan")


LAB = ["vol tres basse","vol basse","vol mediane","vol haute","vol tres haute"]
print(f"{'etat':<18}{'q':>4}{'n disjoint':>12}{'M':>8}{'IC95 bootstrap':>22}"
      f"{'ecarte de 1 ?':>15}")
print("-"*82)
sig_cells, tot = [], 0
for k in range(NB):
    for q in QS:
        W = SEG[q].get(k, [])
        if len(W) < 60:
            continue
        m = M_of(W)
        bs = []
        n = len(W)
        for _ in range(BOOT):
            samp = [W[random.randrange(n)] for _ in range(n)]
            bs.append(M_of(samp))
        bs.sort()
        lo, hi = bs[int(.025*BOOT)], bs[int(.975*BOOT)]
        ecart = not (lo <= 1.0 <= hi)
        tot += 1
        if ecart: sig_cells.append((LAB[k], q, m, lo, hi))
        print(f"{LAB[k]:<18}{q:>4}{n:>12}{m:>8.3f}"
              f"{'['+format(lo,'.3f')+' ; '+format(hi,'.3f')+']':>22}"
              f"{'OUI' if ecart else 'non':>15}")

print(f"\nSous martingale, M = 1,000 exactement. IC95 par bootstrap sur")
print(f"fenetres disjointes : la seule lecture honnete de l'incertitude.")
print(f"\ncellules dont l'IC95 EXCLUT 1 : {len(sig_cells)}/{tot}")
attendu = 0.05*tot
print(f"attendu par hasard a 5 % : {attendu:.1f}")
for lab, q, m, lo, hi in sig_cells:
    print(f"  {lab:<18} q={q:<3} M={m:.3f}  [{lo:.3f} ; {hi:.3f}]")
print(f"\nKILL CONDITION : nombre de cellules significatives <= attendu par")
print(f"hasard -> aucune structure directionnelle etablie.")
print(f"  -> {len(sig_cells)} contre {attendu:.1f} attendues : "
      f"{'ATTEINTE' if len(sig_cells) <= attendu else 'a examiner'}")
json.dump({"sig": len(sig_cells), "tot": tot,
           "cells": [[l,q,m,lo,hi] for l,q,m,lo,hi in sig_cells]},
          open(f"{SCAN}/mds_boot.json","w"))
