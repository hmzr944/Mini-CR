"""Ratio de variance CONDITIONNEL : isoler la direction de la volatilite.

POURQUOI alpha NE SUFFIT PAS. alpha mesure la croissance du deplacement
ABSOLU. La volatilite etant moyenne-reversive, un etat de forte volatilite est
suivi d'une volatilite plus faible : |dp| croit alors plus lentement que la
racine du temps et alpha BAISSE — sans la moindre structure directionnelle.
Mon test ne distinguait pas « prix previsible » de « volatilite previsible ».

CE QUE MESURE LE RATIO DE VARIANCE. VR(q) = Var(somme de q rendements)
/ (q x Var d'un rendement). Sous marche aleatoire a volatilite VARIABLE,
VR = 1 exactement : la volatilite qui bouge ne cree aucun ecart. VR s'ecarte
de 1 uniquement en presence d'autocorrelation, c'est-a-dire de structure
DIRECTIONNELLE. C'est le discriminant que alpha n'etait pas.

  VR < 1  retour : le prix revient, une capture par retour a de la place
  VR > 1  tendance : le prix persiste
  VR = 1  aucune structure directionnelle, quelle que soit la volatilite

CONDITIONNEMENT CAUSAL. L'etat a t n'utilise que [t-24h, t]. Les rendements
mesures sont ceux de t vers t+q. Une seule variable d'etat, cinq buckets, tous
rapportes.

TRADUCTION ECONOMIQUE OBLIGATOIRE. Un VR significativement different de 1 ne
suffit pas : l'ecart est converti en bps par aller-retour et compare au cout
reel. Un effet statistique sans economie nette n'est pas une decouverte.
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
print(f"{len(CHOIX)} instruments, budget {CHOIX[-1][0]:.0f} a {CHOIX[0][0]:.0f}\n")

SEG = defaultdict(list)   # bucket -> liste de (serie de rendements futurs, cout)
n_ok = 0
for b, v, s, vol, hs in CHOIX:
    c = fetch(v, s)
    if len(c) < 400: continue
    ts = sorted(c); p = [c[t] for t in ts]
    r = [math.log(p[i+1]/p[i]) if p[i] > 0 and p[i+1] > 0 else 0.0
         for i in range(len(p)-1)]
    etat = [None]*TRAIL + [st.pstdev(r[i-TRAIL:i]) if i >= TRAIL else None
                           for i in range(TRAIL, len(r))]
    val = [e for e in etat if e and e > 0]
    if len(val) < 200: continue
    sl = [sorted(val)[int(q*len(val))] for q in [(k+1)/NB for k in range(NB-1)]]
    cout = 2*(FEES[v] + hs)
    for i in range(TRAIL, len(r) - max(QS) - 1):
        e = etat[i]
        if not e or e <= 0: continue
        k = sum(1 for x in sl if e > x)
        SEG[k].append((r[i:i+max(QS)], cout))
    n_ok += 1
    time.sleep(0.03)

print(f"{n_ok} instruments | {sum(len(v) for v in SEG.values()):,} fenetres\n")
if n_ok < 10:
    print("ECHANTILLON INSUFFISANT."); raise SystemExit

LAB = ["vol tres basse","vol basse","vol mediane","vol haute","vol tres haute"]
print(f"{'etat':<18}{'n':>9}", end="")
for q in QS: print(f"{'VR('+str(q)+')':>10}", end="")
print(f"{'meilleur ecart':>16}{'bps/A-R':>10}{'cout':>8}{'net':>9}")
print("-"*104)
res = {}
for k in range(NB):
    W = SEG.get(k, [])
    if len(W) < 300:
        print(f"{LAB[k]:<18}{len(W):>9}   insuffisant"); continue
    r1 = [w[0][0] for w in W]
    v1 = st.pvariance(r1)
    if v1 <= 0: continue
    vrs = {}
    for q in QS:
        sq = [sum(w[0][:q]) for w in W]
        vrs[q] = st.pvariance(sq)/(q*v1)
    # ecart le plus marque a 1, et son economie : l'ecart de variance
    # se traduit en amplitude exploitable sqrt(|1-VR|) x sigma x sqrt(q)
    qb = max(QS, key=lambda q: abs(vrs[q]-1.0))
    ec = vrs[qb]-1.0
    sig1 = math.sqrt(v1)*10_000.0
    bps = math.sqrt(abs(ec))*sig1*math.sqrt(qb)
    cout = st.median([w[1] for w in W])
    res[k] = (vrs, ec, bps, cout)
    print(f"{LAB[k]:<18}{len(W):>9}", end="")
    for q in QS: print(f"{vrs[q]:>10.3f}", end="")
    print(f"{ec:>+16.3f}{bps:>10.1f}{cout:>8.1f}{bps-cout:>9.1f}")

print(f"\nSous marche aleatoire a volatilite VARIABLE, VR = 1,000 exactement.")
print(f"Un ecart a 1 mesure une autocorrelation, donc une structure DIRECTIONNELLE.")
if res:
    ecs = [abs(v[1]) for v in res.values()]
    print(f"\n|VR - 1| maximal sur tous les etats : {max(ecs):.3f}")
    nets = [v[2]-v[3] for v in res.values()]
    print(f"meilleur net par aller-retour : {max(nets):+.1f} bps")
    print(f"\nKILL CONDITION : |VR - 1| < 0,05 dans tous les etats -> aucune")
    print(f"structure directionnelle conditionnelle detectable a ces horizons.")
    if max(ecs) < 0.05:
        print(f"  -> {max(ecs):.3f} : ATTEINTE.")
    else:
        print(f"  -> {max(ecs):.3f} >= 0,05 : structure presente, economie ci-dessus.")
json.dump({str(k): [v[1], v[2], v[3]] for k, v in res.items()},
          open(f"{SCAN}/vr_cond.json","w"))
