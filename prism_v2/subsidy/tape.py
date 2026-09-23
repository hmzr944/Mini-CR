"""Simulation de remplissage contre la bande PUBLIQUE des echanges.

POURQUOI CECI EXISTE. Des six inconnues, `flux_subi` est celle qui multiplie
le cout : la subvention est brute tant qu'on ignore combien de fois par jour
on est rempli a la distance ou l'on cote. C'est aussi la seule des six qui se
mesure SANS CAPITAL — la bande des echanges est publique, et un ordre au
repos est entierement simulable a posteriori.

LA REGLE DE PRIORITE, et pourquoi elle est pessimiste dans le bon sens. Un
ordre pose a un prix ou dorment deja `S` parts se place DERRIERE elles : il
n'est servi qu'apres consommation de `S`. On suppose donc la file
ENTIEREMENT perdue a chaque prix — dernier arrive, dernier servi, sans
jamais reclamer d'anciennete. Cette hypothese SOUS-ESTIME les remplissages,
donc SOUS-ESTIME le cout de neutralisation.

C'est une direction d'erreur inconfortable et elle est assumee : elle rend le
resultat FAVORABLE, donc toute conclusion positive tiree d'ici reste une
borne superieure. L'hypothese inverse — etre servi en premier — surestimerait
les remplissages et flatterait la prudence sans rien prouver. On prefere une
borne dont on connait le sens.

AUCUNE DONNEE FUTURE N'ENTRE DANS UNE DECISION. Le carnet a l'instant t
decide du placement ; seuls les echanges POSTERIEURS a t produisent des
remplissages. La separation est structurelle, pas une convention de code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class Trade:
    """Un echange public : prix, taille, sens du PRENEUR."""

    ts: float
    price: float
    size: float
    taker_is_buy: bool


@dataclass
class RestingQuote:
    """Un ordre au repos, et la file devant lui."""

    price: float
    size: float
    is_bid: bool
    #: parts deja presentes a ce prix quand on arrive. On passe derriere.
    queue_ahead: float
    placed_ts: float
    filled: float = 0.0
    fills: List[Trade] = field(default_factory=list)

    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)


def _crosses(q: RestingQuote, t: Trade) -> bool:
    """L'echange atteint-il mon prix ?

    Un preneur VENDEUR frappe les bids : il me sert si mon bid est >= a son
    prix. Un preneur ACHETEUR leve les asks : il me sert si mon ask est <=.
    """
    if q.is_bid:
        return (not t.taker_is_buy) and t.price <= q.price
    return t.taker_is_buy and t.price >= q.price


def simulate(quote: RestingQuote, trades: Sequence[Trade]) -> RestingQuote:
    """Rejoue la bande contre un ordre au repos. Mute et rend `quote`.

    Seuls les echanges POSTERIEURS au placement comptent : rejouer un echange
    anterieur reviendrait a se faire remplir par le passe.
    """
    ahead = quote.queue_ahead
    for t in sorted(trades, key=lambda x: x.ts):
        if t.ts <= quote.placed_ts:
            continue
        if quote.remaining() <= 0:
            break
        if not _crosses(quote, t):
            continue
        vol = t.size
        if ahead > 0:                      # la file devant se sert d'abord
            consumed = min(ahead, vol)
            ahead -= consumed
            vol -= consumed
        if vol <= 0:
            continue
        got = min(vol, quote.remaining())
        if got > 0:
            quote.filled += got
            quote.fills.append(Trade(t.ts, t.price, got, t.taker_is_buy))
    return quote


def fills_per_day(quote: RestingQuote, window_seconds: float
                  ) -> Optional[float]:
    """Remplissages par jour, extrapoles depuis la fenetre observee.

    Rend None sous une heure d'observation. Extrapoler un flux de marche de
    prediction depuis quelques minutes produirait un nombre qui a l'air d'une
    mesure : ces marches passent des heures sans un seul echange, et une
    fenetre courte tombe systematiquement sur zero ou sur une rafale.
    """
    if window_seconds < 3_600.0:
        return None
    return len(quote.fills) * 86_400.0 / window_seconds


def filled_notional(quote: RestingQuote) -> float:
    return sum(f.price * f.size for f in quote.fills)
