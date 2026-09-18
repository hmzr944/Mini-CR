"""LA VALEUR CONDITIONNELLE D'UNE ACTION, apprise sur l'apprentissage seul.

Le mandat interdit deux choses que ce module doit eviter par construction :

  1. conclure d'une moyenne globale nulle a l'absence d'opportunite. D'ou
     l'estimation de E[PnL net | etat, action] cellule par cellule, et non
     d'une moyenne unique ;
  2. la selection par oracle. D'ou la regle absolue de ce fichier : TOUT ce
     qui regarde les donnees — bornes de quantiles, choix des variables,
     valeurs des cellules — est calcule sur l'echantillon d'APPRENTISSAGE.
     L'echantillon de test n'est jamais lu pendant l'ajustement. Il ne sert
     qu'une fois, a la fin, pour produire le seul chiffre qui compte.

RETRECISSEMENT. Une cellule vue trois fois ne peut pas commander une action.
La valeur retenue est

    valeur = moyenne x n / (n + k)

avec k declare d'avance. Une cellule pauvre est donc tiree vers zero,
c'est-a-dire vers NO_TRADE. C'est la protection principale contre le
sur-ajustement de cellules rares.

NO_TRADE vaut exactement zero et n'est jamais estime : c'est le seuil que
toute action doit franchir.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from prism_v2.policy.action import Action

#: Poids du retrecissement, en nombre d'observations equivalentes. Declare
#: d'avance, jamais ajuste sur un resultat.
PRIOR_N = 50.0
#: Nombre maximal de variables retenues. Au-dela, les cellules se vident :
#: avec B bacs par variable, une representation a d variables cree B^d
#: cellules, et le retrecissement les annule toutes.
MAX_FEATURES = 3
#: Nombre de bacs par variable. 3 = bas / moyen / haut.
N_BINS = 3


@dataclass(frozen=True)
class Binner:
    """Discretise une variable par quantiles appris sur l'apprentissage."""
    feature: str
    edges: Tuple[float, ...]        # N_BINS - 1 bornes internes

    def bin_of(self, value: float) -> int:
        k = 0
        for e in self.edges:
            if value > e:
                k += 1
        return k


def fit_binner(feature: str, values: Sequence[float],
               n_bins: int = N_BINS) -> Optional[Binner]:
    """Bornes de quantiles. None si la variable est trop peu variable.

    Une variable constante (ou quasi) ne peut pas separer des etats : elle
    est ecartee au lieu d'etre discretisee arbitrairement.
    """
    xs = sorted(values)
    if len(xs) < n_bins * 10:
        return None
    edges = []
    for k in range(1, n_bins):
        edges.append(xs[int(len(xs) * k / n_bins)])
    if len(set(edges)) != len(edges) or edges[0] <= xs[0]:
        return None
    return Binner(feature, tuple(edges))


@dataclass
class ActionValue:
    """E[PnL net | cellule, action], retrecie vers zero."""
    features: Tuple[str, ...]
    binners: Tuple[Binner, ...]
    cells: Dict[Tuple[int, ...], Dict[Action, Tuple[float, int]]] = \
        field(default_factory=dict)
    prior_n: float = PRIOR_N

    def cell_of(self, state: Dict[str, float]) -> Tuple[int, ...]:
        return tuple(b.bin_of(state[b.feature]) for b in self.binners)

    def value(self, state: Dict[str, float], action: Action) -> float:
        """Valeur retrecie d'une action dans l'etat donne, en bps."""
        if not action.is_trade:
            return 0.0
        e = self.cells.get(self.cell_of(state), {}).get(action)
        if e is None:
            return 0.0
        moyenne, n = e
        return moyenne * n / (n + self.prior_n)

    def best(self, state: Dict[str, float], actions: Sequence[Action]
             ) -> Tuple[Action, float]:
        """Action de valeur maximale. NO_TRADE gagne les egalites.

        Une action n'est retenue que si sa valeur est STRICTEMENT positive :
        a valeur nulle ou negative, ne rien faire est toujours au moins aussi
        bon et ne consomme ni capital ni capacite.
        """
        meilleur, v_max = Action.NO_TRADE, 0.0
        for a in actions:
            if not a.is_trade:
                continue
            v = self.value(state, a)
            if v > v_max:
                meilleur, v_max = a, v
        return meilleur, v_max

    def support(self) -> Tuple[int, int]:
        """(cellules, observations) — pour l'audit de densite."""
        n = sum(cnt for c in self.cells.values() for _, cnt in c.values())
        return len(self.cells), n


def fit(samples: Sequence[Tuple[Dict[str, float], Dict[Action, float]]],
        features: Sequence[str], prior_n: float = PRIOR_N
        ) -> Optional[ActionValue]:
    """Ajuste la table de valeurs sur des echantillons d'APPRENTISSAGE.

    Chaque echantillon est un couple (etat, {action: net_bps realise}). Le
    net de CHAQUE action est connu pour chaque instant parce que la
    simulation les evalue toutes : c'est un contrefactuel exact, pas une
    reconstruction. Une seule sera choisie au moment de decider.
    """
    if not features:
        return None
    binners = []
    for f in features:
        b = fit_binner(f, [s[f] for s, _ in samples])
        if b is None:
            return None
        binners.append(b)
    av = ActionValue(tuple(features), tuple(binners), prior_n=prior_n)
    brut: Dict[Tuple[int, ...], Dict[Action, List[float]]] = {}
    for etat, nets in samples:
        cle = tuple(b.bin_of(etat[b.feature]) for b in binners)
        d = brut.setdefault(cle, {})
        for a, net in nets.items():
            if a.is_trade:
                d.setdefault(a, []).append(net)
    for cle, d in brut.items():
        av.cells[cle] = {a: (st.fmean(v), len(v)) for a, v in d.items() if v}
    return av


def train_value_bps(av: ActionValue,
                    samples: Sequence[Tuple[Dict[str, float],
                                            Dict[Action, float]]],
                    actions: Sequence[Action]) -> float:
    """Valeur de la politique SUR L'APPRENTISSAGE, en bps par decision.

    Sert uniquement de critere de selection des variables. Ce nombre est
    optimiste par construction — la politique a vu ces donnees — et n'est
    JAMAIS presente comme un resultat.
    """
    if not samples:
        return 0.0
    total = 0.0
    for etat, nets in samples:
        a, _ = av.best(etat, actions)
        if a.is_trade:
            total += nets.get(a, 0.0)
    return total / len(samples)


def select_features(samples: Sequence[Tuple[Dict[str, float],
                                            Dict[Action, float]]],
                    candidates: Sequence[str],
                    actions: Sequence[Action],
                    max_features: int = MAX_FEATURES,
                    prior_n: float = PRIOR_N
                    ) -> Tuple[Tuple[str, ...], Optional[ActionValue], float]:
    """Selection avant, gloutonne, SUR L'APPRENTISSAGE SEUL.

    Ajoute a chaque tour la variable qui ameliore le plus la valeur de la
    politique sur l'apprentissage, et s'arrete des qu'aucune n'ameliore.
    Le resultat est une representation d'etat CHOISIE PAR LES DONNEES : le
    mandat interdit de decider d'avance quelles variables comptent.
    """
    retenues: List[str] = []
    meilleur_av, meilleure_val = None, 0.0
    while len(retenues) < max_features:
        gagnante, gagnante_av, gagnante_val = None, None, meilleure_val
        for f in candidates:
            if f in retenues:
                continue
            av = fit(samples, retenues + [f], prior_n=prior_n)
            if av is None:
                continue
            v = train_value_bps(av, samples, actions)
            if v > gagnante_val:
                gagnante, gagnante_av, gagnante_val = f, av, v
        if gagnante is None:
            break
        retenues.append(gagnante)
        meilleur_av, meilleure_val = gagnante_av, gagnante_val
    return tuple(retenues), meilleur_av, meilleure_val
