"""Registre des plafonds economiques mesures. Section 21 du mandat.

POURQUOI CE MODULE EXISTE. Le projet a teste une dizaine de familles. Chaque
test a produit un PLAFOND : le meilleur rendement que cette famille ait
atteint, mesure. Sans registre, ce savoir se perd, et la famille suivante est
etudiee depuis zero — ou pire, une famille deja morte est reprise parce que son
nom sonne differemment.

Un plafond n'est pas une opinion. C'est un nombre, avec sa taille
d'echantillon et sa methode. Un candidat dont la borne SUPERIEURE passe sous
le plafond d'une famille morte est mort par arithmetique, sans etude.

CE QUE CE MODULE NE FAIT PAS. Il n'interdit rien definitivement. La section 25
du mandat est explicite : aucune conclusion n'est protegee. Mais reviver une
famille exige une PREUVE qui depasse le plafond enregistre, pas une intuition
— et `revive` l'impose.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

#: Motifs de mort economique. Chacun est un constat mesure, pas un jugement.
NO_MAGNITUDE = "MAGNITUDE_INSUFFISANTE"      # le brut est trop petit
COST_DOMINATES = "COUT_DOMINANT"             # le brut existe, le cout l'efface
NOT_HEDGEABLE = "NON_COUVRABLE"              # le flux existe, le risque reste
NOT_PERSISTENT = "NON_PERSISTANT"            # des pointes, pas un flux
NO_CAPACITY = "CAPACITE_INSUFFISANTE"        # trop peu de profondeur
MEASUREMENT_INVALID = "MESURE_INVALIDE"      # la donnee ne porte pas la mesure

#: Denominateur d'un rendement. Les melanger est l'erreur qui a fait croire
#: pendant des semaines que le carry valait 0,008x l'objectif alors que le
#: chiffre compare etait un rendement sur NOTIONNEL et le seuil un rendement
#: sur CAPITAL. Le type le rend desormais impossible.
CAPITAL = "CAPITAL"
NOTIONAL = "NOTIONNEL"
_DENOMINATORS = (CAPITAL, NOTIONAL)

#: Niveau d'agregation d'un plafond. Un rendement mesure sur UNE paire et un
#: rendement mesure sur un PORTEFEUILLE de paires ne sont pas comparables : le
#: second beneficie de la mutualisation du coussin (facteur 2,21 mesure sur 16
#: paires). Lire 19,6 par paire comme une degradation de 33,3 en portefeuille
#: est une faute d'unite, au meme titre que melanger capital et notionnel.
PAIR = "PAIRE"
PORTFOLIO = "PORTEFEUILLE"
_AGGREGATIONS = (PAIR, PORTFOLIO)


@dataclass(frozen=True)
class Ceiling:
    """Plafond economique mesure d'une famille, en bps/jour de CAPITAL."""

    family: str
    ceiling_bps_per_day: float
    reason: str
    n_observations: int
    method: str
    denominator: str = CAPITAL
    aggregation: str = PORTFOLIO
    note: str = ""

    def __post_init__(self) -> None:
        if self.aggregation not in _AGGREGATIONS:
            raise ValueError(f"{self.family}: agregation inconnue "
                             f"{self.aggregation!r}")
        if self.denominator not in _DENOMINATORS:
            raise ValueError(f"{self.family}: denominateur inconnu "
                             f"{self.denominator!r} — un rendement sans "
                             f"denominateur declare n'est pas comparable")
        if self.n_observations <= 0:
            raise ValueError(f"{self.family}: un plafond sans observation "
                             f"n'est pas un plafond")
        if not self.method:
            raise ValueError(f"{self.family}: un plafond sans methode n'est "
                             f"pas verifiable")

    def kills(self, candidate_bps_per_day: float,
              denominator: str = CAPITAL,
              aggregation: str = PORTFOLIO) -> bool:
        """Ce plafond condamne-t-il un candidat de la meme famille ?

        Refuse de comparer deux rendements de denominateurs differents : le
        levier separe les deux d'un facteur qui n'est pas 1.
        """
        if denominator != self.denominator:
            raise ValueError(
                f"{self.family}: comparaison entre un rendement sur "
                f"{denominator} et un plafond sur {self.denominator}. "
                f"Convertis d'abord par le levier.")
        if aggregation != self.aggregation:
            raise ValueError(
                f"{self.family}: comparaison entre une mesure {aggregation} et "
                f"un plafond {self.aggregation}. La mutualisation du coussin "
                f"separe les deux d'un facteur mesure, pas de 1.")
        return candidate_bps_per_day <= self.ceiling_bps_per_day


@dataclass
class KillRegistry:
    """Les plafonds connus, et le crible qu'ils forment."""

    ceilings: Dict[str, Ceiling]

    @classmethod
    def from_list(cls, items: Sequence[Ceiling]) -> "KillRegistry":
        return cls({c.family: c for c in items})

    def best_known(self) -> Optional[Ceiling]:
        """Le plafond le plus HAUT jamais mesure, toutes familles confondues.

        C'est la barre que tout nouveau candidat doit franchir pour etre le
        meilleur resultat du projet — et non simplement « prometteur ».
        """
        capitaux = [c for c in self.ceilings.values()
                    if c.denominator == CAPITAL]
        if not capitaux:
            return None
        return max(capitaux, key=lambda c: c.ceiling_bps_per_day)

    def screen(self, candidate_bps_per_day: float, threshold_bps_per_day: float,
               denominator: str = CAPITAL) -> Dict[str, object]:
        """Un candidat merite-t-il du temps de recherche ?

        Deux questions distinctes, et les confondre est une faute :
          - bat-il le meilleur resultat connu du projet ?
          - atteint-il l'objectif ?
        Un candidat peut faire le premier sans le second : c'est un progres
        qui ne resout pas le probleme, et il doit etre nomme ainsi.
        """
        if denominator != CAPITAL:
            raise ValueError(
                "le crible compare au seuil de l'objectif, qui porte sur le "
                "CAPITAL. Convertis le candidat par son levier d'abord.")
        best = self.best_known()
        beats = (best is None or candidate_bps_per_day > best.ceiling_bps_per_day)
        reaches = candidate_bps_per_day >= threshold_bps_per_day
        if reaches:
            verdict = "ATTEINT L'OBJECTIF"
        elif beats:
            verdict = "MEILLEUR CONNU, MAIS SOUS L'OBJECTIF"
        else:
            verdict = "SOUS UN PLAFOND DEJA MESURE — mort par arithmetique"
        return {"verdict": verdict, "beats_best_known": beats,
                "reaches_objective": reaches,
                "best_known": best.family if best else None,
                "best_known_bps": best.ceiling_bps_per_day if best else None,
                "gap_factor": (threshold_bps_per_day / candidate_bps_per_day
                               if candidate_bps_per_day > 0 else None)}

    def revive(self, family: str, new_bps_per_day: float, evidence: str,
               n_observations: int) -> Ceiling:
        """Relever le plafond d'une famille exige de le DEPASSER, mesure.

        La section 25 du mandat autorise a revenir sur toute conclusion ; elle
        n'autorise pas a le faire sans preuve. Une valeur inferieure ou egale
        au plafond enregistre est refusee : elle ne dit rien de neuf.
        """
        old = self.ceilings.get(family)
        if old is not None and new_bps_per_day <= old.ceiling_bps_per_day:
            raise ValueError(
                f"{family}: {new_bps_per_day:.2f} bps/jour ne depasse pas le "
                f"plafond mesure ({old.ceiling_bps_per_day:.2f}). Une famille "
                f"ne se ranime pas sans preuve superieure.")
        if not evidence or n_observations <= 0:
            raise ValueError(f"{family}: une revision exige une preuve et un "
                             f"echantillon")
        c = Ceiling(family=family, ceiling_bps_per_day=new_bps_per_day,
                    reason="REVISE", n_observations=n_observations,
                    method=evidence,
                    denominator=old.denominator if old else CAPITAL,
                    aggregation=old.aggregation if old else PORTFOLIO)
        self.ceilings[family] = c
        return c

    def render(self, threshold_bps_per_day: float) -> str:
        lines = [f"{'famille':<42}{'plafond bps/j':>15}{'sur':>10}"
                 f"{'agregation':>14}{'x objectif':>12}{'n':>9}   motif",
                 "-" * 118]
        for c in sorted(self.ceilings.values(),
                        key=lambda x: -x.ceiling_bps_per_day):
            ratio = (f"{c.ceiling_bps_per_day/threshold_bps_per_day:>12.4f}"
                     if c.denominator == CAPITAL else f"{'—':>12}")
            lines.append(f"{c.family:<42}{c.ceiling_bps_per_day:>15.2f}"
                         f"{c.denominator:>10}{c.aggregation:>14}{ratio}"
                         f"{c.n_observations:>9,}   {c.reason}")
        b = self.best_known()
        if b:
            lines.append("")
            lines.append(f"meilleur plafond jamais mesure : {b.family} a "
                         f"{b.ceiling_bps_per_day:.2f} bps/jour de capital")
            lines.append(f"objectif : {threshold_bps_per_day:.2f} bps/jour  -> "
                         f" il manque un facteur "
                         f"{threshold_bps_per_day/b.ceiling_bps_per_day:,.1f}")
        return "\n".join(lines)
