"""CONDITIONS D'ATTEINTE : ce qui devrait etre vrai pour 1 000 -> 5 000 en 60 j.

POURQUOI CE SCAN EXISTE. Le mandat (section 1) demande explicitement « quelles
conditions economiques seraient necessaires », et le tableau de bord n'y
repondait que par un ratio — « il manque un facteur 8,2 ». Un facteur n'est pas
une condition : il ne dit pas SUR QUOI il faudrait gagner ce facteur, et il
suggere qu'un seul levier suffirait. Ce scan remplace le ratio par la FRONTIERE
elle-meme, et elle a une propriete que le ratio cachait.

L'ARITHMETIQUE. Sur une journee, avec :
    e  = edge BRUT par aller-retour, en bps de NOTIONNEL
    c  = cout complet d'un aller-retour, en bps de NOTIONNEL
    n  = nombre d'allers-retours par jour
    L  = levier (notionnel / capital)
le rendement net quotidien sur CAPITAL vaut  n * L * (e - c).

La cible mord a 271,87 bps/jour (5x en 60 jours ; voir Objective). D'ou :

    e = c + cible / (n * L)

Cette forme rend visible ce que le facteur 8,2 masquait : le terme cout `c`
est un PLANCHER ADDITIF. Quand l'edge brut disponible est inferieur au cout,
AUCUN n et AUCUN L ne resolvent l'equation — augmenter la rotation ou le
levier multiplie une quantite negative. Le probleme n'est pas d'etre « a un
facteur 8,2 » : il est que le signe de (e - c) est negatif sur toutes les
grandeurs que le projet a mesurees.

SOURCES. `c` est mesure EN DIRECT sur le carnet public OKX (spread au touch +
bareme taker public). `e` est confronte aux magnitudes brutes que le registre
a effectivement mesurees, citees avec leur scan d'origine.

    python -m prism_v2.scans.conditions
"""
from __future__ import annotations

import json
import statistics as st
import urllib.request
from typing import Dict, List, Optional, Tuple

from prism_v2.dashboard import Objective

OBJECTIF = Objective(capital_eur=1_000.0, target_eur_per_day=20.0,
                     multiple=5.0, days=60.0)

#: Bareme public OKX, perpetuels, niveau sans palier (le seul accessible a
#: 1 000 EUR). Verifie sur la grille publique ; ce n'est pas une hypothese
#: favorable, c'est le tarif le plus cher de la grille — donc celui qui
#: s'applique.
TAKER_BPS = 5.0
MAKER_BPS = 2.0

#: MAGNITUDES BRUTES MESUREES dans ce depot, en bps de notionnel par
#: occasion. Aucune n'est une prediction : chacune est la taille du
#: phenomene observe, AVANT tout cout. Elles bornent `e` par le haut.
MAGNITUDES_BRUTES: List[Tuple[str, float, str]] = [
    ("concession d'urgence (rafales agressives)", 0.26,
     "concession_verdict.py — 29 038 rafales, carnet 400 niveaux"),
    ("ecart de prix dans une rafale > 10 impressions", 0.53,
     "concession_verdict.py"),
    ("differentiel de funding, 16 paires", 2.57,
     "flux quotidien, 92 jours — deja un bps/JOUR, pas par aller-retour"),
    ("crible de mecanismes, plus grand effet observable", 2.00,
     "CRIBLE_MECANISMES.md — « le plus grand effet observable vaut 2 bps »"),
    ("flux capte par allocation causale", 6.46,
     "carry_alloc.py — le plus grand differentiel observe"),
    ("borne haute des quatre formes de gain", 6.50,
     "etat.py — « toutes les grandeurs mesurees tombent entre 0,26 et 6,5 »"),
]

_OKX = "https://www.okx.com"

#: Taille de l'echantillon d'instruments sur lequel le cout est mesure. Le
#: CHOIX des instruments, lui, n'est pas ecrit ici : il est derive du volume
#: 24 h rendu par l'API. Figer une liste de symboles a la main est
#: precisement le defaut que le projet s'est deja reproche — « PRISM n'avait
#: jamais regarde que 21 instruments, tous du quintile le plus pauvre ; le
#: biais de selection etait le mien » — et qu'une garde d'architecture
#: (test_no_hardcoded_instrument_universe_anywhere) interdit desormais.
N_INSTRUMENTS = 10


def required_gross_bps(target_bps_per_day: float, n_per_day: float,
                       leverage: float, cost_bps: float) -> Optional[float]:
    """L'edge BRUT par aller-retour qu'il faudrait, en bps de notionnel.

    Renvoie None quand la question n'a pas de sens (rotation ou levier nul) :
    une division par zero n'est pas une exigence infinie, c'est une absence
    de strategie.
    """
    if n_per_day <= 0 or leverage <= 0:
        return None
    return cost_bps + target_bps_per_day / (n_per_day * leverage)


def net_bps_per_day(gross_bps: float, cost_bps: float, n_per_day: float,
                    leverage: float) -> float:
    """Le rendement net quotidien sur CAPITAL. Peut etre negatif, et l'est."""
    return n_per_day * leverage * (gross_bps - cost_bps)


def rotations_required(target_bps_per_day: float, gross_bps: float,
                       cost_bps: float, leverage: float) -> Optional[float]:
    """Combien d'allers-retours par jour, a edge et cout donnes ?

    Renvoie None quand `gross <= cost` : c'est le resultat central de ce scan.
    Sous le cout, la rotation ne repare rien — elle accelere la perte. Rendre
    ce cas par un grand nombre plutot que par None serait le mensonge exact
    que le mandat interdit.
    """
    if leverage <= 0 or gross_bps <= cost_bps:
        return None
    return target_bps_per_day / (leverage * (gross_bps - cost_bps))


def _get(path: str) -> dict:
    req = urllib.request.Request(_OKX + path, headers={"User-Agent": "curl/8"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def univers_par_volume(n: int = N_INSTRUMENTS) -> List[str]:
    """Les `n` perpetuels les plus traites, tels que l'API les classe.

    Derive et non ecrit : le volume 24 h decide, pas moi. Mesurer le cout sur
    des instruments choisis a la main donnerait le cout de MON echantillon, et
    le projet a deja paye ce biais une fois.
    """
    tickers = _get("/api/v5/market/tickers?instType=SWAP")["data"]
    par_volume = sorted(tickers, key=lambda t: -float(t.get("volCcy24h") or 0))
    return [t["instId"] for t in par_volume[:n]]


def mesure_cout_direct(instruments: Optional[List[str]] = None
                       ) -> Dict[str, float]:
    """Cout d'aller-retour mesure MAINTENANT sur le carnet public OKX.

    Une jambe = traverser le spread une fois a l'entree, une fois a la sortie,
    plus deux frais taker. Aucune cle, aucun ordre : books5 est public.
    """
    insts = instruments if instruments is not None else univers_par_volume()
    spreads = []
    for inst in insts:
        d = _get(f"/api/v5/market/books?instId={inst}&sz=1")["data"][0]
        if not d.get("bids") or not d.get("asks"):
            continue
        bid, ask = float(d["bids"][0][0]), float(d["asks"][0][0])
        mid = (bid + ask) / 2.0
        if mid <= 0:
            continue
        spreads.append((ask - bid) / mid * 1e4)
    if not spreads:
        raise RuntimeError("aucun carnet lisible : pas de mesure de cout")
    med = st.median(spreads)
    return {"spread_median_bps": med,
            "ar_taker_1_jambe_bps": med + 2 * TAKER_BPS,
            "ar_taker_2_jambes_bps": 2 * (med + 2 * TAKER_BPS),
            "ar_maker_1_jambe_bps": 2 * MAKER_BPS,
            "n_instruments": len(spreads)}


def main() -> None:
    seuil = OBJECTIF.binding_bps_per_day()
    print("=" * 78)
    print("CONDITIONS D'ATTEINTE DE L'OBJECTIF")
    print("=" * 78)
    print(f"cible                  : {seuil:.2f} bps/jour de capital "
          f"(x{OBJECTIF.multiple:.0f} en {OBJECTIF.days:.0f} j)")

    try:
        m = mesure_cout_direct()
        src = "MESURE EN DIRECT (carnet public OKX)"
    except Exception as exc:                       # pragma: no cover - reseau
        print(f"\n[reseau indisponible : {exc}]")
        print("Le cout n'est pas mesurable ici. Le scan s'arrete : substituer "
              "une valeur de rapport serait presenter une hypothese comme une "
              "mesure.")
        return

    c1 = m["ar_taker_1_jambe_bps"]
    print(f"source du cout         : {src}, {m['n_instruments']} instruments")
    print(f"spread median au touch : {m['spread_median_bps']:.3f} bps")
    print(f"AR taker, 1 jambe      : {c1:.2f} bps  "
          f"(dont {2*TAKER_BPS:.0f} de frais, "
          f"{100*2*TAKER_BPS/c1:.0f} % du total)")
    print(f"AR taker, 2 jambes     : {m['ar_taker_2_jambes_bps']:.2f} bps "
          f"(toute position couverte)")
    print(f"AR maker pur, 1 jambe  : {m['ar_maker_1_jambe_bps']:.2f} bps "
          f"— remplissage NON garanti, donc borne inferieure de cout, pas un "
          f"cout")

    # 1. Ce qu'il faudrait gagner, a cout REEL
    print()
    print("-" * 78)
    print("A. EDGE BRUT REQUIS PAR ALLER-RETOUR, au cout taker mesure")
    print("-" * 78)
    print(f"{'AR/jour':>10}{'levier 1x':>14}{'levier 5.37x':>14}"
          f"{'levier 9.43x':>14}   (bps de notionnel)")
    for n in (1, 5, 10, 25, 50, 100, 250):
        row = f"{n:>10}"
        for L in (1.0, 5.37, 9.43):
            e = required_gross_bps(seuil, n, L, c1)
            row += f"{e:>14.2f}"
        print(row)
    print()
    print("Le cout est un PLANCHER ADDITIF : aucune colonne ne descend sous "
          f"{c1:.2f} bps, quel que soit le levier ou la rotation.")

    # 2. Ce que le projet a reellement mesure
    print()
    print("-" * 78)
    print("B. MAGNITUDES BRUTES REELLEMENT MESUREES DANS CE DEPOT")
    print("-" * 78)
    print(f"{'phenomene':<48}{'brut bps':>10}{'e - c':>10}")
    for nom, e, _src in sorted(MAGNITUDES_BRUTES, key=lambda x: x[1]):
        print(f"{nom:<48}{e:>10.2f}{e - c1:>10.2f}")
    meilleur = max(e for _n, e, _s in MAGNITUDES_BRUTES)
    print()
    print(f"meilleure magnitude brute mesuree : {meilleur:.2f} bps")
    print(f"cout d'un aller-retour            : {c1:.2f} bps")
    print(f"marge par aller-retour            : {meilleur - c1:+.2f} bps")

    # 3. Le verdict arithmetique
    print()
    print("-" * 78)
    print("C. VERDICT")
    print("-" * 78)
    r = rotations_required(seuil, meilleur, c1, 9.43)
    if r is None:
        print("AUCUNE combinaison (rotation, levier) n'atteint la cible.")
        print(f"La meilleure magnitude brute jamais mesuree ({meilleur:.2f} "
              f"bps) est SOUS le cout d'un aller-retour ({c1:.2f} bps).")
        print("Le terme (e - c) est negatif : augmenter la rotation ou le "
              "levier multiplie une perte.")
        print()
        print("Ce que l'objectif exigerait donc, au choix et sans exclusive :")
        print(f"  - un edge brut > {c1:.2f} bps par aller-retour, soit "
              f"{c1/meilleur:.1f}x la plus grande magnitude mesuree ;")
        print(f"  - OU un cout < {meilleur:.2f} bps par aller-retour, ce qui "
              f"exige un remplissage passif des DEUX cotes a frais nul ;")
        print("    -> teste : fee_floor_bh.py, frais mis a zero, "
              "0/17 survivants a Benjamini-Hochberg.")
        print("  - OU une famille dont l'edge brut n'a jamais ete mesure ici.")
    else:
        print(f"a levier 9,43x et edge brut {meilleur:.2f} bps, il faudrait "
              f"{r:.1f} allers-retours par jour.")
    print("=" * 78)


if __name__ == "__main__":
    main()
