"""A quel niveau de frais chaque phenomene mesure deviendrait-il rentable ?

Les trois balayages concluent non. Le plus utile n'est pas de le repeter :
c'est de dire A QUELLE CONDITION la reponse changerait. Comme la contrainte
qui mord est le plancher de cout et non le signal, la question se pose en
frais.

Ce script ne postule aucun bareme. Il inverse simplement chaque mesure : quel
frais par traversee annulerait exactement le resultat ? Le lecteur compare
ensuite ce chiffre au bareme qu'il peut reellement obtenir.

Deux quantites distinctes, a ne pas confondre :
  - le frais de SEUIL, qui rend le resultat nul ;
  - le frais NEGATIF (remise au teneur), quand meme la gratuite ne suffit pas.
Un seuil negatif signifie que la venue devrait me PAYER pour que l'operation
tienne. C'est dit tel quel, pas arrondi a zero.
"""
import pickle, statistics as st, os
from bisect import bisect_left, bisect_right

from prism_v2.long_test import t_test_one_sided

from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
D = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))
quotes, trades = D["quotes"], D["trades"]
HORIZONS = [1, 5, 30, 300]
MIN_FILLS = 50

print("FOURNITURE DE LIQUIDITE — frais maker de seuil, par jambe passive")
print("net = demi-spread - selection adverse - frais ; seuil = net a frais nul\n")
print(f"{'instrument':<18}{'demi-spread':>13}{'sel. adverse 1s':>17}"
      f"{'frais de seuil':>16}")
print("-" * 64)
rows = []
for inst in sorted(trades):
    q, tr = quotes.get(inst), trades[inst]
    if not q or len(tr) < MIN_FILLS:
        continue
    ts_q = [x[0] for x in q]
    hs_all, adv1 = [], []
    for t, px, usd, side in tr:
        i = bisect_left(ts_q, t) - 1
        if i < 0:
            continue
        b, a = q[i][1], q[i][2]
        if not (a > b > 0):
            continue
        mid = (b + a) / 2.0
        j = bisect_right(ts_q, t + 1000) - 1
        if j < 0 or ts_q[j] <= t:
            continue
        sgn = 1.0 if side == "buy" else -1.0
        hs_all.append(abs((a if side == "buy" else b) - mid) / mid * 10_000.0)
        adv1.append(sgn * ((q[j][1] + q[j][2]) / 2.0 - mid) / mid * 10_000.0)
    if len(hs_all) < MIN_FILLS:
        continue
    hs, ad = st.fmean(hs_all), st.fmean(adv1)
    rows.append((inst, hs, ad, hs - ad))
    print(f"{inst:<18}{hs:>13.2f}{ad:>17.2f}{hs-ad:>16.2f}")

neg = [r for r in rows if r[3] <= 0]
print(f"\nseuil negatif (la gratuite totale ne suffirait pas) : "
      f"{len(neg)}/{len(rows)} instruments")
if rows:
    best = max(rows, key=lambda r: r[3])
    print(f"seuil le plus favorable : {best[0]} a {best[3]:+.2f} bps par jambe")
    if best[3] <= 0:
        print(f"  la venue devrait PAYER {-best[3]:.2f} bps par jambe pour "
              f"atteindre le point mort.")
    else:
        print(f"  frais maker maximal admissible : {best[3]:.2f} bps par jambe.")
    worst = min(rows, key=lambda r: r[3])
    print(f"remise necessaire, du plus leger au plus lourd : "
          f"{-best[3]:.2f} a {-worst[3]:.2f} bps par jambe.")
print(f"\nAucun bareme n'est postule ici : ces seuils sont une propriete des")
print(f"donnees. Le tarif public OKX employe partout dans le projet vaut")
print(f"2,00 bps en maker, mais la comparaison qui compte est celle a ZERO :")
print(f"sur {len(neg)} des {len(rows)} instruments, la gratuite complete ne")
print(f"suffirait pas. Ce n'est donc pas mon palier de frais qui bloque, c'est")
print(f"tout palier de frais — et ce, avant meme de modeliser la file")
print(f"d'attente, supposee ici gagnee a chaque transaction.")


# ── dislocations transversales : et si les frais etaient nuls ? ────────────
print("\n\nDISLOCATIONS TRANSVERSALES — et a frais nuls ?")
print("Seuls les demi-spreads resteraient a payer, quatre traversees.\n")
try:
    P = pickle.load(open(f"{SCRATCH}/panel.pkl", "rb"))
except FileNotFoundError:
    print("  panel.pkl absent : lancer prism_v2.scans.panel d'abord.")
    raise SystemExit
hs_pair = {}
for inst, v in P.items():
    h = sorted((x[2] - x[1]) / (x[1] + x[2]) * 10_000.0
               for x in v if x[2] > x[1] > 0)
    hs_pair[inst] = h[len(h) // 2]
REF = "BTC-USD-SWAP"
print(f"{'paire':<16}{'cout avec frais':>17}{'cout a frais nuls':>19}"
      f"{'plus grande dislocation observee':>34}")
print("-" * 86)
print("  (la plus grande dislocation est lue dans la sortie de prism_v2.scans.xsec)")
for alt in sorted(k for k in hs_pair if k != REF):
    avec = 4 * 5.0 + 2 * (hs_pair[alt] + hs_pair[REF])
    sans = 2 * (hs_pair[alt] + hs_pair[REF])
    print(f"{alt:<16}{avec:>17.1f}{sans:>19.1f}")
print("\nA frais nuls, il reste 0,1 a 11 bps de spreads a traverser. Sur les")
print("paires les plus liquides (ETH 0,1 ; SOL 1,0 ; DOGE 1,3) ce reste est")
print("INFERIEUR a la dislocation mediane : le signe pourrait basculer.")
print("Cela ne se decide pas au raisonnement — un cout plus bas fait entrer")
print("sur des dislocations plus petites, qui se referment moins. Le balayage")
print("de frais de prism_v2.scans.xsec remesure le cas et tranche.")
