"""Le net a frais nuls survit-il a un echantillon honnete ?

CE QUE fee_floor.py A MONTRE. En annulant le terme de frais, sept des treize
instruments affichent un net positif a l'horizon 300 s, dont BCH-USDT-SWAP a
+3,08 bps par remplissage avec t = 8,8.

POURQUOI JE NE LE CROIS PAS ENCORE. Ce t = 8,8 est calcule sur 1 348
remplissages. La bande dure 30 heures. A l'horizon 300 s il n'existe que
30 x 3600 / 300 = 360 fenetres DISJOINTES, et tous les remplissages tombant
dans la meme fenetre voient la meme trajectoire de prix : ce sont des copies
d'une seule observation, pas 1 348 observations. Le t affiche est gonfle par
le recouvrement. Quatre resultats spectaculaires du projet sont morts
exactement la.

CE CRIBLE. Moyenne du net par BLOC DISJOINT de longueur h, puis test de
Student sur les moyennes de blocs, puis correction de Benjamini-Hochberg
sur les treize instruments. Un bloc = une observation. Le frais est nul :
c'est le plancher absolu, aucune venue ne descend plus bas.

CE QU'IL NE TESTE PAS. La probabilite de remplissage reste INCONNUE :
PRISM n'a pas de modele de file d'attente. Tout ce qui sort d'ici est une
BORNE SUPERIEURE, file supposee gagnee a chaque transaction.
"""
import pickle, statistics as st, math, os
from bisect import bisect_left, bisect_right

SCRATCH = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
D = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))
quotes, trades = D["quotes"], D["trades"]

HORIZONS = [30, 300]
MIN_FILLS = 50
MIN_BLOCS = 20
MAKER_BPS = 0.0                 # plancher absolu : MEXC makerCommission 0


def tstat(x):
    if len(x) < 2:
        return float("nan")
    sd = st.stdev(x)
    return st.fmean(x) / (sd / math.sqrt(len(x))) if sd > 0 else float("nan")


def p_unilateral(t, n):
    """p d'un t de Student unilateral, par l'approximation normale.

    n >= 20 blocs est impose plus bas : l'ecart a la loi exacte y est
    inferieur au troisieme chiffre, et il joue CONTRE la decouverte.
    """
    if t != t:
        return 1.0
    return 0.5 * math.erfc(t / math.sqrt(2.0))


resultats = []
for inst in sorted(trades):
    q, tr = quotes.get(inst), trades[inst]
    if not q or len(tr) < MIN_FILLS:
        continue
    ts_q = [x[0] for x in q]
    t0 = ts_q[0]
    for h in HORIZONS:
        blocs = {}
        for t, px, usd, side in tr:
            i = bisect_left(ts_q, t) - 1
            if i < 0:
                continue
            b, a = q[i][1], q[i][2]
            if not (a > b > 0):
                continue
            mid = (b + a) / 2.0
            j = bisect_right(ts_q, t + h * 1000) - 1
            if j < 0 or ts_q[j] <= t:
                continue
            sgn = 1.0 if side == "buy" else -1.0
            fill = a if side == "buy" else b
            hs = abs(fill - mid) / mid * 10_000.0
            m2 = (q[j][1] + q[j][2]) / 2.0
            adv = sgn * (m2 - mid) / mid * 10_000.0
            blocs.setdefault(int((t - t0) // (h * 1000)), []).append(
                hs - adv - MAKER_BPS)
        if len(blocs) < MIN_BLOCS:
            continue
        m = [st.fmean(v) for v in blocs.values()]
        n_fills = sum(len(v) for v in blocs.values())
        t_bloc = tstat(m)
        resultats.append((inst, h, n_fills, len(m), st.fmean(m), t_bloc,
                          p_unilateral(t_bloc, len(m))))

print(f"frais maker : {MAKER_BPS} bps (plancher : MEXC makerCommission 0)")
print(f"bande : 30 h. Un bloc DISJOINT de longueur h = une observation.\n")
print(f"{'instrument':<18}{'h':>5}{'remplis.':>10}{'blocs':>7}"
      f"{'net moyen':>11}{'t bloc':>9}{'p':>9}")
print("-" * 69)
for inst, h, nf, nb, mu, t, p in resultats:
    print(f"{inst:<18}{h:>5}{nf:>10,}{nb:>7}{mu:>11.2f}{t:>9.2f}{p:>9.4f}")

#: Benjamini-Hochberg sur l'ensemble des tests menes ici. Le nombre de tests
#: est le nombre de couples (instrument, horizon) reellement evalues, pas le
#: nombre de ceux qui ont survecu.
ps = sorted((p, i) for i, (_, _, _, _, _, _, p) in enumerate(resultats))
m_tests = len(ps)
seuil, k_max = 0.05, 0
for rang, (p, _) in enumerate(ps, 1):
    if p <= seuil * rang / m_tests:
        k_max = rang
retenus = {i for _, i in ps[:k_max]}

print(f"\nBenjamini-Hochberg, {m_tests} tests, FDR 5 %")
if not retenus:
    print("  AUCUN test ne survit.")
    print("  Le net positif de fee_floor.py etait un effet du recouvrement")
    print("  des fenetres. A frais NULS, c'est-a-dire au plancher absolu du")
    print("  cout, aucun instrument de cette bande ne fournit de liquidite")
    print("  a profit, meme file d'attente supposee gagnee.")
else:
    print(f"  {len(retenus)} test(s) survivent :")
    for i in sorted(retenus, key=lambda i: -resultats[i][4]):
        inst, h, nf, nb, mu, t, p = resultats[i]
        print(f"    {inst:<18} h={h}s  net {mu:+.2f} bps/remplissage  "
              f"{nb} blocs  t={t:.2f}  p={p:.4f}")
    print("\n  BORNE SUPERIEURE. La probabilite de remplissage est INCONNUE.")
    print("  Ces nets supposent la file gagnee a chaque transaction.")
