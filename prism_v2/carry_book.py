"""Construction du livre de carry inverse/lineaire -- meme sous-jacent, OKX.

MECANISME. `X-USD-SWAP` (perpetuel INVERSE, marge en coin) et `X-USDT-SWAP`
(perpetuel LINEAIRE, marge en USDT) portent le meme sous-jacent mais ont deux
carnets et deux clienteles distinctes : le lineaire est le defaut du retail a
effet de levier, l'inverse exige de detenir le coin en collateral. Deux demandes
de levier differentes produisent deux taux de funding differents. On encaisse
l'ecart en etant short la jambe qui paie et long celle qui recoit.

Le PnL prix des deux jambes s'annule exactement (le PnL USD de l'inverse est
lineaire en P_sortie/P_entree, comme celui du lineaire). Il ne reste que le
funding differentiel, et un RESIDU : le basis USD/USDT, qui est la seule chose
capable de liquider le livre.

CE QUE CE MODULE NE FAIT PAS. Il ne passe aucun ordre, n'ouvre aucune
connexion authentifiee et ne lit aucune cle. Il construit un livre cible et
son economie ; l'execution est un autre probleme, et elle n'est pas resolue
(cf. LIMITES, en bas).

LA REGLE DE SELECTION EST DECLAREE ICI, AVANT SON RESULTAT. Elle n'a pas ete
ajustee apres avoir vu le rendement du livre ; toute revision doit etre datee
et justifiee dans l'historique git, faute de quoi ce module retombe dans le
defaut qui a tue V33 (14 parametres choisis sur le resultat).
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Sequence

# ─────────────────────────────── Critères, déclarés ──────────────────────────

#: Pire dérive adverse tolérée du résidu sur 14 jours, en bps de notionnel.
#: Au-delà, la paire ne peut pas être tenue à un levier utile : le résidu seul
#: consomme le coussin. Mesuré sur l'historique de bougies disponible.
MAX_RESIDUAL_DRIFT_BPS = 250.0

#: t minimal du différentiel de funding. 3,0 est un seuil conventionnel, choisi
#: avant la mesure et non ajusté ensuite.
MIN_T_STAT = 3.0

#: CORRIGE le 21/09/2026 (cf. CORRECTION_CAPACITE.md).
#:
#: Le critere precedent exigeait mediane/moyenne >= 0,30 sur le differentiel
#: PAR REGLEMENT. C'etait la mauvaise grandeur : la position se tient N jours,
#: soit 3N reglements, et la loi des grands nombres efface a cet horizon
#: exactement l'irregularite que ce critere penalisait. Mesure, il s'est revele
#: ANTI-CORRELE avec ce qui compte -- il rejetait ADA, DOT, SUI, UNI (3 blocs de
#: 30 j positifs sur 3) et retenait ETH, SOL (2 sur 3).
#:
#: Le critere correct porte sur des BLOCS DISJOINTS a l'horizon de detention :
#: tous doivent etre positifs. Un bloc = une observation independante.
MIN_POSITIVE_BLOCK_RATIO = 1.0
MIN_BLOCKS = 3

#: CORRIGE le 21/09/2026 (cf. CORRECTION_CAPACITE.md).
#:
#: La capacite etait plafonnee a 25 % de la profondeur a 5 niveaux. C'est le bon
#: critere pour un ordre qui doit passer MAINTENANT ; ce n'en est pas un pour une
#: position tenue des semaines, qu'on entre sur plusieurs heures. Mesure, le
#: volume 24 h vaut 45x a 9 875x la profondeur instantanee.
#:
#: On retient donc une PARTICIPATION au volume echange. 1 % est conservateur
#: pour une entree patiente et sans urgence.
VOLUME_PARTICIPATION = 0.01

#: Volume 24 h minimal, en USD, sur la jambe CONTRAIGNANTE. En dessous, meme
#: une entree patiente represente une part deraisonnable du flux.
MIN_VOLUME_USD = 200_000.0

#: Nombre minimal de règlements de funding communs aux deux jambes.
MIN_SETTLEMENTS = 200

#: Nombre minimal d'heures de bougies pour mesurer le résidu. Une paire trop
#: jeune n'a pas traversé assez de régimes pour que sa queue soit informative.
MIN_RESIDUAL_HOURS = 2_000

#: Facteur de sécurité exigé entre le coussin de liquidation et la pire dérive
#: adverse observée du résidu. 3x : l'échantillon ne contient qu'une cascade
#: (10/10/2025), donc la pire dérive observée est une borne basse du possible.
SAFETY_FACTOR = 3.0

#: Coût d'aller-retour, en bps de notionnel, 4 jambes.
#: TAKER : 4 x 5 bps de frais + les spreads traversés (ajoutés par paire).
#: MAKER : 4 x 2 bps de frais, demi-spread encaissé supposé ENTIEREMENT
#: neutralisé par la sélection adverse — hypothèse conservatrice, conforme à ce
#: que le dépôt a mesuré (SCAN_DONNEES.md, mesure 2).
TAKER_FEE_BPS, MAKER_FEE_BPS = 5.0, 2.0


# ─────────────────────────────── Types ───────────────────────────────────────

@dataclass(frozen=True)
class Pair:
    """Une paire inverse/linéaire, avec tout ce qui décide de son sort."""
    coin: str
    diff_bps_per_settlement: Sequence[float]   #: différentiel payé, par règlement
    imr_inverse: float                          #: marge initiale palier 1, jambe inverse
    imr_linear: float
    mmr_inverse: float                          #: marge de maintenance palier 1
    mmr_linear: float
    depth_usd_inverse: float                    #: médiane, 5 niveaux — informatif
    depth_usd_linear: float
    half_spread_inverse_bps: float
    half_spread_linear_bps: float
    worst_residual_drift_bps: float             #: pire dérive adverse 14 j
    residual_hours: int
    volume_24h_usd_inverse: float = 0.0         #: ce qui borne vraiment la taille
    volume_24h_usd_linear: float = 0.0

    # — grandeurs dérivées —
    @property
    def n(self) -> int:
        return len(self.diff_bps_per_settlement)

    @property
    def r_bps_per_day(self) -> float:
        """Flux brut capté, en bps/jour de notionnel (3 règlements/jour)."""
        return st.fmean(self.diff_bps_per_settlement) * 3.0

    @property
    def t_stat(self) -> float:
        d = self.diff_bps_per_settlement
        if len(d) < 2:
            return 0.0
        sd = st.stdev(d)
        return st.fmean(d) / (sd / len(d) ** 0.5) if sd > 0 else 0.0

    def holding_blocks(self, holding_days: float) -> List[float]:
        """Differentiel moyen, en bps/jour, sur des blocs DISJOINTS de la duree
        de detention. Chaque bloc est une observation independante."""
        n = int(round(holding_days * 3))          # 3 reglements par jour
        d = list(self.diff_bps_per_settlement)
        if n < 1:
            return []
        return [st.fmean(d[i:i + n]) * 3.0 for i in range(0, len(d) - n + 1, n)]

    def positive_block_ratio(self, holding_days: float) -> float:
        b = self.holding_blocks(holding_days)
        return sum(x > 0 for x in b) / len(b) if b else 0.0

    @property
    def deployable_usd(self) -> float:
        """Notionnel soutenable : une part du volume de la jambe contraignante."""
        return VOLUME_PARTICIPATION * min(self.volume_24h_usd_inverse,
                                          self.volume_24h_usd_linear)

    @property
    def imr_sum(self) -> float:
        return self.imr_inverse + self.imr_linear

    @property
    def mmr_sum(self) -> float:
        return self.mmr_inverse + self.mmr_linear

    @property
    def max_leverage(self) -> float:
        """Levier plafond imposé par la marge initiale des deux jambes.

        Il n'y a AUCUNE compensation entre les jambes : la compensation des
        risques (« risk unit merge ») exige le palier VIP3 chez OKX, hors de
        portee a ce capital. Les deux marges initiales s'additionnent.
        """
        return 1.0 / self.imr_sum

    def liquidation_buffer_bps(self, leverage: float) -> float:
        """Dérive adverse du résidu, en bps, qui épuise le compte.

        En marge multi-devises, le PnL prix des deux jambes se compense dans le
        collatéral commun converti en USD ; il ne reste que le résidu. La
        liquidation survient quand l'equity passe sous la marge de maintenance.
        """
        return 1e4 * (1.0 / leverage - self.mmr_sum)

    def safe_leverage(self) -> float:
        """Le plus grand levier dont le coussin couvre SAFETY_FACTOR x la pire
        dérive adverse observée du résidu."""
        need = SAFETY_FACTOR * self.worst_residual_drift_bps / 1e4
        denom = need + self.mmr_sum
        return min(self.max_leverage, 1.0 / denom) if denom > 0 else self.max_leverage

    def roundtrip_cost_bps(self, maker: bool) -> float:
        if maker:
            return 4 * MAKER_FEE_BPS
        spreads = 2 * (self.half_spread_inverse_bps + self.half_spread_linear_bps)
        return 4 * TAKER_FEE_BPS + spreads


@dataclass
class Rejection:
    coin: str
    criterion: str
    value: float
    threshold: float


@dataclass
class Book:
    pairs: List[Pair]
    rejected: List[Rejection] = field(default_factory=list)

    @property
    def coins(self) -> List[str]:
        return [p.coin for p in self.pairs]

    def portfolio_series(self) -> List[float]:
        """Différentiel équipondéré, sur les règlements communs à toutes les paires."""
        if not self.pairs:
            return []
        # chaque paire est indexée par position ; on aligne par le suffixe commun
        k = min(p.n for p in self.pairs)
        cols = [list(p.diff_bps_per_settlement)[-k:] for p in self.pairs]
        return [st.fmean(v) for v in zip(*cols)]

    def r_bps_per_day(self) -> float:
        s = self.portfolio_series()
        return st.fmean(s) * 3.0 if s else 0.0

    def t_stat(self) -> float:
        s = self.portfolio_series()
        if len(s) < 2 or st.stdev(s) == 0:
            return 0.0
        return st.fmean(s) / (st.stdev(s) / len(s) ** 0.5)

    def leverage(self) -> float:
        """Levier du livre : le plus contraignant des leviers sûrs par paire.

        On ne moyenne pas : une seule paire liquidée emporte le collatéral
        commun, donc c'est le minimum qui gouverne.
        """
        return min((p.safe_leverage() for p in self.pairs), default=0.0)

    @property
    def capacity_usd(self) -> float:
        """Notionnel TOTAL soutenable, somme des capacites par paire.

        CORRIGE le 21/09/2026 : la version precedente bornait a 25 % de la
        profondeur instantanee, ce qui est le critere d'un ordre urgent, pas
        d'une position tenue des semaines. Voir CORRECTION_CAPACITE.md.
        """
        return sum(p.deployable_usd for p in self.pairs)

    def max_capital_eur(self) -> float:
        """Capital au-dela duquel la capacite mord, paire par paire."""
        return sum(p.deployable_usd / p.safe_leverage() for p in self.pairs
                   if p.safe_leverage() > 0)

    def net_bps_per_day_of_capital(self, holding_days: float, maker: bool) -> float:
        """R(T) = L x (r - c/T), en bps/jour de CAPITAL."""
        if not self.pairs or holding_days <= 0:
            return 0.0
        c = st.fmean([p.roundtrip_cost_bps(maker) for p in self.pairs])
        return self.leverage() * (self.r_bps_per_day() - c / holding_days)

    def annual_return(self, holding_days: float, maker: bool) -> float:
        return (1 + self.net_bps_per_day_of_capital(holding_days, maker) / 1e4) ** 365 - 1


# ─────────────────────────────── Sélection ───────────────────────────────────

def select(pairs: Sequence[Pair], holding_days: float = 14.0) -> Book:
    """Applique les critères déclarés en tête de module, dans l'ordre.

    Chaque rejet est enregistré avec le critère et la valeur qui l'a causé :
    un livre vide doit rester explicable, et « 0 paire retenue » ne doit jamais
    etre indiscernable d'un bug (c'est le faux negatif qui a coute deux
    semaines au projet, cf. commit 5ef49cb).
    """
    kept: List[Pair] = []
    rejected: List[Rejection] = []
    for p in pairs:
        blocks = p.holding_blocks(holding_days)
        ratio = p.positive_block_ratio(holding_days)
        checks = [
            ("echantillon_funding", p.n, MIN_SETTLEMENTS, p.n >= MIN_SETTLEMENTS),
            ("heures_residu", p.residual_hours, MIN_RESIDUAL_HOURS,
             p.residual_hours >= MIN_RESIDUAL_HOURS),
            ("derive_residu_14j", p.worst_residual_drift_bps, MAX_RESIDUAL_DRIFT_BPS,
             p.worst_residual_drift_bps <= MAX_RESIDUAL_DRIFT_BPS),
            ("t_differentiel", p.t_stat, MIN_T_STAT, p.t_stat >= MIN_T_STAT),
            ("nb_blocs_detention", len(blocks), MIN_BLOCKS, len(blocks) >= MIN_BLOCKS),
            ("blocs_positifs", ratio, MIN_POSITIVE_BLOCK_RATIO,
             ratio >= MIN_POSITIVE_BLOCK_RATIO),
            ("volume_inverse", p.volume_24h_usd_inverse, MIN_VOLUME_USD,
             p.volume_24h_usd_inverse >= MIN_VOLUME_USD),
            ("volume_lineaire", p.volume_24h_usd_linear, MIN_VOLUME_USD,
             p.volume_24h_usd_linear >= MIN_VOLUME_USD),
        ]
        failed = next((c for c in checks if not c[3]), None)
        if failed:
            rejected.append(Rejection(p.coin, failed[0], failed[1], failed[2]))
        else:
            kept.append(p)
    return Book(pairs=kept, rejected=rejected)


# ─────────────────────────────── Limites ─────────────────────────────────────

LIMITES = """\
1. L'historique de funding est plafonne a ~95 jours par l'API OKX (286 releves).
   Cela fait SIX blocs disjoints a 14 jours de detention, TROIS a 30 jours. Le
   minimum atteignable pour p < 0,05 est 5 blocs ; a 30 jours il manque donc
   55 jours de collecte. Le livre n'est PAS valide hors echantillon.
2. Le cout maker suppose que le demi-spread encaisse est exactement neutralise
   par la selection adverse, et que les 4 jambes sont remplies passivement. Ni
   la probabilite de remplissage ni le markout ne sont mesures. En taker, le
   livre est negatif a court horizon.
3. La pire derive du residu vient d'un echantillon contenant UNE cascade
   (10/10/2025). Les residus y bougent ensemble : la mutualisation du coussin
   ne joue pas dans l'evenement qui decide de la survie. D'ou SAFETY_FACTOR=3.
4. Aucune execution n'existe. Ce module produit un livre cible, pas des ordres.
"""
