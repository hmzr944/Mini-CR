"""alpha du residu MEME SOUS-JACENT (inverse contre lineaire).

CE QUI EST TESTE. alpha = 0,493 a ete mesure sur une couverture par
CORRELATION : deux actifs differents, relies par un beta estime. Rien
n'empeche leur ecart de deriver, et la mesure dit qu'il derive.

Une couverture MEME SOUS-JACENT est un objet different. BTC-USD-SWAP et
BTC-USDT-SWAP portent le meme bitcoin ; leur ecart n'est pas un residu de
correlation, c'est le peg USDT/USD plus le basis entre deux contrats. Un peg
n'est pas une marche aleatoire — sauf s'il casse.

Si alpha y est nettement plus faible, le coussin cesse de croitre et le levier
n'est plus borne par racine de T. La question du netting de marge entre une
jambe margee en coin et une jambe margee en USDT devient alors decisive.

CRITERE D'ABANDON DECLARE AVANT LA MESURE : alpha >= 0,40 -> la contrainte de
levier est economique et non architecturale, la ligne est abandonnee.

DONNEES : 375 jours de bougies horaires deja dans le depot, 12 sous-jacents
avec leurs deux jambes. 3,75 fois plus que la mesure sur correlation.
"""
import json, math, os, statistics as st
from pathlib import Path

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
RAW = json.load(open(Path("prism_v2/data/candles_1h.json")))["data"]
BASES = sorted({k.split("-")[0] for k in RAW if k.endswith("-USD-SWAP")}
               & {k.split("-")[0] for k in RAW if k.endswith("-USDT-SWAP")})
DUREES_J = [1, 2, 3, 5, 7, 10, 14, 20, 30]
QUANTILE = 0.95
SEUIL_ABANDON = 0.40

print(f"{len(BASES)} sous-jacents cotes en inverse ET en lineaire : {BASES}\n")

RESID, SPANS = {}, {}
for b in BASES:
    a = {t: c for t, h, l, c in RAW[f"{b}-USD-SWAP"]}
    d = {t: c for t, h, l, c in RAW[f"{b}-USDT-SWAP"]}
    g = sorted(set(a) & set(d))
    if len(g) < 2000:
        print(f"  {b}: chevauchement insuffisant ({len(g)} h), exclu")
        continue
    ra = [math.log(a[y]/a[x]) for x, y in zip(g, g[1:])]
    rd = [math.log(d[y]/d[x]) for x, y in zip(g, g[1:])]
    # Pas de beta estime : meme sous-jacent, couverture 1 pour 1 en notionnel.
    # Le residu EST l'ecart inverse - lineaire, c'est-a-dire le peg + le basis.
    RESID[b] = [x - y for x, y in zip(ra, rd)]
    SPANS[b] = (g[-1]-g[0])/86_400_000

n_h = min(len(v) for v in RESID.values())
print(f"{len(RESID)} paires exploitables | {n_h} heures "
      f"({st.median(list(SPANS.values())):.0f} jours)\n")


def pires(serie, w):
    out = []
    for i in range(len(serie)-w):
        c, lo = 0.0, 0.0
        for j in range(i, i+w):
            c += serie[j]
            if c < lo: lo = c
        out.append(-lo)
    return out


print(f"{'duree':>7}{'fen. disjointes':>17}{'coussin q95':>14}"
      f"{'racine(T) attendu':>19}{'ecart':>9}")
print("-"*68)
pts, base = [], None
for T in DUREES_J:
    w = T*24
    if w >= n_h - 10: continue
    tous, disj = [], 0
    for b, s in RESID.items():
        tous.extend(pires(s, w))
        disj += max(0, (len(s)-w)//w)
    if not tous: continue
    tous.sort()
    q = tous[min(len(tous)-1, int(QUANTILE*len(tous)))]
    if base is None: base = (T, q)
    att = base[1]*math.sqrt(T/base[0])
    pts.append((T, q, disj))
    print(f"{T:>6}j{disj:>17,}{q*100:>13.3f}%{att*100:>18.3f}%"
          f"{(q/att-1)*100:>8.0f}%")

if len(pts) < 4:
    print("\nECHANTILLON INSUFFISANT — rien n'est conclu.")
    raise SystemExit

xs = [math.log(p[0]) for p in pts]
ys = [math.log(p[1]) for p in pts]
mx, my = st.fmean(xs), st.fmean(ys)
sxx = sum((x-mx)**2 for x in xs)
alpha = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/sxx
inter = my - alpha*mx
res = [y-(inter+alpha*x) for x, y in zip(xs, ys)]
se = math.sqrt(sum(r*r for r in res)/max(1, len(pts)-2)/sxx)
print(f"\nalpha MEME SOUS-JACENT : {alpha:.3f}  (erreur-type {se:.3f})")
print(f"  intervalle a 95 % : [{alpha-1.96*se:.3f} ; {alpha+1.96*se:.3f}]")
print(f"  pour memoire, couverture par CORRELATION : alpha = 0,493 [0,469 ; 0,517]")
print(f"  R2 = {1 - sum(r*r for r in res)/sum((y-my)**2 for y in ys):.4f}")

print(f"\nCRITERE D'ABANDON DECLARE D'AVANCE : alpha >= {SEUIL_ABANDON}")
if alpha >= SEUIL_ABANDON:
    print(f"  -> alpha = {alpha:.3f} : LIGNE ABANDONNEE. La contrainte de "
          f"levier est ECONOMIQUE, pas architecturale.")
else:
    print(f"  -> alpha = {alpha:.3f} < {SEUIL_ABANDON} : le residu revient. "
          f"La question du netting de marge devient decisive.")
    print(f"\n  Coussin a 14 j : {[p[1] for p in pts if p[0]==14]} "
          f"contre {[p[1] for p in pts if p[0]==1]} a 1 j")

json.dump({"alpha": alpha, "se": se, "points": pts,
           "bases": list(RESID)}, open(os.path.join(SCAN, "alpha_peg.json"), "w"))
