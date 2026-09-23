"""Economie d'un fill PASSIF sur un perpetuel, mesuree depuis la bande seule.

    PnL du fill passif = demi-spread encaisse
                       + derive du mid apres execution   (le MARKOUT)
                       - frais maker   (NEGATIF si la venue paie un rebate)

Le demi-spread se lit, le frais se lit, le markout se mesure. C'est la seule
inconnue, et c'est elle qui a ferme la famille maker OKX.

CE QUE LA BANDE PERMET, ET CE QU'ELLE NE PERMET PAS. On n'a pas l'historique
du carnet, seulement les echanges. Un prix d'echange n'est PAS un mid : il
alterne entre bid et ask, et cette alternance est precisement le piege que le
balayage large du projet avait deja documente (le rho des alts vient du rebond
bid-ask). Mesurer un markout sur des prix d'echange bruts fabriquerait donc
une derive qui n'existe pas.

D'ou l'estimateur a DEUX COTES : Backpack publie le sens du preneur, donc un
echange preneur-vendeur s'est fait AU BID et un echange preneur-acheteur AU
ASK. Le mid s'estime par la demi-somme des deux plus recents, chacun de son
cote. L'alternance est ainsi neutralisee au lieu d'etre subie.

HYPOTHESE DE FILE, VOLONTAIREMENT GENEREUSE — et declaree comme telle. On
suppose la PREMIERE place : tout echange qui atteint le prix nous execute.
C'est l'hypothese la plus favorable au maker, elle lui donne tous les fills, y
compris les benins. Un net NEGATIF sous cette hypothese est donc decisif ; un
net positif n'est qu'une BORNE SUPERIEURE, car la vraie file degraderait le
melange. On prefere une borne dont on connait le sens.

AUCUN INCONNU NE COMPTE POUR ZERO. Si le mid ne peut pas etre estime a
l'horizon demande — pas d'echange des deux cotes dans la fenetre de fraicheur
— le markout de ce fill est INCONNU et le fill est compte comme tel, jamais
assimile a une derive nulle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from prism_v2.subsidy.tape import Trade

#: Horizons de markout, en secondes. Ceux de `markout.py` (1 a 30 s) visent un
#: carnet dense ; les marches minces vises ici passent des minutes sans un
#: echange, et un horizon court y retomberait systematiquement sur INCONNU.
MARKOUT_HORIZONS_S: Tuple[float, ...] = (60.0, 300.0, 1_800.0)

#: Au-dela de cet age, une observation d'un cote n'estime plus le mid courant.
#: Borne choisie, pas mesuree : elle est declaree ici plutot qu'enfouie dans un
#: appel, et tout resultat en depend.
MAX_SIDE_STALENESS_S = 3_600.0

#: Distance maximale au mid a laquelle un ordre compte pour le score de
#: liquidite du programme Backpack (documentation publique du programme).
MM_LIQUIDITY_BAND_BPS = 100.0


@dataclass(frozen=True)
class TwoSidedMid:
    """Un mid ESTIME a partir d'un echange de chaque cote."""

    bid_px: float
    ask_px: float
    bid_ts: float
    ask_ts: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid_px + self.ask_px)

    @property
    def spread_bps(self) -> float:
        return 1e4 * (self.ask_px - self.bid_px) / self.mid


def estimate_mid(trades: Sequence[Trade], at_ts: float,
                 max_staleness_s: float = MAX_SIDE_STALENESS_S
                 ) -> Optional[TwoSidedMid]:
    """Mid estime a `at_ts` depuis le dernier echange de CHAQUE cote.

    Rend None — jamais une valeur de repli — dans trois cas ou l'estimation
    n'aurait pas de sens :

      - un cote n'a aucun echange avant `at_ts` ;
      - un cote est plus vieux que `max_staleness_s` ;
      - les deux observations se croisent (bid > ask), ce qui signale que le
        prix a bouge entre elles et que leur demi-somme ne decrit aucun carnet.

    Seuls les echanges ANTERIEURS OU EGAUX a `at_ts` sont lus : aucune donnee
    future n'entre dans une estimation.
    """
    bid: Optional[Trade] = None
    ask: Optional[Trade] = None
    for t in sorted(trades, key=lambda x: x.ts):
        if t.ts > at_ts:
            break
        if t.taker_is_buy:          # preneur acheteur : il a leve l'ASK
            ask = t
        else:                       # preneur vendeur : il a frappe le BID
            bid = t
    if bid is None or ask is None:
        return None
    if at_ts - bid.ts > max_staleness_s or at_ts - ask.ts > max_staleness_s:
        return None
    if bid.price > ask.price:
        return None
    return TwoSidedMid(bid.price, ask.price, bid.ts, ask.ts)


#: Rapport maximal tolere entre le spread IMPLIQUE par l'estimateur a deux
#: cotes et le spread LU au carnet. Au-dela, l'estimateur ne decrit plus un
#: carnet mais l'amplitude de prix entre deux echanges eloignes dans le temps.
MAX_IMPLIED_TO_LIVE_SPREAD_RATIO = 1.5


def tape_mid_is_fit_for_level(implied_spread_bps: float,
                              live_spread_bps: float,
                              max_ratio: float = MAX_IMPLIED_TO_LIVE_SPREAD_RATIO
                              ) -> bool:
    """L'estimateur a deux cotes peut-il servir a mesurer un NIVEAU ?

    POURQUOI CETTE GARDE EXISTE. Le premier passage de ce module rapportait un
    demi-spread median de 22,93 bps sur W_USDC_PERP, dont le carnet cotait
    1,72 bps au meme moment. Le chiffre n'etait pas un spread : les deux
    echanges servant a estimer le mid peuvent etre separes d'une heure, et
    leur demi-somme absorbe alors toute la derive de prix survenue entre eux.
    Le « demi-spread encaisse » suivait la fenetre de fraicheur — 22,93 bps a
    3 600 s, 9,08 a 300 s, 3,42 a 60 s — ce qui est la signature d'un
    artefact, pas d'une mesure.

    La garde est donc structurelle : un NIVEAU (le demi-spread) ne se tire pas
    de la bande, il se LIT au carnet. Seule une DERIVE (le markout) reste
    tirable de la bande, parce que le biais de niveau se soustrait en partie
    entre deux estimations successives — en partie seulement, et cela reste
    une limite declaree, pas une garantie.
    """
    if live_spread_bps <= 0:
        return False
    return implied_spread_bps <= max_ratio * live_spread_bps


def half_spread_at_touch_bps(book_spread_bps: float) -> float:
    """Demi-spread encaisse par une cotation AU TOUCHER, depuis un carnet LU.

    C'est la seule facon honnete d'obtenir ce terme sans historique de carnet.
    Elle a son propre defaut, declare : le carnet est lu MAINTENANT alors que
    la bande couvre la semaine passee. Les deux ne decrivent pas le meme
    instant, et leur combinaison est une approximation, pas une mesure jointe.
    """
    if book_spread_bps <= 0:
        raise ValueError("spread de carnet non strictement positif")
    return 0.5 * book_spread_bps


def half_spread_earned_bps(fill_price: float, passive_is_buy: bool,
                           mid_before: float) -> float:
    """Ce que le maker encaisse a l'execution, en bps du prix de fill.

    Un achat passif se fait AU BID, donc sous le mid : l'ecart est encaisse.
    Une vente passive se fait AU ASK, donc au-dessus. Le signe est porte par
    le sens, pas par une convention d'appelant.
    """
    if fill_price <= 0:
        raise ValueError("prix de fill non strictement positif")
    edge = (mid_before - fill_price) if passive_is_buy else (fill_price - mid_before)
    return 1e4 * edge / fill_price


def markout_bps(fill_price: float, passive_is_buy: bool,
                mid_before: float, mid_after: float) -> float:
    """Derive du MID entre l'execution et l'horizon, dans le sens de la position.

    LE MID, PAS LE PRIX DE FILL. Mesurer la derive depuis le prix de fill
    donnerait `mid_after - fill`, c'est-a-dire le PnL TOTAL du fill — lequel
    contient deja le demi-spread. L'additionner au demi-spread le compterait
    deux fois, et gonflerait tout marche stationnaire d'un demi-spread
    fantome. La decomposition n'est valide que si les deux termes sont
    disjoints :

        demi-spread = mid_avant - fill        (ce que la cotation encaisse)
        markout     = mid_apres - mid_avant   (ce que le marche reprend)
        somme       = mid_apres - fill        (le PnL du fill)

    Negative = adverse selection : le marche est parti contre le fill. C'est
    le signe attendu, et toute la question est son amplitude devant le
    demi-spread encaisse.

    Les deux termes sont exprimes en bps du PRIX DE FILL, seul denominateur
    commun qui rende leur somme lisible comme un PnL.
    """
    if fill_price <= 0:
        raise ValueError("prix de fill non strictement positif")
    drift = (mid_after - mid_before) if passive_is_buy else (mid_before - mid_after)
    return 1e4 * drift / fill_price


def breakeven_maker_fee_bps(half_spread: float, markout: float) -> float:
    """Frais maker maximal que le fill supporte avant de devenir perdant.

    POURQUOI CETTE FORME plutot qu'un net. Le bareme maker de l'entite EEA et
    le rebate du programme de market making ne sont PAS publics au niveau
    requis. Calculer un net exigerait de supposer ce taux ; on rend donc la
    CONDITION a la place du resultat, comme `conditions.py` le fait pour
    l'edge taker.

    Valeur positive : le fill supporte un frais jusqu'a ce niveau.
    Valeur negative : le fill est perdant meme a frais nul, et il faut un
    REBATE d'au moins cette magnitude pour qu'il devienne neutre.
    """
    return half_spread + markout


@dataclass(frozen=True)
class PassiveFill:
    """Un fill passif simule, et ce que la bande dit de sa suite."""

    ts: float
    price: float
    size: float
    passive_is_buy: bool
    half_spread_bps: float
    #: horizon (s) -> markout en bps. Un horizon absent est INCONNU, pas nul.
    markout_bps: dict

    def breakeven_fee_bps(self, horizon_s: float) -> Optional[float]:
        mo = self.markout_bps.get(horizon_s)
        if mo is None:
            return None
        return breakeven_maker_fee_bps(self.half_spread_bps, mo)


def simulate_passive_fills(trades: Sequence[Trade],
                           horizons_s: Sequence[float] = MARKOUT_HORIZONS_S,
                           max_staleness_s: float = MAX_SIDE_STALENESS_S
                           ) -> List[PassiveFill]:
    """Chaque echange de la bande devient un fill passif de l'autre cote.

    Un preneur VENDEUR a frappe un bid : le maker de ce bid a ACHETE. Un
    preneur ACHETEUR a leve un ask : le maker a VENDU. On se met a la place de
    ce maker, sur toute la bande, sous l'hypothese de premiere place declaree
    en tete de module.

    Le mid AVANT le fill est estime sur les echanges strictement anterieurs :
    inclure l'echange qui nous execute contaminerait le demi-spread par le
    prix de notre propre execution.
    """
    ordered = sorted(trades, key=lambda x: x.ts)
    out: List[PassiveFill] = []
    for i, t in enumerate(ordered):
        before = ordered[:i]
        mid_before = estimate_mid(before, t.ts, max_staleness_s)
        if mid_before is None:
            continue
        passive_is_buy = not t.taker_is_buy
        hs = half_spread_earned_bps(t.price, passive_is_buy, mid_before.mid)
        mo = {}
        for h in horizons_s:
            m = estimate_mid(ordered, t.ts + h, max_staleness_s)
            if m is None:
                continue
            mo[h] = markout_bps(t.price, passive_is_buy, mid_before.mid, m.mid)
        out.append(PassiveFill(t.ts, t.price, t.size, passive_is_buy, hs, mo))
    return out


def excess_markout_bps(markout: float, unconditional_drift: float) -> float:
    """Markout NET de la derive que le marche avait de toute facon.

    POURQUOI CETTE FONCTION EXISTE. Une mesure a un seul cote — n'examiner que
    les achats passifs, par exemple — confond l'adverse selection avec la
    DIRECTION du marche pendant la fenetre. Sur les 75 min collectees, le mid
    a derive de +133,8 bps sur NEAR et +142,2 sur kBONK : un acheteur passif y
    gagnait sans aucune competence, simplement parce que tout montait.

    Le chiffre brut le montrait : markout +1,18 bps sur NEAR, ce qui ressemble
    a l'absence d'adverse selection. Mais la derive INCONDITIONNELLE a 60 s,
    mesuree a chaque instantane sans aucun fill, valait +1,36. L'acheteur
    passif faisait donc MOINS BIEN que ne rien decider du tout : l'adverse
    selection etait bien la, masquee par une fenetre haussiere.

    Une mesure a DEUX cotes annule cette derive par construction, puisque les
    achats et les ventes la subissent en sens inverse. C'est pourquoi le rejeu
    a deux cotes donnait un resultat negatif la ou l'analyse a un seul cote
    semblait positive. Quand un seul cote est examine, ce retranchement n'est
    pas une option : c'est la correction sans laquelle le chiffre mesure une
    tendance et non un mecanisme.
    """
    return markout - unconditional_drift
