"""La formule de recompense de Polymarket, transcrite et non paraphrasee.

SOURCE : documentation publique du programme « Liquidity Rewards ». Chaque
regle transcrite ici porte sa citation. Ce qui n'est pas documente est une
HYPOTHESE et porte le mot, pour que personne ne la lise comme une mesure.

    S(v, s) = ((v - s) / v)^2 * b

  v : `max_spread` du marche, en CENTS — publie par marche
  s : distance de l'ordre au mid ajuste, en CENTS
  b : multiplicateur « in-game » (1,0 hors evenement sportif en cours)

La contribution d'un ordre vaut S * taille. Le score decroit en CARRE de la
distance : a la bordure de la bande (s = v) il vaut exactement ZERO. C'est le
fait economique central de ce module — on n'est pas paye pour poser un ordre
dans la bande, on est paye pour le poser PRES DU MID, la ou il sera rempli.
Tout le probleme tient dans cet arbitrage, et il est ici explicite plutot que
cache dans un coefficient.

COMBINAISON DES DEUX COTES, telle que documentee :

  mid dans [0,10 ; 0,90] : Q = max( min(Q_bid, Q_ask), max(Q_bid, Q_ask) / c )
  mid hors de cette bande : Q = min(Q_bid, Q_ask)

avec c = 3,0. Hors de [0,10 ; 0,90] la double cotation est donc OBLIGATOIRE :
un seul cote y rapporte zero. C'est precisement la ou vivent les contrats
binaires peu chers, et l'ignorer ferait surestimer la recompense d'un facteur
infini sur ces marches.

ECHANTILLONNAGE : Q est calcule CHAQUE MINUTE, et l'epoque compte 10 080
echantillons (une semaine). Un ordre absent du carnet ne score pas : la
disponibilite operationnelle est un facteur multiplicatif, pas un detail.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

#: Bande de mid au-dela de laquelle la double cotation devient obligatoire.
MIDPOINT_BAND = (0.10, 0.90)

#: Facteur `c` de penalite du cote unique. Documente comme « currently 3.0 on
#: all markets » : c'est donc une valeur OBSERVEE a une date, pas une
#: constante de protocole. Surchargeable pour cette raison.
SIDE_SCALING = 3.0

#: Echantillons par epoque : une minute, une semaine.
SAMPLES_PER_EPOCH = 10_080


@dataclass(frozen=True)
class QualifyingOrder:
    """Un ordre au repos, tel que le programme le voit.

    `spread_cents` est la distance au mid AJUSTE, en cents, toujours positive.
    `size` est en PARTS, l'unite native du contrat binaire — jamais en
    dollars : le meme dollar achete huit fois plus de parts a 0,12 qu'a 0,95,
    et confondre les deux fausse le score d'un facteur huit.
    """

    spread_cents: float
    size: float
    is_bid: bool

    def __post_init__(self) -> None:
        if self.spread_cents < 0:
            raise ValueError("la distance au mid est une valeur absolue")
        if self.size <= 0:
            raise ValueError("un ordre de taille nulle n'est pas un ordre")


def order_score(order: QualifyingOrder, max_spread_cents: float,
                min_size: float, in_game_multiplier: float = 1.0) -> float:
    """S(v, s) * taille, ou zero si l'ordre ne qualifie pas.

    Deux causes d'exclusion, et elles rendent zero — jamais une petite valeur
    positive : hors de la bande, et sous la taille minimale. Les rendre par un
    residu ferait croire a un revenu la ou le programme ne paie rien.
    """
    if max_spread_cents <= 0:
        raise ValueError("une bande qualifiante nulle ne definit pas de score")
    if order.size < min_size:
        return 0.0
    if order.spread_cents >= max_spread_cents:
        return 0.0
    ratio = (max_spread_cents - order.spread_cents) / max_spread_cents
    return ratio * ratio * in_game_multiplier * order.size


def two_sided_score(orders: Sequence[QualifyingOrder], midpoint: float,
                    max_spread_cents: float, min_size: float,
                    in_game_multiplier: float = 1.0,
                    side_scaling: float = SIDE_SCALING) -> float:
    """Le Q d'un participant sur UN echantillon, les deux cotes combines.

    Hors de [0,10 ; 0,90], un carnet cote d'un seul cote rend EXACTEMENT
    zero. C'est la regle qui decide de la viabilite sur les contrats a bas
    prix, et elle est appliquee ici sans adoucissement.
    """
    lo, hi = MIDPOINT_BAND
    q_bid = sum(order_score(o, max_spread_cents, min_size, in_game_multiplier)
                for o in orders if o.is_bid)
    q_ask = sum(order_score(o, max_spread_cents, min_size, in_game_multiplier)
                for o in orders if not o.is_bid)
    if lo <= midpoint <= hi:
        if side_scaling <= 0:
            raise ValueError("le facteur de penalite du cote unique est > 0")
        return max(min(q_bid, q_ask), max(q_bid, q_ask) / side_scaling)
    return min(q_bid, q_ask)


def epoch_share(my_q: float, others_q: Optional[float]) -> Optional[float]:
    """Part du pool : Q_moi / somme(Q). None quand la concurrence est INCONNUE.

    `others_q` VAUT None quand le Q des autres n'a pas ete mesure, et la part
    est alors inconnue — pas 100 %. La distinction n'est pas theorique : dans
    cette meme session, une liquidite qualifiante mesuree a zero a ete
    remplacee par 1 dollar « pour eviter la division », ce qui a produit des
    parts de 99 % et un rendement annonce de 112 %/jour. Le carnet reel
    montrait un spread de 16,6 cents pour une bande de 6,5 : personne n'y
    cotait parce que personne n'y trouvait son compte, et l'absence de
    concurrence etait un AVERTISSEMENT lu comme une aubaine.

    `others_q = 0.0` est autre chose, et doit etre etabli : la concurrence a
    ete mesuree, et elle est nulle. L'appelant qui l'affirme en repond.
    """
    if others_q is None:
        return None
    if others_q < 0:
        raise ValueError("un Q concurrent negatif n'a pas de sens")
    if my_q <= 0:
        return 0.0
    return my_q / (my_q + others_q)


def daily_reward(pool_usdc_per_day: float, share: Optional[float],
                 uptime: float = 1.0) -> Optional[float]:
    """Recompense quotidienne attendue, disponibilite comprise.

    `uptime` est la fraction des 1 440 echantillons quotidiens ou les ordres
    sont effectivement au carnet. Un bot arrete douze heures ne touche pas la
    moitie : il ne score pas sur ces echantillons, point.
    """
    if share is None:
        return None
    if not 0.0 <= uptime <= 1.0:
        raise ValueError("la disponibilite est une fraction de [0, 1]")
    return pool_usdc_per_day * share * uptime
