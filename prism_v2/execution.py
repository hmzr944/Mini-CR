"""PaperExecutor — simulation d'execution sur carnet reellement observe.

Il n'existe AUCUNE classe RealExecutor dans V2, aucune cle, aucun endpoint
prive, aucun chemin de code vers un ordre reel. ExecutionMode ne contient
qu'une valeur : PAPER.

Le fill est calcule en marchant le carnet observe, pas par une hypothese de
slippage forfaitaire. Les fills partiels sont donc reels : si le carnet
n'absorbe pas le notionnel, l'execution est partielle et le dit.

Tous les calculs monetaires passent par contracts.py, donc par
l'InstrumentSpec : le PnL d'un inverse utilise la formule en 1/prix.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contracts import OKX_LV1_TAKER_RATE, build_order, fee, pnl, usd_notional
import uuid

from .core_types import Direction, ExecutionMode, utc_now_iso
from .instruments import InstrumentSpec
from .opportunity import Candidate
from .orderbook import OrderBook


class OrderState(str, enum.Enum):
    """Etats d'un ordre. Un ordre n'est FILLED que si un fill l'etablit.

    L'intention de remplir n'est pas un remplissage : c'est la confusion qui
    fait diverger un ledger de la realite. UNKNOWN existe pour les cas ou
    l'etat n'a pas pu etre etabli — il ne doit jamais etre suppose FILLED.
    """

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderState.FILLED, OrderState.CANCELLED,
                        OrderState.REJECTED, OrderState.EXPIRED)

    @property
    def has_exposure(self) -> bool:
        return self in (OrderState.PARTIALLY_FILLED, OrderState.FILLED)


#: Transitions autorisees. Toute autre transition leve : un ordre ne saute
#: jamais de CREATED a FILLED sans passer par SUBMITTED.
ALLOWED_TRANSITIONS: Dict[OrderState, frozenset] = {
    OrderState.CREATED: frozenset({OrderState.SUBMITTED, OrderState.REJECTED,
                                   OrderState.CANCELLED}),
    OrderState.SUBMITTED: frozenset({OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                     OrderState.CANCELLED, OrderState.REJECTED,
                                     OrderState.EXPIRED, OrderState.UNKNOWN}),
    OrderState.PARTIALLY_FILLED: frozenset({OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                            OrderState.CANCELLED, OrderState.EXPIRED,
                                            OrderState.UNKNOWN}),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.UNKNOWN: frozenset({OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                   OrderState.CANCELLED, OrderState.REJECTED}),
}


class IllegalTransition(RuntimeError):
    """Transition d'etat interdite : incoherence d'execution, FAIL CLOSED."""


@dataclass
class OrderLifecycle:
    """Journal des transitions d'un ordre. Meme en PAPER.

    Sert a la reconciliation : un ledger qui affirme FILLED alors que le
    cycle de vie ne montre aucun fill est une incoherence detectable.
    """

    order_id: str
    inst_id: str
    state: OrderState = OrderState.CREATED
    transitions: List[Dict[str, Any]] = field(default_factory=list)
    filled_notional_usd: float = 0.0
    requested_notional_usd: float = 0.0

    def transition(self, new_state: OrderState, reason: str = "",
                   filled_usd: Optional[float] = None) -> None:
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            raise IllegalTransition(
                f"{self.order_id}: {self.state.value} -> {new_state.value} interdit")
        self.transitions.append({"from": self.state.value, "to": new_state.value,
                                 "reason": reason, "ts_utc": utc_now_iso()})
        self.state = new_state
        if filled_usd is not None:
            self.filled_notional_usd = filled_usd

    @property
    def fill_ratio(self) -> float:
        if self.requested_notional_usd <= 0:
            return 0.0
        return self.filled_notional_usd / self.requested_notional_usd

    def to_dict(self) -> Dict[str, Any]:
        return {"order_id": self.order_id, "inst_id": self.inst_id,
                "state": self.state.value, "transitions": self.transitions,
                "requested_notional_usd": self.requested_notional_usd,
                "filled_notional_usd": self.filled_notional_usd,
                "fill_ratio": self.fill_ratio}


@dataclass(frozen=True)
class PaperFill:
    mode: str                       # toujours "PAPER"
    ts_utc: str
    inst_id: str
    inst_type: str
    direction: str
    submitted_notional_usd: float
    filled_notional_usd: float
    contracts: float
    exec_price: Optional[float]
    reference_price: float          # mid au moment de la soumission
    is_partial: bool
    is_rejected: bool
    reject_reason: str
    levels_consumed: int
    fee_settle_ccy: float
    settle_ccy: str
    fee_usd: float
    fee_bps: float
    slippage_vs_mid_bps: Optional[float]
    state: str = OrderState.UNKNOWN.value
    lifecycle: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class PaperRoundTrip:
    entry: PaperFill
    exit: PaperFill
    realized_pnl_settle_ccy: float
    settle_ccy: str
    realized_pnl_usd: float
    realized_bps: float             # net de frais, en bps du notionnel USD d'entree
    gross_bps: float                # avant frais
    total_fee_bps: float
    unclosed_contracts: float = 0.0        # exposition residuelle non debouclee
    unclosed_notional_usd: float = 0.0
    fully_closed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"entry": self.entry.to_dict(), "exit": self.exit.to_dict(),
                "realized_pnl_settle_ccy": self.realized_pnl_settle_ccy,
                "settle_ccy": self.settle_ccy,
                "realized_pnl_usd": self.realized_pnl_usd,
                "realized_bps": self.realized_bps, "gross_bps": self.gross_bps,
                "total_fee_bps": self.total_fee_bps,
                "unclosed_contracts": self.unclosed_contracts,
                "unclosed_notional_usd": self.unclosed_notional_usd,
                "fully_closed": self.fully_closed}


class PaperExecutor:
    """Simule une soumission contre un carnet observe. Jamais d'ordre reel."""

    mode = ExecutionMode.PAPER

    def __init__(self, fee_rate: float = OKX_LV1_TAKER_RATE):
        #: Taux ASSUMED (bareme public Lv1). Le PaperExecutor l'utilise pour
        #: chiffrer un fill simule ; il ne pretend pas connaitre le tier reel.
        self.fee_rate = fee_rate

    def submit(self, spec: InstrumentSpec, direction: Direction, book: OrderBook,
               target_notional_usd: float, *, is_exit: bool = False) -> PaperFill:
        """Soumet un ordre marche simule et marche le carnet."""
        spec.validate()
        lifecycle = OrderLifecycle(
            order_id=f"paper-{uuid.uuid4().hex[:12]}", inst_id=spec.inst_id,
            requested_notional_usd=target_notional_usd)
        lifecycle.transition(OrderState.SUBMITTED, "soumission simulee")
        # A l'entree un LONG lifte les asks ; a la sortie il frappe les bids.
        if is_exit:
            side = "bid" if direction is Direction.LONG else "ask"
        else:
            side = direction.taker_side
        mid = book.mid
        walk = book.walk(side, target_notional_usd)

        if walk.vwap is None or walk.filled_notional <= 0:
            lifecycle.transition(OrderState.REJECTED, "aucune liquidite")
            return self._rejected(spec, direction, target_notional_usd, mid,
                                  "carnet vide ou aucune liquidite disponible", lifecycle)

        order = build_order(spec, walk.filled_notional, walk.vwap)
        if not order.is_executable:
            lifecycle.transition(OrderState.REJECTED, order.reason)
            return self._rejected(spec, direction, target_notional_usd, mid,
                                  order.reason, lifecycle)

        filled_usd = usd_notional(spec, order.contracts, walk.vwap)
        f = fee(spec, order.contracts, walk.vwap, self.fee_rate)
        slip = book.market_impact_bps(side, walk.filled_notional)
        partial = walk.is_partial or order.residual_usd > 1e-9
        lifecycle.transition(
            OrderState.PARTIALLY_FILLED if partial else OrderState.FILLED,
            f"fill simule sur carnet {book.ts_utc}", filled_usd=filled_usd)

        return PaperFill(
            mode=self.mode.value, ts_utc=utc_now_iso(), inst_id=spec.inst_id,
            inst_type=spec.inst_type.value, direction=direction.value,
            submitted_notional_usd=target_notional_usd,
            filled_notional_usd=filled_usd, contracts=order.contracts,
            exec_price=walk.vwap, reference_price=mid,
            is_partial=partial, is_rejected=False, reject_reason="",
            state=lifecycle.state.value, lifecycle=lifecycle.to_dict(),
            levels_consumed=walk.levels_consumed,
            fee_settle_ccy=f.fee_settle_ccy, settle_ccy=spec.settle_ccy,
            fee_usd=f.fee_usd, fee_bps=f.fee_bps_of_usd_notional,
            slippage_vs_mid_bps=slip,
            metadata={"book_ts": book.ts_utc, "seq_id": book.seq_id, "side": side,
                      "requested_notional": target_notional_usd,
                      "book_filled_notional": walk.filled_notional,
                      "quantization_residual_usd": order.residual_usd,
                      "book_exhausted": walk.exhausted,
                      "fee_rate_quality": "ASSUMED (bareme public Lv1)"},
        )

    def _rejected(self, spec: InstrumentSpec, direction: Direction,
                  target: float, mid: float, reason: str,
                  lifecycle: Optional[OrderLifecycle] = None) -> PaperFill:
        return PaperFill(
            mode=self.mode.value, ts_utc=utc_now_iso(), inst_id=spec.inst_id,
            inst_type=spec.inst_type.value, direction=direction.value,
            submitted_notional_usd=target, filled_notional_usd=0.0, contracts=0.0,
            exec_price=None, reference_price=mid, is_partial=False, is_rejected=True,
            reject_reason=reason, levels_consumed=0, fee_settle_ccy=0.0,
            settle_ccy=spec.settle_ccy, fee_usd=0.0, fee_bps=0.0,
            slippage_vs_mid_bps=None, state=OrderState.REJECTED.value,
            lifecycle=lifecycle.to_dict() if lifecycle else None)

    def round_trip(self, spec: InstrumentSpec, direction: Direction,
                   entry_book: OrderBook, exit_book: OrderBook,
                   target_notional_usd: float) -> Optional[PaperRoundTrip]:
        """Entree puis sortie simulees sur deux carnets observes.

        Le PnL utilise la formule officielle du type de contrat (en 1/prix
        pour un inverse), jamais une approximation lineaire.
        """
        entry = self.submit(spec, direction, entry_book, target_notional_usd)
        if entry.is_rejected:
            return None
        # On DEBOUCLE les contrats ouverts, on ne redimensionne pas depuis un
        # notionnel : re-quantifier au prix de sortie laisserait un residu
        # (defaut reel attrape par la garde de coherence du ledger).
        exit_target = usd_notional(spec, entry.contracts, exit_book.mid)
        ex = self.submit(spec, direction, exit_book, exit_target, is_exit=True)
        if ex.is_rejected or ex.exec_price is None or entry.exec_price is None:
            return None

        # Si la sortie remplit moins que l'entree, une exposition SUBSISTE.
        # La passer sous silence produirait un PnL qui ne correspond a aucune
        # position reelle : on la calcule et on la RAPPORTE.
        contracts = min(entry.contracts, ex.contracts)
        unclosed = max(0.0, entry.contracts - ex.contracts)
        unclosed_usd = (usd_notional(spec, unclosed, ex.exec_price)
                        if unclosed > 0 else 0.0)
        p = pnl(spec, direction, contracts, entry.exec_price, ex.exec_price)
        notion_in = usd_notional(spec, contracts, entry.exec_price)
        fees_usd = entry.fee_usd + ex.fee_usd
        fee_bps = fees_usd / notion_in * 10_000.0 if notion_in else 0.0
        net_usd = p.pnl_usd_at_exit - fees_usd
        # Les frais sont payes dans la devise de reglement : on convertit au
        # prix de sortie, coherent avec pnl_usd_at_exit.
        fees_settle = entry.fee_settle_ccy + ex.fee_settle_ccy
        return PaperRoundTrip(
            entry=entry, exit=ex,
            realized_pnl_settle_ccy=p.pnl_settle_ccy - fees_settle,
            settle_ccy=spec.settle_ccy,
            realized_pnl_usd=net_usd,
            realized_bps=net_usd / notion_in * 10_000.0 if notion_in else 0.0,
            gross_bps=p.return_bps_usd, total_fee_bps=fee_bps,
            unclosed_contracts=unclosed, unclosed_notional_usd=unclosed_usd,
            fully_closed=unclosed <= 1e-12)
