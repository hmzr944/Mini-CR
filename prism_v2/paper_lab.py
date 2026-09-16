"""PAPER LAB — laboratoire causal. Quatre notions d'edge, jamais confondues.

    THEORETICAL_EDGE  l'exces statistique mesure sur l'echantillon.
                      Ne suppose ni latence, ni cout, ni execution.
    CAUSAL_EDGE       le meme effet, mais la DECISION est figee a T et
                      l'entree a lieu a T+latence, au prix reellement
                      disponible a cet instant. Aucune donnee posterieure a
                      T n'a servi a decider.
    EXECUTABLE_EDGE   le causal, moins spread, impact et frais, a la TAILLE
                      reellement soumettable.
    PAPER_PNL         le resultat de l'aller-retour simule, frais compris,
                      incluant les fills partiels et le debouclage.

Les confondre est la faute qui fait passer une statistique pour un profit.
Le resultat les porte toutes les quatre, cote a cote.

CAUSALITE STRUCTURELLE : le laboratoire ne recoit qu'un `EventTimeline` et un
instant de decision. `EventTimeline.book_at(t)` ne peut pas retourner un
carnet futur — la garantie est dans la structure de donnees, pas dans une
promesse.

FRICTIONS NON MESURABLES : probabilite de fill maker, adverse selection reelle
et latence d'ordre restent UNKNOWN. Elles ne sont jamais mises a zero : le
resultat porte la liste de ce qu'il EXCLUT, et un resultat qui exclut des
couts est une BORNE SUPERIEURE.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contracts import build_order, usd_notional
from .core_types import Direction, utc_now_iso
from .execution import PaperExecutor, PaperRoundTrip
from .instruments import InstrumentSpec
from .orderbook import EmptyBook, OrderBook
from .replay import EventTimeline


class PaperOutcome(str, enum.Enum):
    COMPLETED = "COMPLETED"
    NO_BOOK_AT_DECISION = "NO_BOOK_AT_DECISION"
    NO_BOOK_AT_ENTRY = "NO_BOOK_AT_ENTRY"
    NO_BOOK_AT_EXIT = "NO_BOOK_AT_EXIT"
    ORDER_NOT_SUBMITTABLE = "ORDER_NOT_SUBMITTABLE"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    INVALIDATED_BEFORE_ENTRY = "INVALIDATED_BEFORE_ENTRY"
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"


@dataclass(frozen=True)
class PaperExperiment:
    """Resultat d'une experience causale. Les quatre edges y sont separes."""

    outcome: PaperOutcome
    inst_id: str
    direction: str
    decision_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    latency_ms: int
    horizon_ms: int
    requested_notional_usd: float

    theoretical_edge_bps: Optional[float] = None
    causal_edge_bps: Optional[float] = None
    executable_edge_bps: Optional[float] = None
    paper_pnl_usd: Optional[float] = None
    paper_pnl_bps: Optional[float] = None

    filled_notional_usd: Optional[float] = None
    fill_ratio: Optional[float] = None
    is_partial: Optional[bool] = None
    fully_closed: Optional[bool] = None
    unclosed_notional_usd: Optional[float] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    spread_cost_bps: Optional[float] = None
    impact_cost_bps: Optional[float] = None
    fee_cost_bps: Optional[float] = None
    fee_quality: str = "UNKNOWN"
    latency_decay_bps: Optional[float] = None

    #: Frictions volontairement EXCLUES. Leur presence fait du resultat une
    #: BORNE SUPERIEURE, jamais une prevision.
    excluded_frictions: tuple = ()
    detail: str = ""
    measured_at: str = field(default_factory=utc_now_iso)

    @property
    def is_upper_bound(self) -> bool:
        return bool(self.excluded_frictions)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["outcome"] = self.outcome.value
        d["excluded_frictions"] = list(self.excluded_frictions)
        d["is_upper_bound"] = self.is_upper_bound
        return d


#: Frictions que la simulation PAPER ne peut pas mesurer. Elles sont NOMMEES
#: dans chaque resultat plutot que silencieusement mises a zero.
PAPER_EXCLUDED_FRICTIONS = (
    "probabilite de fill maker (aucun fill reel observe)",
    "position dans la file d'attente",
    "adverse selection reelle",
    "latence d'aller-retour d'un ordre reel",
    "rejets et re-soumissions de l'exchange",
)


class PaperLab:
    """Fait tourner des experiences causales sur un EventTimeline."""

    def __init__(self, executor: Optional[PaperExecutor] = None):
        self.executor = executor or PaperExecutor()

    def run(self, timeline: EventTimeline, spec: InstrumentSpec,
            direction: Direction, decision_ts_ms: int, latency_ms: int,
            horizon_ms: int, notional_usd: float,
            theoretical_edge_bps: Optional[float] = None,
            fee_bps: Optional[float] = None,
            fee_quality: str = "UNKNOWN",
            invalidate_if_edge_closes: bool = True) -> PaperExperiment:
        """Une experience complete, etape par etape.

        1. figer l'etat a T (decision)          6. fill partiel eventuel
        2. avancer de `latency_ms`              7. debouclage a T+latence+horizon
        3. reconstruire le carnet causal        8. couts reels
        4. verifier l'invalidation              9. quatre edges separes
        5. simuler l'ordre                      10. PnL PAPER
        """
        base = dict(inst_id=spec.inst_id, direction=direction.value,
                    decision_ts_ms=decision_ts_ms,
                    entry_ts_ms=decision_ts_ms + latency_ms,
                    exit_ts_ms=decision_ts_ms + latency_ms + horizon_ms,
                    latency_ms=latency_ms, horizon_ms=horizon_ms,
                    requested_notional_usd=notional_usd,
                    theoretical_edge_bps=theoretical_edge_bps,
                    fee_quality=fee_quality,
                    excluded_frictions=PAPER_EXCLUDED_FRICTIONS)

        entry_ts = decision_ts_ms + latency_ms
        exit_ts = entry_ts + horizon_ms
        if not timeline.covers(decision_ts_ms) or not timeline.covers(exit_ts):
            return PaperExperiment(outcome=PaperOutcome.OUTSIDE_WINDOW, **base,
                                   detail="fenetre collectee ne couvre pas "
                                          "decision et/ou sortie")

        # 1. Etat FIGE a la decision. Aucune donnee posterieure n'y entre.
        decision_book = timeline.book_at(decision_ts_ms)
        if decision_book is None:
            return PaperExperiment(outcome=PaperOutcome.NO_BOOK_AT_DECISION, **base,
                                   detail="aucun carnet a l'instant de decision")
        try:
            decision_mid = decision_book.mid
        except (EmptyBook, ValueError) as exc:
            return PaperExperiment(outcome=PaperOutcome.NO_BOOK_AT_DECISION, **base,
                                   detail=str(exc))

        # 2-3. Carnet REELLEMENT disponible a l'arrivee de l'ordre.
        entry_book = timeline.book_at(entry_ts)
        exit_book = timeline.book_at(exit_ts)
        if entry_book is None:
            return PaperExperiment(outcome=PaperOutcome.NO_BOOK_AT_ENTRY, **base,
                                   detail="aucun carnet a l'arrivee de l'ordre")
        if exit_book is None:
            return PaperExperiment(outcome=PaperOutcome.NO_BOOK_AT_EXIT, **base,
                                   detail="aucun carnet a la sortie")

        # 4. Invalidation : l'ecart s'est-il referme pendant le trajet ?
        side = direction.taker_side
        try:
            entry_mid = entry_book.mid
            latency_decay = abs(entry_mid - decision_mid) / decision_mid * 10_000.0
            sign = 1.0 if direction is Direction.LONG else -1.0
            adverse_drift = -sign * (entry_mid - decision_mid) / decision_mid * 10_000.0
        except (EmptyBook, ValueError) as exc:
            return PaperExperiment(outcome=PaperOutcome.NO_BOOK_AT_ENTRY, **base,
                                   detail=str(exc))
        if (invalidate_if_edge_closes and theoretical_edge_bps is not None
                and adverse_drift >= abs(theoretical_edge_bps)):
            return PaperExperiment(
                outcome=PaperOutcome.INVALIDATED_BEFORE_ENTRY, **base,
                latency_decay_bps=latency_decay,
                detail=f"le prix a bouge de {adverse_drift:.4f} bps CONTRE la "
                       f"position pendant les {latency_ms}ms de latence, soit "
                       f"plus que l'effet attendu ({abs(theoretical_edge_bps):.4f} "
                       "bps) : l'opportunite n'existait plus a l'arrivee")

        # 5. Ordre reellement soumettable ?
        order = build_order(spec, notional_usd, entry_book.mid)
        if not order.is_executable:
            return PaperExperiment(outcome=PaperOutcome.ORDER_NOT_SUBMITTABLE,
                                   **base, latency_decay_bps=latency_decay,
                                   detail=order.reason)

        # 5-7. Aller-retour simule sur les carnets REELS des deux instants.
        rt: Optional[PaperRoundTrip] = self.executor.round_trip(
            spec, direction, entry_book, exit_book, order.usd_notional)
        if rt is None:
            return PaperExperiment(outcome=PaperOutcome.INSUFFICIENT_DEPTH, **base,
                                   latency_decay_bps=latency_decay,
                                   detail="profondeur insuffisante pour un "
                                          "aller-retour a cette taille")

        # 8. Couts reellement constates sur les carnets.
        try:
            spread_cost = (entry_book.crossing_cost_bps(side)
                           + exit_book.crossing_cost_bps(
                               "bid" if direction is Direction.LONG else "ask"))
            imp_in = entry_book.slippage_vs_touch_bps(side, order.usd_notional)
            imp_out = exit_book.slippage_vs_touch_bps(
                "bid" if direction is Direction.LONG else "ask", order.usd_notional)
            # Un impact non mesurable (carnet trop mince a cette taille) est
            # INCONNU, pas nul : le mettre a zero offrirait gratuitement le
            # cout le plus favorable possible.
            impact_cost = (None if imp_in is None or imp_out is None
                           else imp_in + imp_out)
        except (EmptyBook, ValueError):
            spread_cost = impact_cost = None

        # 9. CAUSAL EDGE : mouvement du mid entre arrivee et sortie, dans le
        #    sens pris, calcule via la mecanique du contrat (inverse-aware).
        from .contracts import contracts_for_usd_notional, pnl as _pnl
        n_ctr = contracts_for_usd_notional(spec, order.usd_notional, entry_book.mid)
        causal = _pnl(spec, direction, n_ctr, entry_book.mid,
                      exit_book.mid).return_bps_usd

        fee_bps_rt = fee_bps if fee_bps is not None else None
        executable = None
        if spread_cost is not None and impact_cost is not None:
            executable = causal - spread_cost - impact_cost
            if fee_bps_rt is not None:
                executable -= fee_bps_rt
            else:
                # Les frais sont UNKNOWN : on ne les met pas a zero, on refuse
                # de produire un executable_edge complet.
                executable = None

        return PaperExperiment(
            outcome=PaperOutcome.COMPLETED, **base,
            causal_edge_bps=causal, executable_edge_bps=executable,
            paper_pnl_usd=rt.realized_pnl_usd, paper_pnl_bps=rt.realized_bps,
            filled_notional_usd=rt.entry.filled_notional_usd,
            fill_ratio=(rt.entry.filled_notional_usd / notional_usd
                        if notional_usd else None),
            is_partial=rt.entry.is_partial, fully_closed=rt.fully_closed,
            unclosed_notional_usd=rt.unclosed_notional_usd,
            entry_price=rt.entry.exec_price, exit_price=rt.exit.exec_price,
            spread_cost_bps=spread_cost, impact_cost_bps=impact_cost,
            fee_cost_bps=fee_bps_rt, latency_decay_bps=latency_decay,
            detail=("resultat PAPER : BORNE SUPERIEURE, il exclut "
                    + str(len(PAPER_EXCLUDED_FRICTIONS)) + " frictions non mesurables"))


def summarise(experiments: List[PaperExperiment]) -> Dict[str, Any]:
    """Synthese d'une serie d'experiences. Les quatre edges restent separes."""
    from .edge_health import describe
    by_outcome: Dict[str, int] = {}
    for e in experiments:
        by_outcome[e.outcome.value] = by_outcome.get(e.outcome.value, 0) + 1
    done = [e for e in experiments if e.outcome is PaperOutcome.COMPLETED]
    return {
        "n_experiments": len(experiments),
        "by_outcome": dict(sorted(by_outcome.items(), key=lambda kv: -kv[1])),
        "n_completed": len(done),
        "theoretical_edge_bps": describe([e.theoretical_edge_bps for e in done
                                          if e.theoretical_edge_bps is not None]),
        "causal_edge_bps": describe([e.causal_edge_bps for e in done
                                     if e.causal_edge_bps is not None]),
        "executable_edge_bps": describe([e.executable_edge_bps for e in done
                                         if e.executable_edge_bps is not None]),
        "paper_pnl_bps": describe([e.paper_pnl_bps for e in done
                                   if e.paper_pnl_bps is not None]),
        "paper_pnl_usd_total": sum(e.paper_pnl_usd for e in done
                                   if e.paper_pnl_usd is not None) or 0.0,
        "n_partial_fills": sum(1 for e in done if e.is_partial),
        "n_not_fully_closed": sum(1 for e in done if e.fully_closed is False),
        "latency_decay_bps": describe([e.latency_decay_bps for e in experiments
                                       if e.latency_decay_bps is not None]),
        "excluded_frictions": list(PAPER_EXCLUDED_FRICTIONS),
        "interpretation": (
            "PAPER_PNL exclut des frictions non mesurables : c'est une BORNE "
            "SUPERIEURE de ce qu'un ordre reel obtiendrait, jamais une prevision."),
    }
