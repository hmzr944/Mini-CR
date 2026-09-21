"""OU EXACTEMENT LA POLITIQUE ECHOUE : signal, execution, ou capital ?

policy_fit.py n'a retenu AUCUNE action, meme sur l'apprentissage. Ce
resultat ne dit pas encore POURQUOI, et le mandat exige de nommer le goulot
avant de travailler dessus. Trois causes possibles, mutuellement exclusives :

  SIGNAL      aucun etat ne predit un mouvement, quel que soit son cout ;
  EXECUTION   des etats predisent un mouvement, mais plus petit que le cout ;
  CAPITAL     le mouvement net est positif mais trop rare ou trop petit pour
              rentabiliser le capital immobilise.

Ce crible les separe en mesurant, cellule par cellule et SUR
L'APPRENTISSAGE, le mouvement BRUT conditionnel — c'est-a-dire ce que la
meilleure prediction possible rapporterait a cout nul — et en le comparant
au cout reel d'un aller-retour.

Le mouvement brut conditionnel est une quantite optimiste : la cellule est
choisie apres l'avoir vue. C'est voulu. Si meme cet optimisme ne depasse pas
le cout, le goulot n'est pas l'execution.
"""
import json, os, pickle, statistics as st

from prism_v2.fees import OKX_PUBLIC_MAKER_BPS, OKX_PUBLIC_TAKER_BPS
from prism_v2.instruments import parse_okx_instrument
from prism_v2.margin import load_schedules
from prism_v2.policy.action import Action, FeeModel
from prism_v2.policy.engine import Market, build_samples, split_index
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import N_BINS, fit_binner

from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
TRAIN_FRACTION = 0.60
CFG = StateConfig(short_rows=20, long_rows=200)
HORIZONS_ROWS = {"10 s": 33, "30 s": 100, "60 s": 200, "300 s": 1000}
MIN_CELL = 30

panel = pickle.load(open(f"{SCRATCH}/panel.pkl", "rb"))
raw = json.load(open(f"{SCRATCH}/insts.json"))
sch = load_schedules()
fees = FeeModel(OKX_PUBLIC_MAKER_BPS, OKX_PUBLIC_TAKER_BPS)

marches = []
for inst in sorted(panel):
    spec = parse_okx_instrument(raw.get(inst) or {})
    s = sch.get(inst) or sch.get(inst.rsplit("-", 1)[0])
    if spec and s:
        marches.append(Market(inst, spec, s, fees, panel[inst]))

n_rows = min(len(m.rows) for m in marches)
cut = split_index(n_rows, TRAIN_FRACTION)

depth_ref, flow_ref = {}, {}
for m in marches:
    d = [min(r[3], r[4]) for r in m.rows[:cut] if r[3] > 0 and r[4] > 0]
    depth_ref[m.inst_id] = st.median(d) if d else 0.0
    flow_ref[m.inst_id] = flow_median(m.rows, CFG, 0, cut)

print("APPRENTISSAGE SEUL, cellules a UNE variable, choisies APRES coup.")
print("ATTENTION AU DOUBLE COMPTE. Dans ce moteur, gross_bps est deja le")
print("mouvement PAYE : il part du prix d'entree reellement traverse et")
print("arrive au prix de sortie reellement traverse. Le spread est donc")
print("DEDANS. Le seul cout qui reste a soustraire est le frais. Une")
print("premiere version de ce crible retranchait le spread une seconde")
print("fois et sous-estimait le net de 4,9 bps.\n")
print(f"{'horizon':>9}{'decisions':>11}{'|brut| moy':>12}{'frais A-R':>11}"
      f"{'meilleure cellule':>22}{'action':>13}{'n':>7}{'net':>9}")
print("-" * 94)

resume = {}
for nom, h in HORIZONS_ROWS.items():
    ech = []
    for m in marches:
        if flow_ref[m.inst_id] <= 0 or depth_ref[m.inst_id] <= 0:
            continue
        b = StateBuilder(CFG, flow_ref[m.inst_id])
        for s in build_samples(m, b, h, 0, cut, max_size_usd=50_000.0):
            s.state["depth_ct"] /= depth_ref[m.inst_id]
            ech.append(s)
    if len(ech) < 300:
        continue
    frais = 2 * fees.taker_bps
    brut_abs = st.fmean([abs(s.fills[Action.LONG_TAKER].net_bps + frais)
                         for s in ech])

    best = (-1e9, "", None, 0)
    for f in FEATURES:
        bn = fit_binner(f, [s.state[f] for s in ech])
        if bn is None:
            continue
        seaux = {}
        for s in ech:
            seaux.setdefault(bn.bin_of(s.state[f]), []).append(s)
        for k, v in seaux.items():
            if len(v) < MIN_CELL:
                continue
            for a in (Action.LONG_TAKER, Action.SHORT_TAKER):
                mu = st.fmean([x.nets[a] for x in v])
                if mu > best[0]:
                    best = (mu, f"{f}[{k}]", a, len(v))
    print(f"{nom:>9}{len(ech):>11,}{brut_abs:>12.2f}{frais:>11.2f}"
          f"{best[1]:>22}{best[2].value:>13}{best[3]:>7,}{best[0]:>9.2f}")
    resume[nom] = {"brut_abs": brut_abs, "frais": frais,
                   "cellule": best[1], "action": best[2].value,
                   "n": best[3], "net": best[0]}

print("\nLECTURE")
pos = {k: v for k, v in resume.items() if v["net"] > 0}
if not pos:
    print("  Aucune cellule a une variable ne produit un net positif, meme")
    print("  choisie apres coup. Le goulot n'est pas l'execution : c'est")
    print("  l'absence d'un mouvement conditionnel assez grand.")
else:
    print(f"  {len(pos)} horizon(s) portent une cellule a net positif :")
    for k, v in pos.items():
        print(f"    {k:>7}  {v['cellule']:<18} {v['action']:<12} "
              f"net {v['net']:+.2f} bps  n={v['n']:,}")
    print("\n  ATTENTION : cellule choisie APRES coup parmi "
          f"{len(FEATURES)*N_BINS*2*len(resume)} essais.")
    print("  Ce n'est pas un resultat, c'est une piste a valider hors")
    print("  echantillon. policy_fit.py s'en charge, et lui seul decide.")

json.dump(resume, open(f"{SCRATCH}/policy_diag.json", "w"), indent=1)
