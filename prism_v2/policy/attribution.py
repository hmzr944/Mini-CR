"""POURQUOI LE REALISE DIFFERE DE L'ATTENDU, poste par poste.

Le registre sait deja ce qu'une decision a rapporte. Il ne sait pas POURQUOI
elle n'a pas rapporte ce qu'on attendait — et sans cela le systeme ne peut
pas apprendre de son exploitation, seulement du mouvement des prix, ce qui
est exactement le paradigme abandonne.

reconciliation.py fait deja ce travail pour l'ancien pipeline, mais sa
taxonomie est entierement tournee vers l'EXECUTION : remplissage partiel,
impact, rejet, quantification. Elle n'a aucune cause pour « le modele s'est
trompe sur le mouvement ». Or c'est precisement le mode d'echec observe :
la politique gagne +2,85 bps en apprentissage et perd -3,47 en test, sans
qu'aucun cout n'ait varie d'un centieme. Ce module ajoute donc la partie
manquante plutot que de reecrire l'existante.

LA DECOMPOSITION. Pour chaque decision executee :

    realise - attendu  =  ecart de COUT
                        + ecart de TAILLE
                        + ecart de REMPLISSAGE
                        + ecart de MODELE

Les trois premiers sont calculables exactement a partir de ce que la
decision a enregistre. Le QUATRIEME EST UN RESIDU : ce qui reste quand tout
ce qui est mesurable a ete retire. L'appeler « modele » n'est pas une
explication, c'est un aveu — et c'est voulu : un residu nomme « divers »
laisserait croire qu'on a compris.

CE QUE CE MODULE NE FAIT PAS. Il n'invente aucune cause. Quand une decision
depend d'un remplissage passif dont la probabilite est INCONNUE, l'ecart de
remplissage est marque INCONNU et n'est PAS impute au modele : lui attribuer
le residu ferait passer une ignorance pour un diagnostic.
"""
from __future__ import annotations

import enum
import statistics as st
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from prism_v2.policy.action import EXECUTABLE, UPPER_BOUND
from prism_v2.policy.ledger import Decision


class Cause(str, enum.Enum):
    """Postes d'ecart. Ordonnes du plus mesurable au plus residuel."""
    COUT = "COUT"                 # frais ou spread differents du modele
    TAILLE = "TAILLE"             # notionnel reduit par la profondeur
    REMPLISSAGE = "REMPLISSAGE"   # le passif n'a pas ete rempli comme suppose
    MODELE = "MODELE"             # residu : le mouvement n'etait pas celui-la
    INCONNU = "INCONNU"           # non imputable faute de mesure


@dataclass(frozen=True)
class Ecart:
    """Decomposition de l'ecart d'une decision, en bps du notionnel."""
    attendu_bps: float
    realise_bps: float
    postes: Dict[Cause, float]
    imputable: bool               # False si un poste est INCONNU

    @property
    def total_bps(self) -> float:
        return self.realise_bps - self.attendu_bps

    def dominant(self) -> Cause:
        """Poste qui explique la plus grande part de l'ecart, en valeur absolue."""
        if not self.postes:
            return Cause.INCONNU
        return max(self.postes.items(), key=lambda kv: abs(kv[1]))[0]


def attribuer(d: Decision, cout_attendu_bps: float) -> Optional[Ecart]:
    """Decompose l'ecart d'une decision executee.

    `cout_attendu_bps` est le cout que le modele de valeur avait DEJA
    integre quand il a produit `predicted_bps`. Dans la politique actuelle,
    la valeur d'une cellule est une moyenne de PnL NETS : le cout y est donc
    deja compris, et l'ecart de cout attendu vaut zero tant que le bareme ne
    change pas. C'est une propriete a VERIFIER, pas a supposer — d'ou le
    passage explicite de ce parametre plutot qu'une valeur implicite.
    """
    if not d.action.is_trade:
        return None
    postes: Dict[Cause, float] = {}

    # COUT : ce que l'execution a reellement paye, contre ce que le modele
    # avait dans le ventre. Un ecart non nul ici signifierait que le bareme
    # a bouge entre l'apprentissage et le test.
    cout_reel = d.fee_bps + d.slippage_bps
    postes[Cause.COUT] = -(cout_reel - cout_attendu_bps)

    # TAILLE : la profondeur au touch a-t-elle reduit le notionnel ? Cela ne
    # change pas le PnL EN BPS, seulement en USD ; le poste est donc nul en
    # bps par construction et present pour que son absence soit visible.
    postes[Cause.TAILLE] = 0.0

    # REMPLISSAGE : une entree passive suppose un remplissage dont la
    # probabilite est INCONNUE. On ne chiffre pas ce qu'on ne mesure pas.
    imputable = True
    if d.status == UPPER_BOUND:
        imputable = False

    # MODELE : le residu. Tout ce que les postes mesurables n'expliquent pas.
    explique = sum(postes.values())
    postes[Cause.MODELE] = (d.net_bps - d.predicted_bps) - explique
    return Ecart(attendu_bps=d.predicted_bps, realise_bps=d.net_bps,
                 postes=postes, imputable=imputable)


@dataclass
class Attribution:
    """Agregat des ecarts sur un ensemble de decisions."""
    ecarts: List[Ecart]

    @property
    def n(self) -> int:
        return len(self.ecarts)

    def moyenne(self, cause: Cause) -> float:
        if not self.ecarts:
            return 0.0
        return st.fmean(e.postes.get(cause, 0.0) for e in self.ecarts)

    def attendu_moyen(self) -> float:
        return st.fmean(e.attendu_bps for e in self.ecarts) if self.ecarts else 0.0

    def realise_moyen(self) -> float:
        return st.fmean(e.realise_bps for e in self.ecarts) if self.ecarts else 0.0

    def part_imputable(self) -> float:
        if not self.ecarts:
            return 0.0
        return sum(1 for e in self.ecarts if e.imputable) / len(self.ecarts)

    def verdict(self) -> str:
        """Nomme le goulot, ou refuse de le nommer.

        Le mandat interdit de presenter une ignorance comme un diagnostic :
        quand la majorite des decisions depend d'un remplissage inconnu,
        aucune cause n'est declaree.
        """
        if not self.ecarts:
            return "AUCUNE DECISION EXECUTEE"
        if self.part_imputable() < 0.5:
            return ("NON IMPUTABLE : la majorite des decisions depend d'un "
                    "remplissage passif de probabilite INCONNUE")
        c = max((Cause.COUT, Cause.TAILLE, Cause.MODELE),
                key=lambda k: abs(self.moyenne(k)))
        if abs(self.moyenne(c)) < 0.01:
            return "AUCUN ECART MATERIEL entre attendu et realise"
        return f"GOULOT : {c.value}"

    def resume(self) -> Dict[str, object]:
        return {
            "decisions": self.n,
            "attendu_bps": self.attendu_moyen(),
            "realise_bps": self.realise_moyen(),
            "ecart_bps": self.realise_moyen() - self.attendu_moyen(),
            "ecart_cout_bps": self.moyenne(Cause.COUT),
            "ecart_taille_bps": self.moyenne(Cause.TAILLE),
            "ecart_modele_bps": self.moyenne(Cause.MODELE),
            "part_imputable": self.part_imputable(),
            "verdict": self.verdict(),
        }


def attribuer_tout(decisions: Sequence[Decision], cout_attendu_bps: float
                   ) -> Attribution:
    out = []
    for d in decisions:
        e = attribuer(d, cout_attendu_bps)
        if e is not None:
            out.append(e)
    return Attribution(out)
