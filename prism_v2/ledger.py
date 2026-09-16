"""Capture Ledger — memoire persistante et versionnee du systeme.

C'est la brique dont l'absence a fait perdre l'historique T1-T45 : 45 taches
revendiquees, zero document, aucun resultat reproductible. Ici, CHAQUE
opportunite produit un enregistrement, y compris — et surtout — quand elle
est rejetee ou non resolue. Un rejet est une information, pas un non-evenement.

Format : JSONL append-only, une ligne = une observation. Versionne dans le
depot. Aucun secret : le ledger ne contient que des donnees de marche
publiques, des mesures derivees et des resultats PAPER.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import SCHEMA_VERSION, __version__
from .core_types import ExecutionMode, utc_now_iso
from .economics import CaptureStatus, Evaluation
from .execution import PaperFill, PaperRoundTrip
from .opportunity import Candidate
from .reconciliation import Reconciliation

DEFAULT_LEDGER_PATH = Path(__file__).parent / "ledger" / "captures.jsonl"

#: Champs garantis presents sur chaque enregistrement.
REQUIRED_FIELDS = (
    "schema_version", "ts_utc", "inst_id", "inst_type", "opportunity_type",
    "gross_capture_bps", "fees_bps", "spread_bps", "impact_bps", "slippage_bps",
    "funding_bps", "capacity_usd", "expected_net_capture_bps", "status",
    "rejection_reason", "execution_mode", "realized_pnl_usd", "provenance",
)

#: Motifs interdits : garde anti-fuite de secret au moment de l'ecriture.
_SECRET_HINTS = ("api_key", "apikey", "secret", "passphrase", "password",
                 "private_key", "token", "okx_api", "authorization")


class SecretInLedger(ValueError):
    """Une tentative d'ecriture contient une cle ressemblant a un secret."""


def _assert_no_secrets(record: Dict[str, Any], _path: str = "") -> None:
    for k, v in record.items():
        key_l = str(k).lower()
        if any(h in key_l for h in _SECRET_HINTS):
            raise SecretInLedger(f"champ potentiellement secret dans le ledger: {_path}{k}")
        if isinstance(v, dict):
            _assert_no_secrets(v, f"{_path}{k}.")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    _assert_no_secrets(item, f"{_path}{k}[{i}].")


def _cost_value(costs: Optional[Any], name: str) -> Optional[float]:
    """Valeur d'une composante, ou None si UNKNOWN. JAMAIS 0 par defaut."""
    if costs is None:
        return None
    for c in costs.components():
        if c.name == name:
            return c.value_bps
    return None


def _cost_quality(costs: Optional[Any], name: str) -> Optional[str]:
    if costs is None:
        return None
    for c in costs.components():
        if c.name == name:
            return c.quality.value
    return None


@dataclass
class CaptureLedger:
    path: Path = field(default_factory=lambda: DEFAULT_LEDGER_PATH)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---- ecriture ------------------------------------------------------------
    def record(self, candidate: Candidate, evaluation: Evaluation,
               costs: Optional[Any] = None,
               fill: Optional[PaperFill] = None,
               round_trip: Optional[PaperRoundTrip] = None,
               reconciliation: Optional[Reconciliation] = None,
               run_id: Optional[str] = None,
               notes: str = "") -> Dict[str, Any]:
        """Construit et persiste l'enregistrement d'une opportunite.

        Les couts UNKNOWN sont ecrits `null`, jamais 0, et leur qualite est
        conservee dans `cost_quality` pour rendre l'ecart visible a la lecture.
        """
        spec = candidate.instrument
        rec: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "engine_version": __version__,
            "run_id": run_id,
            "ts_utc": candidate.ts_utc,
            "recorded_at": utc_now_iso(),
            # ── instrument (complet : aucune ambiguite spot/lineaire/inverse)
            "inst_id": spec.inst_id,
            "inst_type": spec.inst_type.value,
            "ct_type": spec.ct_type,
            "ct_val": spec.ct_val,
            "ct_mult": spec.ct_mult,
            "settle_ccy": spec.settle_ccy,
            # ── opportunite
            "opportunity_type": candidate.opportunity_type,
            "direction": candidate.direction.value,
            "gross_capture_bps": candidate.gross_capture_bps,
            "confidence": candidate.confidence,
            "confidence_definition": candidate.confidence_definition,
            # ── couts (null si UNKNOWN)
            "fees_bps": _cost_value(costs, "fees"),
            "spread_bps": _cost_value(costs, "spread"),
            "impact_bps": _cost_value(costs, "impact"),
            "slippage_bps": _cost_value(costs, "slippage"),
            "funding_bps": _cost_value(costs, "funding"),
            "total_cost_bps": evaluation.total_cost_bps,
            "cost_quality": {n: _cost_quality(costs, n) for n in
                             ("fees", "spread", "impact", "slippage", "funding")},
            "weakest_quality": evaluation.weakest_quality.value,
            "unresolved_components": evaluation.unresolved_components,
            # ── economie
            "capacity_usd": candidate.capacity_usd,
            "expected_net_capture_bps": evaluation.expected_net_capture_bps,
            "status": evaluation.status.value,
            "rejection_reason": evaluation.rejection_reason,
            # ── execution (PAPER uniquement)
            "execution_mode": fill.mode if fill else ExecutionMode.PAPER.value,
            "executed": fill is not None and not fill.is_rejected,
            "fill": fill.to_dict() if fill else None,
            "round_trip": round_trip.to_dict() if round_trip else None,
            "realized_pnl_usd": round_trip.realized_pnl_usd if round_trip else None,
            "realized_pnl_settle_ccy": (round_trip.realized_pnl_settle_ccy
                                        if round_trip else None),
            "realized_bps": round_trip.realized_bps if round_trip else None,
            "reconciliation": reconciliation.to_dict() if reconciliation else None,
            # ── tracabilite
            "metadata": candidate.metadata,
            "provenance": candidate.provenance.to_dict(),
            "notes": notes,
        }
        missing = [f for f in REQUIRED_FIELDS if f not in rec]
        if missing:
            raise ValueError(f"enregistrement incomplet, champs manquants: {missing}")
        _assert_no_secrets(rec)
        self._append(rec)
        return rec

    def _append(self, rec: Dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True, default=str)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---- lecture -------------------------------------------------------------
    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.read_all())

    def __len__(self) -> int:
        return len(self.read_all())

    def summary(self) -> Dict[str, Any]:
        rows = self.read_all()
        by_status: Dict[str, int] = {}
        by_opp: Dict[str, int] = {}
        by_inst: Dict[str, int] = {}
        unresolved: Dict[str, int] = {}
        for r in rows:
            by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
            by_opp[r.get("opportunity_type", "?")] = by_opp.get(r.get("opportunity_type", "?"), 0) + 1
            by_inst[r.get("inst_id", "?")] = by_inst.get(r.get("inst_id", "?"), 0) + 1
            for c in r.get("unresolved_components") or []:
                unresolved[c] = unresolved.get(c, 0) + 1
        executed = [r for r in rows if r.get("executed")]
        realized = [r["realized_pnl_usd"] for r in rows if r.get("realized_pnl_usd") is not None]
        return {
            "path": str(self.path), "n_records": len(rows),
            "by_status": dict(sorted(by_status.items())),
            "by_opportunity": dict(sorted(by_opp.items())),
            "by_instrument": dict(sorted(by_inst.items())),
            "unresolved_component_counts": dict(sorted(unresolved.items())),
            "n_executed_paper": len(executed),
            "n_with_realized_pnl": len(realized),
            "sum_realized_pnl_usd": round(sum(realized), 8) if realized else None,
            "execution_modes": sorted({r.get("execution_mode") for r in rows} - {None}),
        }
