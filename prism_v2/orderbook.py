"""Carnet L2 et mesures de COUT qui en decoulent.

REGLE ANTI-DERIVE : aucun signal ici. L'imbalance, la profondeur et la pente
du carnet sont des mesures de cout et de capacite, jamais des predicteurs de
direction. Aucune fonction de ce module ne retourne un sens de trade.

INVERSE-AWARE (deux points, tous deux corriges apres revue adversariale) :

1. CONVERSION DES TAILLES. Les tailles de niveau sont en CONTRATS pour les
   swaps (verifie : tous les `sz` observes sont des multiples de lotSz) et en
   ccy de base pour le spot. La conversion en USD passe obligatoirement par
   contracts.usd_notional(spec, ...), donc par ct_val/ct_mult/ct_type. Pour un
   inverse, le notionnel USD d'un niveau NE depend PAS du prix.

2. DENOMINATEUR DES BPS. Convention unique et explicite :
       cout normalise par le notionnel USD A L'ENTREE,
       marque en USD au prix de reference.
   Elle donne, pour l'inverse ET pour le lineaire, la MEME formule :

       cout_bps = (prix_execution - prix_reference) / prix_EXECUTION * 1e4
                  (signe de sorte qu'un cout soit toujours positif)

   Demonstration pour un inverse long, face = ctVal*sz*ctMult :
       perte_coin = face * (1/p_exec - 1/p_ref)
       perte_usd  = perte_coin * p_ref = face * (p_ref - p_exec) / p_exec
       en bps de face -> (p_exec - p_ref) / p_exec * 1e4
   Le denominateur est le prix d'EXECUTION, pas le mid. Diviser par le mid est
   la convention lineaire : l'ecart est du second ordre (C^2/1e4 bps pour un
   cout de C bps) mais il est systematique et asymetrique entre achat et vente.

`spread_bps` reste defini au mid : c'est un DESCRIPTEUR de marche, pas un cout.
Les couts passent par crossing_cost_bps / market_impact_bps / slippage_vs_touch_bps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .contracts import usd_notional
from .core_types import Provenance, ms_to_iso, utc_now_iso
from .instruments import InstrumentSpec


class EmptyBook(ValueError):
    """Carnet sans bid ou sans ask : aucune mesure n'est definissable."""


@dataclass(frozen=True)
class Level:
    price: float
    size: float             # unite native : contrats (swap) ou ccy de base (spot)
    notional_usd: float     # converti via InstrumentSpec


@dataclass
class FillWalk:
    """Resultat de la consommation du carnet pour un notionnel demande."""

    side: str                     # "ask" (on achete) | "bid" (on vend)
    requested_notional: float
    filled_notional: float
    vwap: Optional[float]
    levels_consumed: int
    exhausted: bool
    worst_price: Optional[float]

    @property
    def is_partial(self) -> bool:
        return self.filled_notional + 1e-9 < self.requested_notional

    @property
    def fill_ratio(self) -> float:
        if self.requested_notional <= 0:
            return 0.0
        return self.filled_notional / self.requested_notional


def cost_bps(exec_price: float, ref_price: float, side: str) -> float:
    """Cout de traversee en bps, convention instrument-agnostique.

    Voir le docstring du module pour la demonstration : cette formule est
    valable a l'identique pour un inverse et pour un lineaire, parce que le
    denominateur est le prix d'execution et le numerateur la degradation
    signee dans le sens defavorable.
    """
    if exec_price <= 0:
        raise ValueError(f"exec_price doit etre > 0 (recu {exec_price})")
    signed = (exec_price - ref_price) if side == "ask" else (ref_price - exec_price)
    return signed / exec_price * 10_000.0


@dataclass
class OrderBook:
    instrument: InstrumentSpec
    bids: List[Level]             # decroissant en prix
    asks: List[Level]             # croissant en prix
    ts_utc: str
    provenance: Provenance
    seq_id: Optional[str] = None
    raw_depth: int = 0

    # ---- construction --------------------------------------------------------
    @classmethod
    def from_okx(cls, instrument: InstrumentSpec, payload: Sequence[Dict[str, Any]],
                 provenance: Provenance) -> "OrderBook":
        if not payload:
            raise EmptyBook(f"payload vide pour {getattr(instrument, 'inst_id', instrument)}")
        instrument.validate()
        row = payload[0]

        def build(rows: Sequence[Sequence[str]]) -> List[Level]:
            out: List[Level] = []
            for r in rows:
                price, size = float(r[0]), float(r[1])
                if price <= 0 or size <= 0:
                    continue
                out.append(Level(price, size, usd_notional(instrument, size, price)))
            return out

        bids = sorted(build(row.get("bids", [])), key=lambda l: -l.price)
        asks = sorted(build(row.get("asks", [])), key=lambda l: l.price)
        ts = row.get("ts")
        return cls(instrument=instrument, bids=bids, asks=asks,
                   ts_utc=ms_to_iso(ts) if ts else utc_now_iso(),
                   provenance=provenance,
                   seq_id=str(row["seqId"]) if row.get("seqId") is not None else None,
                   raw_depth=max(len(bids), len(asks)))

    # ---- descripteurs de marche ---------------------------------------------
    def _require_both(self) -> None:
        if not self.bids or not self.asks:
            raise EmptyBook(f"{self.instrument.inst_id}: bids={len(self.bids)} asks={len(self.asks)}")

    @property
    def best_bid(self) -> float:
        self._require_both()
        return self.bids[0].price

    @property
    def best_ask(self) -> float:
        self._require_both()
        return self.asks[0].price

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_bps(self) -> float:
        """DESCRIPTEUR de marche (convention standard, au mid). Pas un cout."""
        m = self.mid
        if m <= 0:
            raise EmptyBook(f"{self.instrument.inst_id}: mid invalide")
        return self.spread / m * 10_000.0

    def crossing_cost_bps(self, side: str) -> float:
        """COUT reel de traversee mid -> touch, instrument-correct."""
        touch = self.best_ask if side == "ask" else self.best_bid
        return cost_bps(touch, self.mid, side)

    # ---- profondeur ----------------------------------------------------------
    def bid_depth(self, max_levels: Optional[int] = None) -> float:
        return sum(l.notional_usd for l in (self.bids[:max_levels] if max_levels else self.bids))

    def ask_depth(self, max_levels: Optional[int] = None) -> float:
        return sum(l.notional_usd for l in (self.asks[:max_levels] if max_levels else self.asks))

    def depth(self, side: str, max_levels: Optional[int] = None) -> float:
        return self.ask_depth(max_levels) if side == "ask" else self.bid_depth(max_levels)

    # ---- traversee a un notionnel donne -------------------------------------
    def walk(self, side: str, notional_usd: float) -> FillWalk:
        """Consomme le carnet jusqu'a `notional_usd` et retourne le VWAP reel.

        Si la profondeur est insuffisante, `exhausted=True` et le VWAP ne porte
        que sur la portion realisable : on ne fabrique jamais de liquidite
        au-dela de ce qui est affiche.
        """
        if side not in ("bid", "ask"):
            raise ValueError(f"side invalide: {side!r}")
        if notional_usd <= 0:
            raise ValueError(f"notional doit etre > 0 (recu {notional_usd})")
        self._require_both()

        levels = self.asks if side == "ask" else self.bids
        remaining, cost, filled, consumed = notional_usd, 0.0, 0.0, 0
        worst: Optional[float] = None
        for lvl in levels:
            if remaining <= 1e-12:
                break
            take = min(remaining, lvl.notional_usd)
            cost += lvl.price * take
            filled += take
            remaining -= take
            consumed += 1
            worst = lvl.price
        return FillWalk(side=side, requested_notional=notional_usd, filled_notional=filled,
                        vwap=(cost / filled) if filled > 0 else None,
                        levels_consumed=consumed, exhausted=remaining > 1e-9,
                        worst_price=worst)

    def vwap_for_notional(self, side: str, notional_usd: float) -> Optional[float]:
        return self.walk(side, notional_usd).vwap

    def market_impact_bps(self, side: str, notional_usd: float) -> Optional[float]:
        """Cout total de traversee depuis le mid (spread + impact), en bps.

        Instrument-correct : denominateur = VWAP d'execution (cf module).
        None si aucun fill n'est possible.
        """
        walk = self.walk(side, notional_usd)
        if walk.vwap is None:
            return None
        return cost_bps(walk.vwap, self.mid, side)

    def slippage_vs_touch_bps(self, side: str, notional_usd: float) -> Optional[float]:
        """Degradation au-dela du meilleur prix affiche (impact pur, hors spread)."""
        walk = self.walk(side, notional_usd)
        if walk.vwap is None:
            return None
        touch = self.best_ask if side == "ask" else self.best_bid
        return cost_bps(walk.vwap, touch, side)

    # ---- serialisation -------------------------------------------------------
    def snapshot_dict(self, levels: int = 25) -> Dict[str, Any]:
        return {
            "ts_utc": self.ts_utc, "inst_id": self.instrument.inst_id,
            "inst_type": self.instrument.inst_type.value,
            "ct_type": self.instrument.ct_type, "ct_val": self.instrument.ct_val,
            "ct_mult": self.instrument.ct_mult, "settle_ccy": self.instrument.settle_ccy,
            "seq_id": self.seq_id, "mid": self.mid,
            "best_bid": self.best_bid, "best_ask": self.best_ask,
            "spread_bps": self.spread_bps,
            "bid_depth_usd": self.bid_depth(), "ask_depth_usd": self.ask_depth(),
            "bids": [[l.price, l.size, l.notional_usd] for l in self.bids[:levels]],
            "asks": [[l.price, l.size, l.notional_usd] for l in self.asks[:levels]],
            "provenance": self.provenance.to_dict(),
        }
