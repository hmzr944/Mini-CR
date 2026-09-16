"""CROSS_VENUE — ecart EXECUTABLE entre deux venues, jamais un spread affiche.

Ce que les scanners publics se font reprocher : ils comparent des tickers et
annoncent des spreads que personne ne peut capturer. Ici, on compare
`fillable_bid` et `fillable_ask` pour une TAILLE DONNEE, en marchant chaque
carnet, et on retranche les frais des deux venues avant d'emettre quoi que
ce soit.

Contraintes portees explicitement par chaque candidate :
  - capital PRE-POSITIONNE des deux cotes (un transfert prend des minutes,
    bien au-dela de la duree de vie d'un ecart) ;
  - risque de jambe : si une seule jambe passe, la position est directionnelle ;
  - fraicheur : le delai de transport de chaque venue est inscrit, car un
    ecart calcule sur une cotation perimee n'existe pas.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core_types import Direction, Provenance, ms_to_iso, utc_now_iso
from ..discovery import DetectionOutcome, Detector, Family
from ..market_state import MarketState
from ..opportunity import Candidate

PROBE_NOTIONAL_USD = 1_000.0
#: Au-dela de ce delai, une cotation est traitee comme perimee pour la
#: comparaison. Justification : c'est l'ordre de grandeur de vie d'un ecart
#: inter-venues ; comparer au-dela reviendrait a comparer deux instants
#: differents. Ce n'est pas un parametre optimise.
MAX_QUOTE_AGE_MS = 3_000


class CrossVenueDetector(Detector):
    family = Family.CROSS_VENUE
    requires = ("venue_quotes",)

    def __init__(self, probe_notional_usd: float = PROBE_NOTIONAL_USD,
                 max_quote_age_ms: int = MAX_QUOTE_AGE_MS):
        self.probe = probe_notional_usd
        self.max_age_ms = max_quote_age_ms

    def detect(self, state: MarketState) -> DetectionOutcome:
        quotes: Dict[str, Any] = state.venue_quotes or {}
        if len(quotes) < 2:
            return DetectionOutcome.insufficient(
                self.family,
                f"{len(quotes)} venue(s) cotee(s) — il en faut au moins 2",
                ["venue_quotes"])

        fresh, stale = {}, {}
        for name, q in quotes.items():
            delay = getattr(q, "transport_delay_ms", None)
            if delay is not None and abs(delay) > self.max_age_ms:
                stale[name] = delay
            else:
                fresh[name] = q
        if len(fresh) < 2:
            return DetectionOutcome.insufficient(
                self.family,
                f"cotations trop anciennes pour comparer: {stale} "
                f"(seuil {self.max_age_ms}ms)", ["fresh_venue_quotes"])

        out: List[Candidate] = []
        names = sorted(fresh)
        for i, a_name in enumerate(names):
            for b_name in names[i + 1:]:
                for buy_n, sell_n in ((a_name, b_name), (b_name, a_name)):
                    c = self._evaluate_pair(state, fresh[buy_n], fresh[sell_n])
                    if c is not None:
                        out.append(c)

        if not out:
            return DetectionOutcome.nothing(
                self.family,
                f"{len(fresh)} venues comparees a ${self.probe:,.0f} : aucun ecart "
                f"executable superieur aux frais des deux jambes"
                + (f" (perimees: {sorted(stale)})" if stale else ""))
        return DetectionOutcome.ok(self.family, out)

    def _evaluate_pair(self, state: MarketState, buy_q: Any,
                       sell_q: Any) -> Optional[Candidate]:
        buy = buy_q.fillable("ask", self.probe)     # on achete : on lifte l'ask
        sell = sell_q.fillable("bid", self.probe)   # on vend : on frappe le bid
        if buy is None or sell is None:
            return None
        buy_px, sell_px = buy["vwap"], sell["vwap"]
        if buy_px <= 0:
            return None

        gross_bps = (sell_px - buy_px) / buy_px * 10_000.0
        # Seuil ECONOMIQUE, pas un reglage : sous la somme des frais taker des
        # deux venues, l'ecart ne peut pas etre capture, meme parfaitement.
        fee_floor = buy_q.taker_fee_bps + sell_q.taker_fee_bps
        if gross_bps <= fee_floor:
            return None

        return Candidate(
            ts_utc=ms_to_iso(state.ts_ms), instrument=state.instrument,
            opportunity_type="CROSS_VENUE_DISLOCATION", family=self.family.value,
            candidate_id=self.new_candidate_id(), direction=Direction.LONG,
            gross_capture_bps=gross_bps,
            capacity_usd=min(buy["depth_usd"], sell["depth_usd"]),
            causal_reference_ts_ms=state.ts_ms,
            expected_horizon_ms=self.max_age_ms,
            required_execution="TAKER_BOTH_VENUES_SIMULTANEOUS",
            provenance=Provenance("MULTI", "cross_venue", utc_now_iso(),
                                  state.instrument.inst_id,
                                  extra={"buy_venue": buy_q.venue,
                                         "sell_venue": sell_q.venue}),
            invalidation_conditions={
                "max_quote_age_ms": self.max_age_ms,
                "requires_both_legs": True,
                "requires_prefunded_capital_both_venues": True},
            legs=[{"role": "BUY", "venue": buy_q.venue, "symbol": buy_q.symbol,
                   "exec_price": buy_px, "levels_consumed": buy["levels"],
                   "depth_usd": buy["depth_usd"],
                   "fee_bps": buy_q.taker_fee_bps,
                   "fee_quality": buy_q.fee_quality.value,
                   "settle_ccy": buy_q.settle_ccy,
                   "transport_delay_ms": buy_q.transport_delay_ms},
                  {"role": "SELL", "venue": sell_q.venue, "symbol": sell_q.symbol,
                   "exec_price": sell_px, "levels_consumed": sell["levels"],
                   "depth_usd": sell["depth_usd"],
                   "fee_bps": sell_q.taker_fee_bps,
                   "fee_quality": sell_q.fee_quality.value,
                   "settle_ccy": sell_q.settle_ccy,
                   "transport_delay_ms": sell_q.transport_delay_ms}],
            observed_state=state.snapshot(),
            metadata={
                "prices_are_fillable_vwap": True,
                "not_a_displayed_spread": True,
                "probe_notional_usd": self.probe,
                "fee_floor_bps": fee_floor,
                "leg_risk": True,
                "max_transport_delay_ms": max(buy_q.transport_delay_ms,
                                              sell_q.transport_delay_ms),
                "capital_constraint": (
                    "exige du capital pre-positionne sur LES DEUX venues ; "
                    "un transfert prend des minutes, un ecart vit des secondes"),
                "settlement_mismatch": buy_q.settle_ccy != sell_q.settle_ccy,
            })
