"""Si les frais tombaient a zero, un seul instrument deviendrait-il rentable ?

POURQUOI CETTE MESURE. Le cout d'aller-retour en taker vaut 21,8 bps, dont
20 de frais. Le mandat dit : quand le brut existe et que le net est negatif,
travailler sur le COUT, pas sur une nouvelle strategie. J'ai donc verifie si
une venue facture moins. Resultat de la verification (baremes publics
MEXC api/v3/exchangeInfo, quatre symboles) :

    takerCommission 0.0005 = 5,0 bps  ->  IDENTIQUE a OKX.
    makerCommission 0      = 0,0 bps  ->  contre 2,0 bps chez OKX.

Ma premisse « MEXC facture 2 bps contre 5 » etait FAUSSE cote taker. Ce qui
survit est une seule chose, cote passif : un frais maker nul existe.

CE QUE CE CRIBLE TESTE. Pas « MEXC est-il meilleur » : la microstructure
mesuree ici est celle d'OKX, et je ne transporte pas une microstructure d'une
venue a l'autre. Il teste une borne indifferente a la venue :

    en annulant ENTIEREMENT le terme de frais, le demi-spread encaisse
    depasse-t-il la selection adverse quelque part ?

Si NON, alors aucune reduction de cout, chez aucune venue, ne peut rendre la
fourniture de liquidite rentable sur cet univers : zero est le plancher. Le
levier COUT du mandat se ferme par mesure et non par opinion. Si OUI, le
candidat doit passer le pont de PnL complet, ou la probabilite de
remplissage reste INCONNUE.

BORNE SUPERIEURE, TROIS FOIS.
  1. File d'attente supposee gagnee a chaque transaction.
  2. Frais portes a zero, ce qui n'est vrai d'aucune jambe chez OKX.
  3. La colonne « meilleur h » choisit l'horizon APRES avoir vu le resultat.
     Elle est affichee pour montrer que meme ce choix tricheur ne sauve rien ;
     la decision se lit sur les horizons DECLARES, pas sur elle.

ANTI-LOOK-AHEAD. Carnet strictement anterieur a la transaction, comme dans
mm_scan.py dont ce crible reprend la mecanique de remplissage.
"""
import pickle, statistics as st, math, os
from bisect import bisect_left, bisect_right

SCRATCH = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
D = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))
quotes, trades = D["quotes"], D["trades"]

HORIZONS = [1, 5, 30, 300]
MIN_FILLS = 50
#: Baremes maker balayes, en bps par jambe passive. 2,0 = OKX mesure.
#: 0,0 = MEXC mesure (makerCommission 0). Les valeurs intermediaires
#: n'existent chez personne : elles montrent la PENTE.
FEE_GRID = [2.0, 1.0, 0.5, 0.0]


def tstat(x):
    if len(x) < 2:
        return float("nan")
    sd = st.stdev(x)
    return st.fmean(x) / (sd / math.sqrt(len(x))) if sd > 0 else float("nan")


#: Par instrument : demi-spread moyen encaisse, et selection adverse moyenne
#: a chaque horizon. Le net a un frais f s'ecrit hs - adv - f : le frais est
#: une CONSTANTE, il suffit donc de mesurer une fois et de balayer ensuite.
panel = {}
for inst in sorted(trades):
    q, tr = quotes.get(inst), trades[inst]
    if not q or len(tr) < MIN_FILLS:
        continue
    ts_q = [x[0] for x in q]
    hs_list, adv = [], {h: [] for h in HORIZONS}
    for t, px, usd, side in tr:
        i = bisect_left(ts_q, t) - 1
        if i < 0:
            continue
        b, a = q[i][1], q[i][2]
        if not (a > b > 0):
            continue
        mid = (b + a) / 2.0
        sgn = 1.0 if side == "buy" else -1.0
        fill = a if side == "buy" else b
        ok = {}
        for h in HORIZONS:
            j = bisect_right(ts_q, t + h * 1000) - 1
            if j < 0 or ts_q[j] <= t:
                ok = None
                break
            m2 = (q[j][1] + q[j][2]) / 2.0
            ok[h] = sgn * (m2 - mid) / mid * 10_000.0
        if ok is None:
            continue
        hs_list.append(abs(fill - mid) / mid * 10_000.0)
        for h in HORIZONS:
            adv[h].append(ok[h])
    if len(hs_list) >= MIN_FILLS:
        span_d = (q[-1][0] - q[0][0]) / 86_400_000.0
        panel[inst] = (hs_list, adv,
                       len(hs_list) / span_d if span_d > 0 else 0.0)

print(f"instruments retenus : {len(panel)}  (>= {MIN_FILLS} remplissages)")
print(f"demi-spread median : "
      f"{st.median([st.fmean(v[0]) for v in panel.values()]):.2f} bps")
print(f"selection adverse mediane a 30 s : "
      f"{st.median([st.fmean(v[1][30]) for v in panel.values()]):.2f} bps\n")

print("BRUT MOINS SELECTION ADVERSE, FRAIS EXCLUS")
print("Le frais ne change pas ce nombre. Il en est soustrait ensuite.\n")
print(f"{'horizon':>9}{'net median':>13}{'net max':>11}{'instrument':>20}"
      f"{'>0 sur':>9}")
print("-" * 64)
brut = {}
for h in HORIZONS:
    vals = [(st.fmean(v[0]) - st.fmean(v[1][h]), k) for k, v in panel.items()]
    brut[h] = dict((k, x) for x, k in vals)
    mx, arg = max(vals)
    print(f"{str(h)+' s':>9}{st.median([x for x, _ in vals]):>13.2f}"
          f"{mx:>11.2f}{arg:>20}{sum(1 for x, _ in vals if x > 0):>5}/"
          f"{len(vals):<3}")

print("\n\nCOMBIEN D'INSTRUMENTS A NET POSITIF, PAR NIVEAU DE FRAIS")
print("Colonnes : horizons DECLARES. 'max h' choisit l'horizon apres coup.\n")
print(f"{'frais maker':>13}", end="")
for h in HORIZONS:
    print(f"{str(h)+' s':>10}", end="")
print(f"{'max h':>10}   remarque")
print("-" * 76)
for f in FEE_GRID:
    print(f"{f:>10.1f} bps", end="")
    for h in HORIZONS:
        print(f"{sum(1 for k in panel if brut[h][k] - f > 0):>10}", end="")
    nmax = sum(1 for k in panel if max(brut[h][k] for h in HORIZONS) - f > 0)
    note = ""
    if f == 2.0:
        note = "OKX mesure"
    elif f == 0.0:
        note = "MEXC mesure (makerCommission 0)"
    print(f"{nmax:>10}   {note}")

print(f"\ntotal instruments : {len(panel)}")

#: Verdict economique au plancher absolu : frais nuls.
survivants = []
for k, v in panel.items():
    for h in HORIZONS:
        n = brut[h][k]
        if n > 0:
            t = tstat([a - b for a, b in zip(v[0], v[1][h])])
            survivants.append((n, k, h, t, v[2]))
print("\nA FRAIS NULS, INSTRUMENTS A NET POSITIF "
      "(tous horizons declares confondus)")
if not survivants:
    print("  AUCUN, sur aucun horizon.")
    print("  Zero est le plancher des frais. Aucune venue ne peut descendre")
    print("  plus bas. La selection adverse mange seule le demi-spread.")
else:
    for n, k, h, t, fpd in sorted(survivants, reverse=True):
        print(f"  {k:<18} net {n:+.2f} bps/remplissage  h={h}s  t={t:.1f}  "
              f"{fpd:,.0f} remplissages/jour")
