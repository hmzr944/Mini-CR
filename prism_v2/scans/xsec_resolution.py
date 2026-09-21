"""CE QUE CET ECHANTILLON PEUT ET NE PEUT PAS TRANCHER.

Le test de permutation a produit un nombre plus important que son propre
verdict : le 95e centile de la distribution NULLE. C'est le PLANCHER DE
BRUIT de la mesure — la plage qu'une redistribution au hasard produit
couramment, sans qu'aucune information n'existe.

Confronte au cout d'un aller-retour, il partage les horizons en trois, et
cette distinction commande la suite :

  RESOLU ET MORT     plancher de bruit SOUS le cout, plage observee sous le
                     cout aussi. La question est tranchee : pas d'economie.
  RESOLU ET VIVANT   plancher de bruit sous le cout, plage observee dessus.
                     Il y aurait quelque chose. (Aucun cas ici.)
  NON RESOLU         plancher de bruit AU-DESSUS du cout. La mesure ne peut
                     pas distinguer un edge monetisable du hasard. Ce n'est
                     pas un resultat negatif : c'est une absence de resultat,
                     et il est interdit de la presenter autrement.

L'horizon 300 s tombe dans le troisieme cas, et cela explique retroactivement
toute une classe d'artefacts du projet : c'est l'horizon ou les chiffres
spectaculaires apparaissaient, precisement parce que le bruit y est grand.

CE QUE CELA EXIGE COMME DONNEE. La plage nulle decroit comme 1/racine(blocs).
Pour amener le plancher de bruit sous le cout il faut donc

    blocs_requis = blocs_actuels x (plancher / cout)^2

et la duree de panneau correspondante. C'est la seule justification
acceptable d'une collecte : elle nomme la decision qu'elle changerait.
"""
import json, os

from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
#: Cout plancher d'un aller-retour, en bps : frais maker NUL (bareme public
#: MEXC verifie) plus une sortie en traversee chez OKX. Rien ne descend plus
#: bas, donc c'est le seuil le plus favorable qui puisse exister.
COUT_PLANCHER = 5.0
#: Pas de la grille du panneau, en secondes.
PAS_S = 0.3

tests = json.load(open(f"{SCRATCH}/xsec_lead_null.json"))
#: Plages observees aux horizons courts, mesurees par xsec_lead.py sur des
#: echantillons bien plus grands. Reportees ici pour completer le tableau ;
#: elles n'ont pas ete soumises au test de permutation, justement parce
#: qu'elles sont trop petites pour financer quoi que ce soit.
COURTS = {"0,9 s": (0.21, 232_935), "3 s": (0.48, 69_870),
          "10 s": (0.61, 21_165), "30 s": (1.59, 6_975)}

par_h = {}
for t in tests:
    h = t["horizon"]
    cur = par_h.get(h)
    if cur is None or t["p95_nulle"] > cur["p95"]:
        par_h[h] = {"p95": t["p95_nulle"], "blocs": t["instants"]}
    par_h[h]["plage_max"] = max(par_h[h].get("plage_max", 0.0), t["plage"])

print(f"cout plancher d'un aller-retour : {COUT_PLANCHER} bps "
      f"(frais maker nul + sortie en traversee)\n")
print(f"{'horizon':>8}{'blocs':>8}{'plage obs.':>12}{'bruit p95':>11}"
      f"{'statut':>16}{'blocs requis':>14}{'panneau requis':>16}")
print("-" * 86)

for nom, (plage, n) in COURTS.items():
    print(f"{nom:>8}{n:>8,}{plage:>12.2f}{'—':>11}{'RESOLU ET MORT':>16}"
          f"{'—':>14}{'—':>16}")

besoin = {}
for nom in ("60 s", "300 s"):
    v = par_h.get(nom)
    if v is None:
        continue
    h_s = float(nom.split()[0].replace(",", ".")) if nom != "300 s" else 300.0
    if v["p95"] <= COUT_PLANCHER:
        statut = ("RESOLU ET MORT" if v["plage_max"] <= COUT_PLANCHER
                  else "RESOLU ET VIVANT")
        req, duree = "—", "—"
    else:
        statut = "NON RESOLU"
        k = v["blocs"] * (v["p95"] / COUT_PLANCHER) ** 2
        req = f"{k:,.0f}"
        duree = f"{k * h_s / 3600:,.1f} h"
        besoin[nom] = {"blocs_requis": k, "heures": k * h_s / 3600,
                       "p95": v["p95"], "blocs": v["blocs"]}
    print(f"{nom:>8}{v['blocs']:>8,}{v['plage_max']:>12.2f}{v['p95']:>11.2f}"
          f"{statut:>16}{req:>14}{duree:>16}")

print("\nLECTURE")
print("  Aux horizons de 0,9 s a 60 s, la mesure est RESOLUE et l'information")
print("  transversale ne finance rien : les plages valent 0,21 a 2,11 bps la")
print(f"  ou un aller-retour en coute {COUT_PLANCHER:.0f} au mieux.")
if besoin:
    for nom, b in besoin.items():
        print(f"\n  A {nom}, la mesure n'est PAS resolue : le hasard seul produit")
        print(f"  couramment une plage de {b['p95']:.1f} bps sur {b['blocs']} blocs,")
        print(f"  soit {b['p95']/COUT_PLANCHER:.1f} fois le cout. Rien ne peut y etre")
        print("  distingue, ni dans un sens ni dans l'autre. C'est INCONNU, pas")
        print("  negatif, et cela explique pourquoi c'est precisement a cet")
        print("  horizon que les resultats spectaculaires du projet naissaient.")
        print(f"\n  Pour trancher, il faudrait {b['blocs_requis']:,.0f} blocs, soit")
        print(f"  {b['heures']:,.1f} h de panneau contre 6,5 h aujourd'hui "
              f"({b['heures']/6.5:.0f}x).")
        print("  DECISION QUE CETTE DONNEE CHANGERAIT : elle dirait si une")
        print("  politique transversale a 300 s vaut d'etre construite, question")
        print("  aujourd'hui sans reponse possible.")

json.dump({"cout_plancher": COUT_PLANCHER, "besoin": besoin},
          open(f"{SCRATCH}/xsec_resolution.json", "w"), indent=1)
