"""Dislocations transversales sur un panneau synchrone de 15 perpetuels inverses.

Ce que cette donnee permet et qu'aucune autre du depot ne permettait : quinze
instruments echantillonnes sur la MEME horloge a 250 ms pendant 6,5 heures.
Toutes les mesures precedentes portaient sur un instrument a la fois, ou sur
des instruments lus a des instants differents. Un ecart entre deux instruments
ne pouvait donc pas etre distingue d'un decalage d'echantillonnage.

Les quinze sont regles en USD. Aucun taux de change n'est traverse.

MESURE. Pour chaque alt j, sur une fenetre glissante causale, on estime beta
contre BTC. On regarde ensuite, sur les K dernieres secondes, de combien j a
bouge en plus de beta x BTC. C'est la dislocation. Puis on mesure combien elle
se referme dans les H secondes suivantes.

CAUSALITE. Le beta a l'instant t est estime sur [t - W - K, t - K] :
strictement avant la fenetre de dislocation elle-meme, donc la dislocation ne
participe pas a l'estimation de son propre beta.

COUT. Deux jambes, aller et retour : quatre traversees. Frais taker 4 x 5 bps,
plus deux fois la somme des demi-spreads des deux jambes. Le cout est calcule
par paire a partir des spreads REELLEMENT observes sur cette paire.
"""
import pickle, statistics as st, math, sys
from bisect import bisect_right

from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

import os as _os
#: Repertoire de travail des mesures. Les fichiers intermediaires (panneau,
#: bande) n'ont pas leur place dans le depot : ils se recalculent.
from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
_os.makedirs(SCRATCH, exist_ok=True)



P = pickle.load(open(f"{SCRATCH}/panel.pkl", "rb"))
REF = "BTC-USD-SWAP"
BAR_MS = 1_000        # pas de la grille
W_BARS = 600          # fenetre d'estimation du beta (10 min)
K_BARS = 30           # fenetre de dislocation (30 s)
H_BARS = [10, 30, 60, 300]
TAKER = 5.0
#: Balayage du frais par traversee. 5,0 bps est le tarif public taker OKX.
#: 0,0 n'est offert par aucune venue connue ici : il sert de borne. Si le
#: signe ne bascule meme pas a frais nuls, aucun bareme ne le fera.
FEE_SWEEP = [5.0, 2.0, 1.0, 0.0]

# --- grille commune -------------------------------------------------------
t0 = max(v[0][0] for v in P.values())
t1 = min(v[-1][0] for v in P.values())
grid = list(range(t0, t1 + 1, BAR_MS))
mids, hs = {}, {}
for inst, v in P.items():
    ts = [x[0] for x in v]
    m, out = [], []
    for g in grid:
        i = bisect_right(ts, g) - 1
        if i < 0:
            out.append(None)
            continue
        b, a = v[i][1], v[i][2]
        out.append((b + a) / 2.0 if a > b > 0 else None)
    mids[inst] = out
    hh = sorted((x[2] - x[1]) / (x[1] + x[2]) * 10_000.0 for x in v if x[2] > x[1] > 0)
    hs[inst] = hh[len(hh) // 2]

good = [k for k in range(len(grid))
        if all(mids[i][k] is not None for i in mids)]
print(f"grille {BAR_MS} ms : {len(grid):,} pas, {len(good):,} complets sur "
      f"{len(mids)} instruments ({(t1-t0)/3_600_000:.2f} h)")
if len(good) < len(grid) * 0.9:
    print("  ATTENTION : moins de 90 % des pas sont complets.")

idx = {k: n for n, k in enumerate(good)}
R = {i: [math.log(mids[i][good[n]] / mids[i][good[n-1]]) * 10_000.0
         for n in range(1, len(good))] for i in mids}
N = len(good) - 1
print(f"rendements par barre : {N:,}\n")


def beta(y, x, lo, hi):
    xs = x[lo:hi]
    ys = y[lo:hi]
    mx = st.fmean(xs)
    my = st.fmean(ys)
    den = sum((a - mx) ** 2 for a in xs)
    if den <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / den


ALTS = [i for i in sorted(mids) if i != REF]
print(f"{'paire':<16}{'cout bps':>10}{'n':>8}{'|disl| med':>12}{'p99':>9}"
      f"{'max':>9}{'>cout':>8}", end="")
for h in H_BARS:
    print(f"{'ferm'+str(h)+'s':>10}", end="")
print()
print("-" * 106)

nets = {}
for alt in ALTS:
    cost = 4 * TAKER + 2 * (hs[alt] + hs[REF])
    y, x = R[alt], R[REF]
    disl, closes = [], {h: [] for h in H_BARS}
    big = 0
    for n in range(W_BARS + K_BARS, N - max(H_BARS)):
        b = beta(y, x, n - W_BARS - K_BARS, n - K_BARS)
        if b is None:
            continue
        d = sum(y[n - K_BARS:n]) - b * sum(x[n - K_BARS:n])
        disl.append(abs(d))
        if abs(d) < cost:
            continue
        big += 1
        sgn = -1.0 if d > 0 else 1.0          # on parie sur le retour
        for h in H_BARS:
            fwd = sum(y[n:n + h]) - b * sum(x[n:n + h])
            closes[h].append(sgn * fwd - cost)
    if not disl:
        continue
    ds = sorted(disl)
    nn = len(ds)
    print(f"{alt:<16}{cost:>10.1f}{nn:>8}{ds[nn//2]:>12.1f}{ds[int(.99*nn)]:>9.1f}"
          f"{ds[-1]:>9.1f}{big/nn:>7.1%}", end="")
    for h in H_BARS:
        v = closes[h]
        print(f"{(st.fmean(v) if len(v) >= 30 else float('nan')):>10.1f}", end="")
        if len(v) >= 30:
            nets[f"{alt}@{h}s"] = v
    print()

print(f"\n« ferm Hs » = PnL net moyen en bps par aller-retour, cout deja retire,")
print(f"sur les seuls episodes ou la dislocation depassait le cout de la paire.")

pv = {k: t_test_one_sided(v)[1] for k, v in nets.items()}
surv = benjamini_hochberg(pv, q=0.10)
print(f"\n{len(pv)} tests (paire x horizon), Benjamini-Hochberg a q=0,10")
pos = [(k, st.fmean(nets[k]), t_test_one_sided(nets[k])[0], len(nets[k]))
       for k in nets if st.fmean(nets[k]) > 0]
pos.sort(key=lambda r: -r[1])
print(f"tests a moyenne positive : {len(pos)}/{len(pv)}")
for k, m, t, n in pos[:10]:
    print(f"  {k:<24}{m:>9.2f} bps  t={t:>6.2f}  n={n:>6}  "
          f"{'SURVIT BH' if surv[k] else 'ne survit pas'}")
if not pos:
    print("  AUCUN. Aucune dislocation superieure au cout ne se referme "
          "assez pour payer ce cout.")

# ── le signe basculerait-il a frais plus bas ? ─────────────────────────────
# Sur les paires les plus liquides, les demi-spreads seuls valent moins que
# la dislocation mediane. Le dire ne suffit pas : on remesure, en faisant
# varier le frais par traversee, seuil de declenchement compris. Un frais
# plus bas declenche sur des dislocations plus petites, donc le resultat
# n'est PAS le meme calcul decale d'une constante.
print(f"\n\nBALAYAGE DU FRAIS PAR TRAVERSEE")
print(f"Le seuil de declenchement suit le cout : baisser les frais fait entrer")
print(f"sur des dislocations plus petites. Rien n'est decale d'une constante.\n")
print(f"{'paire':<16}", end="")
for fee in FEE_SWEEP:
    print(f"{'frais ' + format(fee, '.0f') + ' bps':>16}", end="")
print()
print("-" * 80)
any_pos = []
for alt in ALTS:
    y, x = R[alt], R[REF]
    print(f"{alt:<16}", end="")
    for fee in FEE_SWEEP:
        cost = 4 * fee + 2 * (hs[alt] + hs[REF])
        vals = []
        for n in range(W_BARS + K_BARS, N - max(H_BARS)):
            b = beta(y, x, n - W_BARS - K_BARS, n - K_BARS)
            if b is None:
                continue
            d = sum(y[n - K_BARS:n]) - b * sum(x[n - K_BARS:n])
            if abs(d) < cost:
                continue
            sgn = -1.0 if d > 0 else 1.0
            fwd = sum(y[n:n + 30]) - b * sum(x[n:n + 30])
            vals.append(sgn * fwd - cost)
        if len(vals) < 30:
            print(f"{'n<30':>16}", end="")
            continue
        m = st.fmean(vals)
        t, _ = t_test_one_sided(vals)
        print(f"{format(m, '+.1f') + ' (t=' + format(t, '.1f') + ')':>16}", end="")
        if m > 0:
            any_pos.append((alt, fee, m, t, len(vals)))
    print()

print(f"\ncellules a moyenne positive : {len(any_pos)}")
for alt, fee, m, t, n in sorted(any_pos, key=lambda r: -r[2]):
    print(f"  {alt:<16} frais {fee:.0f} bps  net {m:+.2f} bps  t={t:.2f}  n={n}")
if not any_pos:
    print("  AUCUNE, y compris a frais nuls. Ce n'est pas le bareme qui bloque.")
