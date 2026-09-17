"""L'unique survivant est-il un mecanisme, ou une derive du milieu ?

BCH-USDT-SWAP a 300 s ressort a +1,08 bps par remplissage (t=3,07), seul
survivant sur 52 tests. Mais le MEME instrument perd a 1 s (-2,37), 5 s
(-1,96) et 30 s (-1,58). Le signe ne bascule qu'a cinq minutes.

Un teneur de marche est paye — ou puni — a l'horizon ou le remplissage porte
de l'information, pas exclusivement cinq minutes plus tard. Un profit qui
n'apparait qu'a 300 s ressemble a une derive du milieu, c'est-a-dire a une
exposition directionnelle, pas a une remuneration de la liquidite.

Trois placebos, chacun coupant un lien different :
  1. INSTANTS FACTICES : memes sens d'agresseur, instants tires au hasard
     dans la meme fenetre. Si le resultat survit, il ne doit rien aux
     remplissages.
  2. SENS MELANGES : memes instants, sens d'agresseur permutes. Si le
     resultat survit, il ne doit rien a l'information portee par le sens.
  3. SENS INVERSE : memes instants, sens retourne. Un vrai mecanisme doit
     changer de signe.
"""
import pickle, random, statistics as st, sys
from bisect import bisect_left, bisect_right

from prism_v2.long_test import t_test_one_sided

import os as _os
SCRATCH = _os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
D = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))
INST, H, MAKER = "BCH-USDT-SWAP", 300, 2.0
q, tr = D["quotes"][INST], D["trades"][INST]
ts_q = [x[0] for x in q]
random.seed(20260917)


def evaluate(events):
    """events = [(ts, side)] ; renvoie la serie de net bps par remplissage."""
    out = []
    for t, side in events:
        i = bisect_left(ts_q, t) - 1
        if i < 0:
            continue
        b, a = q[i][1], q[i][2]
        if not (a > b > 0):
            continue
        mid = (b + a) / 2.0
        sgn = 1.0 if side == "buy" else -1.0
        hs = abs((a if side == "buy" else b) - mid) / mid * 10_000.0
        j = bisect_right(ts_q, t + H * 1000) - 1
        if j < 0 or ts_q[j] <= t:
            continue
        m2 = (q[j][1] + q[j][2]) / 2.0
        out.append(hs - sgn * (m2 - mid) / mid * 10_000.0 - MAKER)
    return out


real = [(t, s) for t, px, usd, s in tr]
sides = [s for _, s in real]
times = [t for t, _ in real]
lo, hi = ts_q[0], ts_q[-1] - H * 1000

cases = {"reel": real}
fake_t = sorted(random.randint(lo, hi) for _ in range(len(real)))
cases["1. instants factices"] = list(zip(fake_t, sides))
shuf = sides[:]
random.shuffle(shuf)
cases["2. sens melanges"] = list(zip(times, shuf))
cases["3. sens inverse"] = [(t, "sell" if s == "buy" else "buy") for t, s in real]

print(f"{INST}, horizon {H}s, frais maker {MAKER} bps\n")
print(f"{'cas':<24}{'n':>7}{'net bps/fill':>14}{'t':>8}{'p':>9}")
print("-" * 62)
res = {}
for name, ev in cases.items():
    v = evaluate(ev)
    t, p = t_test_one_sided(v)
    res[name] = st.fmean(v)
    print(f"{name:<24}{len(v):>7}{st.fmean(v):>14.2f}{t:>8.2f}{p:>9.4f}")

print()
r = res["reel"]
f = res["1. instants factices"]
m = res["2. sens melanges"]
inv = res["3. sens inverse"]
print(f"Le placebo a instants factices retient "
      f"{f/r:.0%} du resultat reel." if abs(r) > 1e-9 else "")
print(f"Le placebo a sens melanges retient "
      f"{m/r:.0%} du resultat reel." if abs(r) > 1e-9 else "")
print(f"Le sens inverse donne {inv:+.2f} bps "
      f"(un mecanisme devrait le rendre nettement negatif).")
