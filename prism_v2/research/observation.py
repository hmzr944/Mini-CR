"""AGENT 1 — MARKET OBSERVER : observer sans imposer de strategie.

Une OBSERVATION est un releve d'etat de marche horodate et trace. Ce n'est
pas un trade, pas un signal, pas une direction. Elle repond a "que se
passe-t-il ?", jamais a "que faut-il faire ?".

ESPACE DE PRIMITIVES
L'observateur releve TOUTES les grandeurs de `FEATURE_SPACE` a chaque instant,
sans savoir lesquelles comptent. C'est ce qui rend la suite mecanique : le
RelationAgent balaiera tout l'espace au lieu de suivre une intuition.

LIMITE ASSUMEE ET CENTRALE : cet espace de primitives est ECRIT A LA MAIN.
Le systeme decouvre librement DANS cet espace, il ne decouvre pas l'espace
lui-meme. C'est la raison pour laquelle le verdict d'ouverture (cf
OPEN_DISCOVERY.md) est STRUCTURED et non OPEN.

Aucune primitive n'est un indicateur technique : toutes decrivent l'etat du
carnet et du flux a un instant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..core_types import Provenance
from ..market_state import MarketState

#: Primitives relevees. Le nom est la cle du snapshot de MarketState.
#: L'observateur n'en privilegie aucune : c'est la mesure qui tranchera.
FEATURE_SPACE: tuple = (
    "spread_bps",
    "microprice_deviation_bps",
    "depth_imbalance",
    "depth_bid_usd",
    "depth_ask_usd",
    "executable_vs_mid_bps",
    "depth_change_ratio_bid_5s",
    "depth_change_ratio_ask_5s",
    "displacement_bps_5s",
    "realized_vol_bps_30s",
    "aggressive_imbalance_5s",
    "trade_intensity_5s",
    "trade_to_book_ratio_5s",
    "n_forced_flow_10s",
    "funding_rate",
)


@dataclass(frozen=True)
class Observation:
    """Releve d'etat, immuable, entierement trace.

    `values` ne contient que des grandeurs mesurees. Une grandeur non
    calculable est ABSENTE, jamais mise a zero : un zero inventerait une
    information.
    """

    ts_ms: int
    inst_id: str
    venue: str
    values: Dict[str, float]
    provenance: Provenance
    #: Grandeurs de FEATURE_SPACE non calculables a cet instant.
    missing: tuple = ()

    def get(self, feature: str) -> Optional[float]:
        return self.values.get(feature)

    def to_dict(self) -> Dict[str, Any]:
        return {"ts_ms": self.ts_ms, "inst_id": self.inst_id, "venue": self.venue,
                "values": dict(self.values), "missing": list(self.missing),
                "provenance": self.provenance.to_dict()}


def observe_state(state: MarketState, venue: str = "OKX") -> Observation:
    """Releve l'etat courant. Aucune interpretation, aucune direction."""
    snap = state.snapshot()
    values: Dict[str, float] = {}
    missing: List[str] = []
    for feature in FEATURE_SPACE:
        v = snap.get(feature)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values[feature] = float(v)
        else:
            missing.append(feature)
    return Observation(
        ts_ms=state.ts_ms, inst_id=state.instrument.inst_id, venue=venue,
        values=values, missing=tuple(missing),
        provenance=Provenance(
            exchange=venue, endpoint="market_state.snapshot",
            fetched_at=state.book.ts_utc or "", inst_id=state.instrument.inst_id,
            extra={"book_seq_id": state.book.seq_id,
                   "inst_type": state.instrument.inst_type.value}))


@dataclass
class ObservationLog:
    """Serie d'observations par instrument, triee par horodatage.

    Sert de substrat aux agents suivants. N'interprete rien.
    """

    by_instrument: Dict[str, List[Observation]] = field(default_factory=dict)
    total: int = 0

    def add(self, obs: Observation) -> None:
        self.by_instrument.setdefault(obs.inst_id, []).append(obs)
        self.total += 1

    def series(self, inst_id: str) -> List[Observation]:
        return sorted(self.by_instrument.get(inst_id, []), key=lambda o: o.ts_ms)

    def instruments(self) -> List[str]:
        return sorted(self.by_instrument)

    def feature_coverage(self) -> Dict[str, Dict[str, Any]]:
        """Combien d'observations portent reellement chaque primitive.

        Une primitive peu couverte ne peut pas soutenir une relation : le
        rapporter evite de conclure sur du vide.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for feature in FEATURE_SPACE:
            n = sum(1 for lst in self.by_instrument.values()
                    for o in lst if feature in o.values)
            out[feature] = {"n": n,
                            "coverage": (n / self.total) if self.total else 0.0}
        return out

    def window_ms(self) -> Optional[tuple]:
        ts = [o.ts_ms for lst in self.by_instrument.values() for o in lst]
        return (min(ts), max(ts)) if ts else None

    def summary(self) -> Dict[str, Any]:
        w = self.window_ms()
        return {"total_observations": self.total,
                "instruments": self.instruments(),
                "window_ms": w,
                "window_duration_s": ((w[1] - w[0]) / 1000.0) if w else None,
                "feature_space_size": len(FEATURE_SPACE),
                "feature_coverage": self.feature_coverage()}
