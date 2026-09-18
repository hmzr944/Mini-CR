"""POLITIQUE ADAPTATIVE : apprise sur la premiere moitie, jugee sur la seconde.

CE QUE CE CRIBLE TESTE, ET POURQUOI MAINTENANT. Tout ce que le projet avait
mesure jusqu'ici l'etait INCONDITIONNELLEMENT : une moyenne par instrument,
par famille, par horizon. Le mandat interdit d'en conclure quoi que ce soit
sur l'existence d'une structure CONDITIONNELLE. La question posee ici est
donc la seule qui reste ouverte :

    existe-t-il un ETAT DU MARCHE dans lequel une ACTION vaut mieux que
    NE RIEN FAIRE, une fois tous les couts payes ?

MATIERE. Le panneau synchrone : 15 perpetuels inverses OKX, 78 000 lignes
chacun, pas de 300 ms, 6,5 heures, avec de chaque cote meilleur bid, meilleur
ask, tailles au touch et flux agressif signe. C'est la seule donnee du depot
qui porte simultanement le flux, la liquidite, la reponse du prix et la
volatilite, c'est-a-dire les quatre ingredients que la section 28 demande de
croiser au lieu de les regarder un par un.

PROTOCOLE, fixe avant de voir le moindre resultat.
  1. Coupure temporelle 60 / 40. L'apprentissage precede le test.
  2. TOUT ce qui regarde les donnees — normalisations, bornes de quantiles,
     choix des variables, choix de l'horizon, valeurs des cellules — est
     calcule sur l'apprentissage SEUL.
  3. Le test n'est lu qu'une fois, a la fin, et produit UN chiffre. Pas de
     retour en arriere, pas de second essai : c'est ce qui distingue une
     politique executable d'une selection par oracle.
  4. NO_TRADE est disponible a chaque instant et vaut zero. Une action doit
     le DEPASSER pour etre choisie.
  5. Deux comptes separes : les actions a remplissage certain (EXECUTABLE)
     et celles qui dependent d'un remplissage passif inconnu (BORNE_SUP).
     Elles ne sont jamais additionnees.

TEMOINS. La politique apprise est comparee a « toujours long », « toujours
court » et « ne rien faire ». Sans ces temoins, un resultat positif ne
prouverait pas que c'est la CONDITION qui travaille.
"""
import json, os, pickle, statistics as st, sys

from prism_v2.fees import OKX_PUBLIC_MAKER_BPS, OKX_PUBLIC_TAKER_BPS
from prism_v2.instruments import parse_okx_instrument
from prism_v2.margin import load_schedules
from prism_v2.policy.action import (ALL_ACTIONS, EXECUTABLE, TAKER_ACTIONS,
                                    UPPER_BOUND, Action, FeeModel)
from prism_v2.policy.engine import Market, build_samples, run_policy, split_index
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import ActionValue, fit, select_features

SCRATCH = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")

#: Declares AVANT tout resultat.
TRAIN_FRACTION = 0.60
CAPITAL_RESERVE_USD = 1_000.0
#: Horizons candidats, en lignes de panneau (pas de 300 ms). Le choix de
#: l'horizon fait partie de la politique et se fait sur l'apprentissage.
HORIZONS_ROWS = {"10 s": 33, "30 s": 100, "60 s": 200, "300 s": 1000}
CFG = StateConfig(short_rows=20, long_rows=200)

panel = pickle.load(open(f"{SCRATCH}/panel.pkl", "rb"))
raw = json.load(open(f"{SCRATCH}/insts.json"))
schedules = load_schedules()
fees = FeeModel(maker_bps=OKX_PUBLIC_MAKER_BPS, taker_bps=OKX_PUBLIC_TAKER_BPS)

marches = []
for inst in sorted(panel):
    spec = parse_okx_instrument(raw.get(inst) or {})
    fam = inst.rsplit("-", 1)[0] if inst.endswith("-SWAP") else inst
    sch = schedules.get(inst) or schedules.get(fam)
    if spec is None or sch is None:
        print(f"  ecarte {inst} : spec ou bareme de marge absent")
        continue
    marches.append(Market(inst_id=inst, spec=spec, schedule=sch, fees=fees,
                          rows=panel[inst]))
print(f"{len(marches)} instruments, {len(panel[marches[0].inst_id]):,} lignes, "
      f"frais maker {fees.maker_bps} / taker {fees.taker_bps} bps\n")

n_rows = min(len(m.rows) for m in marches)
cut = split_index(n_rows, TRAIN_FRACTION)
print(f"coupure temporelle : apprentissage [0, {cut:,})  test [{cut:,}, {n_rows:,})")
print(f"soit {cut*0.3/3600:.2f} h d'apprentissage et "
      f"{(n_rows-cut)*0.3/3600:.2f} h de test\n")

#: Normalisation de la profondeur : mediane de l'APPRENTISSAGE, par
#: instrument. Sans elle, depth_ct n'est pas comparable d'un instrument a
#: l'autre et la mise en commun des cellules serait un melange de torchons.
depth_ref = {}
flow_ref = {}
for m in marches:
    d = [min(r[3], r[4]) for r in m.rows[:cut] if r[3] > 0 and r[4] > 0]
    depth_ref[m.inst_id] = st.median(d) if d else 0.0
    flow_ref[m.inst_id] = flow_median(m.rows, CFG, 0, cut)


def normalise(s, inst):
    """Rend la profondeur comparable entre instruments. Reference = TRAIN."""
    r = depth_ref[inst]
    if r > 0:
        s["depth_ct"] = s["depth_ct"] / r
    return s


def echantillons(h_rows, lo, hi):
    out = []
    for m in marches:
        if flow_ref[m.inst_id] <= 0 or depth_ref[m.inst_id] <= 0:
            continue
        b = StateBuilder(CFG, flow_ref[m.inst_id])
        for s in build_samples(m, b, h_rows, lo, hi,
                               max_size_usd=CAPITAL_RESERVE_USD * 50.0,
                               max_capital_usd=CAPITAL_RESERVE_USD):
            normalise(s.state, m.inst_id)
            out.append(s)
    out.sort(key=lambda s: s.ts_ms)
    return out


def temoin(samples, action, cap):
    """Politique constante : toujours la meme action. Sert de temoin."""
    from prism_v2.policy.ledger import Decision, Ledger
    led = Ledger(reserved_capital_usd=cap)
    for s in samples:
        f = s.fills[action]
        led.record(Decision(
            ts_ms=s.ts_ms, instrument=s.inst_id, state=s.state, action=action,
            target_position_usd=(s.size_usd if action.is_long else -s.size_usd),
            entry_px=f.entry_px, exit_px=f.exit_px, size_usd=s.size_usd,
            holding_s=s.holding_s, gross_bps=f.gross_bps, fee_bps=f.fee_bps,
            spread_bps=f.spread_bps, slippage_bps=0.0,
            adverse_selection_bps=None, net_bps=f.net_bps,
            capital_usd=s.capital_usd, status=f.status, predicted_bps=0.0))
    return led


# =========================================================== APPRENTISSAGE
#: Deux espaces d'action, juges SEPAREMENT et jamais additionnes.
#:   TAKER      remplissage certain des deux cotes -> EXECUTABLE.
#:   AVEC MAKER entree passive -> depend d'un remplissage dont la
#:              probabilite est INCONNUE -> BORNE SUPERIEURE.
ESPACES = (("TAKER SEUL (executable)", TAKER_ACTIONS, EXECUTABLE),
           ("AVEC ENTREE PASSIVE (borne sup.)", ALL_ACTIONS, UPPER_BOUND))

#: Echantillons construits une seule fois par horizon et partages par les
#: deux espaces : les instants de decision sont les memes, seules les
#: actions disponibles changent.
TRAIN = {nom: echantillons(h, 0, cut) for nom, h in HORIZONS_ROWS.items()}
TEST = {nom: echantillons(h, cut, n_rows) for nom, h in HORIZONS_ROWS.items()}

resultats = []
for label, actions, statut in ESPACES:
    print(f"\n{'='*78}\nAPPRENTISSAGE — {label}\n{'='*78}")
    print("La valeur affichee est celle de la politique SUR L'APPRENTISSAGE :")
    print("optimiste par construction, ce n'est PAS un resultat.\n")
    print(f"{'horizon':>9}{'decisions':>11}{'variables retenues':>44}"
          f"{'val. train':>12}")
    print("-" * 78)
    meilleur = None
    for nom, h in HORIZONS_ROWS.items():
        tr = TRAIN[nom]
        if len(tr) < 500:
            print(f"{nom:>9}{len(tr):>11,}   echantillon insuffisant")
            continue
        paires = [(x.state, x.nets) for x in tr]
        feats, av, val = select_features(paires, FEATURES, actions)
        txt = ", ".join(feats) if feats else "(aucune)"
        print(f"{nom:>9}{len(tr):>11,}{txt:>44}{val:>12.3f}")
        if av is not None and (meilleur is None or val > meilleur[2]):
            meilleur = (nom, av, val, feats, h)
    if meilleur is None:
        print(f"\n  AUCUNE action retenue sur l'apprentissage pour {label}.")
        print("  La valeur de toute action y est restee <= 0 : NO_TRADE partout.")
        resultats.append((label, statut, None, None))
        continue
    nom, av, val_tr, feats, h = meilleur
    cellules, obs = av.support()
    print(f"\n  POLITIQUE RETENUE  horizon {nom}  variables {feats}")
    print(f"  {cellules} cellules, {obs:,} observations d'apprentissage")
    resultats.append((label, statut, (nom, av, val_tr, feats, h), None))

# ================================================================== TEST
print(f"\n{'='*78}\nTEST — donnees jamais vues. Un seul passage par espace.")
print(f"{'='*78}")
print(f"{'politique':<34}{'trades':>8}{'net bps/tr':>12}{'net USD':>10}"
      f"{'bps/jour':>11}{'statut':>13}")
print("-" * 88)

n_ref = list(HORIZONS_ROWS)[0]
for lbl, a in (("toujours LONG (temoin)", Action.LONG_TAKER),
               ("toujours SHORT (temoin)", Action.SHORT_TAKER)):
    x = temoin(TEST[n_ref], a, CAPITAL_RESERVE_USD).summary(EXECUTABLE)
    print(f"{lbl:<34}{x['trades']:>8,}{x['net_bps_moyen']:>12.3f}"
          f"{x['net_usd']:>10.2f}{(x['bps_par_jour'] or 0):>11.2f}"
          f"{EXECUTABLE:>13}")
print(f"{'ne rien faire (temoin)':<34}{0:>8}{0.0:>12.3f}{0.0:>10.2f}"
      f"{0.0:>11.2f}{EXECUTABLE:>13}")

sorties = {}
for (label, statut, retenu, _), (_, actions, _) in zip(resultats, ESPACES):
    if retenu is None:
        print(f"{label:<34}{0:>8}{0.0:>12.3f}{0.0:>10.2f}{0.0:>11.2f}"
              f"{statut:>13}")
        sorties[label] = None
        continue
    nom, av, val_tr, feats, h = retenu
    led = run_policy(TEST[nom], av, actions, CAPITAL_RESERVE_USD)
    x = led.summary(statut)
    print(f"{label:<34}{x['trades']:>8,}{x['net_bps_moyen']:>12.3f}"
          f"{x['net_usd']:>10.2f}{(x['bps_par_jour'] or 0):>11.2f}"
          f"{statut:>13}")
    sorties[label] = (x, nom, feats, val_tr)

for label, v in sorties.items():
    if v is None:
        continue
    x, nom, feats, val_tr = v
    print(f"\n{label} — comptabilite complete")
    print(f"  horizon {nom}, variables {feats}, valeur train {val_tr:.3f} bps")
    for k in ("decisions", "trades", "part_no_trade", "net_usd",
              "net_bps_moyen", "net_bps_median", "bps_par_jour",
              "capital_reserve_usd", "capital_pondere_usd",
              "capital_pic_usd", "tient_dans_la_reserve", "utilisation",
              "turnover_par_jour", "drawdown_usd", "duree_moyenne_s",
              "cout_execution_bps", "jours"):
        val = x[k]
        print(f"    {k:<22}"
              f"{val if not isinstance(val, float) else format(val, '.4f'):>16}")
    bj = x["bps_par_jour"] or 0.0
    print(f"    {'objectif':<22}{271.87:>16.2f} bps/jour")
    print(f"    {'atteint':<22}{bj:>16.2f} bps/jour  "
          f"{'POSITIF' if bj > 0 else 'NEGATIF OU NUL'}")

json.dump({k: (v[0] if v else None) for k, v in sorties.items()},
          open(f"{SCRATCH}/policy_fit.json", "w"), indent=1, default=str)
print(f"\ndetail ecrit dans {SCRATCH}/policy_fit.json")
