"""L'economie de la subvention : ce qu'elle rapporte moins ce qu'un
remplissage coute — et la liste nommee de ce qui reste inconnu.

L'EQUATION. Contrairement a tout le reste du depot, elle ne contient aucun
terme predictif :

    net = pool * part * disponibilite  -  somme(cout de neutralisation)

POURQUOI LE COUT EST BORNE ET NON ESTIME. Sur un perpetuel, un remplissage
subi laisse une position dont la derive future doit etre ESTIMEE — c'est le
markout, et c'est ce qui a ferme la famille maker OKX. Sur un contrat
binaire, YES + NO = 1,00 $ PAR CONSTRUCTION. Acheter le complement rend la
position sans risque, quelle que soit l'issue, et son prix est LU dans le
carnet a l'instant du remplissage.

    cout de neutralisation = (prix_paye + ask_complement - 1,00) * parts
                             + frais TAKER sur la jambe de neutralisation

Ce nombre est une BORNE SUPERIEURE de la perte : neutraliser tout de suite
est la pire des sorties disciplinees, puisqu'elle renonce a tout retour du
prix. Une strategie qui survit a cette borne survit a toutes les autres. Le
depot a passe des semaines a estimer des derives ; ici le pire cas se lit.

LE FRAIS QUI SE CACHE DU MAUVAIS COTE. La documentation est explicite :
« Makers are never charged fees. Only takers pay fees. » On encaisse donc la
subvention sans frais. Mais NEUTRALISER, c'est TRAVERSER : la jambe qui rend
la position sans risque est une jambe taker, et elle paie

    frais = parts * taux * p * (1 - p)

avec un taux de 0,04 a 0,07 selon la categorie. A p = 0,50 cela fait 1,25 a
1,75 cent par part, soit 2,5 a 3,5 % du notionnel — plusieurs fois le spread.
Ignorer ce terme ferait passer pour gratuite la seule operation qui protege.

UNE CATEGORIE ECHAPPE A CE FRAIS : les marches geopolitiques, taux 0. Ce
n'est pas une preference, c'est le seul endroit ou l'inventaire se neutralise
au prix du spread seul. Cette lecture des frais est donc un CRITERE DE
SELECTION derive d'une mesure, et non un gout.

CE QUE CE MODULE REFUSE DE FAIRE. Il ne remplace aucune inconnue par zero.
Un cout non mesure rend None et contamine le net, qui rend None a son tour.
Un net None n'est pas un net nul : c'est l'aveu qu'on ne sait pas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from prism_v2.subsidy.scoring import daily_reward

#: LES CINQ INCONNUES QUI PEUVENT RETOURNER LE SIGNE. Elles sont ecrites ici,
#: dans le code, et non dans un rapport : un rapport se perime, un import non.
#: Chaque entree dit ce qu'il faudrait MESURER, pas ce qu'on espere.
UNKNOWNS: Dict[str, str] = {
    "part_reelle":
        "La part du pool est modelisee au prorata du Q. Le Q reel pondere en "
        "CARRE la distance au mid et retient le cote le plus faible : coter "
        "loin ne rapporte presque rien. Mesure requise : le Q des "
        "concurrents, reconstruit depuis le carnet, minute par minute.",
    "pool_effectivement_verse":
        "Le pool affiche est un budget publie. Rien dans la documentation ne "
        "garantit qu'il est verse EN ENTIER quand peu de makers qualifient. "
        "Mesure requise : les versements observes sur une epoque complete.",
    "flux_subi":
        "Le nombre de remplissages par jour, a la distance ou l'on cote. "
        "C'est lui qui multiplie le cout de neutralisation. Mesurable sans "
        "capital en rejouant la bande publique des echanges contre un ordre "
        "simule.",
    "disponibilite":
        "Q est echantillonne chaque minute. Une interruption ne coute pas un "
        "prorata : elle annule les echantillons manques. Mesure requise : la "
        "disponibilite reelle d'un processus sur une semaine.",
    "taux_de_frais_taker":
        "Le taux applique a la jambe de neutralisation. Publie par CATEGORIE "
        "(0 geopolitique, 0,04 a 0,07 ailleurs) et non par marche dans l'API "
        "du carnet. Mesure requise : la categorie de chaque marche retenu, "
        "confirmee contre un versement reel.",
    "acces":
        "Juridique et operationnel : ce compte peut-il ouvrir, financer et "
        "retirer sur cette venue ? Aucune mesure de marche n'y repond.",
}


#: Taux de frais TAKER par categorie, tels que publies. La categorie
#: geopolitique est a zero : c'est le seul endroit ou neutraliser ne coute
#: que le spread.
TAKER_FEE_RATES = {"geopolitical": 0.00, "politics": 0.04, "finance": 0.04,
                   "tech": 0.04, "mentions": 0.04, "sports": 0.05,
                   "economics": 0.05, "culture": 0.05, "weather": 0.05,
                   "other": 0.05, "crypto": 0.07}


def taker_fee_usd(price: float, shares: float,
                  rate: Optional[float]) -> Optional[float]:
    """frais = parts * taux * p * (1 - p). None si le taux est inconnu.

    La forme en p(1-p) est symetrique et maximale a 0,50. Elle rend le frais
    FAIBLE sur les contrats extremes et LOURD au milieu : un detail qui
    deplace entierement le choix des marches.
    """
    if rate is None:
        return None
    if not 0.0 <= price <= 1.0:
        raise ValueError("le prix d'un binaire vit dans [0, 1]")
    if not 0.0 <= rate <= 1.0:
        # UN TAUX N'EST PAS UN CHAMP BRUT. `maker_base_fee` vaut 1000 dans
        # l'API du carnet ; passe ici tel quel, il produit un frais 25 000
        # fois trop grand — et le resultat garde l'air d'une mesure : un cout
        # de neutralisation de 1 400 $/jour sur 1 000 $ de capital, lu comme
        # « la famille est tuee par son propre cout ». C'est arrive.
        # Les taux de TAKER_FEE_RATES sont des FRACTIONS (0,04 = 4 %).
        raise ValueError(
            f"taux de frais {rate!r} hors de [0, 1] : un taux est une "
            f"FRACTION, pas le champ brut d'une API. Convertis avant "
            f"d'appeler (maker_base_fee=1000 signifie 0,10, pas 1000).")
    return shares * rate * price * (1.0 - price)


@dataclass(frozen=True)
class Fill:
    """Un remplissage subi, et le carnet au moment ou il survient."""

    price_paid: float          # prix du jeton achete, en dollars par part
    complement_ask: Optional[float]   # meilleur ask du jeton complementaire
    shares: float
    #: taux taker de la jambe de neutralisation. None = INCONNU, et le cout
    #: devient None a son tour : un frais non mesure n'est pas un frais nul.
    taker_rate: Optional[float] = None

    def neutralisation_cost_usd(self) -> Optional[float]:
        """Cout, en dollars, de rendre la position sans risque tout de suite.

        Rend None quand le complement n'est pas cote ou quand le taux de
        frais est inconnu : on ne peut alors PAS chiffrer la protection, et
        l'ignorer reviendrait a compter un risque pour zero.
        """
        if self.complement_ask is None:
            return None
        fee = taker_fee_usd(self.complement_ask, self.shares, self.taker_rate)
        if fee is None:
            return None
        spread_part = (self.price_paid + self.complement_ask - 1.0) * self.shares
        return spread_part + fee


@dataclass
class MarketEconomics:
    """L'economie d'un marche subventionne, bornes et inconnues comprises."""

    question: str
    pool_usdc_per_day: float
    share: Optional[float]
    capital_usd: float
    uptime: float = 1.0
    fills: List[Fill] = field(default_factory=list)
    #: None tant que le flux subi n'a pas ete mesure. JAMAIS 0.0 par defaut :
    #: supposer qu'on n'est jamais rempli est l'hypothese la plus favorable
    #: qui soit, et c'est exactement celle que ce module refuse.
    measured_fills_per_day: Optional[float] = None

    def gross_usd_per_day(self) -> Optional[float]:
        return daily_reward(self.pool_usdc_per_day, self.share, self.uptime)

    def neutralisation_cost_per_day(self) -> Optional[float]:
        """Cout quotidien de neutralisation, None si le flux est inconnu."""
        if self.measured_fills_per_day is None:
            return None
        if not self.fills:
            return None
        costs = [f.neutralisation_cost_usd() for f in self.fills]
        if any(c is None for c in costs):
            return None
        mean = sum(costs) / len(costs)
        return mean * self.measured_fills_per_day

    def net_usd_per_day(self) -> Optional[float]:
        g = self.gross_usd_per_day()
        c = self.neutralisation_cost_per_day()
        if g is None or c is None:
            return None
        return g - c

    def gross_pct_per_day(self) -> Optional[float]:
        g = self.gross_usd_per_day()
        if g is None or self.capital_usd <= 0:
            return None
        return 100.0 * g / self.capital_usd

    def status(self) -> str:
        """Le statut au sens de la section 13 du mandat, derive et non choisi."""
        if self.share is None:
            return "DONNEES INSUFFISANTES"
        if self.net_usd_per_day() is None:
            return "EDGE BRUT OBSERVE"          # le net n'est pas calculable
        return "EDGE NET POSITIF" if self.net_usd_per_day() > 0 \
            else "EDGE NET NEGATIF"


def portfolio_gross(markets: List[MarketEconomics]) -> Optional[float]:
    """Brut agrege. None des qu'UN marche a une part inconnue.

    Sommer en sautant les inconnus produirait un total qui a l'air complet.
    """
    vals = [m.gross_usd_per_day() for m in markets]
    if any(v is None for v in vals):
        return None
    return sum(vals)
