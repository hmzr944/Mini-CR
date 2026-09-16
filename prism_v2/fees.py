"""Frais — ObservedFees / DerivedFees / AssumedFees.

PRIORITE ECONOMIQUE. Le dernier run l'a montre : les couts CONNUS suffisent a
tuer la plupart des candidates, et les frais en sont le poste dominant. Tant
qu'ils restent ASSUMED, aucune evaluation ne peut passer en mode EXECUTION.

TROIS NIVEAUX, JAMAIS CONFONDUS
    ObservedFees : lus sur le compte reel (/api/v5/account/trade-fee).
                   Seuls a autoriser EvaluationMode.EXECUTION.
    DerivedFees  : deduits de fills reels (frais payes / notionnel).
    AssumedFees  : bareme public. Utilisables en DISCOVERY et en
                   CAPTURE_VALIDATION, jamais pour engager du capital.

SECURITE
Ce module ne lit AUCUNE cle, n'en accepte AUCUNE en argument, et ne journalise
aucun secret. Il expose une INTERFACE que devrait implementer un client
authentifie, et documente exactement ce qui manque. Le fournir sans cle est
impossible par construction : `NoCredentialsFeeProvider` retourne UNKNOWN.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .core_types import Quality, utc_now_iso
from .costs import CostComponent
from .instruments import InstrumentSpec


class FeeTierSource(str, enum.Enum):
    ACCOUNT_API = "ACCOUNT_API"           # /api/v5/account/trade-fee — authentifie
    REALISED_FILLS = "REALISED_FILLS"     # deduit de fills reels
    PUBLIC_SCHEDULE = "PUBLIC_SCHEDULE"   # bareme publie
    NONE = "NONE"


@dataclass(frozen=True)
class FeeSchedule:
    """Bareme applicable a un instrument, avec sa qualite epistemique."""

    inst_id: str
    maker_bps: Optional[float]
    taker_bps: Optional[float]
    quality: Quality
    source: FeeTierSource
    note: str
    observed_at: str = field(default_factory=utc_now_iso)
    n_samples: Optional[int] = None

    def __post_init__(self) -> None:
        if self.quality is Quality.UNKNOWN:
            if self.maker_bps is not None or self.taker_bps is not None:
                raise ValueError("UNKNOWN ne peut pas porter de valeur")
        else:
            if self.maker_bps is None or self.taker_bps is None:
                raise ValueError(f"{self.quality.value} exige maker et taker")
            if self.maker_bps < 0 or self.taker_bps < 0:
                raise ValueError("frais negatifs : un rebate doit etre explicite")

    @property
    def is_known(self) -> bool:
        return self.quality is not Quality.UNKNOWN

    @property
    def authorises_execution(self) -> bool:
        """Seuls des frais OBSERVED autorisent un engagement de capital."""
        return self.quality is Quality.OBSERVED

    def round_trip_bps(self, taker_legs: int = 2, maker_legs: int = 0
                       ) -> Optional[float]:
        if not self.is_known:
            return None
        # Une jambe DEMANDEE dont le taux est absent rendrait le total faux par
        # defaut : on refuse de chiffrer plutot que d'offrir cette jambe.
        if taker_legs and self.taker_bps is None:
            return None
        if maker_legs and self.maker_bps is None:
            return None
        return (taker_legs * (self.taker_bps or 0.0)
                + maker_legs * (self.maker_bps or 0.0))

    def to_component(self, taker_legs: int = 2, maker_legs: int = 0) -> CostComponent:
        """Traduit en composante de cout, en preservant la qualite."""
        value = self.round_trip_bps(taker_legs, maker_legs)
        if value is None:
            return CostComponent.unknown(
                "fees", source=self.source.value, note=self.note)
        return CostComponent(name="fees", value_bps=value, quality=self.quality,
                             source=f"{self.source.value} ({self.inst_id})",
                             note=f"{self.note} "
                                  f"(taker_legs={taker_legs}, maker_legs={maker_legs})")

    def to_dict(self) -> Dict[str, Any]:
        return {"inst_id": self.inst_id, "maker_bps": self.maker_bps,
                "taker_bps": self.taker_bps, "quality": self.quality.value,
                "source": self.source.value, "note": self.note,
                "observed_at": self.observed_at, "n_samples": self.n_samples,
                "authorises_execution": self.authorises_execution}


class FeeProvider(abc.ABC):
    """Interface d'un fournisseur de frais."""

    name: str

    @abc.abstractmethod
    def schedule_for(self, spec: InstrumentSpec) -> FeeSchedule:
        ...


#: Bareme public OKX, palier de base (Lv1), perpetuels.
OKX_PUBLIC_MAKER_BPS = 2.0
OKX_PUBLIC_TAKER_BPS = 5.0


class AssumedFeeProvider(FeeProvider):
    """Bareme PUBLIC. Explicitement ASSUMED : le palier reel du compte peut
    etre meilleur (volume) ou pire (promotions expirees). Ne peut jamais
    autoriser une execution."""

    name = "ASSUMED_PUBLIC_SCHEDULE"

    def __init__(self, maker_bps: float = OKX_PUBLIC_MAKER_BPS,
                 taker_bps: float = OKX_PUBLIC_TAKER_BPS):
        self.maker_bps = maker_bps
        self.taker_bps = taker_bps

    def schedule_for(self, spec: InstrumentSpec) -> FeeSchedule:
        return FeeSchedule(
            inst_id=spec.inst_id, maker_bps=self.maker_bps, taker_bps=self.taker_bps,
            quality=Quality.ASSUMED, source=FeeTierSource.PUBLIC_SCHEDULE,
            note="bareme public OKX palier de base, NON verifie sur ce compte. "
                 "Insuffisant pour EvaluationMode.EXECUTION.")


class NoCredentialsFeeProvider(FeeProvider):
    """Posture stricte : aucun identifiant, donc frais UNKNOWN.

    C'est le fournisseur par defaut. Il documente PRECISEMENT ce qui manque,
    et n'accepte aucune cle : impossible d'en fournir une par ce chemin.
    """

    name = "NO_CREDENTIALS"

    #: Ce qu'il faudrait pour passer a OBSERVED. Documentation, pas une demande.
    MISSING_CAPABILITIES = (
        "endpoint authentifie GET /api/v5/account/trade-fee",
        "identifiants en LECTURE SEULE fournis par l'environnement d'execution, "
        "jamais par le code ni par un argument",
        "permission de lecture du compte (aucune permission de trading requise)",
    )

    def schedule_for(self, spec: InstrumentSpec) -> FeeSchedule:
        return FeeSchedule(
            inst_id=spec.inst_id, maker_bps=None, taker_bps=None,
            quality=Quality.UNKNOWN, source=FeeTierSource.NONE,
            note="aucun identifiant disponible. Manque: "
                 + " ; ".join(self.MISSING_CAPABILITIES))


class RealisedFeeProvider(FeeProvider):
    """Frais DERIVED de fills reels : frais payes / notionnel execute.

    Le seul chemin vers du DERIVED sans lecture de compte. Exige des fills
    REELS — une simulation PAPER ne paie aucun frais, donc n'en derive aucun.
    """

    name = "REALISED_FILLS"

    def __init__(self, min_samples: int = 10):
        self.min_samples = min_samples
        self._samples: Dict[str, List[tuple]] = {}

    def observe_fill(self, inst_id: str, fee_paid_usd: float,
                     notional_usd: float, is_maker: bool) -> None:
        if notional_usd <= 0:
            raise ValueError("notionnel nul : aucun taux derivable")
        if fee_paid_usd < 0:
            raise ValueError("frais negatif : un rebate doit etre explicite")
        bps = fee_paid_usd / notional_usd * 10_000.0
        self._samples.setdefault(inst_id, []).append((bps, is_maker))

    def schedule_for(self, spec: InstrumentSpec) -> FeeSchedule:
        samples = self._samples.get(spec.inst_id, [])
        makers = [b for b, m in samples if m]
        takers = [b for b, m in samples if not m]
        if len(samples) < self.min_samples or not takers:
            return FeeSchedule(
                inst_id=spec.inst_id, maker_bps=None, taker_bps=None,
                quality=Quality.UNKNOWN, source=FeeTierSource.REALISED_FILLS,
                note=f"{len(samples)} fill(s) reel(s) < {self.min_samples} requis "
                     f"(dont {len(takers)} taker) — taux non derivable")
        med = lambda v: sorted(v)[len(v) // 2]
        return FeeSchedule(
            inst_id=spec.inst_id,
            maker_bps=med(makers) if makers else 0.0,
            taker_bps=med(takers), quality=Quality.DERIVED,
            source=FeeTierSource.REALISED_FILLS, n_samples=len(samples),
            note=f"mediane sur {len(samples)} fills REELS. DERIVED, pas OBSERVED : "
                 "le bareme du compte n'a pas ete lu directement.")


@dataclass
class FeeBook:
    """Agrege les fournisseurs et retient le MEILLEUR niveau disponible.

    Ordre de preference : OBSERVED > DERIVED > ASSUMED > UNKNOWN. On ne
    degrade jamais une qualite acquise, et on ne la surestime jamais.
    """

    providers: List[FeeProvider] = field(default_factory=list)

    _ORDER = {Quality.OBSERVED: 0, Quality.DERIVED: 1,
              Quality.ASSUMED: 2, Quality.UNKNOWN: 3}

    def add(self, provider: FeeProvider) -> "FeeBook":
        self.providers.append(provider)
        return self

    def best_for(self, spec: InstrumentSpec) -> FeeSchedule:
        if not self.providers:
            return NoCredentialsFeeProvider().schedule_for(spec)
        schedules = [p.schedule_for(spec) for p in self.providers]
        return min(schedules, key=lambda s: self._ORDER[s.quality])

    def status(self, specs: List[InstrumentSpec]) -> Dict[str, Any]:
        by_quality: Dict[str, int] = {}
        rows = []
        for spec in specs:
            s = self.best_for(spec)
            by_quality[s.quality.value] = by_quality.get(s.quality.value, 0) + 1
            rows.append(s.to_dict())
        any_exec = any(r["authorises_execution"] for r in rows)
        return {
            "providers": [p.name for p in self.providers],
            "by_quality": dict(sorted(by_quality.items())),
            "schedules": rows,
            "execution_authorised": any_exec,
            "blocker": (None if any_exec else
                        "aucun instrument ne dispose de frais OBSERVED : "
                        "EvaluationMode.EXECUTION restera bloque. "
                        "Manque: " + " ; ".join(
                            NoCredentialsFeeProvider.MISSING_CAPABILITIES)),
        }
