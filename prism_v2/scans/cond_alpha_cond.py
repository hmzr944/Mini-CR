"""alpha CONDITIONNEL : la moyenne cache-t-elle un etat ou le mouvement differe ?

LA FAUTE CORRIGEE. alpha = 0,545 a ete mesure INCONDITIONNELLEMENT : E|dp(h)|
poolé sur tous les instants. Ce resultat est compatible avec « certaines
situations ont une forte previsibilite mais sont rares » — mon experience ne
pouvait pas les voir, elle les moyennait. Elle etablit l'absence de structure
temporelle MOYENNE, pas l'absence de structure conditionnelle.

CE QUI REND LE TEST DISCRIMINANT. alpha est l'exposant d'echelle du
deplacement : il est sans dimension. Conditionner sur le NIVEAU de volatilite
change l'amplitude des mouvements, pas leur exposant — sauf s'il existe une
vraie structure conditionnelle. Un alpha qui bouge entre buckets est donc le
signe d'un changement de regime du processus, pas d'un changement d'echelle.

VARIABLE D'ETAT, UNE SEULE, choisie par raisonnement economique et non par
balayage : la volatilite realisee glissante rapportee a sa propre mediane par
instrument. C'est la quantite meme qui decide si une economie est possible.
Cinq buckets, TOUS rapportes — jamais le meilleur.

CAUSALITE. L'etat a l'instant t n'utilise que la fenetre [t-24h, t]. alpha est
mesure sur les deplacements AVANT t vers t+h. Aucune information posterieure
n'entre dans la definition de l'etat.
"""
import json, math, os, statistics as st, time, urllib.request
from collections import defaultdict

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
os.makedirs(SCAN, exist_ok=True)
FEES = {"OKX": 5.0, "MEXC": 2.0, "Bitget": 6.0}
HOR = [1, 2, 4, 8, 16, 32, 64]
TRAIL = 24                 # fenetre d'etat, en heures
NB = 5                     # buckets d'etat


def get(u, timeout=25):
    r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def okx(inst, pages=14):
    rows, after = {}, None
    for _ in range(pages):
        u = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after: u += f"&after={after}"
        try: d = get(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: rows[int(r[0])] = float(r[4])
            except (ValueError, IndexError): continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after: break
        after = nb
    return rows


def bitget(sym):
    try:
        d = get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}"
                f"&productType=USDT-FUTURES&granularity=1H&limit=1000").get("data") or []
        return {int(r[0]): float(r[4]) for r in d}
    except Exception: return {}


def mexc(sym):
    try:
        d = get(f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=60m&limit=1000")
        return {int(r[0]): float(r[4]) for r in d}
    except Exception: return {}


# Univers : toute la gamme de budget, pas un coin. Source : univers_budget.py
U = json.load(open(f"{SCAN}/univers.json"))
E = [(sig/(2*(FEES[v]+hs)), v, s, vol) for v, s, hs, sig, vol in U
     if v in FEES and vol >= 2_000_000.0]
E.sort(key=lambda x: -x[0])
CHOIX = E[:12] + E[len(E)//2-6:len(E)//2+6] + E[-12:]
print(f"{len(CHOIX)} instruments couvrant toute la gamme de budget "
      f"({CHOIX[-1][0]:.0f} a {CHOIX[0][0]:.0f})\n")

# --- collecte des rendements et de l'etat causal ---
PAIRS = []       # (bucket, h, |deplacement|)
n_ok = 0
for b, v, s, vol in CHOIX:
    c = okx(s) if v == "OKX" else (bitget(s) if v == "Bitget" else mexc(s))
    if len(c) < 400: continue
    ts = sorted(c); p = [c[t] for t in ts]
    r = [math.log(p[i+1]/p[i]) if p[i] > 0 and p[i+1] > 0 else 0.0
         for i in range(len(p)-1)]
    # etat CAUSAL : volatilite realisee sur [t-TRAIL, t]
    etat = []
    for i in range(len(r)):
        if i < TRAIL: etat.append(None); continue
        w = r[i-TRAIL:i]
        etat.append(st.pstdev(w) if len(w) > 2 else None)
    valides = [e for e in etat if e is not None and e > 0]
    if len(valides) < 200: continue
    seuils = [sorted(valides)[int(q*len(valides))] for q in
              [(k+1)/NB for k in range(NB-1)]]
    for i in range(TRAIL, len(p)-max(HOR)):
        e = etat[i]
        if e is None or e <= 0: continue
        k = sum(1 for sgl in seuils if e > sgl)
        for h in HOR:
            if i+h >= len(p) or p[i] <= 0 or p[i+h] <= 0: continue
            PAIRS.append((k, h, abs(math.log(p[i+h]/p[i]))))
    n_ok += 1
    time.sleep(0.03)

print(f"{n_ok} instruments exploitables | {len(PAIRS):,} observations\n")
if n_ok < 10:
    print("ECHANTILLON INSUFFISANT — rien n'est conclu."); raise SystemExit

# --- alpha par bucket d'etat ---
par = defaultdict(lambda: defaultdict(list))
for k, h, d in PAIRS: par[k][h].append(d)

def fit(m):
    pts = [(h, st.fmean(v)) for h, v in sorted(m.items()) if len(v) >= 200]
    if len(pts) < 5: return None
    xs = [math.log(x[0]) for x in pts]; ys = [math.log(max(x[1], 1e-12)) for x in pts]
    mx, my = st.fmean(xs), st.fmean(ys); sxx = sum((x-mx)**2 for x in xs)
    if sxx <= 0: return None
    a = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/sxx
    res = [y-(my+a*(x-mx)) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(z*z for z in res)/max(1, len(pts)-2)/sxx)
    return a, se, sum(len(v) for v in m.values())

LAB = ["vol tres basse", "vol basse", "vol mediane", "vol haute", "vol tres haute"]
print(f"{'etat (quintile de vol. glissante)':<34}{'n':>11}{'alpha':>9}{'+/-':>8}"
      f"{'|dp| a 1h':>12}{'|dp| a 64h':>12}")
print("-"*88)
alphas = {}
for k in range(NB):
    F = fit(par[k])
    if F is None:
        print(f"{LAB[k]:<34}   insuffisant"); continue
    a, se, n = F
    alphas[k] = (a, se)
    d1 = st.fmean(par[k][1])*10_000 if par[k][1] else float('nan')
    d64 = st.fmean(par[k][64])*10_000 if par[k][64] else float('nan')
    print(f"{LAB[k]:<34}{n:>11,}{a:>9.3f}{se:>8.3f}{d1:>12.0f}{d64:>12.0f}")

glob = fit({h: [d for k, hh, d in PAIRS if hh == h] for h in HOR})
if glob: print(f"\n{'INCONDITIONNEL (tous etats)':<34}{glob[2]:>11,}{glob[0]:>9.3f}{glob[1]:>8.3f}")
print(f"{'marche aleatoire':<34}{'':>11}{0.5:>9.3f}")

if len(alphas) >= 2:
    vals = [v[0] for v in alphas.values()]
    ecart = max(vals) - min(vals)
    print(f"\nECART alpha entre buckets extremes : {ecart:.3f}")
    print(f"erreurs-types : {min(v[1] for v in alphas.values()):.3f} a "
          f"{max(v[1] for v in alphas.values()):.3f}")
    print(f"\nKILL CONDITION : ecart < 0,03 -> cette variable d'etat ne")
    print(f"conditionne rien d'economiquement utile.")
    if ecart < 0.03:
        print(f"  -> ecart = {ecart:.3f} : ATTEINTE. Cette conditionnalite est")
        print(f"     fermee. La conditionnalite EN GENERAL ne l'est pas.")
    else:
        print(f"  -> ecart = {ecart:.3f} >= 0,03 : structure conditionnelle")
        print(f"     presente. Reste a en mesurer l'economie.")
json.dump({str(k): v for k, v in alphas.items()}, open(f"{SCAN}/alpha_cond.json","w"))
