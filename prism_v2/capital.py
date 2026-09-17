"""Capital reellement immobilise par une position, mesure et non declare.

LE PROBLEME QUE CE MODULE RESOUT. L'objectif du projet est

    PnL net / capital immobilise / temps

et le denominateur n'etait jamais calcule. Deux lectures opposees circulaient,
toutes deux fausses :

1. « entierement collateralise » — capital = notionnel, levier 1x. C'est un
   choix de financement, pas une contrainte. L'exchange ne l'exige pas.
2. « levier maximal affiche » — capital = notionnel / lever. C'est le levier
   du PREMIER palier seulement, et il ignore que la position doit survivre
   entre l'entree et la sortie, pas seulement a l'instant de l'entree.

Le capital reellement immobilise se situe entre les deux et se mesure :

    capital = notionnel x (marge initiale du palier + coussin de survie)

Le coussin de survie n'est pas un parametre de confort. C'est le mouvement
adverse que la jambe doit encaisser, sur la DUREE DE DETENTION, sans etre
liquidee. Il est lu dans les rendements observes, a un quantile declare.

POURQUOI JAMBE PAR JAMBE. Sur une paire delta-neutre inverse/lineaire, le PnL
combine est nul a la precision machine, mais les deux jambes sont margees dans
des DEVISES DIFFERENTES : l'inverse en coin, la lineaire en USDT. Le gain de
l'une ne peut pas empecher la liquidation de l'autre, sauf netting explicite
de l'exchange. Le coussin se calcule donc sur le mouvement de PRIX de chaque
jambe, pas sur le PnL net de la paire. Supposer l'inverse est exactement ce
qui fait exploser une position delta-neutre a fort levier.

CE QUE LE COUSSIN NE PEUT PAS FAIRE. Un echantillon de N jours ne montre pas
un mouvement qui n'arrive qu'une fois tous les 10 N jours. Le coussin mesure
est donc une BORNE INFERIEURE du coussin necessaire, et `horizon_reliable`
le dit explicitement plutot que de laisser croire le contraire.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from collections import deque
from typing import (Deque, Dict, List, Optional, Sequence,
                    Tuple)

from .contracts import usd_notional
from .instruments import InstrumentSpec
from .margin import MarginSchedule


@dataclass(frozen=True)
class SurvivalBuffer:
    """Coussin de survie mesure, et ce qu'il vaut."""

    fraction: float            # part du notionnel, ex. 0,05 = 5 %
    quantile: float            # quantile demande, ex. 0,999
    horizon_s: float           # duree de detention couverte
    n_windows: int             # fenetres observees
    sample_days: float
    reliable: bool             # l'echantillon peut-il porter ce quantile ?
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {"fraction": self.fraction, "quantile": self.quantile,
                "horizon_s": self.horizon_s, "n_windows": self.n_windows,
                "sample_days": self.sample_days, "reliable": self.reliable,
                "note": self.note}


def survival_buffer(prices: Sequence[Tuple[int, float]], horizon_s: float,
                    quantile: float = 0.999,
                    direction: int = 0) -> Optional[SurvivalBuffer]:
    """Mouvement adverse a `quantile` sur une fenetre de `horizon_s`.

    `prices` est une suite (timestamp_ms, prix) triee. `direction` vaut +1 si
    la jambe perd quand le prix MONTE (position courte), -1 si elle perd quand
    il baisse (position longue), 0 pour prendre le pire des deux sens — c'est
    le choix par defaut, et le plus prudent.

    Le quantile porte sur le mouvement MAXIMAL ATTEINT dans la fenetre, pas sur
    le mouvement de bout en bout : une liquidation se declenche au plus bas
    traverse, pas au prix de sortie. Confondre les deux sous-estime le coussin
    exactement dans le cas qui tue.

    None si l'echantillon ne contient aucune fenetre complete.
    """
    if not prices or horizon_s <= 0 or not 0.0 < quantile < 1.0:
        return None
    ts = [p[0] for p in prices]
    px = [p[1] for p in prices]
    span_days = (ts[-1] - ts[0]) / 86_400_000.0
    win_ms = int(horizon_s * 1000)
    # Maximum et minimum glissants en O(n) par deques monotones. Une boucle
    # naive serait en O(n x taille de fenetre) : sur 78 000 points a 250 ms et
    # une fenetre de 300 s, elle ne finit pas. La mesure serait la meme ; c'est
    # le temps de calcul qui interdirait de la faire.
    n_pts = len(ts)
    max_fwd = [0.0] * n_pts
    min_fwd = [0.0] * n_pts
    # On parcourt a rebours, en empilant l'indice courant A DROITE. Les
    # indices les plus GRANDS (donc les plus tardifs, ceux qui sortent de la
    # fenetre quand elle recule) se trouvent alors A GAUCHE : c'est de la
    # gauche qu'il faut evincer, et c'est a gauche que se lit l'extremum.
    # Evincer du mauvais cote donne un resultat plausible mais faux ; c'est ce
    # que `test_concorde_avec_la_force_brute` a attrape.
    dq_hi: Deque[int] = deque()
    dq_lo: Deque[int] = deque()
    for i in range(n_pts - 1, -1, -1):
        limit = ts[i] + win_ms
        while dq_hi and ts[dq_hi[0]] > limit:
            dq_hi.popleft()
        while dq_lo and ts[dq_lo[0]] > limit:
            dq_lo.popleft()
        while dq_hi and px[dq_hi[-1]] <= px[i]:
            dq_hi.pop()
        dq_hi.append(i)
        while dq_lo and px[dq_lo[-1]] >= px[i]:
            dq_lo.pop()
        dq_lo.append(i)
        max_fwd[i] = px[dq_hi[0]]
        min_fwd[i] = px[dq_lo[0]]
    worst: List[float] = []
    for i in range(n_pts):
        if px[i] <= 0 or ts[i] + win_ms > ts[-1]:
            continue
        up = (max_fwd[i] - px[i]) / px[i]
        down = (px[i] - min_fwd[i]) / px[i]
        if direction > 0:
            worst.append(up)
        elif direction < 0:
            worst.append(down)
        else:
            worst.append(max(up, down))
    if not worst:
        return None
    worst.sort()
    n = len(worst)
    idx = min(n - 1, int(math.ceil(quantile * n)) - 1)
    # Un quantile a 0,999 demande au moins 1 000 observations INDEPENDANTES.
    # Les fenetres glissantes se recouvrent : on compte les fenetres
    # disjointes, seules reellement independantes.
    independent = span_days * 86_400.0 / horizon_s
    reliable = independent >= 1.0 / (1.0 - quantile)
    note = ("" if reliable else
            f"echantillon trop court pour un quantile {quantile}: "
            f"{independent:.0f} fenetres disjointes, "
            f"{1.0/(1.0-quantile):.0f} necessaires — BORNE INFERIEURE")
    return SurvivalBuffer(fraction=worst[max(idx, 0)], quantile=quantile,
                          horizon_s=horizon_s, n_windows=n,
                          sample_days=span_days, reliable=reliable, note=note)


@dataclass(frozen=True)
class LegCapital:
    inst_id: str
    notional_usd: float
    initial_margin_usd: float
    buffer_usd: float
    capital_usd: float
    effective_leverage: float
    buffer: Optional[SurvivalBuffer]


def leg_capital(spec: InstrumentSpec, contracts: float, price: float,
                schedule: MarginSchedule,
                buffer: Optional[SurvivalBuffer]) -> Optional[LegCapital]:
    """Capital a bloquer sur UNE jambe pour ne pas etre liquide.

    None si la taille sort du bareme de l'exchange : la position n'existe pas.
    Un coussin absent n'est pas un coussin nul — la fonction rend None, parce
    qu'une position sans coussin connu n'a pas de cout en capital connu.
    """
    if buffer is None:
        return None
    im = schedule.initial_margin_usd(spec, contracts, price)
    if im is None:
        return None
    notion = usd_notional(spec, contracts, price)
    buf = notion * buffer.fraction
    cap = im + buf
    return LegCapital(inst_id=spec.inst_id, notional_usd=notion,
                      initial_margin_usd=im, buffer_usd=buf, capital_usd=cap,
                      effective_leverage=(notion / cap) if cap > 0 else 0.0,
                      buffer=buffer)


@dataclass(frozen=True)
class PositionCapital:
    legs: Sequence[LegCapital]
    capital_usd: float
    notional_usd: float
    effective_leverage: float
    netting_assumed: bool
    reliable: bool

    def bps_per_day_on_capital(self, bps_per_day_on_notional: float) -> float:
        """Traduit un flux exprime en bps du NOTIONNEL en bps du CAPITAL.

        C'est la conversion que le projet omettait : comparer un rendement
        sur notionnel a un seuil sur capital sous-estime la strategie du
        facteur de levier. La comparer sans coussin la surestime du meme
        facteur. Les deux erreurs sont ici impossibles.
        """
        return bps_per_day_on_notional * self.effective_leverage


def position_capital(legs: Sequence[LegCapital], netting: bool = False
                     ) -> Optional[PositionCapital]:
    """Capital d'une position a plusieurs jambes.

    `netting=False` additionne les capitaux : c'est la lecture qui vaut quand
    les jambes sont margees dans des devises differentes et que l'exchange ne
    les compense pas. C'est le defaut, parce que le netting est une faveur de
    l'exchange, pas un droit.

    `netting=True` prend le maximum : la compensation parfaite, borne haute
    jamais atteinte en pratique. Elle n'est jamais le defaut et doit etre
    demandee explicitement.
    """
    legs = [l for l in legs if l is not None]
    if not legs:
        return None
    cap = max(l.capital_usd for l in legs) if netting \
        else sum(l.capital_usd for l in legs)
    # Notionnel de la POSITION, pas somme des jambes : sur une paire couverte
    # les deux jambes portent le meme notionnel et l'exposition est celle d'un
    # seul cote. Prendre le maximum est exact pour une paire equilibree et
    # prudent partout ailleurs — cela ne gonfle jamais le rendement.
    notion = max(l.notional_usd for l in legs)
    return PositionCapital(
        legs=legs, capital_usd=cap, notional_usd=notion,
        effective_leverage=(notion / cap) if cap > 0 else 0.0,
        netting_assumed=netting,
        reliable=all(l.buffer is not None and l.buffer.reliable for l in legs))


# ══════════════════════════════════════════════════════════════════════════
# PERTE EXACTE D'UNE JAMBE, DANS SA PROPRE DEVISE DE MARGE
# ══════════════════════════════════════════════════════════════════════════
def loss_fraction(spec: InstrumentSpec, direction: "Direction",
                  entry_price: float, worst_price: float) -> float:
    """Perte d'une jambe au pire prix traverse, en part de sa marge.

    Pourquoi ce n'est PAS le mouvement de prix. La liquidation se juge dans la
    devise de REGLEMENT de la jambe, et sur un contrat inverse cette devise
    est le coin. La relation n'y est pas lineaire :

      - short inverse, prix x(1+u)  ->  perte = u/(1+u), BORNEE par 1 ;
      - long  inverse, prix x(1-d)  ->  perte = d/(1-d), NON bornee ;
      - lineaire                    ->  perte = mouvement de prix, exactement.

    Traiter les trois comme « le mouvement de prix » surestime le coussin d'un
    cote et le SOUS-ESTIME de l'autre — c'est-a-dire precisement du cote qui
    liquide. La formule officielle du type de contrat est donc utilisee telle
    quelle, via `contracts.pnl`.

    Retourne 0,0 si le mouvement est favorable.
    """
    from .contracts import pnl as _pnl
    r = _pnl(spec, direction, 1.0, entry_price, worst_price)
    if r.pnl_settle_ccy >= 0:
        return 0.0
    return -r.return_bps_settle / 10_000.0


def buffer_for_leg(spec: InstrumentSpec, direction: "Direction",
                   prices: Sequence[Tuple[int, float]], horizon_s: float,
                   quantile: float = 0.99) -> Optional[SurvivalBuffer]:
    """Coussin d'une jambe, exprime dans SA devise de marge.

    Deux etapes distinctes, volontairement separees :
      1. quel mouvement de PRIX adverse faut-il encaisser ? — mesure dans les
         prix, au quantile demande ;
      2. que coute ce mouvement DANS LA DEVISE DE MARGE de la jambe ? — donne
         par la formule officielle du contrat, non lineaire sur un inverse.

    Le sens adverse est deduit du sens de la position, jamais demande a
    l'appelant : se tromper dessus ne leve aucune erreur et divise le coussin.
    """
    from .core_types import Direction as _D
    adverse = -1 if direction is _D.LONG else 1     # long : la baisse fait mal
    b = survival_buffer(prices, horizon_s, quantile, direction=adverse)
    if b is None or not prices:
        return b
    entry = prices[0][1]
    worst = entry * (1.0 + adverse * b.fraction)
    return SurvivalBuffer(
        fraction=loss_fraction(spec, direction, entry, worst),
        quantile=b.quantile, horizon_s=b.horizon_s, n_windows=b.n_windows,
        sample_days=b.sample_days, reliable=b.reliable, note=b.note)
