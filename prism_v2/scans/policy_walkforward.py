"""LA BOUCLE FERMEE : gelee contre marche en avant, et attribution de l'ecart.

CE QUI MANQUAIT. La politique etait ajustee une fois puis gelee. Les maillons
ATTRIBUTION -> APPRENTISSAGE -> ADAPTATION de la boucle n'existaient pas :
le systeme ne pouvait ni diagnostiquer sa deception, ni y reagir. Le fait
empirique disponible — +2,85 bps en apprentissage, -3,47 en test — est
precisement une deception que rien ne diagnostiquait.

CE QUE CE CRIBLE MESURE.
  1. La politique GELEE : reference, une coupure, un ajustement, un test.
  2. La politique EN MARCHE EN AVANT : segments successifs, chacun ajuste
     uniquement sur ce qui le precede, avec perte de confiance quand le
     realise deçoit.
  3. L'ATTRIBUTION de l'ecart attendu/realise, poste par poste.

LA QUESTION ECONOMIQUE. L'adaptation change-t-elle le PnL net par capital et
par jour ? Et si elle ne le rend pas positif, DIT-ELLE AU MOINS pourquoi —
c'est-a-dire designe-t-elle le goulot au lieu de le constater ?

CE QUE CE CRIBLE NE FAIT PAS. Il ne reessaie aucune famille morte. Il teste
un maillon de la boucle qui n'a jamais ete teste, sur des donnees deja
collectees, sans en demander de nouvelles.
"""
import json, os, pickle, statistics as st

from prism_v2.fees import OKX_PUBLIC_MAKER_BPS, OKX_PUBLIC_TAKER_BPS
from prism_v2.instruments import parse_okx_instrument
from prism_v2.margin import load_schedules
from prism_v2.policy.action import (ALL_ACTIONS, EXECUTABLE, TAKER_ACTIONS,
                                    UPPER_BOUND, Action, FeeModel)
from prism_v2.policy.adaptive import CONFIANCE_MAX, parcourir
from prism_v2.policy.attribution import attribuer_tout
from prism_v2.policy.engine import Market, build_samples, run_policy, split_index
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import select_features

SCRATCH = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
TRAIN_FRACTION = 0.60
CAPITAL_RESERVE_USD = 1_000.0
CFG = StateConfig(short_rows=20, long_rows=200)
HORIZON_ROWS = 1000          # 300 s : le seul horizon ou la politique gelee
                             # avait retenu quelque chose
N_SEGMENTS = 8

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


def echantillons(lo, hi):
    out = []
    for m in marches:
        if flow_ref[m.inst_id] <= 0 or depth_ref[m.inst_id] <= 0:
            continue
        b = StateBuilder(CFG, flow_ref[m.inst_id])
        for x in build_samples(m, b, HORIZON_ROWS, lo, hi,
                               max_size_usd=CAPITAL_RESERVE_USD * 50.0,
                               max_capital_usd=CAPITAL_RESERVE_USD):
            x.state["depth_ct"] /= depth_ref[m.inst_id]
            out.append(x)
    out.sort(key=lambda x: x.ts_ms)
    return out


TOUT = echantillons(0, n_rows)
print(f"{len(marches)} instruments, horizon 300 s, {len(TOUT):,} decisions")
print(f"capital reserve {CAPITAL_RESERVE_USD:,.0f} USD\n")

#: Cout que le modele de valeur avait deja integre : les valeurs de cellule
#: sont des moyennes de PnL NETS, donc le cout y est compris. Le verifier est
#: le role de l'attribution, pas le supposer.
COUT_ATTENDU = {EXECUTABLE: 2 * fees.taker_bps,
                UPPER_BOUND: fees.maker_bps + fees.taker_bps}

resultats = {}
for label, actions, statut in (("TAKER (executable)", TAKER_ACTIONS, EXECUTABLE),
                               ("ENTREE PASSIVE (borne sup.)", ALL_ACTIONS,
                                UPPER_BOUND)):
    print("=" * 78)
    print(label)
    print("=" * 78)

    # --- REFERENCE : une coupure, un ajustement, un test ------------------
    appr = [x for x in TOUT if x.index < cut]
    test = [x for x in TOUT if x.index >= cut]
    feats, av, val_tr = select_features([(x.state, x.nets) for x in appr],
                                        FEATURES, actions)
    if av is None:
        print("  GELEE : aucune variable retenue, la politique ne trade pas.\n")
        gel = None
    else:
        led = run_policy(test, av, actions, CAPITAL_RESERVE_USD)
        gel = led.summary(statut)
        att_g = attribuer_tout([d for d in led.decisions if d.action.is_trade
                                and d.status == statut], COUT_ATTENDU[statut])

    # --- MARCHE EN AVANT --------------------------------------------------
    ada = parcourir(TOUT, FEATURES, actions, n_segments=N_SEGMENTS,
                    part_apprentissage=TRAIN_FRACTION,
                    capital_usd=CAPITAL_RESERVE_USD,
                    cout_attendu_bps=COUT_ATTENDU[statut], statut=statut)
    adas = ada.registre.summary(statut)

    print(f"{'':<22}{'trades':>8}{'net bps/tr':>12}{'net USD':>10}"
          f"{'bps/jour':>11}{'drawdown':>10}{'util.':>8}")
    print("-" * 81)
    print(f"{'ne rien faire':<22}{0:>8}{0.0:>12.3f}{0.0:>10.2f}"
          f"{0.0:>11.2f}{0.0:>10.2f}{0.0:>8.3f}")
    if gel:
        print(f"{'GELEE (reference)':<22}{gel['trades']:>8,}"
              f"{gel['net_bps_moyen']:>12.3f}{gel['net_usd']:>10.2f}"
              f"{(gel['bps_par_jour'] or 0):>11.2f}{gel['drawdown_usd']:>10.2f}"
              f"{gel['utilisation']:>8.3f}")
    print(f"{'MARCHE EN AVANT':<22}{adas['trades']:>8,}"
          f"{adas['net_bps_moyen']:>12.3f}{adas['net_usd']:>10.2f}"
          f"{(adas['bps_par_jour'] or 0):>11.2f}{adas['drawdown_usd']:>10.2f}"
          f"{adas['utilisation']:>8.3f}")

    print(f"\n  segments : barre imposee, confiance, attendu, realise, trades")
    for s in ada.segments:
        print(f"    {s.indice}  barre {s.seuil_entree_bps:>6.3f}  "
              f"conf {s.confiance_entree:>5.3f}  "
              f"attendu {s.attendu_bps:>+7.3f}  realise {s.realise_bps:>+8.3f}"
              f"  {s.trades:>4} trades")
    print(f"    biais final : {ada.biais_final_bps():+.3f} bps   "
          f"confiance finale : {ada.confiance_finale():.4f} "
          f"(depart {CONFIANCE_MAX})")

    r = ada.attribution.resume()
    print(f"\n  ATTRIBUTION DE L'ECART (marche en avant)")
    for k in ("decisions", "attendu_bps", "realise_bps", "ecart_bps",
              "ecart_cout_bps", "ecart_taille_bps", "ecart_modele_bps",
              "part_imputable"):
        v = r[k]
        print(f"    {k:<20}{v if not isinstance(v, float) else format(v, '+.4f'):>14}")
    print(f"    {'verdict':<20}{r['verdict']:>14}")
    print()
    resultats[label] = {"gelee": gel, "adaptative": adas,
                        "attribution": r,
                        "confiance_finale": ada.confiance_finale(),
                      "biais_final_bps": ada.biais_final_bps(),
                        "segments": [s.__dict__ for s in ada.segments]}

json.dump(resultats, open(f"{SCRATCH}/policy_walkforward.json", "w"),
          indent=1, default=str)
print(f"detail ecrit dans {SCRATCH}/policy_walkforward.json")
