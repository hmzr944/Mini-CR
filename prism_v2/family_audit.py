"""Autopsie des familles : OU PRISM perd-il les opportunites ?

LA QUESTION. On sait qu'a la fin presque rien ne survit. On ne sait pas si
c'est parce que le marche ne contient rien, ou parce que le detecteur, le seuil
economique ou le protocole sont trop restrictifs. Ces deux causes produisent le
MEME observable — « aucune opportunite » — et c'est le pire mode d'echec
possible pour un moteur de recherche.

Ce module instrumente l'entonnoir, etape par etape, famille par famille :

    ETATS EXAMINES
          v  (donnees manquantes)
    DETECTEUR EXECUTABLE
          v  (seuil du detecteur)
    CANDIDATS -- raw edge
          v  (cout connu)
    NET > 0
          v  (cout inconnu)
    RESOLU
          v  (capacite, sizing, router)
    ACTIONNABLE

Sans ce comptage, « 0 opportunite » est ininterpretable. C'est exactement le
faux negatif que le commit 5ef49cb avait diagnostique, pour lequel une garde
(`sanity.check_firing_rate`) a ete ecrite -- et qui n'etait appelee NULLE PART.
Ce module la branche.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from prism_v2.discovery import DetectionStatus, Detector, Family
from prism_v2.sanity import check_firing_rate

#: Un detecteur qui ne produit aucun candidat sur au moins ce nombre d'etats
#: varies est presume DEGENERE, pas silencieux a bon droit.
DEAD_DETECTOR_MIN_STATES = 200


@dataclass
class FamilyFunnel:
    """Comptage d'une famille a travers l'entonnoir."""

    family: Family
    states_examined: int = 0
    insufficient_data: int = 0
    detector_nothing: int = 0
    candidates: int = 0
    #: motifs de rejet du detecteur, tels que le detecteur les formule
    nothing_reasons: Counter = field(default_factory=Counter)
    missing_inputs: Counter = field(default_factory=Counter)
    #: aval economique
    net_positive: int = 0
    net_non_positive: int = 0
    unresolved: int = 0
    unresolved_components: Counter = field(default_factory=Counter)
    errors: int = 0
    error_kinds: Counter = field(default_factory=Counter)

    @property
    def ran(self) -> int:
        """Etats ou le detecteur a pu s'executer (donnees suffisantes)."""
        return self.states_examined - self.insufficient_data

    @property
    def detection_rate(self) -> float:
        return self.candidates / self.ran if self.ran else 0.0

    def is_presumed_dead(self) -> bool:
        """Zero candidat sur un echantillon large et varie : presume degenere."""
        return self.candidates == 0 and self.ran >= DEAD_DETECTOR_MIN_STATES

    def verdict(self):
        """Branche la garde anti-detecteur-muet, jusqu'ici orpheline."""
        return check_firing_rate(self.family.value, self.candidates, max(self.ran, 0))

    def to_dict(self) -> Dict:
        return {
            "family": self.family.value,
            "states_examined": self.states_examined,
            "insufficient_data": self.insufficient_data,
            "detector_ran": self.ran,
            "detector_nothing": self.detector_nothing,
            "candidates": self.candidates,
            "detection_rate": round(self.detection_rate, 6),
            "presumed_dead": self.is_presumed_dead(),
            "net_positive": self.net_positive,
            "net_non_positive": self.net_non_positive,
            "unresolved": self.unresolved,
            "errors": self.errors,
            "top_nothing_reasons": dict(self.nothing_reasons.most_common(3)),
            "top_missing_inputs": dict(self.missing_inputs.most_common(3)),
            "top_unresolved": dict(self.unresolved_components.most_common(3)),
            "top_errors": dict(self.error_kinds.most_common(3)),
        }


def _bucket(reason: str) -> str:
    """Regroupe un motif verbeux en classe, sans perdre sa nature."""
    r = (reason or "").lower()
    for key, label in (
        ("sous le spread", "seuil: sous le spread"),
        ("elargissement insuffisant", "seuil: elargissement insuffisant"),
        ("aucun retrait", "seuil: aucun retrait de profondeur"),
        ("non calculable", "donnees: grandeur non calculable"),
        ("insuffisant", "donnees: historique insuffisant"),
        ("aucun trade", "donnees: aucun trade"),
        ("mediane", "seuil: ecart a la mediane"),
        ("volatilite", "seuil: spread justifie par la volatilite"),
    ):
        if key in r:
            return label
    return (reason or "sans motif")[:60]


def run_funnel(detectors: Sequence[Detector], states: Iterable,
               evaluate=None) -> Dict[str, FamilyFunnel]:
    """Passe chaque etat dans chaque detecteur et compte les disparitions.

    `evaluate` est optionnel : s'il est fourni, les candidats traversent aussi
    la porte economique et l'on compte ou ils meurent. Sans lui, l'audit
    s'arrete au detecteur -- ce qui suffit deja a distinguer « le marche ne
    contient rien » de « le detecteur ne peut pas se declencher ».
    """
    funnels = {d.family.value: FamilyFunnel(d.family) for d in detectors}
    for state in states:
        for det in detectors:
            f = funnels[det.family.value]
            f.states_examined += 1
            try:
                out = det.detect(state)
            except Exception as exc:                      # un detecteur qui leve
                f.errors += 1                             # est un defaut, pas un
                f.error_kinds[type(exc).__name__] += 1    # resultat negatif
                continue
            if out.status is DetectionStatus.INSUFFICIENT_DATA:
                f.insufficient_data += 1
                for m in (out.missing or ["(non precise)"]):
                    f.missing_inputs[m] += 1
                continue
            if not out.candidates:
                f.detector_nothing += 1
                f.nothing_reasons[_bucket(out.reason)] += 1
                continue
            f.candidates += len(out.candidates)
            if evaluate is None:
                continue
            for cand in out.candidates:
                try:
                    ev = evaluate(cand)
                except Exception as exc:
                    f.errors += 1
                    f.error_kinds[f"eval:{type(exc).__name__}"] += 1
                    continue
                status = getattr(ev.status, "value", str(ev.status))
                if status == "ACCEPTED":
                    f.net_positive += 1
                elif status == "UNRESOLVED":
                    f.unresolved += 1
                    for c in (ev.unresolved_components or ["(non precise)"])[:3]:
                        f.unresolved_components[c] += 1
                else:
                    f.net_non_positive += 1
    return funnels


def render(funnels: Dict[str, FamilyFunnel]) -> str:
    """Rapport lisible. Les familles presumees mortes sont nommees en premier."""
    lines = []
    dead = [f for f in funnels.values() if f.is_presumed_dead()]
    if dead:
        lines.append("DETECTEURS PRESUMES DEGENERES "
                     "(0 candidat sur un echantillon large et varie) :")
        for f in dead:
            lines.append(f"  {f.family.value:26s} 0 candidat / {f.ran} executions")
            for r, n in f.nothing_reasons.most_common(2):
                lines.append(f"      {n:6d} x {r}")
        lines.append("")
    lines.append(f"{'famille':26s} {'etats':>7s} {'donnees KO':>11s} "
                 f"{'seuil KO':>9s} {'candidats':>10s} {'taux':>9s}")
    lines.append("-" * 78)
    for f in sorted(funnels.values(), key=lambda x: -x.candidates):
        lines.append(f"{f.family.value:26s} {f.states_examined:7d} "
                     f"{f.insufficient_data:11d} {f.detector_nothing:9d} "
                     f"{f.candidates:10d} {f.detection_rate:9.4%}")
    return "\n".join(lines)
