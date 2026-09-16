"""Qualite des donnees. FAIL CLOSED.

Un carnet perime, une sequence trouee ou un horodatage incoherent ne doivent
jamais produire une decision. Le systeme prefere NE RIEN FAIRE a agir sur une
donnee dont il ne peut pas garantir la fraicheur.

Ce module ne juge PAS l'economie : il juge si la donnee est utilisable.
Il est place AVANT l'economie dans la chaine, et rien ne peut le contourner.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .instruments import InstrumentSpec, InvalidInstrument


class QualityVerdict(str, enum.Enum):
    OK = "OK"                 # utilisable
    DEGRADED = "DEGRADED"     # utilisable avec reserve explicite, jamais silencieuse
    UNUSABLE = "UNUSABLE"     # FAIL CLOSED : aucune decision possible

    @property
    def blocks_trading(self) -> bool:
        return self is QualityVerdict.UNUSABLE


class QualityIssue(str, enum.Enum):
    STALE_BOOK = "STALE_BOOK"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    TIMESTAMP_INVERSION = "TIMESTAMP_INVERSION"
    CLOCK_ANOMALY = "CLOCK_ANOMALY"
    INCOMPLETE_BOOK = "INCOMPLETE_BOOK"
    CROSSED_BOOK = "CROSSED_BOOK"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"
    INVALID_CONTRACT_METADATA = "INVALID_CONTRACT_METADATA"
    MALFORMED_EVENT = "MALFORMED_EVENT"
    MISSING_TIMESTAMP = "MISSING_TIMESTAMP"
    ABNORMAL_SPREAD = "ABNORMAL_SPREAD"


#: Problemes qui interdisent toute decision, sans exception.
BLOCKING_ISSUES = frozenset({
    QualityIssue.STALE_BOOK, QualityIssue.SEQUENCE_GAP,
    QualityIssue.INCOMPLETE_BOOK, QualityIssue.CROSSED_BOOK,
    QualityIssue.INSTRUMENT_MISMATCH, QualityIssue.INVALID_CONTRACT_METADATA,
    QualityIssue.MALFORMED_EVENT, QualityIssue.MISSING_TIMESTAMP,
    QualityIssue.TIMESTAMP_INVERSION,
    # Un horodatage dans le futur au-dela de la derive toleree casse la
    # relation entre l'horloge de l'exchange et la notre : l'age du carnet,
    # et donc TOUTE mesure de fraicheur ou de latence, devient indeterminable.
    # FAIL CLOSED : on ne peut pas juger un carnet dont on ne sait pas l'age.
    QualityIssue.CLOCK_ANOMALY,
})


@dataclass(frozen=True)
class QualityPolicy:
    """Seuils de qualite. EXPLICITES et CONFIGURABLES, jamais devines.

    Ce ne sont pas des parametres economiques : les faire varier ne change
    pas un resultat de rentabilite, seulement ce que le systeme accepte de
    regarder. Ils ne doivent jamais etre ajustes pour obtenir un resultat.
    """

    max_book_age_ms: int = 2_000
    max_clock_skew_ms: int = 10_000
    #: Un spread au-dela de ce seuil est ANORMAL (marche disloque ou carnet
    #: casse). DEGRADED, pas bloquant : c'est peut-etre l'evenement etudie.
    abnormal_spread_bps: float = 500.0
    allow_degraded: bool = True


@dataclass
class QualityReport:
    verdict: QualityVerdict
    issues: List[QualityIssue] = field(default_factory=list)
    details: List[str] = field(default_factory=list)
    book_age_ms: Optional[int] = None
    transport_delay_ms: Optional[int] = None
    checked_at_ms: Optional[int] = None

    @property
    def is_usable(self) -> bool:
        return not self.verdict.blocks_trading

    def blocking_issues(self) -> List[QualityIssue]:
        return [i for i in self.issues if i in BLOCKING_ISSUES]

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict.value,
                "issues": [i.value for i in self.issues],
                "details": self.details, "book_age_ms": self.book_age_ms,
                "transport_delay_ms": self.transport_delay_ms,
                "checked_at_ms": self.checked_at_ms}


def assess_book(book: Any, spec: InstrumentSpec, now_ms: int,
                policy: QualityPolicy = QualityPolicy(),
                expected_seq: Optional[int] = None,
                seen_seq_ids: Optional[set] = None) -> QualityReport:
    """Juge un carnet. Retourne UNUSABLE des qu'un probleme bloquant apparait.

    `now_ms` est fourni par l'appelant (jamais lu ici) pour que la fonction
    reste deterministe et testable.
    """
    issues: List[QualityIssue] = []
    details: List[str] = []
    age_ms: Optional[int] = None

    # 1. Coherence instrument — le carnet decrit-il bien CET instrument ?
    book_inst = getattr(getattr(book, "instrument", None), "inst_id", None)
    if book_inst != spec.inst_id:
        issues.append(QualityIssue.INSTRUMENT_MISMATCH)
        details.append(f"carnet={book_inst!r} attendu={spec.inst_id!r}")

    # 2. Metadonnees de contrat valides — sinon tout calcul est faux.
    try:
        spec.validate()
    except InvalidInstrument as exc:
        issues.append(QualityIssue.INVALID_CONTRACT_METADATA)
        details.append(str(exc))

    # 3. Horodatage present et coherent.
    ts_ms = getattr(book, "ts_ms", None)
    if ts_ms is None:
        issues.append(QualityIssue.MISSING_TIMESTAMP)
        details.append("carnet sans horodatage exchange exploitable")
    else:
        age_ms = now_ms - ts_ms
        if age_ms < -policy.max_clock_skew_ms:
            issues.append(QualityIssue.CLOCK_ANOMALY)
            details.append(f"horodatage dans le futur de {-age_ms}ms "
                           f"(derive d'horloge > {policy.max_clock_skew_ms}ms)")
        elif age_ms > policy.max_book_age_ms:
            issues.append(QualityIssue.STALE_BOOK)
            details.append(f"carnet age de {age_ms}ms > {policy.max_book_age_ms}ms")

    # 4. Sequence — un trou signifie un carnet potentiellement faux.
    seq = getattr(book, "seq_id", None)
    if seq is not None:
        if seen_seq_ids is not None and seq in seen_seq_ids:
            issues.append(QualityIssue.DUPLICATE_EVENT)
            details.append(f"seqId {seq} deja vu")
        if expected_seq is not None and str(seq) != str(expected_seq):
            issues.append(QualityIssue.SEQUENCE_GAP)
            details.append(f"seqId {seq} != attendu {expected_seq}")

    # 5. Integrite structurelle du carnet.
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        issues.append(QualityIssue.INCOMPLETE_BOOK)
        details.append(f"bids={len(bids)} asks={len(asks)}")
    else:
        best_bid, best_ask = bids[0].price, asks[0].price
        if best_bid >= best_ask:
            issues.append(QualityIssue.CROSSED_BOOK)
            details.append(f"carnet croise: bid {best_bid} >= ask {best_ask}")
        else:
            mid = (best_bid + best_ask) / 2.0
            spread_bps = (best_ask - best_bid) / mid * 10_000.0
            if spread_bps > policy.abnormal_spread_bps:
                issues.append(QualityIssue.ABNORMAL_SPREAD)
                details.append(f"spread {spread_bps:.1f}bps > "
                               f"{policy.abnormal_spread_bps}bps")

    blocking = [i for i in issues if i in BLOCKING_ISSUES]
    if blocking:
        verdict = QualityVerdict.UNUSABLE
    elif issues:
        verdict = (QualityVerdict.DEGRADED if policy.allow_degraded
                   else QualityVerdict.UNUSABLE)
    else:
        verdict = QualityVerdict.OK

    return QualityReport(verdict=verdict, issues=issues, details=details,
                         book_age_ms=age_ms, checked_at_ms=now_ms)


@dataclass
class SequenceTracker:
    """Suit la continuite d'un flux par canal+instrument.

    OKX books5 ne garantit pas une sequence strictement contigue entre
    snapshots : on detecte donc une REGRESSION (seq qui recule) et les
    DOUBLONS, qui sont des anomalies certaines, plutot que d'inventer une
    regle de contiguite qui produirait des faux positifs.
    """

    last_seq: Dict[str, int] = field(default_factory=dict)
    seen: Dict[str, set] = field(default_factory=dict)
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    duplicates: int = 0
    regressions: int = 0

    def observe(self, key: str, seq: Optional[int]) -> List[QualityIssue]:
        if seq is None:
            return []
        issues: List[QualityIssue] = []
        bucket = self.seen.setdefault(key, set())
        if seq in bucket:
            self.duplicates += 1
            issues.append(QualityIssue.DUPLICATE_EVENT)
        bucket.add(seq)
        if len(bucket) > 10_000:
            bucket.clear()
            bucket.add(seq)
        prev = self.last_seq.get(key)
        if prev is not None and seq < prev:
            self.regressions += 1
            self.gaps.append({"key": key, "previous": prev, "received": seq,
                              "kind": "regression"})
            issues.append(QualityIssue.SEQUENCE_GAP)
        self.last_seq[key] = max(seq, prev) if prev is not None else seq
        return issues

    def to_dict(self) -> Dict[str, Any]:
        return {"tracked_streams": len(self.last_seq), "duplicates": self.duplicates,
                "regressions": self.regressions, "gaps": self.gaps[:20]}
