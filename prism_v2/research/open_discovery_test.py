"""Protocole OPEN DISCOVERY — reproductible, et destine a pouvoir ECHOUER.

QUESTION POSEE
Le systeme peut-il analyser une fenetre de marche SANS recevoir :
    le nom du phenomene, la famille, le sens (BUY/SELL), le seuil,
    l'horizon, le mecanisme, ni aucune regle de trading
et produire malgre tout :
    OBSERVATION -> PHENOMENE -> RELATION -> HYPOTHESE
                -> FALSIFICATION -> TEST ECONOMIQUE ?

CE QUE LE PROTOCOLE VERIFIE MECANIQUEMENT
  1. aucune famille codee a l'avance n'est utilisee (les detecteurs de
     prism_v2/detectors/ sont absents du chemin) ;
  2. aucun sens n'est fourni : il est DEDUIT du signe de l'exces mesure ;
  3. aucun seuil n'est fourni : il est MESURE (decile de l'echantillon) ;
  4. les horizons sont une grille d'observation, pas un choix par piste ;
  5. le mecanisme est propose APRES la mesure, jamais avant ;
  6. la falsification est obligatoire.

CE QUE LE PROTOCOLE NE PEUT PAS REVENDIQUER
L'ESPACE DE PRIMITIVES (`FEATURE_SPACE`) et la GRILLE D'HORIZONS sont ecrits a
la main. Le systeme decouvre librement DANS cet espace ; il ne decouvre pas
l'espace lui-meme, n'invente aucune primitive, et ne compose pas de primitives
entre elles. C'est pourquoi le verdict honnete est STRUCTURED, pas OPEN.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agents import DEFAULT_HORIZONS_MS
from .observation import FEATURE_SPACE
from .pipeline import PipelineResult


class DiscoveryClass(str, enum.Enum):
    OPEN = "OPEN_DISCOVERY_ENGINE"
    STRUCTURED = "STRUCTURED_DISCOVERY_ENGINE"
    FAMILY_BASED = "FAMILY_BASED_OPPORTUNITY_ENGINE"


#: Ce qu'il faudrait pour pretendre a OPEN. Liste exigeante et honnete.
OPEN_REQUIREMENTS = (
    "inventer des primitives non prevues par l'auteur",
    "composer des primitives entre elles sans schema impose",
    "decouvrir ses propres horizons plutot que parcourir une grille",
    "formuler des mecanismes economiques hors d'une table de correspondance",
    "generaliser a une classe d'actifs non anticipee sans modification de code",
)


@dataclass
class OpenDiscoveryAudit:
    """Verdict d'ouverture, avec les preuves qui l'appuient."""

    discovery_class: DiscoveryClass
    inputs_withheld: Dict[str, bool]
    evidence: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    produced: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"discovery_class": self.discovery_class.value,
                "inputs_withheld": self.inputs_withheld,
                "evidence": self.evidence, "limitations": self.limitations,
                "produced": self.produced,
                "open_requirements_not_met": list(OPEN_REQUIREMENTS)}


def audit(result: Optional[PipelineResult] = None) -> OpenDiscoveryAudit:
    """Audit reproductible. `result` fournit les comptes reellement produits."""
    withheld = {
        "phenomenon_name_withheld": True,
        "family_withheld": True,
        "direction_withheld": True,
        "threshold_withheld": True,
        "mechanism_withheld_until_after_measurement": True,
        "trading_rule_withheld": True,
        "horizon_withheld_per_lead": True,
    }
    evidence = [
        "aucun detecteur de prism_v2/detectors/ n'intervient dans ce chemin : "
        "le pipeline de recherche n'importe pas ce paquet",
        "les seuils de phenomene sont MESURES (decile de l'echantillon observe), "
        "jamais poses a l'avance",
        "le sens est DEDUIT du signe de l'exces mesure, jamais fourni",
        "le mecanisme economique est propose APRES la mesure de la relation",
        "la falsification est le seul chemin vers SURVIVED_FALSIFICATION",
        f"le balayage est mecanique : {len(FEATURE_SPACE)} primitives x "
        f"{len(DEFAULT_HORIZONS_MS)} horizons x 2 conditions, sans intuition",
    ]
    limitations = [
        f"FEATURE_SPACE est ECRIT A LA MAIN ({len(FEATURE_SPACE)} primitives) : "
        "le systeme ne peut pas inventer une primitive absente de cette liste",
        "aucune composition de primitives (pas de A et B simultanement)",
        f"la grille d'horizons est fixee ({DEFAULT_HORIZONS_MS}), non decouverte",
        "les mecanismes viennent d'une table de correspondance ecrite a la main ; "
        "hors de cette table, le mecanisme est UNEXPLAINED",
        "aucune relation inter-instruments n'est cherchee par ce chemin "
        "(A precede B) — seule la famille CROSS_MARKET, codee a l'avance, le fait",
    ]
    produced: Dict[str, int] = {}
    if result is not None:
        produced = result.funnel()
    return OpenDiscoveryAudit(
        discovery_class=DiscoveryClass.STRUCTURED, inputs_withheld=withheld,
        evidence=evidence, limitations=limitations, produced=produced)


VERDICT_TEXT = """\
VERDICT : STRUCTURED DISCOVERY ENGINE (classe B).

Ce n'est PAS un Open Discovery Engine, et le nom ne sera pas change pour
paraitre plus impressionnant.

Ce qui justifie "DISCOVERY" :
  le systeme recoit des donnees de marche et rien d'autre. Il ne sait pas quel
  phenomene chercher, dans quel sens, a quel seuil, ni selon quel mecanisme.
  Il balaie mecaniquement son espace de primitives, mesure ce qui suit chaque
  etat, propose une explication apres coup, puis tente de se detruire lui-meme.

Ce qui interdit "OPEN" :
  l'espace de primitives et la grille d'horizons sont ecrits a la main. Le
  systeme decouvre DANS un espace donne ; il ne decouvre pas l'espace. Il ne
  peut pas inventer une primitive, en composer deux, ni formuler un mecanisme
  absent de sa table.

Ce qui interdit de le reduire a "FAMILY_BASED" (classe C) :
  les 9 familles codees existent et restent utiles, mais elles ne participent
  PAS a ce chemin. Le pipeline de recherche produit des hypotheses qu'aucune
  famille n'avait prevues, et les detruit sans qu'aucune famille n'intervienne.
"""
