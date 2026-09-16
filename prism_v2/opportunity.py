"""Interface Opportunity — generique, pluggable, sans aucun score technique.

Le noyau economique (economics/execution/ledger) ne connait QUE cette
interface. Ajouter une opportunite demain ne doit modifier aucun module du
noyau : c'est verifie par tests/v2/test_architecture.py.

INTERDIT dans toute implementation d'Opportunity :
  - RSI, EMA, MACD, ADX, Bollinger, stochastique, "score" agrege ;
  - tout predicteur de direction fonde sur la forme passee des prix.
Une Opportunity decrit une SITUATION ECONOMIQUE mesurable, pas une prevision.

Si les donnees necessaires manquent, detect() doit retourner
INSUFFICIENT_DATA avec un motif. Inventer un signal est une faute.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Direction, Provenance, utc_now_iso
from .instruments import InstrumentSpec
from .orderbook import OrderBook


class DetectionStatus(str, enum.Enum):
    OK = "OK"                             # detection menee a bien (0 ou n candidats)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"   # donnees manquantes : on ne devine pas
    ERROR = "ERROR"


@dataclass
class MarketContext:
    """Tout ce que le noyau sait du marche a un instant, pour un instrument.

    Une Opportunity recoit ce contexte et rien d'autre. Elle ne fait aucun
    appel reseau elle-meme : la collecte est separee de l'interpretation.
    """

    instrument: InstrumentSpec
    ts_utc: str = field(default_factory=utc_now_iso)
    book: Optional[OrderBook] = None
    ticker: Optional[Dict[str, Any]] = None
    candles: Optional[List[List[str]]] = None
    funding: Optional[Dict[str, Any]] = None
    liquidations: Optional[List[Dict[str, Any]]] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def require_book(self) -> OrderBook:
        if self.book is None:
            raise ValueError(f"{self.instrument.inst_id}: carnet absent du contexte")
        return self.book


@dataclass
class Candidate:
    """Une opportunite economique mesuree, avant confrontation aux couts.

    `gross_capture_bps` est l'amplitude BRUTE, exprimee en bps du notionnel
    USD d'entree — meme unite que les couts, pour que la soustraction ait un
    sens. Ce n'est PAS un PnL : c'est ce qu'il y aurait a capturer avant
    frais, spread, impact, slippage et funding.

    `confidence` reste None sauf definition objective explicite. Aucun score
    heuristique n'est admis ici.
    """

    ts_utc: str
    instrument: InstrumentSpec
    opportunity_type: str
    direction: Direction
    gross_capture_bps: float
    capacity_usd: Optional[float]
    provenance: Provenance
    confidence: Optional[float] = None
    confidence_definition: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentSpec):
            raise TypeError("Candidate.instrument doit etre un InstrumentSpec complet, "
                            f"recu {type(self.instrument).__name__}")
        if not isinstance(self.direction, Direction):
            raise TypeError("Candidate.direction doit etre un Direction")
        if self.confidence is not None and not self.confidence_definition:
            raise ValueError("confidence fournie sans confidence_definition : "
                             "une confiance sans definition objective est interdite")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence hors [0,1]: {self.confidence}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_utc": self.ts_utc, "inst_id": self.instrument.inst_id,
            "inst_type": self.instrument.inst_type.value,
            "ct_type": self.instrument.ct_type,
            "settle_ccy": self.instrument.settle_ccy,
            "opportunity_type": self.opportunity_type,
            "direction": self.direction.value,
            "gross_capture_bps": self.gross_capture_bps,
            "capacity_usd": self.capacity_usd,
            "confidence": self.confidence,
            "confidence_definition": self.confidence_definition,
            "metadata": self.metadata,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class DetectionResult:
    status: DetectionStatus
    candidates: List[Candidate] = field(default_factory=list)
    reason: str = ""
    missing: List[str] = field(default_factory=list)

    @classmethod
    def ok(cls, candidates: List[Candidate]) -> "DetectionResult":
        return cls(DetectionStatus.OK, candidates)

    @classmethod
    def insufficient(cls, reason: str, missing: Optional[List[str]] = None) -> "DetectionResult":
        return cls(DetectionStatus.INSUFFICIENT_DATA, [], reason, missing or [])

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status.value, "reason": self.reason,
                "missing": self.missing, "n_candidates": len(self.candidates)}


class Opportunity(abc.ABC):
    """Contrat que toute famille d'opportunite doit respecter.

    Le noyau n'appelle que `name`, `requires` et `detect`.
    """

    #: Identifiant stable, ecrit tel quel au ledger.
    name: str = "unnamed"

    #: Champs de MarketContext necessaires. Sert au noyau pour expliquer un
    #: INSUFFICIENT_DATA sans connaitre l'implementation.
    requires: tuple = ()

    @abc.abstractmethod
    def detect(self, ctx: MarketContext) -> DetectionResult:
        """Retourne les candidats observes, ou INSUFFICIENT_DATA."""

    def missing_requirements(self, ctx: MarketContext) -> List[str]:
        return [r for r in self.requires if getattr(ctx, r, None) in (None, [], {})]


class OpportunityRegistry:
    """Branchement d'opportunites. Le noyau itere ici sans rien connaitre
    des implementations : ajouter une famille ne le modifie pas."""

    def __init__(self) -> None:
        self._items: Dict[str, Opportunity] = {}

    def register(self, opp: Opportunity) -> "OpportunityRegistry":
        if not isinstance(opp, Opportunity):
            raise TypeError(f"{opp!r} n'implemente pas Opportunity")
        if opp.name in self._items:
            raise ValueError(f"opportunite deja enregistree: {opp.name}")
        self._items[opp.name] = opp
        return self

    def detect_all(self, ctx: MarketContext) -> Dict[str, DetectionResult]:
        out: Dict[str, DetectionResult] = {}
        for name, opp in self._items.items():
            missing = opp.missing_requirements(ctx)
            if missing:
                out[name] = DetectionResult.insufficient(
                    f"donnees manquantes dans le contexte: {', '.join(missing)}", missing)
                continue
            try:
                out[name] = opp.detect(ctx)
            except Exception as exc:  # une opportunite ne doit jamais tuer le noyau
                out[name] = DetectionResult(DetectionStatus.ERROR, [], f"{type(exc).__name__}: {exc}")
        return out

    def names(self) -> List[str]:
        return sorted(self._items)

    def __len__(self) -> int:
        return len(self._items)
