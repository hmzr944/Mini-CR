"""COUCHE A : l'etat TRANSVERSAL separe-t-il les resultats futurs ?

Avant de construire une politique, le mandat impose de savoir quelle couche
echoue. Celle-ci est la premiere : l'information existe-t-elle ?

CE QUI EST NOUVEAU ICI, ET CE QUI NE L'EST PAS. Le projet a deja mesure une
« dislocation transversale » et l'a fermee a -0,04 bps. Ce n'est pas la meme
question. Celle-la comparait des PRIX entre instruments a un instant donne.
Celle-ci demande si le marche ENTIER, deja bouge, annonce le mouvement d'un
instrument qui ne l'a pas encore fait — un avance-retard, pas un ecart de
niveau. La politique construite jusqu'a present ne pouvait pas le voir : elle
ne regardait qu'un instrument a la fois.

MESURE. Sur l'APPRENTISSAGE seul, pour chaque variable transversale et chaque
horizon, on partage les instants en terciles et on mesure le rendement FUTUR
moyen du milieu, en points de base. L'ECART entre le tercile haut et le
tercile bas est la plage directionnelle exploitable : c'est elle, et non la
significativite, qu'il faut comparer au cout.

YARDSTICK. Un aller-retour coute 10 bps en traversee (frais seuls, le spread
est en plus), 7 bps en entree passive chez OKX, 5 bps avec un frais maker nul.
Une plage inferieure a ces nombres ne peut pas etre monetisee, quelle que
soit la politique qu'on batirait dessus.

ANTI-FUITE. Panneau reindexe sur une grille de temps commune et causale
(crosssec.align) : a un indice donne, tous les instruments designent le meme
instant, et la valeur retenue est la derniere PUBLIEE avant cet instant.
Agregats transversaux a EXCLUSION DE SOI.
"""
import json, os, pickle, statistics as st

from prism_v2.policy.crosssec import CROSS_FEATURES, CrossSection, Grid
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import fit_binner

from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
TRAIN_FRACTION = 0.60
CFG = StateConfig(short_rows=20, long_rows=200)
#: Horizons en pas de grille (300 ms). Un avance-retard vit a la seconde :
#: la grille descend donc bien plus bas que les 300 s precedents.
HORIZONS = {"0,9 s": 3, "3 s": 10, "10 s": 33, "30 s": 100, "60 s": 200,
            "300 s": 1000}
MIN_CELL = 200
FRAIS = {"traversee": 10.0, "entree passive OKX": 7.0, "maker nul": 5.0}

cache = pickle.load(open(f"{SCRATCH}/panel_grid.pkl", "rb"))
times, panel = cache["grid_times"], cache["panel"]
grid = Grid(times, {i: tuple(range(len(times))) for i in panel})
xs = CrossSection(panel, grid)

n = len(times)
cut = int(n * TRAIN_FRACTION)
insts = sorted(panel)
print(f"{len(insts)} instruments, grille de {n:,} points a 300 ms")
print(f"apprentissage [0, {cut:,}) = {cut*0.3/3600:.2f} h  "
      f"(le test n'est pas lu ici)\n")

flow_ref = {i: (flow_median(panel[i], CFG, 0, cut) or 1.0) for i in insts}
builders = {i: StateBuilder(CFG, flow_ref[i]) for i in insts}

#: Rendement futur du milieu, par instrument et par horizon. C'est la
#: quantite a expliquer : ce que le prix fait APRES l'instant de decision.
mids = {i: [((r[1] + r[2]) / 2.0 if r else None) for r in panel[i]]
        for i in insts}


def futur(inst, k, h):
    a, b = mids[inst][k], mids[inst][k + h] if k + h < n else None
    if a is None or b is None or a <= 0:
        return None
    return (b - a) / a * 10_000.0


print(f"{'horizon':>8}{'variable':>16}{'n':>9}{'bas':>9}{'milieu':>9}"
      f"{'haut':>9}{'PLAGE':>9}   verdict")
print("-" * 92)

resume = {}
for nom, h in HORIZONS.items():
    # Un point de decision tous les h pas : observations disjointes.
    pts = []
    pas = max(h, 1)
    for k in range(CFG.warmup, cut - h, pas):
        for inst in insts:
            e = builders[inst].at(panel[inst], k)
            if e is None:
                continue
            c = xs.at(inst, k, CFG.short_rows, CFG.long_rows)
            if c is None:
                continue
            f = futur(inst, k, h)
            if f is None:
                continue
            e.update(c)
            pts.append((e, f))
    if len(pts) < MIN_CELL * 3:
        continue
    meilleur = (0.0, "", 0)
    for f in CROSS_FEATURES + FEATURES:
        bn = fit_binner(f, [e[f] for e, _ in pts])
        if bn is None:
            continue
        seaux = {}
        for e, y in pts:
            seaux.setdefault(bn.bin_of(e[f]), []).append(y)
        if len(seaux) < 3 or min(len(v) for v in seaux.values()) < MIN_CELL:
            continue
        m = {k2: st.fmean(v) for k2, v in seaux.items()}
        plage = max(m.values()) - min(m.values())
        if f in CROSS_FEATURES and plage > meilleur[0]:
            meilleur = (plage, f, len(pts))
        if f == "residu_court" or (f in CROSS_FEATURES and plage == meilleur[0]):
            v = "MONETISABLE" if plage > min(FRAIS.values()) else "sous le cout"
            print(f"{nom:>8}{f:>16}{len(pts):>9,}{m.get(0,0):>9.2f}"
                  f"{m.get(1,0):>9.2f}{m.get(2,0):>9.2f}{plage:>9.2f}   {v}")
    resume[nom] = {"plage": meilleur[0], "variable": meilleur[1],
                   "n": meilleur[2]}

print(f"\nMEILLEURE PLAGE TRANSVERSALE PAR HORIZON")
print(f"{'horizon':>8}{'variable':>16}{'plage bps':>11}{'n':>9}"
      f"{'vs maker nul (5 bps)':>24}")
print("-" * 68)
for nom, v in resume.items():
    if not v["variable"]:
        continue
    print(f"{nom:>8}{v['variable']:>16}{v['plage']:>11.2f}{v['n']:>9,}"
          f"{v['plage'] - FRAIS['maker nul']:>+24.2f}")

vivant = [k for k, v in resume.items() if v["plage"] > FRAIS["maker nul"]]
print("\nLECTURE (couche A)")
if vivant:
    print(f"  La plage depasse le cout plancher a : {', '.join(vivant)}.")
    print("  L'information transversale existe et elle est assez grande pour")
    print("  etre monetisable EN PRINCIPE. Passer a la couche B : une")
    print("  politique peut-elle la capter hors echantillon ?")
else:
    print("  A AUCUN horizon la plage transversale n'atteint le cout plancher")
    print("  de 5 bps. L'information transversale ne separe pas assez les")
    print("  resultats futurs : la couche A est morte, et construire une")
    print("  politique par-dessus serait du travail sur un goulot inexistant.")

json.dump(resume, open(f"{SCRATCH}/xsec_lead.json", "w"), indent=1)
