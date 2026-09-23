"""SIGNAL OU EXECUTION ? La reponse par balayage du frais, jusqu'a zero.

policy_fit.py a rendu deux verdicts hors echantillon :
  TAKER SEUL          aucune action, jamais. La politique reste a NO_TRADE.
  ENTREE PASSIVE      +2,85 bps en apprentissage, -3,47 bps en test. La
                      politique ne generalise pas.

Le mandat exige de nommer le goulot avant de travailler dessus, et de ne
pas conclure « pas d'opportunite » sans avoir pousse le levier disponible a
sa borne. Le levier disponible est le FRAIS : le bareme public MEXC porte
makerCommission = 0, verifie. Zero est le plancher arithmetique ; aucune
venue ne descend plus bas.

Ce crible refait donc l'ajustement COMPLET — selection des variables,
choix de l'horizon, valeurs des cellules, tout sur l'apprentissage seul —
pour chaque niveau de frais maker, et lit le test une fois par niveau.

CE QUE LE RESULTAT TRANCHE.
  Si le test devient positif quand le frais tombe -> le goulot etait
  l'EXECUTION, et le chemin passe par une venue a frais maker nul.
  Si le test reste negatif a frais nul -> le goulot n'est pas le cout : le
  signal conditionnel lui-meme est trop faible, et aucune venue n'y changera
  quoi que ce soit.
"""
import json, os, pickle, statistics as st

from prism_v2.fees import OKX_PUBLIC_MAKER_BPS, OKX_PUBLIC_TAKER_BPS
from prism_v2.instruments import parse_okx_instrument
from prism_v2.margin import load_schedules
from prism_v2.policy.action import ALL_ACTIONS, UPPER_BOUND, Action, FeeModel
from prism_v2.policy.engine import Market, build_samples, run_policy, split_index
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import select_features

from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
TRAIN_FRACTION = 0.60
CAPITAL_RESERVE_USD = 1_000.0
CFG = StateConfig(short_rows=20, long_rows=200)
HORIZONS_ROWS = {"10 s": 33, "30 s": 100, "60 s": 200, "300 s": 1000}
#: Frais MAKER balayes, en bps par jambe passive. 2,0 = OKX mesure ;
#: 0,0 = MEXC mesure (makerCommission 0). Le taker reste celui d'OKX : la
#: sortie traverse toujours, et ce frais-la n'est nul nulle part.
MAKER_GRID = [2.0, 1.0, 0.5, 0.0]

panel = pickle.load(open(f"{SCRATCH}/panel.pkl", "rb"))
raw = json.load(open(f"{SCRATCH}/insts.json"))
sch = load_schedules()

n_rows = min(len(v) for v in panel.values())
cut = split_index(n_rows, TRAIN_FRACTION)

depth_ref, flow_ref = {}, {}
base = []
for inst in sorted(panel):
    spec = parse_okx_instrument(raw.get(inst) or {})
    s = sch.get(inst) or sch.get(inst.rsplit("-", 1)[0])
    if not (spec and s):
        continue
    rows = panel[inst]
    d = [min(r[3], r[4]) for r in rows[:cut] if r[3] > 0 and r[4] > 0]
    depth_ref[inst] = st.median(d) if d else 0.0
    flow_ref[inst] = flow_median(rows, CFG, 0, cut)
    base.append((inst, spec, s, rows))

print(f"{len(base)} instruments   taker fixe a {OKX_PUBLIC_TAKER_BPS} bps "
      f"(la sortie traverse toujours)")
print(f"coupure : apprentissage {cut*0.3/3600:.2f} h, "
      f"test {(n_rows-cut)*0.3/3600:.2f} h\n")
print(f"{'maker':>7}{'horizon':>9}{'variables retenues':>40}"
      f"{'val.train':>11}{'trades':>8}{'net bps/tr':>12}{'net USD':>10}")
print("-" * 97)

resume = {}
for mk in MAKER_GRID:
    fees = FeeModel(maker_bps=mk, taker_bps=OKX_PUBLIC_TAKER_BPS)
    marches = [Market(i, sp, s, fees, r) for i, sp, s, r in base]

    def ech(h, lo, hi):
        out = []
        for m in marches:
            if flow_ref[m.inst_id] <= 0 or depth_ref[m.inst_id] <= 0:
                continue
            b = StateBuilder(CFG, flow_ref[m.inst_id])
            for x in build_samples(m, b, h, lo, hi,
                                   max_size_usd=CAPITAL_RESERVE_USD * 50.0,
                                   max_capital_usd=CAPITAL_RESERVE_USD):
                x.state["depth_ct"] /= depth_ref[m.inst_id]
                out.append(x)
        out.sort(key=lambda x: x.ts_ms)
        return out

    meilleur = None
    for nom, h in HORIZONS_ROWS.items():
        tr = ech(h, 0, cut)
        if len(tr) < 500:
            continue
        feats, av, val = select_features([(x.state, x.nets) for x in tr],
                                         FEATURES, ALL_ACTIONS)
        if av is not None and (meilleur is None or val > meilleur[2]):
            meilleur = (nom, h, val, feats, av)
    if meilleur is None:
        print(f"{mk:>7.1f}{'—':>9}{'(aucune)':>40}{0.0:>11.3f}{0:>8}"
              f"{0.0:>12.3f}{0.0:>10.2f}")
        resume[mk] = {"trades": 0, "net_bps": 0.0, "net_usd": 0.0}
        continue
    nom, h, val, feats, av = meilleur
    x = run_policy(ech(h, cut, n_rows), av, ALL_ACTIONS,
                   CAPITAL_RESERVE_USD).summary(UPPER_BOUND)
    print(f"{mk:>7.1f}{nom:>9}{', '.join(feats):>40}{val:>11.3f}"
          f"{x['trades']:>8,}{x['net_bps_moyen']:>12.3f}{x['net_usd']:>10.2f}")
    resume[mk] = {"horizon": nom, "features": list(feats), "train": val,
                  "trades": x["trades"], "net_bps": x["net_bps_moyen"],
                  "net_usd": x["net_usd"]}

print("\nLECTURE")
pos = [m for m, v in resume.items() if v["net_bps"] > 0 and v["trades"] > 0]
if pos:
    print(f"  Le test devient positif a partir d'un frais maker de "
          f"{max(pos):.1f} bps.")
    print("  Le goulot etait donc l'EXECUTION. A instruire : la probabilite")
    print("  de remplissage passif reste INCONNUE et borne ce resultat.")
else:
    print("  Le test reste NEGATIF jusqu'a un frais maker de ZERO, c'est-a-dire")
    print("  au plancher arithmetique du cout. Le goulot n'est donc pas le")
    print("  cout : c'est le signal conditionnel lui-meme. Changer de venue,")
    print("  de bareme ou de palier de frais ne peut rien y faire.")
    ecart = min(v["net_bps"] for v in resume.values() if v["trades"])
    print(f"\n  Meilleur net hors echantillon, tous frais confondus : "
          f"{max(v['net_bps'] for v in resume.values() if v['trades']):+.2f} bps/trade")

json.dump({str(k): v for k, v in resume.items()},
          open(f"{SCRATCH}/policy_costfloor.json", "w"), indent=1)
