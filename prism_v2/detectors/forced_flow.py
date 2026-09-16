"""FORCED_FLOW — liquidations traitees comme un FLUX CONTRAINT, jamais un signal.

Interdiction absolue : `liquidation -> BUY`. Une liquidation n'est pas une
opinion de marche, c'est une vente (ou un achat) forcee. La chaine etudiee est :

    flux contraint -> reponse du carnet -> impact sur le prix
                   -> continuation OU reversion -> edge residuel executable

Le raw edge emis est le DEPLACEMENT DEJA CONSTATE entre le prix de reference
d'avant l'evenement et le prix EXECUTABLE MAINTENANT. C'est une grandeur
observable a l'instant de la detection : elle n'utilise aucune information
future, contrairement a la mesure ex-post du moteur precedent.

Que ce deplacement se resorbe ou non est une HYPOTHESE, explicitement marquee.
C'est le simulateur causal qui la teste, jamais le detecteur.
"""
from __future__ import annotations

from typing import List, Optional

from ..core_types import Direction, Provenance, ms_to_iso
from ..discovery import DetectionOutcome, Detector, Family
from ..market_state import MarketState
from ..opportunity import Candidate

#: Fenetre de recherche de liquidations recentes.
LOOKBACK_MS = 10_000
#: Fenetre de reference pour le prix d'avant l'evenement.
REFERENCE_LOOKBACK_MS = 30_000


class ForcedFlowDetector(Detector):
    family = Family.FORCED_FLOW
    requires = ("forced_flow",)

    def __init__(self, lookback_ms: int = LOOKBACK_MS,
                 reference_lookback_ms: int = REFERENCE_LOOKBACK_MS,
                 probe_notional_usd: float = 1_000.0):
        self.lookback_ms = lookback_ms
        self.reference_lookback_ms = reference_lookback_ms
        self.probe = probe_notional_usd

    def detect(self, state: MarketState) -> DetectionOutcome:
        events = state.recent_forced_flow(self.lookback_ms)
        if not events:
            return DetectionOutcome.nothing(
                self.family, f"aucune liquidation dans les {self.lookback_ms}ms")

        ref = self._reference_mid(state)
        if ref is None:
            return DetectionOutcome.insufficient(
                self.family,
                f"pas d'historique de mid couvrant {self.reference_lookback_ms}ms : "
                "le deplacement ne peut pas etre mesure", ["mid_history"])

        sells = [e for e in events if e.pushes_price_down]
        buys = [e for e in events if not e.pushes_price_down]
        dominant_sell = sum(e.notional_usd for e in sells) >= \
            sum(e.notional_usd for e in buys)
        direction = Direction.LONG if dominant_sell else Direction.SHORT

        # Prix REELLEMENT executable maintenant, du cote a prendre.
        side = direction.taker_side
        try:
            exec_px = state.book.vwap_for_notional(side, self.probe)
        except Exception as exc:
            return DetectionOutcome.insufficient(self.family, f"carnet: {exc}")
        if exec_px is None:
            return DetectionOutcome.nothing(
                self.family, "profondeur insuffisante pour un prix executable")

        # Deplacement deja constate. Positif = le prix est actuellement
        # DEFAVORABLE par rapport a la reference, donc potentiellement disloque.
        if dominant_sell:
            displacement_bps = (ref - exec_px) / exec_px * 10_000.0
        else:
            displacement_bps = (exec_px - ref) / exec_px * 10_000.0

        floor = state.spread_bps
        if displacement_bps <= floor:
            return DetectionOutcome.nothing(
                self.family,
                f"{len(events)} liquidation(s) mais deplacement "
                f"{displacement_bps:.3f} bps sous le spread ({floor:.3f} bps)")

        total_forced = sum(e.notional_usd for e in events)
        return DetectionOutcome.ok(self.family, [Candidate(
            ts_utc=ms_to_iso(state.ts_ms), instrument=state.instrument,
            opportunity_type="FORCED_FLOW_DISLOCATION", family=self.family.value,
            candidate_id=self.new_candidate_id(), direction=direction,
            gross_capture_bps=displacement_bps,
            capacity_usd=state.depth_usd(side),
            causal_reference_ts_ms=state.ts_ms,
            expected_horizon_ms=self.lookback_ms,
            required_execution="TAKER",
            provenance=Provenance("OKX", "liquidation-orders + books",
                                  ms_to_iso(state.ts_ms), state.instrument.inst_id),
            invalidation_conditions={"displacement_closes_below_bps": floor},
            observed_state=state.snapshot(),
            metadata={
                "uses_future_information": False,
                "causal": True,
                "reversion_is_a_hypothesis": (
                    "le deplacement est OBSERVE ; qu'il se resorbe est une "
                    "hypothese que seul le replay causal peut tester. Le "
                    "detecteur n'affirme aucune reversion."),
                "n_forced_events": len(events),
                "forced_notional_usd": total_forced,
                "dominant_side": "sell" if dominant_sell else "buy",
                "reference_mid": ref,
                "executable_price": exec_px,
                "displacement_bps": displacement_bps,
                "trade_to_book_ratio": state.trade_to_book_ratio(self.lookback_ms),
                "depth_change_ratio": state.depth_change_ratio(side, self.lookback_ms),
                "spread_floor_bps": floor,
            })])

    def _reference_mid(self, state: MarketState) -> Optional[float]:
        """Mid d'AVANT l'evenement. Uniquement du passe."""
        target = state.ts_ms - self.reference_lookback_ms
        past = [m for m in state.mid_history if m[0] <= target]
        return past[-1][1] if past else None
