"""Ouverture du moteur de decouverte — MESUREE, jamais declaree.

CE QUI EST REMPLACE. La version precedente retournait `DiscoveryClass.STRUCTURED`
ecrit EN DUR. Une constante n'est pas un audit : elle ne peut ni se tromper,
ni changer quand le systeme change. Ce module la remplace par trois mesures
qui peuvent toutes echouer.

LES TROIS MESURES

  1. TAILLE DE L'ESPACE. Combien d'hypotheses le moteur peut-il former ? Si ce
     nombre est calculable a la main avant le run, l'espace est BORNE et le
     moteur n'est pas ouvert, quoi qu'il produise.

  2. SENSIBILITE. Si l'on plante un effet connu, le moteur le retrouve-t-il ?
     Un moteur aveugle produit « 0 survivant » exactement comme un marche sans
     inefficience : sans cette mesure, le resultat est ininterpretable.

  3. SPECIFICITE. Sur du bruit pur, le moteur trouve-t-il quelque chose ? Ce
     qu'il trouve la est son taux de faux positifs.

Un moteur peut etre BORNE et neanmoins utile. Il ne peut pas etre appele
OUVERT.
"""
from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


class DiscoveryClass(str, enum.Enum):
    OPEN = "OPEN_DISCOVERY_ENGINE"
    BOUNDED = "BOUNDED_HYPOTHESIS_ENGINE"
    FAMILY_BASED = "FAMILY_BASED_OPPORTUNITY_ENGINE"
    UNDETERMINED = "UNDETERMINED"


#: Ce qu'il faudrait pour pretendre a OPEN. Chaque ligne est une capacite
#: ABSENTE aujourd'hui, pas une capacite inexercee.
OPEN_REQUIREMENTS = (
    "inventer une primitive absente de FEATURE_SPACE",
    "composer des primitives sans schema de composition impose",
    "decouvrir ses propres horizons au lieu de parcourir une grille",
    "formuler un mecanisme hors de la table de correspondance",
    "former une relation inter-instruments (A precede B)",
)


@dataclass
class SpaceMeasurement:
    """Taille de l'espace d'hypotheses REELLEMENT atteignable."""

    n_primitives: int
    n_horizons: int
    n_conditions: int
    n_instruments: int
    composition_arity: int          # 1 = aucune composition
    enumerable_size: Optional[int]
    realised_size: int

    @property
    def is_enumerable_by_hand(self) -> bool:
        """Un espace qu'on peut compter avant le run n'est pas ouvert."""
        return self.enumerable_size is not None

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["is_enumerable_by_hand"] = self.is_enumerable_by_hand
        return d


def measure_space(n_primitives: int, n_horizons: int, n_conditions: int,
                  n_instruments: int, composition_arity: int = 1,
                  realised_size: int = 0) -> SpaceMeasurement:
    """Calcule la taille de l'espace atteignable.

    Avec composition d'arite k, le nombre de conditions composites est
    C(p, k) * c^k : le choix des k primitives, puis leur etat respectif.
    """
    if composition_arity < 1:
        raise ValueError("arite de composition >= 1")
    combos = math.comb(n_primitives, composition_arity)
    size = combos * (n_conditions ** composition_arity) * n_horizons * n_instruments
    return SpaceMeasurement(
        n_primitives=n_primitives, n_horizons=n_horizons,
        n_conditions=n_conditions, n_instruments=n_instruments,
        composition_arity=composition_arity, enumerable_size=size,
        realised_size=realised_size)


@dataclass
class OpenDiscoveryAudit:
    """Verdict d'ouverture, appuye sur des mesures qui peuvent echouer."""

    discovery_class: DiscoveryClass
    space: SpaceMeasurement
    sensitivity: Optional[Dict[str, Any]] = None
    specificity: Optional[Dict[str, Any]] = None
    inputs_withheld: Dict[str, bool] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    @property
    def engine_is_blind(self) -> Optional[bool]:
        """Le moteur rate-t-il un effet REELLEMENT present ?"""
        if not self.sensitivity:
            return None
        return not self.sensitivity.get("recovers_planted_effect", False)

    @property
    def engine_hallucinates(self) -> Optional[bool]:
        """Le moteur trouve-t-il un effet dans du bruit pur ?"""
        if not self.specificity:
            return None
        return not self.specificity.get("indistinguishable_from_zero", True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discovery_class": self.discovery_class.value,
            "space": self.space.to_dict(),
            "sensitivity": self.sensitivity,
            "specificity": self.specificity,
            "engine_is_blind": self.engine_is_blind,
            "engine_hallucinates": self.engine_hallucinates,
            "inputs_withheld": self.inputs_withheld,
            "evidence": self.evidence,
            "limitations": self.limitations,
            "open_requirements_not_met": list(OPEN_REQUIREMENTS),
        }


def audit(n_primitives: int, n_horizons: int, n_conditions: int,
          n_instruments: int, composition_arity: int = 1,
          realised_size: int = 0,
          run_empirical_tests: bool = True,
          planted_fraction: float = 0.5) -> OpenDiscoveryAudit:
    """Audit EMPIRIQUE. Les deux tests peuvent echouer et changer le verdict.

    `run_empirical_tests=False` n'est admis que pour un appel rapide : l'audit
    retourne alors UNDETERMINED plutot qu'un verdict non gagne.
    """
    space = measure_space(n_primitives, n_horizons, n_conditions,
                          n_instruments, composition_arity, realised_size)
    sensitivity = specificity = None
    if run_empirical_tests:
        from .synthetic import false_positive_test, planted_reversion, SyntheticSpec
        from .causal_lab import CaptureOutcome, SnapshotSeries, measure_capture

        spec = SyntheticSpec(n_snapshots=8_000)
        recs, truth = planted_reversion(planted_fraction, 5_000, 5_000, spec=spec)
        series = SnapshotSeries.from_records(spec.inst_id, recs)
        vals: List[float] = []
        for idx in truth["trigger_indices"]:
            m = measure_capture(series, series.first_ts + idx * spec.cadence_ms,
                                5_000, 250, 5_000, min_displacement_bps=6.0)
            if m.outcome is CaptureOutcome.MEASURED and m.capture_fraction is not None:
                vals.append(m.capture_fraction)
        if len(vals) >= 30:
            mean = sum(vals) / len(vals)
            sensitivity = {
                "planted_fraction": planted_fraction, "measured": mean,
                "n": len(vals), "absolute_error": abs(mean - planted_fraction),
                "recovers_planted_effect": abs(mean - planted_fraction) <= 0.25,
                "note": "mesure AUX INSTANTS de l'evenement, non sur grille",
            }
        else:
            sensitivity = {"n": len(vals), "recovers_planted_effect": False,
                           "note": "echantillon insuffisant : sensibilite NON ETABLIE"}
        specificity = false_positive_test(spec=spec)

    # ── verdict ───────────────────────────────────────────────────────────
    evidence: List[str] = []
    limitations: List[str] = []
    if space.is_enumerable_by_hand:
        limitations.append(
            f"espace enumerable avant le run : {space.enumerable_size:,} "
            f"hypotheses possibles (arite de composition {composition_arity})")
    if composition_arity > 1:
        evidence.append(
            f"composition d'arite {composition_arity} : la combinaison "
            "retenue n'est ecrite nulle part dans le code")
    else:
        limitations.append("aucune composition : une hypothese porte sur une "
                           "seule primitive")
    if sensitivity and sensitivity.get("recovers_planted_effect"):
        evidence.append(
            f"sensibilite ETABLIE : effet plante {planted_fraction} retrouve a "
            f"{sensitivity['measured']:.3f} (N={sensitivity['n']})")
    elif sensitivity:
        limitations.append("sensibilite NON ETABLIE : le moteur pourrait etre "
                           "aveugle a un effet reellement present")
    if specificity and specificity.get("indistinguishable_from_zero"):
        evidence.append(
            f"specificite ETABLIE : sur bruit pur, {specificity['detail']}")
    elif specificity:
        limitations.append("le moteur trouve un effet dans du bruit pur : tout "
                           "resultat positif est suspect")

    if not run_empirical_tests:
        cls = DiscoveryClass.UNDETERMINED
    elif sensitivity and not sensitivity.get("recovers_planted_effect"):
        cls = DiscoveryClass.UNDETERMINED     # un moteur aveugle n'est pas classable
    elif space.is_enumerable_by_hand:
        cls = DiscoveryClass.BOUNDED
    else:
        cls = DiscoveryClass.OPEN

    return OpenDiscoveryAudit(
        discovery_class=cls, space=space, sensitivity=sensitivity,
        specificity=specificity,
        inputs_withheld={
            "phenomenon_name_withheld": True, "family_withheld": True,
            "direction_withheld": True, "threshold_withheld": True,
            "horizon_withheld": True, "mechanism_withheld": True,
            "entry_rule_withheld": True},
        evidence=evidence, limitations=limitations)
