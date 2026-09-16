"""FUNDING_BASIS — funding et basis traites comme une ECONOMIE, pas un APR.

Erreur classique : "funding eleve donc on vend le perp". Un funding eleve ne
dit rien tant qu'on n'a pas compare ce qu'il rapporte sur l'horizon de
detention a ce que coutent l'entree, la sortie, et le risque de mouvement du
basis pendant la detention.

Le raw edge emis est donc le funding ATTENDU sur l'horizon, en bps du
notionnel, et non un taux annualise. Le Capture Engine y retranche ensuite
l'aller-retour reel.

Le funding est toujours lu sur l'instId REELLEMENT execute. Aucun melange
-USDT-SWAP / -USD-SWAP : c'est l'erreur qu'un audit precedent a identifiee.
"""
from __future__ import annotations

from typing import List, Optional

from ..core_types import Direction, Provenance, ms_to_iso
from ..discovery import DetectionOutcome, Detector, Family
from ..market_state import MarketState
from ..opportunity import Candidate

#: Duree standard d'une periode de funding OKX.
FUNDING_PERIOD_MS = 8 * 3600 * 1000


class FundingBasisDetector(Detector):
    family = Family.FUNDING_BASIS
    requires = ("funding_rate",)

    def __init__(self, horizon_periods: float = 1.0):
        #: Horizon de detention exprime en PERIODES de funding. Une seule
        #: periode par defaut : detenir plus longtemps ajoute du risque de
        #: basis que ce detecteur ne mesure pas.
        self.horizon_periods = horizon_periods

    def detect(self, state: MarketState) -> DetectionOutcome:
        rate = state.funding_rate
        if rate is None:
            return DetectionOutcome.insufficient(
                self.family, f"funding absent pour {state.instrument.inst_id}",
                ["funding_rate"])
        if not state.instrument.inst_type.is_swap:
            return DetectionOutcome.insufficient(
                self.family, f"{state.instrument.inst_id} n'est pas un swap : "
                             "la notion de funding ne s'applique pas")

        expected_bps = abs(rate) * self.horizon_periods * 10_000.0
        # Seuil ECONOMIQUE : sous un aller-retour de spread, le funding
        # encaisse ne couvre meme pas l'entree et la sortie.
        floor = state.spread_bps
        if expected_bps <= floor:
            return DetectionOutcome.nothing(
                self.family,
                f"funding {rate:+.6f}/periode = {expected_bps:.3f} bps sur "
                f"{self.horizon_periods} periode(s), sous le plancher de "
                f"spread aller-retour ({floor:.3f} bps)")

        # Un funding POSITIF est paye par les longs : on est paye en etant SHORT.
        direction = Direction.SHORT if rate > 0 else Direction.LONG
        return DetectionOutcome.ok(self.family, [Candidate(
            ts_utc=ms_to_iso(state.ts_ms), instrument=state.instrument,
            opportunity_type="FUNDING_CARRY", family=self.family.value,
            candidate_id=self.new_candidate_id(), direction=direction,
            gross_capture_bps=expected_bps,
            capacity_usd=min(state.depth_usd("bid"), state.depth_usd("ask")),
            causal_reference_ts_ms=state.ts_ms,
            expected_horizon_ms=int(self.horizon_periods * FUNDING_PERIOD_MS),
            required_execution="TAKER_OR_MAKER",
            provenance=Provenance("OKX", "/public/funding-rate",
                                  ms_to_iso(state.ts_ms), state.instrument.inst_id),
            invalidation_conditions={
                "funding_sign_flip": True,
                "basis_move_exceeds_bps": expected_bps},
            observed_state=state.snapshot(),
            metadata={
                "funding_rate_per_period": rate,
                "horizon_periods": self.horizon_periods,
                "funding_instrument": state.instrument.inst_id,
                "no_instrument_mixing": (
                    "funding lu sur l'instId reellement execute ; aucun melange "
                    "-USDT-SWAP / -USD-SWAP"),
                "unhedged_directional_risk": (
                    "cette jambe seule est DIRECTIONNELLE : le funding encaisse "
                    "peut etre efface par un mouvement de prix bien superieur. "
                    "Une couverture exige une seconde jambe non modelisee ici."),
                "spread_floor_bps": floor,
            })])
