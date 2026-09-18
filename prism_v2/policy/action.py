"""L'ESPACE D'ACTION, et ce que chaque action coute REELLEMENT.

Le mandat exige que NO_TRADE soit une action de plein droit. Elle l'est ici,
et sa valeur vaut exactement zero : aucune estimation, aucun bruit, aucun
biais. Une action de marche n'est choisie que si sa valeur estimee depasse
ce zero APRES tous les couts.

DEUX CLASSES D'ACTIONS, ET ELLES N'ONT PAS LE MEME STATUT.

  TAKER      le remplissage est CERTAIN : on traverse. Le prix d'entree est
             le prix affiche du cote traverse a l'instant de la decision,
             et le prix de sortie est le prix affiche du cote oppose a la
             fin de l'horizon. Rien n'est suppose.

  MAKER_IN   le remplissage d'entree est INCERTAIN et sa probabilite est
             INCONNUE : PRISM n'a pas de modele de file d'attente. Ces
             actions sont donc marquees comme BORNE SUPERIEURE et ne peuvent
             pas produire un PnL presente comme executable. La sortie, elle,
             est prise en TAKER : une seule inconnue par aller-retour plutot
             que deux.

Il n'existe volontairement PAS d'action « maker a l'entree et a la sortie » :
elle empilerait deux remplissages inconnus et produirait un chiffre que rien
ne borne.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Sequence

#: Statut d'un resultat, au sens de la section 12 du mandat.
EXECUTABLE = "EXECUTABLE"        # remplissage certain, couts reels
UPPER_BOUND = "BORNE_SUP"        # depend d'un remplissage passif inconnu


class Action(enum.Enum):
    """Les actions que la politique peut choisir a chaque decision."""
    NO_TRADE = "NO_TRADE"
    LONG_TAKER = "LONG_TAKER"
    SHORT_TAKER = "SHORT_TAKER"
    LONG_MAKER_IN = "LONG_MAKER_IN"
    SHORT_MAKER_IN = "SHORT_MAKER_IN"

    @property
    def is_trade(self) -> bool:
        return self is not Action.NO_TRADE

    @property
    def is_long(self) -> bool:
        return self in (Action.LONG_TAKER, Action.LONG_MAKER_IN)

    @property
    def needs_passive_fill(self) -> bool:
        """L'entree depend-elle d'un remplissage passif inconnu ?"""
        return self in (Action.LONG_MAKER_IN, Action.SHORT_MAKER_IN)

    @property
    def status(self) -> str:
        return UPPER_BOUND if self.needs_passive_fill else EXECUTABLE


#: Actions reellement executables : remplissage certain des deux cotes.
TAKER_ACTIONS = (Action.NO_TRADE, Action.LONG_TAKER, Action.SHORT_TAKER)
#: Ensemble complet, sortie toujours en taker.
ALL_ACTIONS = tuple(Action)


@dataclass(frozen=True)
class FeeModel:
    """Frais d'une venue, en points de base par jambe.

    Jamais de taux global code en dur : les valeurs viennent du bareme
    public de l'instrument considere et sont transmises par l'appelant.
    """
    maker_bps: float
    taker_bps: float

    def __post_init__(self) -> None:
        if self.maker_bps < 0 or self.taker_bps < 0:
            raise ValueError("un frais ne peut pas etre negatif ici")


@dataclass(frozen=True)
class Fill:
    """Resultat d'execution d'une action, en points de base du notionnel."""
    entry_px: float
    exit_px: float
    mid_in: float             # milieu a l'entree, pour la selection adverse
    mid_out: float            # milieu a la sortie
    gross_bps: float          # mouvement de prix capte, avant tout cout
    spread_bps: float         # spread traverse, entree + sortie
    fee_bps: float            # frais, entree + sortie
    net_bps: float            # gross - spread - fee
    status: str


def execute(action: Action, row_in: Sequence[float], row_out: Sequence[float],
            fees: FeeModel) -> Optional[Fill]:
    """Execute une action entre deux lignes de panneau.

    row_in est l'etat du carnet A L'INSTANT DE LA DECISION ; row_out celui a
    la fin de l'horizon. Aucune ligne intermediaire n'est consultee : la
    politique ne dispose d'aucun stop, et ne pretend donc pas en avoir un.

    Retourne None si l'un des deux carnets est inexploitable. Retourne None
    aussi pour NO_TRADE : l'absence de position n'est pas un remplissage,
    et sa valeur est zero par definition, pas par mesure.
    """
    if not action.is_trade:
        return None
    b0, a0 = row_in[1], row_in[2]
    b1, a1 = row_out[1], row_out[2]
    if not (a0 > b0 > 0) or not (a1 > b1 > 0):
        return None
    mid0 = (a0 + b0) / 2.0

    if action.is_long:
        # Entree : a l'ask si on traverse, au bid si on est passif.
        entry = a0 if not action.needs_passive_fill else b0
        exit_ = b1                      # sortie toujours en traversant
        gross = (exit_ - entry) / entry * 10_000.0
    else:
        entry = b0 if not action.needs_passive_fill else a0
        exit_ = a1
        gross = (entry - exit_) / entry * 10_000.0

    # Le spread est deja contenu dans les prix d'entree et de sortie ci-dessus
    # — il n'est PAS soustrait une seconde fois. Il est mesure ici pour
    # l'audit uniquement : c'est le cout de traversee qu'a subi cette
    # decision, exprime en points de base du milieu d'entree.
    traverse_in = 0.0 if action.needs_passive_fill else (a0 - b0) / 2.0 / mid0 * 10_000.0
    traverse_out = (a1 - b1) / 2.0 / ((a1 + b1) / 2.0) * 10_000.0
    spread = traverse_in + traverse_out

    fee = (fees.maker_bps if action.needs_passive_fill else fees.taker_bps) \
        + fees.taker_bps
    return Fill(entry_px=entry, exit_px=exit_,
                mid_in=mid0, mid_out=(a1 + b1) / 2.0, gross_bps=gross,
                spread_bps=spread, fee_bps=fee, net_bps=gross - fee,
                status=action.status)
