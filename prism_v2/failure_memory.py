"""Failure Memory — pourquoi les opportunites meurent.

Pas de ML, pas de boite noire : une TAXONOMIE et un comptage. L'objectif est
de repondre, apres plusieurs semaines de collecte, a une seule question :

    "Quelle contrainte detruit reellement l'edge ?"

Si 95 % des rejets sont COST_TOO_HIGH, le probleme est le cout. Si ce sont des
LATENCY_TOO_HIGH, le probleme est l'infrastructure. Si ce sont des
INSUFFICIENT_DATA, le probleme est la collecte — et surtout PAS l'absence
d'edge. Confondre ces trois cas est exactement ce qui a fait tourner V33 en
rond pendant 45 taches.
"""
from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


class FailureReason(str, enum.Enum):
    NO_EDGE = "NO_EDGE"                              # brut <= 0
    COST_TOO_HIGH = "COST_TOO_HIGH"                  # brut > 0 mais net <= 0
    CAPACITY_TOO_LOW = "CAPACITY_TOO_LOW"
    LATENCY_TOO_HIGH = "LATENCY_TOO_HIGH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_BOOK = "STALE_BOOK"
    LIQUIDITY_WITHDRAWAL = "LIQUIDITY_WITHDRAWAL"
    ADVERSE_SELECTION = "ADVERSE_SELECTION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    RISK_BLOCKED = "RISK_BLOCKED"
    DATA_QUALITY = "DATA_QUALITY"
    # ── causes issues de la couche de RECHERCHE ──────────────────────────
    FEES = "FEES"
    SPREAD = "SPREAD"
    IMPACT = "IMPACT"
    LOOKAHEAD = "LOOKAHEAD"
    DATA_LEAKAGE = "DATA_LEAKAGE"
    DENOMINATION_ERROR = "DENOMINATION_ERROR"
    MULTIPLE_TESTING = "MULTIPLE_TESTING"
    UNKNOWN = "UNKNOWN"

    @property
    def is_data_problem(self) -> bool:
        """Distingue "on n'a pas pu mesurer" de "il n'y a pas d'edge".

        Confondre les deux est la faute qui fait abandonner une piste vivante
        ou poursuivre une piste morte.
        """
        return self in (FailureReason.INSUFFICIENT_DATA, FailureReason.STALE_BOOK,
                        FailureReason.DATA_QUALITY, FailureReason.INSTRUMENT_MISMATCH,
                        FailureReason.UNKNOWN)

    @property
    def is_methodological(self) -> bool:
        """Faute de METHODE, distincte d'une absence d'edge et d'un manque de
        donnee. Une hypothese tuee pour look-ahead ne dit rien du marche :
        elle dit que la mesure etait fausse."""
        return self in (FailureReason.LOOKAHEAD, FailureReason.DATA_LEAKAGE,
                        FailureReason.DENOMINATION_ERROR,
                        FailureReason.MULTIPLE_TESTING)


def classify(record: Dict[str, Any]) -> FailureReason:
    """Impute une cause a un enregistrement de ledger. Deterministe et testable.

    L'ordre des tests compte : une donnee inutilisable prime sur une economie
    negative, car l'economie calculee sur une donnee douteuse ne veut rien dire.
    """
    status = record.get("status")

    quality = record.get("data_quality") or {}
    if quality.get("verdict") == "UNUSABLE":
        issues = set(quality.get("issues") or [])
        if "STALE_BOOK" in issues:
            return FailureReason.STALE_BOOK
        if "INSTRUMENT_MISMATCH" in issues:
            return FailureReason.INSTRUMENT_MISMATCH
        return FailureReason.DATA_QUALITY

    if record.get("risk_blocked"):
        return FailureReason.RISK_BLOCKED

    if status == "UNRESOLVED":
        return FailureReason.INSUFFICIENT_DATA

    recon = record.get("reconciliation") or {}
    cause = recon.get("cause")
    if cause == "PARTIAL_FILL_DEPTH":
        return FailureReason.LIQUIDITY_WITHDRAWAL
    if cause == "ORDER_REJECTED":
        return FailureReason.EXECUTION_FAILURE

    if status == "REJECTED":
        reason = (record.get("rejection_reason") or "").lower()
        if "capacite" in reason or "capacity" in reason:
            return FailureReason.CAPACITY_TOO_LOW
        gross = record.get("gross_capture_bps")
        if gross is not None and gross <= 0:
            return FailureReason.NO_EDGE
        latency = record.get("latency_bps")
        total = record.get("total_cost_bps")
        if latency is not None and total and total > 0 and latency / total > 0.5:
            return FailureReason.LATENCY_TOO_HIGH
        return FailureReason.COST_TOO_HIGH

    return FailureReason.UNKNOWN


@dataclass
class FailureMemory:
    """Agregation des causes. Ne pondere rien, ne predit rien : elle compte."""

    counts: Counter = field(default_factory=Counter)
    by_instrument: Dict[str, Counter] = field(default_factory=dict)
    by_opportunity: Dict[str, Counter] = field(default_factory=dict)
    total: int = 0

    def observe(self, record: Dict[str, Any]) -> FailureReason:
        reason = classify(record)
        self.counts[reason.value] += 1
        self.total += 1
        inst = record.get("inst_id", "?")
        opp = record.get("opportunity_type", "?")
        self.by_instrument.setdefault(inst, Counter())[reason.value] += 1
        self.by_opportunity.setdefault(opp, Counter())[reason.value] += 1
        return reason

    @classmethod
    def from_records(cls, records: Iterable[Dict[str, Any]]) -> "FailureMemory":
        fm = cls()
        for r in records:
            fm.observe(r)
        return fm

    def dominant(self) -> Optional[str]:
        return self.counts.most_common(1)[0][0] if self.counts else None

    def three_way_split(self) -> Dict[str, Any]:
        """Repartition en TROIS causes, pas deux. La distinction decide de
        l'action : collecter, corriger la methode, ou abandonner la piste."""
        data = meth = econ = 0
        for r, n in self.counts.items():
            reason = FailureReason(r)
            if reason.is_methodological:
                meth += n
            elif reason.is_data_problem:
                data += n
            else:
                econ += n
        if self.total == 0:
            verdict = "aucune observation"
        elif meth >= max(data, econ):
            verdict = ("fautes de METHODE dominantes — corriger la mesure avant "
                       "toute conclusion sur le marche")
        elif data > econ:
            verdict = "collecte insuffisante — aucune conclusion economique"
        else:
            verdict = "rejets majoritairement economiques"
        return {"data_problems": data, "methodological_errors": meth,
                "economic_rejections": econ, "total": self.total,
                "verdict": verdict}

    def data_vs_economics(self) -> Dict[str, Any]:
        """Repartition entre "on n'a pas pu mesurer" et "l'economie ne passe pas".

        C'est la lecture la plus importante : elle dit s'il faut collecter
        davantage ou abandonner la piste.
        """
        data = sum(n for r, n in self.counts.items()
                   if FailureReason(r).is_data_problem)
        econ = self.total - data
        return {"data_problems": data, "economic_rejections": econ,
                "total": self.total,
                "verdict": ("collecte insuffisante — aucune conclusion economique"
                            if data > econ else
                            "rejets majoritairement economiques" if self.total else
                            "aucune observation")}

    def to_dict(self) -> Dict[str, Any]:
        return {"total": self.total, "counts": dict(self.counts.most_common()),
                "dominant": self.dominant(),
                "by_instrument": {k: dict(v) for k, v in sorted(self.by_instrument.items())},
                "by_opportunity": {k: dict(v) for k, v in sorted(self.by_opportunity.items())},
                "data_vs_economics": self.data_vs_economics(),
                "three_way_split": self.three_way_split()}
