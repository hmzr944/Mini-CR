"""Chercher des DISTRIBUTIONS, non plus des moyennes. Extension minimale.

POURQUOI CE MODULE EXISTE, ET CE QU'IL CORRIGE. Les 21 plafonds du registre
sont tous des moyennes ou des bornes. `flux de liquidation` y vaut
-8,03 bps/jour sur n = 399. Un mecanisme dont tout le gain serait concentre
dans cinq episodes sur 399 produirait exactement ce chiffre, et serait declare
mort. **Le registre est structurellement aveugle aux queues.**

Ce n'est pas une faute de mesure : une moyenne repond correctement a la
question qu'on lui pose. C'est que la question etait incomplete.

CONSEQUENCE SUR LA REGLE DE REOUVERTURE. `KillRegistry.revive` exige de
DEPASSER le plafond enregistre. Mais ce plafond repond a « quelle est la
moyenne ? ». Mesurer la QUEUE de la meme famille n'est donc pas re-optimiser
une piste morte : c'est poser une question que le plafond n'a jamais adressee.
La classe de mesure ci-dessous rend les deux incomparables par construction,
exactement comme CAPITAL et NOTIONNEL le sont deja.

LES QUATRE NIVEAUX, ET POURQUOI LES CONFONDRE EST LA FAUTE CENTRALE. J'ai
ecrit « il faut 390 bps » en parlant d'un centile de MOUVEMENT, comme si
c'etait un PnL. Ce sont quatre grandeurs distinctes, decroissantes, et chaque
passage de l'une a l'autre coute :

    1. OBSERVE     le mouvement de prix, mesure depuis l'evenement
    2. CAPTURABLE  le mouvement restant APRES le delai de reaction reel
    3. NET         apres frais, spread traverse, et sortie
    4. PORTEFEUILLE  le rendement agrege, apres frequence et capital immobilise

Un centile eleve au niveau 1 ne dit RIEN des niveaux 2 a 4. Le type
`MeasureLevel` rend leur melange impossible.

CE QUE CE MODULE NE FAIT PAS. Il ne detecte aucun evenement et n'execute rien.
Il fournit les primitives de distribution et les gardes de risque, declarees
avant toute lecture de donnee.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: CLASSE DE MESURE. Une moyenne et une queue ne sont pas comparables, et les
#: melanger produirait la faute symetrique de celle du registre : declarer une
#: famille prometteuse parce qu'elle a quelques valeurs extremes.
MEAN = "MOYENNE"
TAIL = "QUEUE"
_MEASURE_CLASSES = (MEAN, TAIL)

#: NIVEAU DE MESURE. Voir le docstring : les confondre est la faute centrale.
OBSERVED = "OBSERVE"           # mouvement de prix brut
CAPTURABLE = "CAPTURABLE"      # apres le delai de reaction reel
NET = "NET"                    # apres frais, spread et sortie
PORTFOLIO_RETURN = "PORTEFEUILLE"   # apres frequence et capital immobilise
_LEVELS = (OBSERVED, CAPTURABLE, NET, PORTFOLIO_RETURN)

#: STATUT D'UNE FAMILLE A QUEUE. Les deux objets que le mandat de recherche
#: demande de tenir separes.
TAIL_DISCOVERY = "DECOUVERTE_DE_QUEUE"      # la queue existe-t-elle ?
CAPTURE_VALIDATION = "VALIDATION_DE_CAPTURE"  # etait-elle capturable ?
_STATUSES = (TAIL_DISCOVERY, CAPTURE_VALIDATION)

#: Quantiles rapportes. FIXES ICI, avant toute lecture, pour qu'aucun ne soit
#: choisi apres coup parce qu'il raconte une meilleure histoire.
REPORTED_QUANTILES: Tuple[float, ...] = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)

#: Part du gain total venant des meilleurs episodes. Au-dela de ce seuil, le
#: mecanisme est une loterie et non une strategie : son esperance depend de
#: quelques tirages, donc son intervalle de confiance est ingouvernable a
#: 1 000 EUR. Seuil declare, pas mesure.
MAX_GAIN_CONCENTRATION = 0.60

#: Fraction d'episodes definissant « les meilleurs » pour la concentration.
CONCENTRATION_TOP_FRACTION = 0.05


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    """Quantile par interpolation lineaire. None sous deux observations.

    Implemente ici plutot qu'importe : le depot n'a aucune dependance tierce,
    et une garde d'architecture l'impose.
    """
    if not 0.0 <= q <= 1.0:
        raise ValueError("un quantile se situe entre 0 et 1")
    xs = sorted(values)
    if len(xs) < 2:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


@dataclass(frozen=True)
class Event:
    """Un evenement horodate, avant tout regroupement."""

    ts: float
    instrument: str
    size_usd: float = 0.0


@dataclass(frozen=True)
class Episode:
    """Des evenements PROCHES, regroupes en un seul episode de marche.

    POURQUOI LE REGROUPEMENT EST OBLIGATOIRE. Une cascade de liquidations
    produit des dizaines d'evenements en quelques secondes, tous suivis du
    MEME mouvement de prix. Les compter separement multiplierait un seul
    mouvement par le nombre de ses declencheurs, gonflerait n, et ferait
    passer un echantillon de 5 mouvements pour 399 observations
    independantes. C'est la faute qui rend un resultat spectaculaire et faux.
    """

    start_ts: float
    end_ts: float
    n_events: int
    instrument: str
    total_size_usd: float


def group_into_episodes(events: Sequence[Event], gap_s: float) -> List[Episode]:
    """Regroupe par instrument les evenements separes de moins de `gap_s`.

    `gap_s` est declare par l'appelant AVANT lecture, et il est le parametre
    le plus sensible du protocole : trop court, une cascade compte pour dix ;
    trop long, deux episodes distincts fusionnent et leur mouvement se
    compense.
    """
    if gap_s <= 0:
        raise ValueError("un ecart de regroupement est strictement positif")
    par_inst: Dict[str, List[Event]] = {}
    for e in events:
        par_inst.setdefault(e.instrument, []).append(e)

    out: List[Episode] = []
    for inst, evs in par_inst.items():
        evs = sorted(evs, key=lambda x: x.ts)
        bloc = [evs[0]]
        for e in evs[1:]:
            if e.ts - bloc[-1].ts <= gap_s:
                bloc.append(e)
            else:
                out.append(_episode(bloc, inst))
                bloc = [e]
        out.append(_episode(bloc, inst))
    return sorted(out, key=lambda x: x.start_ts)


def _episode(bloc: Sequence[Event], inst: str) -> Episode:
    return Episode(bloc[0].ts, bloc[-1].ts, len(bloc), inst,
                   sum(e.size_usd for e in bloc))


@dataclass(frozen=True)
class Outcome:
    """Le resultat d'UN episode, a un niveau de mesure declare."""

    episode: Episode
    level: str
    bps: float

    def __post_init__(self):
        if self.level not in _LEVELS:
            raise ValueError(f"niveau de mesure inconnu : {self.level}")


def capturable_bps(price_at_trigger: float, price_at_entry: float,
                   price_at_exit: float, is_long: bool) -> float:
    """Mouvement restant a partir du prix d'ENTREE, pas du declencheur.

    LE POINT QUE CE MODULE EXISTE POUR IMPOSER. Mesurer depuis le prix au
    declenchement suppose une execution instantanee. La latence mesuree depuis
    cet environnement vaut 420 ms, et une dislocation peut se resorber dans
    cette fenetre : le mouvement OBSERVE serait alors entierement reel, et
    entierement inaccessible.

    `price_at_trigger` n'entre donc dans aucun calcul — il n'est present que
    pour rendre l'omission visible a la lecture. Le gain part de l'entree.
    """
    if price_at_entry <= 0:
        raise ValueError("prix d'entree non strictement positif")
    move = (price_at_exit - price_at_entry) if is_long else (price_at_entry - price_at_exit)
    return 1e4 * move / price_at_entry


def net_bps(captured: float, cost_bps: float) -> float:
    """Passe du CAPTURABLE au NET. Le cout est additif et toujours positif."""
    if cost_bps < 0:
        raise ValueError("un cout d'aller-retour ne peut pas etre negatif")
    return captured - cost_bps


@dataclass
class TailProfile:
    """La distribution d'une famille, et ce qu'elle autorise a conclure."""

    family: str
    level: str
    outcomes_bps: List[float]
    gap_s: float
    measure_class: str = TAIL

    def __post_init__(self):
        if self.level not in _LEVELS:
            raise ValueError(f"niveau de mesure inconnu : {self.level}")
        if self.measure_class not in _MEASURE_CLASSES:
            raise ValueError("classe de mesure inconnue")

    @property
    def n(self) -> int:
        return len(self.outcomes_bps)

    def mean(self) -> Optional[float]:
        return st.fmean(self.outcomes_bps) if self.outcomes_bps else None

    def quantiles(self) -> Dict[float, Optional[float]]:
        return {q: quantile(self.outcomes_bps, q) for q in REPORTED_QUANTILES}

    def worst(self) -> Optional[float]:
        return min(self.outcomes_bps) if self.outcomes_bps else None

    def gain_concentration(self,
                           top: float = CONCENTRATION_TOP_FRACTION
                           ) -> Optional[float]:
        """Part du gain TOTAL venant des `top` meilleurs episodes.

        Proche de 1 : le mecanisme est une loterie. Son esperance repose sur
        quelques tirages, donc elle n'est pas gouvernable a 1 000 EUR, meme si
        elle est positive. Rend None si le gain total est nul ou negatif — une
        concentration n'a alors aucun sens, et rendre un nombre ici
        fabriquerait une propriete inexistante.
        """
        gains = [x for x in self.outcomes_bps if x > 0]
        total = sum(gains)
        if total <= 0:
            return None
        k = max(1, int(round(top * self.n)))
        meilleurs = sorted(gains, reverse=True)[:k]
        return sum(meilleurs) / total


@dataclass(frozen=True)
class RiskLimits:
    """Bornes de perte, DECLAREES AVANT lecture. Remplace la garde ambigue.

    La version precedente disait « si la queue gauche depasse la queue droite
    en magnitude, on ferme ». Ce n'etait pas une regle : ni quantile, ni
    unite, ni traitement des extremes. Une queue gauche plus RARE peut
    detruire le capital tout en etant « plus petite » a un quantile choisi.
    """

    #: Perte maximale acceptee sur UN episode, en bps du capital engage.
    max_loss_per_episode_bps: float
    #: Perte cumulee maximale sur l'ensemble de l'echantillon, en bps.
    max_cumulative_loss_bps: float
    #: Quantile bas surveille. Declare, pas choisi apres coup.
    left_quantile: float = 0.05

    def __post_init__(self):
        if self.max_loss_per_episode_bps <= 0 or self.max_cumulative_loss_bps <= 0:
            raise ValueError("une borne de perte est strictement positive")
        if not 0.0 < self.left_quantile < 0.5:
            raise ValueError("le quantile bas se situe strictement entre 0 et 0,5")

    def breaches(self, profile: TailProfile) -> List[str]:
        """Les bornes franchies. Liste VIDE = aucune, jamais un booleen seul.

        Trois controles distincts, parce qu'un seul ne suffit pas :
          - le quantile bas, qui decrit la perte ordinaire d'un mauvais jour ;
          - le PIRE episode, qui decrit la perte qui ferme un compte ;
          - la perte CUMULEE, qui decrit l'erosion sans episode spectaculaire.
        """
        out: List[str] = []
        ql = quantile(profile.outcomes_bps, self.left_quantile)
        if ql is not None and ql < -self.max_loss_per_episode_bps:
            out.append(f"quantile {self.left_quantile:.0%} = {ql:.1f} bps "
                       f"sous la borne {-self.max_loss_per_episode_bps:.1f}")
        pire = profile.worst()
        if pire is not None and pire < -self.max_loss_per_episode_bps:
            out.append(f"pire episode = {pire:.1f} bps sous la borne "
                       f"{-self.max_loss_per_episode_bps:.1f}")
        pertes = sum(x for x in profile.outcomes_bps if x < 0)
        if pertes < -self.max_cumulative_loss_bps:
            out.append(f"perte cumulee = {pertes:.1f} bps sous la borne "
                       f"{-self.max_cumulative_loss_bps:.1f}")
        return out


@dataclass(frozen=True)
class TailVerdict:
    """Le verdict, et il ne peut JAMAIS valoir « rentable » a lui seul."""

    status: str
    passes: bool
    reasons: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.status not in _STATUSES:
            raise ValueError(f"statut inconnu : {self.status}")


def evaluate_tail(profile: TailProfile, limits: RiskLimits,
                  min_n: int, min_net_bps: float) -> TailVerdict:
    """Evalue une queue. Ordre des controles : taille, risque, puis economie.

    L'ORDRE N'EST PAS ARBITRAIRE. Un echantillon trop petit rend tout le reste
    illisible ; une famille qui ruine le compte n'a pas besoin d'etre
    rentable ; l'economie ne se lit qu'en dernier. C'est le meme ordre que
    `economics.evaluate` applique deja (qualite -> economie -> capacite ->
    risque), adapte au fait qu'ici le risque precede l'economie parce que la
    queue gauche est precisement ce qu'on cherche a ne pas decouvrir trop tard.

    Un profil mesure au niveau OBSERVE ou CAPTURABLE ne peut JAMAIS passer :
    seul le NET porte une conclusion economique. C'est la garde qui empeche de
    relire un centile de mouvement comme un profit.
    """
    raisons: List[str] = []
    if profile.n < min_n:
        raisons.append(f"n = {profile.n} sous le minimum {min_n}")
    raisons.extend(limits.breaches(profile))

    if profile.level != NET:
        raisons.append(f"niveau {profile.level} : une conclusion economique "
                       f"exige le niveau {NET}")
        return TailVerdict(TAIL_DISCOVERY, False, raisons)

    conc = profile.gain_concentration()
    if conc is not None and conc > MAX_GAIN_CONCENTRATION:
        raisons.append(f"concentration {conc:.0%} au-dessus de "
                       f"{MAX_GAIN_CONCENTRATION:.0%} : loterie, pas strategie")
    q95 = quantile(profile.outcomes_bps, 0.95)
    if q95 is None or q95 < min_net_bps:
        raisons.append(f"quantile 95 % = "
                       f"{'INCONNU' if q95 is None else f'{q95:.1f} bps'} "
                       f"sous le minimum {min_net_bps:.1f}")
    moy = profile.mean()
    if moy is None or moy <= 0:
        raisons.append(f"esperance = "
                       f"{'INCONNUE' if moy is None else f'{moy:.1f} bps'} "
                       f"non positive : une queue droite ne suffit pas")

    return TailVerdict(CAPTURE_VALIDATION, not raisons, raisons)
