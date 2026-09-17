"""Ou le demi-spread depasse-t-il la selection adverse ? Balayage, pas famille.

Le fait etabli par tout le projet : chaque phenomene directionnel mesure vaut
2 a 3 bps, le cout d'aller-retour en taker en vaut 11. Cette asymetrie ferme
la porte a la prise de liquidite. Reste la fourniture de liquidite, ou le
signe est inverse : on ENCAISSE le spread au lieu de le payer.

Le projet avait mesure -3,34 bps en maker, globalement : demi-spread +1,40,
selection adverse -2,74. Mais « globalement » melange BTC, dont le spread
vaut 0,3 bp, et des paires fines dont le spread en vaut 20. La question que
les donnees peuvent trancher, instrument par instrument :

    demi-spread encaisse  -  selection adverse  -  frais maker  >  0 ?

BORNE SUPERIEURE ASSUMEE. On suppose la file d'attente gagnee a chaque
transaction : tout agresseur nous remplit. C'est faux dans la realite et
c'est voulu — si meme cette borne est negative, la famille est morte sans
qu'il soit besoin de modeliser la file.

ANTI-LOOK-AHEAD. Le carnet utilise a l'instant d'une transaction est le
dernier etat STRICTEMENT ANTERIEUR. Le carnet qui suit la transaction ne
peut donc pas influencer le prix de remplissage.
"""
import pickle, statistics as st, math, sys

import os as _os
#: Repertoire de travail des mesures. Les fichiers intermediaires (panneau,
#: bande) n'ont pas leur place dans le depot : ils se recalculent.
SCRATCH = _os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
_os.makedirs(SCRATCH, exist_ok=True)

from bisect import bisect_left, bisect_right


D = pickle.load(open(f"{SCRATCH}/tape.pkl", "rb"))
quotes, trades = D["quotes"], D["trades"]

MAKER_BPS = 2.0                      # OKX public, par jambe passive
HORIZONS = [1, 5, 30, 300]           # secondes
MIN_FILLS = 50


def tstat(x):
    if len(x) < 2:
        return float("nan")
    sd = st.stdev(x)
    return st.fmean(x) / (sd / math.sqrt(len(x))) if sd > 0 else float("nan")


print(f"frais maker : {MAKER_BPS} bps par jambe passive")
print(f"hypothese : file d'attente gagnee a chaque transaction (BORNE SUPERIEURE)\n")
print(f"{'instrument':<18}{'fills':>8}{'demi-spr':>10}", end="")
for h in HORIZONS:
    print(f"{'adv'+str(h)+'s':>9}", end="")
print(f"{'net@best':>10}{'t':>8}{'bps/jour*':>11}")
print("-" * 100)

rows = []
for inst in sorted(trades):
    q, tr = quotes.get(inst), trades[inst]
    if not q or len(tr) < MIN_FILLS:
        continue
    ts_q = [x[0] for x in q]
    hs_list = []
    adv = {h: [] for h in HORIZONS}
    net = {h: [] for h in HORIZONS}
    for t, px, usd, side in tr:
        i = bisect_left(ts_q, t) - 1        # dernier carnet STRICTEMENT anterieur
        if i < 0:
            continue
        b, a = q[i][1], q[i][2]
        mid = (b + a) / 2.0
        if not (a > b > 0):
            continue
        # l'agresseur achete -> il frappe l'ask -> nous vendons a l'ask
        # l'agresseur vend   -> il frappe le bid -> nous achetons au bid
        # L'agresseur achete -> nous sommes VENDEURS -> une hausse du milieu
        # nous coute. Le cout de selection adverse s'ecrit donc
        #     cout = +(mid_futur - mid)  quand l'agresseur achete
        #            -(mid_futur - mid)  quand l'agresseur vend
        # Une premiere version avait ce signe inverse : la selection adverse
        # ressortait NEGATIVE, c'est-a-dire le marche s'ecartant en notre
        # faveur apres chaque remplissage passif. Impossible, et cela donnait
        # +400 bps/jour. Le signe est fixe ici par la direction economique,
        # pas par le resultat qu'il produit.
        sgn = 1.0 if side == "buy" else -1.0
        fill = a if side == "buy" else b
        hs = abs(fill - mid) / mid * 10_000.0
        ok = {}
        for h in HORIZONS:
            j = bisect_right(ts_q, t + h * 1000) - 1
            if j < 0 or ts_q[j] <= t:
                ok = None
                break
            m2 = (q[j][1] + q[j][2]) / 2.0
            ok[h] = sgn * (m2 - mid) / mid * 10_000.0   # >0 = le marche va CONTRE nous
        if ok is None:
            continue
        hs_list.append(hs)
        for h in HORIZONS:
            adv[h].append(ok[h])
            net[h].append(hs - ok[h] - MAKER_BPS)
    if len(hs_list) < MIN_FILLS:
        continue
    best_h = max(HORIZONS, key=lambda h: st.fmean(net[h]))
    bn = st.fmean(net[best_h])
    span_d = (q[-1][0] - q[0][0]) / 86_400_000.0
    fills_per_day = len(hs_list) / span_d if span_d > 0 else 0.0
    rows.append((inst, len(hs_list), st.fmean(hs_list), bn, best_h,
                 tstat(net[best_h]), fills_per_day))
    print(f"{inst:<18}{len(hs_list):>8}{st.fmean(hs_list):>10.2f}", end="")
    for h in HORIZONS:
        print(f"{st.fmean(adv[h]):>9.2f}", end="")
    print(f"{bn:>10.2f}{tstat(net[best_h]):>8.2f}{bn*fills_per_day/10:>11.1f}")

print("\n* bps/jour pour un stock d'inventaire egal a 10 fois la taille d'un fill")
print("  (fills/jour x net par fill / 10). Indicatif : la file n'est pas modelisee.")

pos = [r for r in rows if r[3] > 0]
print(f"\ninstruments a net positif : {len(pos)}/{len(rows)}")
for r in sorted(pos, key=lambda r: -r[3]):
    print(f"  {r[0]:<18} net {r[3]:+.2f} bps/fill  horizon {r[4]}s  t={r[5]:.1f}  "
          f"{r[6]:,.0f} fills/jour")
if not pos:
    print("  AUCUN. La selection adverse mange le demi-spread partout,")
    print("  meme en supposant la file gagnee a chaque transaction.")
