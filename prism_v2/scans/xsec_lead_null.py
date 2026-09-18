"""La plage transversale de 300 s est-elle autre chose que du bruit ?

xsec_lead.py a produit une plage de 22,00 bps a l'horizon 300 s sur la
variable mkt_ofi, au-dessus du cout plancher de 5 bps. Trois choses
interdisent de la croire telle quelle :

  1. C'EST LE PLUS PETIT ECHANTILLON. La plage croit MONOTONIQUEMENT quand n
     decroit : 0,21 bps a n=232 935, puis 0,48, 0,49, 1,59, 2,11, et 22,00 a
     n=690. Une vraie structure ne se renforce pas parce qu'on la regarde
     moins souvent.
  2. LES 690 LIGNES NE SONT PAS 690 OBSERVATIONS. Ce sont 46 instants x 15
     instruments, et quinze cryptos au meme instant bougent ensemble. Le
     nombre d'observations independantes est de l'ordre de 46.
  3. LES SEAUX NE SONT PAS MONOTONES : 5,79 / 13,93 / -8,08. Un
     avance-retard sur un flux SIGNE devrait ordonner les seaux ; celui-ci
     ne les ordonne pas.

CE CRIBLE. Test de PERMUTATION en blocs. On garde les instants groupes —
tous les instruments d'un meme instant restent ensemble, ce qui preserve leur
correlation — et on redistribue au hasard les etats en face des resultats
futurs. Sous l'hypothese nulle « l'etat ne dit rien du futur », la plage
observee doit ressembler a celles des permutations.

Le p ainsi obtenu est la fraction des permutations dont la plage egale ou
depasse l'observee. Correction de Benjamini-Hochberg sur l'ensemble des
couples (horizon, variable) reellement testes.
"""
import json, os, pickle, random, statistics as st

from prism_v2.policy.crosssec import CROSS_FEATURES, CrossSection, Grid
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import fit_binner

SCRATCH = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
TRAIN_FRACTION = 0.60
CFG = StateConfig(short_rows=20, long_rows=200)
#: Seuls les horizons dont la PLAGE OBSERVEE depasse le cout plancher sont
#: testes. Les plages de 0,21 a 0,61 bps (0,9 s a 10 s) ne peuvent rien
#: financer : savoir si elles sont « significatives » ne changerait aucune
#: decision, et depenser du budget de recherche sur une question sans
#: consequence economique est interdit. 60 s est garde comme temoin de la
#: zone situee sous le cout.
HORIZONS = {"60 s": 200, "300 s": 1000}
MIN_CELL = 40
N_PERM = 2_000
RNG = random.Random(20260918)

cache = pickle.load(open(f"{SCRATCH}/panel_grid.pkl", "rb"))
times, panel = cache["grid_times"], cache["panel"]
grid = Grid(times, {i: tuple(range(len(times))) for i in panel})
xs = CrossSection(panel, grid)
n = len(times)
cut = int(n * TRAIN_FRACTION)
insts = sorted(panel)
flow_ref = {i: (flow_median(panel[i], CFG, 0, cut) or 1.0) for i in insts}
builders = {i: StateBuilder(CFG, flow_ref[i]) for i in insts}
mids = {i: [((r[1] + r[2]) / 2.0 if r else None) for r in panel[i]]
        for i in insts}

TOUTES = CROSS_FEATURES + FEATURES
print(f"permutations par test : {N_PERM:,}   blocs = instants entiers")
print(f"apprentissage seul, {cut*0.3/3600:.2f} h\n")
print(f"{'horizon':>8}{'variable':>16}{'instants':>10}{'lignes':>9}"
      f"{'plage':>9}{'plage nulle p95':>17}{'p':>8}")
print("-" * 77)


def plage(seaux):
    m = [st.fmean(v) for v in seaux.values() if len(v) >= MIN_CELL]
    return (max(m) - min(m)) if len(m) >= 2 else 0.0


tests = []
for nom, h in HORIZONS.items():
    # Un BLOC = un instant, avec tous ses instruments. C'est l'unite
    # d'independance : ce qui est correle a l'interieur reste ensemble.
    blocs = []
    for k in range(CFG.warmup, cut - h, h):
        ligne = []
        for inst in insts:
            e = builders[inst].at(panel[inst], k)
            if e is None:
                continue
            c = xs.at(inst, k, CFG.short_rows, CFG.long_rows)
            if c is None:
                continue
            a, b = mids[inst][k], mids[inst][k + h]
            if a is None or b is None or a <= 0:
                continue
            e.update(c)
            ligne.append((e, (b - a) / a * 10_000.0))
        if ligne:
            blocs.append(ligne)
    if len(blocs) < 20:
        continue
    n_lignes = sum(len(b) for b in blocs)

    for f in TOUTES:
        vals = [e[f] for b in blocs for e, _ in b]
        bn = fit_binner(f, vals)
        if bn is None:
            continue
        seaux = {}
        for b in blocs:
            for e, y in b:
                seaux.setdefault(bn.bin_of(e[f]), []).append(y)
        if len([v for v in seaux.values() if len(v) >= MIN_CELL]) < 2:
            continue
        obs = plage(seaux)

        # NULLE : on garde les blocs d'ETATS intacts et on leur reattribue
        # les blocs de RESULTATS d'autres instants. La structure interne de
        # chaque instant est preservee ; seul le lien etat -> futur est brise.
        nulles = []
        ordre = list(range(len(blocs)))
        for _ in range(N_PERM):
            RNG.shuffle(ordre)
            s2 = {}
            for bi, bj in enumerate(ordre):
                etats, res = blocs[bi], blocs[bj]
                for t in range(min(len(etats), len(res))):
                    s2.setdefault(bn.bin_of(etats[t][0][f]), []).append(res[t][1])
            nulles.append(plage(s2))
        nulles.sort()
        p95 = nulles[int(0.95 * len(nulles))]
        p = sum(1 for x in nulles if x >= obs) / len(nulles)
        tests.append((nom, f, len(blocs), n_lignes, obs, p95, p))
        if f in CROSS_FEATURES:
            print(f"{nom:>8}{f:>16}{len(blocs):>10,}{n_lignes:>9,}"
                  f"{obs:>9.2f}{p95:>17.2f}{p:>8.4f}")

ps = sorted((t[6], i) for i, t in enumerate(tests))
m_tests = len(ps)
k_max = 0
for rang, (p, _) in enumerate(ps, 1):
    if p <= 0.05 * rang / m_tests:
        k_max = rang
retenus = {i for _, i in ps[:k_max]}

print(f"\nBenjamini-Hochberg sur {m_tests} tests (toutes variables), FDR 5 %")
if not retenus:
    print("  AUCUN test ne survit.")
    print("  La plage de 22,00 bps a 300 s est du bruit d'echantillonnage :")
    print("  sur 46 instants seulement, une permutation aleatoire produit")
    print("  couramment une plage aussi grande. La couche A transversale est")
    print("  morte, et il n'y a pas de politique a construire par-dessus.")
else:
    print(f"  {len(retenus)} test(s) survivent :")
    for i in sorted(retenus, key=lambda i: -tests[i][4]):
        nom, f, nb, nl, obs, p95, p = tests[i]
        print(f"    {nom:>7}  {f:<16} plage {obs:>6.2f} bps  "
              f"{nb} instants  p={p:.4f}")

json.dump([{"horizon": t[0], "variable": t[1], "instants": t[2],
            "lignes": t[3], "plage": t[4], "p95_nulle": t[5], "p": t[6]}
           for t in tests], open(f"{SCRATCH}/xsec_lead_null.json", "w"), indent=1)
