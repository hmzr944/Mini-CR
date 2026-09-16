"""DISCOVERY LEDGER — trace immuable de chaque decouverte, gagnante ou non.

Le ledger de capture (prism_v2/ledger.py) enregistre les OPPORTUNITES. Celui-ci
enregistre les DECOUVERTES : ce qui a ete observe, teste, rejete, et pourquoi.

Regle fondatrice, identique a celle du ledger de capture : on ecrit TOUT.
Ne conserver que les decouvertes positives est la definition meme du biais de
survie. Un rejet est une information — souvent la plus utile.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .. import SCHEMA_VERSION, __version__
from ..core_types import utc_now_iso
from .falsification import FalsificationResult
from .hypothesis import Hypothesis

DEFAULT_DISCOVERY_PATH = Path(__file__).parent.parent / "ledger" / "discoveries.jsonl"

#: Champs garantis presents sur chaque enregistrement.
REQUIRED_FIELDS = (
    "discovery_id", "schema_version", "engine_version", "created_at",
    "source_observations", "data_window_ms", "instruments", "venues",
    "features_used", "hypothesis", "research_lineage", "tests_performed",
    "falsification_status", "economic_status", "execution_status",
    "result", "failure_reason",
)


@dataclass(frozen=True)
class DiscoveryRecord:
    """Enregistrement IMMUABLE. Toute revision cree un nouvel enregistrement
    qui reference le precedent — jamais une modification en place."""

    discovery_id: str
    hypothesis: Dict[str, Any]
    source_observations: int
    data_window_ms: List[int]
    instruments: List[str]
    venues: List[str]
    features_used: List[str]
    research_lineage: List[str]
    tests_performed: List[str]
    falsification_status: str
    falsification_reasons: List[str]
    economic_status: str
    execution_status: str
    result: Optional[str]
    failure_reason: Optional[str]
    effective_n: Optional[float]
    multiple_testing: Dict[str, Any]
    capture_study: Optional[Dict[str, Any]] = None
    paper_result: Optional[Dict[str, Any]] = None
    supersedes: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    engine_version: str = __version__
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DiscoveryLedger:
    """JSONL append-only. Rien n'est jamais reecrit."""

    def __init__(self, path: Path = DEFAULT_DISCOVERY_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set = set()

    def record(self, h: Hypothesis, falsification: FalsificationResult,
               lineage: List[str], multiple_testing: Dict[str, Any],
               source_observations: int,
               economic_status: str = "NOT_EVALUATED",
               execution_status: str = "NOT_EXECUTED",
               result: Optional[str] = None,
               failure_reason: Optional[str] = None,
               capture_study: Optional[Dict[str, Any]] = None,
               paper_result: Optional[Dict[str, Any]] = None,
               supersedes: Optional[str] = None) -> DiscoveryRecord:
        if h.hypothesis_id in self._seen and supersedes is None:
            raise ValueError(
                f"{h.hypothesis_id}: deja enregistre. Une revision doit creer un "
                "nouvel enregistrement referencant le precedent (supersedes).")
        rec = DiscoveryRecord(
            discovery_id=h.hypothesis_id, hypothesis=h.to_dict(),
            source_observations=source_observations,
            data_window_ms=list(h.data_window_ms), instruments=list(h.instruments),
            venues=list(h.venues), features_used=list(h.features_used),
            research_lineage=list(lineage), tests_performed=list(h.tests_performed),
            falsification_status=falsification.verdict.value,
            falsification_reasons=[r.value for r in falsification.reasons],
            economic_status=economic_status, execution_status=execution_status,
            result=result, failure_reason=failure_reason,
            effective_n=falsification.effective_n,
            multiple_testing=multiple_testing, capture_study=capture_study,
            paper_result=paper_result, supersedes=supersedes)
        missing = [f for f in REQUIRED_FIELDS if f not in rec.to_dict()]
        if missing:
            raise ValueError(f"enregistrement incomplet: {missing}")
        self._seen.add(h.hypothesis_id)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False,
                                default=str) + "\n")
        return rec

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in
                self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def summary(self) -> Dict[str, Any]:
        rows = self.read_all()
        by_fals: Dict[str, int] = {}
        by_econ: Dict[str, int] = {}
        by_reason: Dict[str, int] = {}
        by_mechanism: Dict[str, int] = {}
        for r in rows:
            by_fals[r["falsification_status"]] = by_fals.get(r["falsification_status"], 0) + 1
            by_econ[r["economic_status"]] = by_econ.get(r["economic_status"], 0) + 1
            for reason in r.get("falsification_reasons") or []:
                by_reason[reason] = by_reason.get(reason, 0) + 1
            m = (r.get("hypothesis") or {}).get("mechanism")
            if m:
                by_mechanism[m] = by_mechanism.get(m, 0) + 1
        return {
            "path": str(self.path), "n_discoveries": len(rows),
            "by_falsification": dict(sorted(by_fals.items())),
            "by_economic_status": dict(sorted(by_econ.items())),
            "by_mechanism": dict(sorted(by_mechanism.items(), key=lambda kv: -kv[1])),
            "rejection_reasons": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
            "n_survived": by_fals.get("SURVIVED", 0),
            "note": "toutes les decouvertes sont enregistrees, y compris les "
                    "rejets — ne garder que les positives serait un biais de survie",
        }
