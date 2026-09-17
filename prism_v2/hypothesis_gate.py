#!/usr/bin/env python3
"""PORTE D'HYPOTHESE — a franchir AVANT d'ecrire la moindre ligne de backtest.

    pourquoi cette opportunite existe
      -> pourquoi elle est repetable
      -> pourquoi elle n'est pas immediatement arbitree
      -> capital necessaire
      -> PnL / capital / jour

Si une idee ne franchit pas cette grille, on ne la teste pas. Le cout d'un
test n'est pas le temps de calcul : c'est la donnee propre qu'il consomme et
qu'on ne recupere jamais.

CE QUE CETTE PORTE ATTRAPE, ET CE QU'ELLE N'ATTRAPE PAS. Elle elimine les
idees sans histoire economique — celles dont personne ne peut dire QUI paie
ni POURQUOI il continue de payer. Elle n'attrape PAS une idee dont
l'histoire est juste mais dont le signal se revele instable : le carry avait
une histoire economique defendable (contrainte de capacite) et il est mort a
la stabilite temporelle. Seul un test hors echantillon tranche cela.

La porte reduit donc le nombre de tests, elle ne les remplace pas. Pretendre
l'inverse serait remplacer une mesure par un raisonnement, ce qui est
exactement l'erreur que ce depot combat.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.capital_efficiency import Objective

#: Longueur minimale d'une reponse, en caracteres. Une porte qui accepte
#: « oui » ou « evident » ne filtre rien.
MIN_ANSWER_CHARS = 80

QUESTIONS = (
    "pourquoi_existe",
    "pourquoi_repetable",
    "pourquoi_pas_arbitree",
    "capital_necessaire",
    "pnl_par_capital_par_jour",
)

ACCEPTED = "ACCEPTE"
REFUSED = "REFUSE"


class GateRefused(RuntimeError):
    """Levee quand on tente de tester une hypothese non validee."""


@dataclass
class Hypothesis:
    """Une idee, avant tout code. Chaque champ doit etre ecrit a la main."""

    name: str
    pourquoi_existe: str = ""
    pourquoi_repetable: str = ""
    pourquoi_pas_arbitree: str = ""
    capital_necessaire: str = ""
    #: Justification ECRITE du chiffre ci-dessous : d'ou vient-il, quelles
    #: grandeurs on multiplie, quelles hypotheses on fait. Sans elle, le
    #: nombre est une intuition deguisee en estimation.
    pnl_par_capital_par_jour: str = ""
    #: Estimation A PRIORI, avant toute donnee. C'est un engagement : si la
    #: mesure en differe d'un ordre de grandeur, l'histoire economique etait
    #: fausse, et c'est une information en soi.
    estimated_bps_per_day: Optional[float] = None
    capital_usd: Optional[float] = None
    #: Le test qui pourrait la TUER. Sans lui, l'hypothese n'est pas
    #: scientifique, quelle que soit la qualite de son histoire.
    falsifying_test: str = ""
    #: Barreau de l'echelle ou l'on s'attend a ce qu'elle meure, et pourquoi
    #: on pense qu'elle le franchira. Force a confronter le post-mortem.
    expected_weakest_rung: str = ""

    def missing_answers(self) -> List[str]:
        out = []
        for q in QUESTIONS:
            v = getattr(self, q, "")
            if isinstance(v, str) and len(v.strip()) < MIN_ANSWER_CHARS:
                out.append(q)
        return out


@dataclass
class GateDecision:
    status: str
    hypothesis: str
    reasons: List[str] = field(default_factory=list)
    ratio_to_objective: Optional[float] = None

    @property
    def accepted(self) -> bool:
        return self.status == ACCEPTED

    def require(self) -> "GateDecision":
        if not self.accepted:
            raise GateRefused(f"{self.hypothesis}: " + " | ".join(self.reasons))
        return self


def evaluate_hypothesis(h: Hypothesis, objective: Objective,
                        min_ratio: float = 1.0) -> GateDecision:
    """Franchit-elle la porte ?

    `min_ratio` est la fraction de l'objectif que l'estimation a priori doit
    atteindre. A 1.0, on n'accepte que ce qui pretend atteindre l'objectif —
    c'est volontairement severe : tester une idee qui, DE L'AVEU MEME de son
    auteur, n'atteint pas l'objectif, consomme de la donnee propre pour un
    resultat connu d'avance.
    """
    reasons: List[str] = []

    missing = h.missing_answers()
    if missing:
        reasons.append("questions sans reponse substantielle: "
                       + ", ".join(missing))

    if not h.falsifying_test.strip():
        reasons.append("aucun test falsifiant : l'hypothese n'est pas testable")

    if not h.expected_weakest_rung.strip():
        reasons.append("barreau le plus faible non identifie : confronter la "
                       "matrice de post-mortem est obligatoire")

    ratio = None
    if h.estimated_bps_per_day is None:
        reasons.append("aucune estimation a priori de bps/jour")
    else:
        required = objective.required_bps_per_day()
        ratio = h.estimated_bps_per_day / required
        if h.estimated_bps_per_day <= 0:
            reasons.append("estimation a priori nulle ou negative")
        elif ratio < min_ratio:
            reasons.append(
                f"estimation {h.estimated_bps_per_day:.2f} bps/jour = "
                f"{ratio:.3f}x l'objectif ({required:.2f}) : sous le seuil "
                f"de {min_ratio:.2f}x")

    if h.capital_usd is not None and h.capital_usd <= 0:
        reasons.append("capital necessaire nul ou negatif")

    status = ACCEPTED if not reasons else REFUSED
    return GateDecision(status, h.name, reasons, ratio)


def render(decision: GateDecision) -> str:
    lines = [f"{decision.hypothesis} -> {decision.status}"]
    if decision.ratio_to_objective is not None:
        lines.append(f"  estimation a priori : "
                     f"{decision.ratio_to_objective:.3f}x l'objectif")
    for r in decision.reasons:
        lines.append(f"  - {r}")
    if decision.accepted:
        lines.append("  la porte est franchie. Elle ne garantit rien : elle")
        lines.append("  atteste seulement qu'il existe une histoire economique")
        lines.append("  et un test capable de la tuer.")
    return "\n".join(lines)
