"""Test de martingale ROBUSTE a l'heteroscedasticite conditionnelle.

DEUX FOIS LE MEME PIEGE, ET LA CORRECTION.

alpha conditionnel : biaise par le retour de volatilite (0,644 en vol basse,
0,437 en vol haute) sans aucune structure directionnelle.

VR conditionnel : je croyais l'avoir corrige. Faux. VR(q) = Var(somme de q)
/ (q x Var d'un seul) utilise un DENOMINATEUR instantane conditionne a l'etat,
alors que le NUMERATEUR couvre les q periodes suivantes pendant lesquelles la
volatilite revient vers sa moyenne. En vol basse le denominateur est petit et
la volatilite remonte : VR explose a 3,49. En vol haute, l'inverse : 0,56.
Monotone, spectaculaire, et entierement du bruit de volatilite.

LE BON STATISTIQUE. Pour une difference de martingale, les termes croises
s'annulent :

    E[(somme des r)^2] = E[somme des r^2]

quelle que soit la dynamique de volatilite, et conditionnellement a toute
information passee. Le rapport

    M(q) = E[(somme des r)^2] / E[somme des r^2]

vaut donc 1 EXACTEMENT sous martingale, y compris conditionne sur un etat.
Numerateur et denominateur portent sur LA MEME FENETRE : aucun retour de
volatilite ne peut les desaligner. Un ecart a 1 mesure exclusivement la somme
des autocovariances, c'est-a-dire une structure DIRECTIONNELLE.

    M < 1  retour
    M > 1  tendance
    M = 1  martingale

ECONOMIE. L'ecart est converti en amplitude : la composante autocorrelee vaut
racine(|num - den|) en unites de prix. C'est une BORNE SUPERIEURE de ce qu'une
capture parfaite pourrait prendre, comparee au cout d'aller-retour reel.
"""
import json, math, os, statistics as st, time, urllib.request
from collections import defaultdict

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
FEES = {"OKX": 5.0, "MEXC": 2.0, "Bitget": 6.0}
QS = [2, 4, 8, 16, 32]
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

SEG = defaultdict(list)
n_ok, couts = 0, []
for b, v, s, vol, hs in CHOIX:
    c = fetch(v, s)
    if len(c) < 400: continue
    ts = sorted(c); p = [c[t] for t in ts]
    r = [math.log(p[i+1]/p[i]) if p[i] > 0 and p[i+1] > 0 else 0.0
         for i in range(len(p)-1)]
    etat = [None]*TRAIL + [st.pstdev(r[i-TRAIL:i]) for i in range(TRAIL, len(r))]
    val = [e for e in etat if e and e > 0]
    if len(val) < 200: continue
    sl = [sorted(val)[int(q*len(val))] for q in [(k+1)/NB for k in range(NB-1)]]
    for i in range(TRAIL, len(r) - max(QS) - 1):
        e = etat[i]
        if not e or e <= 0: continue
        k = sum(1 for x in sl if e > x)
        SEG[k].append(r[i:i+max(QS)])
    couts.append(2*(FEES[v]+hs)); n_ok += 1
    time.sleep(0.03)

COUT = st.median(couts) if couts else float("nan")
print(f"{n_ok} instruments | {sum(len(v) for v in SEG.values()):,} fenetres | "
      f"cout median aller-retour {COUT:.1f} bps\n")
LAB = ["vol tres basse","vol basse","vol mediane","vol haute","vol tres haute"]
print(f"{'etat':<18}{'n':>8}", end="")
for q in QS: print(f"{'M('+str(q)+')':>9}", end="")
print(f"{'|ecart| max':>13}{'amplitude bps':>15}{'net vs cout':>13}")
print("-"*100)
res = {}
for k in range(NB):
    W = SEG.get(k, [])
    if len(W) < 300:
        print(f"{LAB[k]:<18}{len(W):>8}   insuffisant"); continue
    Ms, amps = {}, {}
    for q in QS:
        num = st.fmean([sum(w[:q])**2 for w in W])
        den = st.fmean([sum(x*x for x in w[:q]) for w in W])
        if den <= 0: continue
        Ms[q] = num/den
        amps[q] = math.copysign(math.sqrt(abs(num-den)), num-den)*10_000.0
    if not Ms: continue
    qb = max(Ms, key=lambda q: abs(Ms[q]-1.0))
    amp = abs(amps[qb])
    res[k] = (Ms, Ms[qb]-1.0, amp)
    print(f"{LAB[k]:<18}{len(W):>8}", end="")
    for q in QS: print(f"{Ms.get(q, float('nan')):>9.3f}", end="")
    print(f"{Ms[qb]-1.0:>+13.3f}{amp:>15.1f}{amp-COUT:>13.1f}")

print(f"\nSous martingale, M = 1,000 EXACTEMENT, quelle que soit la dynamique")
print(f"de volatilite et quel que soit le conditionnement sur le passe.")
if res:
    mx = max(abs(v[1]) for v in res.values())
    print(f"\n|M - 1| maximal : {mx:.3f}")
    print(f"amplitude autocorrelee maximale : {max(v[2] for v in res.values()):.1f} bps")
    print(f"cout d'aller-retour             : {COUT:.1f} bps")
    print(f"\nKILL CONDITION : |M - 1| < 0,05 partout -> aucune structure")
    print(f"directionnelle conditionnelle a ces horizons.")
    print(f"  -> {mx:.3f} : {'ATTEINTE' if mx < 0.05 else 'structure presente'}")
json.dump({str(k): [v[1], v[2]] for k, v in res.items()},
          open(f"{SCAN}/mds.json","w"))
