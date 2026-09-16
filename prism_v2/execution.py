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

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .contracts import OKX_LV1_TAKER_RATE, build_order, fee, pnl, usd_notional
from .core_types import Direction, ExecutionMode, utc_now_iso
from .instruments import InstrumentSpec
from .opportunity import Candidate
from .orderbook import OrderBook


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

    def to_dict(self) -> Dict[str, Any]:
        return {"entry": self.entry.to_dict(), "exit": self.exit.to_dict(),
                "realized_pnl_settle_ccy": self.realized_pnl_settle_ccy,
                "settle_ccy": self.settle_ccy,
                "realized_pnl_usd": self.realized_pnl_usd,
                "realized_bps": self.realized_bps, "gross_bps": self.gross_bps,
                "total_fee_bps": self.total_fee_bps}


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
        # A l'entree un LONG lifte les asks ; a la sortie il frappe les bids.
        if is_exit:
            side = "bid" if direction is Direction.LONG else "ask"
        else:
            side = direction.taker_side
        mid = book.mid
        walk = book.walk(side, target_notional_usd)

        if walk.vwap is None or walk.filled_notional <= 0:
            return self._rejected(spec, direction, target_notional_usd, mid,
                                  "carnet vide ou aucune liquidite disponible")

        order = build_order(spec, walk.filled_notional, walk.vwap)
        if not order.is_executable:
            return self._rejected(spec, direction, target_notional_usd, mid, order.reason)

        filled_usd = usd_notional(spec, order.contracts, walk.vwap)
        f = fee(spec, order.contracts, walk.vwap, self.fee_rate)
        slip = book.market_impact_bps(side, walk.filled_notional)

        return PaperFill(
            mode=self.mode.value, ts_utc=utc_now_iso(), inst_id=spec.inst_id,
            inst_type=spec.inst_type.value, direction=direction.value,
            submitted_notional_usd=target_notional_usd,
            filled_notional_usd=filled_usd, contracts=order.contracts,
            exec_price=walk.vwap, reference_price=mid,
            is_partial=walk.is_partial or order.residual_usd > 1e-9,
            is_rejected=False, reject_reason="",
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
                  target: float, mid: float, reason: str) -> PaperFill:
        return PaperFill(
            mode=self.mode.value, ts_utc=utc_now_iso(), inst_id=spec.inst_id,
            inst_type=spec.inst_type.value, direction=direction.value,
            submitted_notional_usd=target, filled_notional_usd=0.0, contracts=0.0,
            exec_price=None, reference_price=mid, is_partial=False, is_rejected=True,
            reject_reason=reason, levels_consumed=0, fee_settle_ccy=0.0,
            settle_ccy=spec.settle_ccy, fee_usd=0.0, fee_bps=0.0,
            slippage_vs_mid_bps=None)

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
        ex = self.submit(spec, direction, exit_book, entry.filled_notional_usd, is_exit=True)
        if ex.is_rejected or ex.exec_price is None or entry.exec_price is None:
            return None

        contracts = min(entry.contracts, ex.contracts)
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
            gross_bps=p.return_bps_usd, total_fee_bps=fee_bps)
