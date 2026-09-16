"""Phenomenes, relations, hypotheses — objets de recherche IMMUABLES et traces.

ANTI-USINE-A-PATTERNS
Chaque objet porte sa lignee complete : d'ou il vient, sur quelles donnees,
avec quelles primitives, quels tests ont ete passes, et quel fut le verdict.
Un objet deja evalue ne peut plus etre modifie — toute revision cree une
NOUVELLE version qui reference la precedente. Une hypothese qu'on retouche
apres avoir vu son resultat n'est plus une hypothese : c'est un ajustement.

COMPTABILITE DES TESTS MULTIPLES
Tester 10 000 relations et rapporter les 3 meilleures est une faute
statistique, pas une decouverte. Le registre compte TOUT ce qui a ete teste
et applique une correction de Benjamini-Hochberg. Le nombre de tests est
rapporte avec les resultats, toujours.
"""
from __future__ import annotations

import enum
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core_types import utc_now_iso


class HypothesisStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"           # formulee, non encore attaquee
    REJECTED = "REJECTED"           # detruite par la falsification
    SURVIVED_FALSIFICATION = "SURVIVED_FALSIFICATION"
    ECONOMICALLY_DEAD = "ECONOMICALLY_DEAD"     # survit mais ne couvre pas ses couts
    ECONOMICALLY_OPEN = "ECONOMICALLY_OPEN"     # couts non mesures -> indecidable
    CAPTURE_TESTED = "CAPTURE_TESTED"           # passee au laboratoire PAPER
    INVALIDATED_OUT_OF_SAMPLE = "INVALIDATED_OUT_OF_SAMPLE"

    @property
    def is_terminal(self) -> bool:
        return self in (HypothesisStatus.REJECTED,
                        HypothesisStatus.ECONOMICALLY_DEAD,
                        HypothesisStatus.INVALIDATED_OUT_OF_SAMPLE)


class EconomicMechanism(str, enum.Enum):
    """Mecanismes economiques documentes pouvant expliquer une relation.

    Une etiquette n'est PAS une preuve : elle dit quelle histoire economique
    rendrait la relation plausible, pour qu'on puisse la tester ou l'ecarter.
    """

    LIQUIDITY_FRAGMENTATION = "LIQUIDITY_FRAGMENTATION"
    CROSS_INSTRUMENT_ARBITRAGE = "CROSS_INSTRUMENT_ARBITRAGE"
    FORCED_FLOW = "FORCED_FLOW"
    INVENTORY_IMBALANCE = "INVENTORY_IMBALANCE"
    LIQUIDITY_WITHDRAWAL = "LIQUIDITY_WITHDRAWAL"
    DELAYED_ADJUSTMENT = "DELAYED_ADJUSTMENT"
    FUNDING_BASIS_INTERACTION = "FUNDING_BASIS_INTERACTION"
    CROSS_VENUE_LATENCY = "CROSS_VENUE_LATENCY"
    TEMPORARY_MARKET_IMPACT = "TEMPORARY_MARKET_IMPACT"
    TEMPORARY_DISLOCATION = "TEMPORARY_DISLOCATION"
    UNEXPLAINED = "UNEXPLAINED"      # aucune histoire economique plausible


@dataclass(frozen=True)
class Phenomenon:
    """AGENT 2 — un etat de marche recurrent et decrit, pas un trade.

    Un phenomene est defini par une CONDITION sur une primitive observee
    (ex: `spread_bps` dans son decile superieur), avec sa frequence et son
    contexte. Il ne porte aucune direction.
    """

    phenomenon_id: str
    feature: str
    condition: str                  # "high" | "low" — decile extreme observe
    threshold: float                # borne du decile, MESUREE sur l'echantillon
    inst_id: str
    venue: str
    n_occurrences: int
    n_observations: int
    window_ms: Tuple[int, int]
    created_at: str = field(default_factory=utc_now_iso)

    @property
    def frequency(self) -> float:
        return self.n_occurrences / self.n_observations if self.n_observations else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["frequency"] = self.frequency
        return d


@dataclass(frozen=True)
class Relation:
    """AGENT 3 — relation temporelle MESUREE entre un phenomene et une suite.

    « Quand le phenomene P est present a T, comment le mid a-t-il bouge entre
    T et T+H ? » C'est une statistique DESCRIPTIVE sur l'echantillon, pas une
    prevision. Aucune relation n'est predictive par defaut.

    `p_value` est bilaterale et approximative (test t sur la moyenne). Elle
    sert UNIQUEMENT a la correction de tests multiples, jamais a affirmer
    une verite.
    """

    relation_id: str
    phenomenon_id: str
    feature: str
    condition: str
    inst_id: str
    horizon_ms: int
    n: int                                  # taille d'echantillon conditionnel
    mean_forward_bps: float
    median_forward_bps: float
    std_forward_bps: float
    baseline_mean_bps: float                # mouvement moyen hors condition
    n_baseline: int
    p_value: Optional[float]
    sample: str                             # "DISCOVERY" | "HOLDOUT"
    created_at: str = field(default_factory=utc_now_iso)

    @property
    def excess_bps(self) -> float:
        """Ecart a la reference. C'est la seule grandeur d'interet : un
        mouvement moyen qui vaut celui du reste du temps n'est pas un edge."""
        return self.mean_forward_bps - self.baseline_mean_bps

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["excess_bps"] = self.excess_bps
        return d


def _hash(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Hypothesis:
    """AGENT 4 — hypothese economique IMMUABLE, avec lignee complete.

    Elle affirme : « la relation R pourrait s'expliquer par le mecanisme M,
    et si c'est le cas elle serait capturable de telle maniere ». Ce n'est
    PAS une verite, et le statut le dit a chaque instant.
    """

    hypothesis_id: str
    version: int
    relation: Relation
    phenomenon: Phenomenon
    mechanism: EconomicMechanism
    mechanism_rationale: str
    #: Primitives REELLEMENT utilisees. Sert a detecter une fuite de donnee.
    features_used: Tuple[str, ...]
    #: Fenetre de donnees dont l'hypothese est issue.
    data_window_ms: Tuple[int, int]
    instruments: Tuple[str, ...]
    venues: Tuple[str, ...]
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    #: Identifiant de la version precedente, si revision.
    supersedes: Optional[str] = None
    tests_performed: Tuple[str, ...] = ()
    rejection_reasons: Tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def propose(cls, relation: Relation, phenomenon: Phenomenon,
                mechanism: EconomicMechanism, rationale: str,
                venues: Sequence[str] = ("OKX",)) -> "Hypothesis":
        hid = _hash(relation.relation_id, mechanism.value, 1)
        return cls(
            hypothesis_id=hid, version=1, relation=relation, phenomenon=phenomenon,
            mechanism=mechanism, mechanism_rationale=rationale,
            features_used=(relation.feature,),
            data_window_ms=phenomenon.window_ms,
            instruments=(relation.inst_id,), venues=tuple(venues))

    def with_status(self, status: HypothesisStatus, tests: Sequence[str] = (),
                    reasons: Sequence[str] = ()) -> "Hypothesis":
        """Nouvelle VERSION portant le verdict. L'originale reste intacte.

        Une hypothese modifiee apres avoir vu son resultat n'est plus une
        hypothese : la chaine de versions rend toute retouche visible.
        """
        return replace(
            self, status=status,
            tests_performed=tuple(self.tests_performed) + tuple(tests),
            rejection_reasons=tuple(self.rejection_reasons) + tuple(reasons),
            version=self.version + 1,
            hypothesis_id=_hash(self.relation.relation_id, self.mechanism.value,
                                self.version + 1),
            supersedes=self.hypothesis_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id, "version": self.version,
            "supersedes": self.supersedes, "status": self.status.value,
            "mechanism": self.mechanism.value,
            "mechanism_rationale": self.mechanism_rationale,
            "features_used": list(self.features_used),
            "data_window_ms": list(self.data_window_ms),
            "instruments": list(self.instruments), "venues": list(self.venues),
            "tests_performed": list(self.tests_performed),
            "rejection_reasons": list(self.rejection_reasons),
            "relation": self.relation.to_dict(),
            "phenomenon": self.phenomenon.to_dict(),
            "created_at": self.created_at}


@dataclass
class MultipleTestingAccount:
    """Comptabilite des tests. Sans elle, toute « decouverte » est suspecte.

    Tester N relations independantes a un seuil de 5 % produit ~0.05*N faux
    positifs. Rapporter les meilleurs sans dire combien ont ete testes est la
    faute classique du data mining.
    """

    n_tests: int = 0
    n_with_pvalue: int = 0
    alpha: float = 0.05
    p_values: List[float] = field(default_factory=list)

    def record(self, p_value: Optional[float]) -> None:
        self.n_tests += 1
        if p_value is not None and 0.0 <= p_value <= 1.0:
            self.n_with_pvalue += 1
            self.p_values.append(p_value)

    def bonferroni_threshold(self) -> Optional[float]:
        return self.alpha / self.n_tests if self.n_tests else None

    def benjamini_hochberg_threshold(self) -> Optional[float]:
        """Seuil BH : controle le taux de fausses decouvertes.

        Retourne le plus grand p tel que p <= alpha * rang / m. None si
        aucune valeur ne passe — c'est-a-dire : aucune decouverte.
        """
        if not self.p_values:
            return None
        ordered = sorted(self.p_values)
        m = len(ordered)
        threshold = None
        for rank, p in enumerate(ordered, start=1):
            if p <= self.alpha * rank / m:
                threshold = p
        return threshold

    def survives_correction(self, p_value: Optional[float]) -> bool:
        if p_value is None:
            return False
        bh = self.benjamini_hochberg_threshold()
        return bh is not None and p_value <= bh

    def to_dict(self) -> Dict[str, Any]:
        bh = self.benjamini_hochberg_threshold()
        return {"n_tests": self.n_tests, "n_with_pvalue": self.n_with_pvalue,
                "alpha": self.alpha,
                "bonferroni_threshold": self.bonferroni_threshold(),
                "benjamini_hochberg_threshold": bh,
                "n_surviving_bh": (sum(1 for p in self.p_values if p <= bh)
                                   if bh is not None else 0),
                "note": ("aucune relation ne survit a la correction pour tests "
                         "multiples" if bh is None else
                         "les relations survivantes restent des statistiques "
                         "d'echantillon, pas des edges demontres")}


@dataclass
class HypothesisRegistry:
    """Registre immuable. Chaque version est conservee, rien n'est ecrase."""

    versions: Dict[str, Hypothesis] = field(default_factory=dict)
    lineage: Dict[str, List[str]] = field(default_factory=dict)
    accounting: MultipleTestingAccount = field(default_factory=MultipleTestingAccount)

    def register(self, h: Hypothesis) -> Hypothesis:
        if h.hypothesis_id in self.versions:
            existing = self.versions[h.hypothesis_id]
            if existing.to_dict() != h.to_dict():
                raise ValueError(
                    f"{h.hypothesis_id}: tentative de modification silencieuse "
                    "d'une hypothese existante — creer une nouvelle version")
            return existing
        self.versions[h.hypothesis_id] = h
        root = h.supersedes or h.hypothesis_id
        chain = self.lineage.setdefault(root, [])
        if h.hypothesis_id not in chain:
            chain.append(h.hypothesis_id)
        return h

    def supersede(self, old: Hypothesis, new: Hypothesis) -> Hypothesis:
        if new.supersedes != old.hypothesis_id:
            raise ValueError("la nouvelle version doit referencer la precedente")
        self.register(new)
        root = old.supersedes or old.hypothesis_id
        self.lineage.setdefault(root, [old.hypothesis_id]).append(new.hypothesis_id)
        return new

    def latest(self) -> List[Hypothesis]:
        """Derniere version de chaque lignee."""
        superseded = {h.supersedes for h in self.versions.values() if h.supersedes}
        return [h for hid, h in self.versions.items() if hid not in superseded]

    def by_status(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for h in self.latest():
            counts[h.status.value] = counts.get(h.status.value, 0) + 1
        return dict(sorted(counts.items()))

    def funnel(self) -> Dict[str, Any]:
        """Entonnoir complet. On rapporte TOUT, pas seulement les survivants."""
        latest = self.latest()
        by_status = self.by_status()
        return {
            "relations_tested": self.accounting.n_tests,
            "hypotheses_proposed": len(latest),
            "hypotheses_rejected": by_status.get("REJECTED", 0),
            "survived_falsification": by_status.get("SURVIVED_FALSIFICATION", 0),
            "economically_dead": by_status.get("ECONOMICALLY_DEAD", 0),
            "economically_open": by_status.get("ECONOMICALLY_OPEN", 0),
            "capture_tested": by_status.get("CAPTURE_TESTED", 0),
            "invalidated_out_of_sample": by_status.get("INVALIDATED_OUT_OF_SAMPLE", 0),
            "total_versions_stored": len(self.versions),
            "multiple_testing": self.accounting.to_dict(),
        }
