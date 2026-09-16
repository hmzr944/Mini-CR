"""CROSS_MARKET — DISLOCATION du basis, et non son NIVEAU.

CORRECTION ISSUE DE LA REVUE ADVERSARIALE
Une premiere version emettait une candidate des que l'ecart entre deux
instruments depassait les demi-spreads. C'etait une faute : un perpetuel
cote structurellement au-dessus du spot (prime de portage), et un contrat
inverse au-dessus d'un lineaire (peg USD vs USDT). Ce NIVEAU de basis est
permanent : il n'est pas capturable sans detenir les deux jambes jusqu'a
convergence, en payant le funding sur toute la duree.

Ce qui peut etre capture, c'est l'ECART DU BASIS A SON PROPRE NIVEAU RECENT
— une dislocation temporaire. Le detecteur compare donc le basis courant a
la mediane de ses observations recentes. Le seuil n'est pas un reglage :
c'est la distribution du basis lui-meme qui sert de reference.


BTC-USD-SWAP (inverse), BTC-USDT-SWAP (lineaire) et BTC-USDT (spot) suivent
le meme actif mais sont TROIS instruments distincts. Leur ecart instantane
est une grandeur economique directement observable : c'est le basis.

Le raw edge est donc reel, pas hypothetique — aucune prevision n'intervient.
Reste a savoir s'il survit aux couts des DEUX jambes, ce que le Capture
Engine tranche.

INSTRUMENT-AWARE : la comparaison se fait sur les prix executables, et les
notionnels de chaque jambe passent par leur propre InstrumentSpec. Aucune
substitution d'instrument n'est possible : les deux jambes sont portees
explicitement dans `legs`.

AVERTISSEMENT ECONOMIQUE PORTE PAR CHAQUE CANDIDATE : un basis inverse/lineaire
n'est pas un arbitrage sans risque. Les deux contrats se reglent dans des
devises differentes (BTC vs USDT), donc une position "neutre" en notionnel USD
garde une exposition residuelle. C'est inscrit dans les metadonnees.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple

from ..core_types import Direction, Provenance, ms_to_iso
from ..discovery import DetectionOutcome, Detector, Family
from ..market_state import MarketState
from ..opportunity import Candidate

#: Taille de sondage pour le prix executable de chaque jambe.
PROBE_NOTIONAL_USD = 1_000.0

#: Devises de cotation economiquement comparables SANS taux de change.
#: USD, USDT et USDC sont toutes des unites ~1 USD : leur ecart EST la
#: grandeur etudiee. EUR, AED, BRL et consorts exigeraient un taux FX que
#: ce systeme n'observe pas — les comparer produirait une "dislocation"
#: purement denominative.
#:
#: DEFAUT REEL CORRIGE ICI : une version precedente comparait BTC-USD-SWAP a
#: BTC-AED et annoncait 26 741 bps d'ecart. C'etait le taux de change
#: USD/AED (~3.67), pas une inefficience.
USD_EQUIVALENT_QUOTES = frozenset({"USD", "USDT", "USDC"})


def comparable(a, b) -> tuple:
    """Deux instruments sont-ils comparables sans taux de change ?"""
    qa = (a.quote or a.settle_ccy or "").upper()
    qb = (b.quote or b.settle_ccy or "").upper()
    if qa not in USD_EQUIVALENT_QUOTES:
        return False, f"{a.inst_id}: cotation {qa!r} non equivalente USD"
    if qb not in USD_EQUIVALENT_QUOTES:
        return False, f"{b.inst_id}: cotation {qb!r} non equivalente USD"
    if a.base != b.base:
        return False, f"sous-jacents differents: {a.base} vs {b.base}"
    return True, ""


class CrossMarketDetector(Detector):
    family = Family.CROSS_MARKET
    requires = ("peers",)

    def __init__(self, probe_notional_usd: float = PROBE_NOTIONAL_USD,
                 history: int = 60, min_history: int = 20):
        self.probe = probe_notional_usd
        #: Historique du basis par paire d'instruments. Sert de REFERENCE :
        #: on cherche un ecart au niveau habituel, pas le niveau lui-meme.
        self.min_history = min_history
        self._basis: Dict[Tuple[str, str], Deque[float]] = defaultdict(
            lambda: deque(maxlen=history))

    @staticmethod
    def _median(values) -> float:
        v = sorted(values)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0

    def detect(self, state: MarketState) -> DetectionOutcome:
        base = state.instrument.base
        candidates_peers, refused = [], []
        for p in state.peers.values():
            ok, why = comparable(state.instrument, p.instrument)
            (candidates_peers if ok else refused).append(p if ok else why)
        peers = candidates_peers
        if not peers:
            return DetectionOutcome.insufficient(
                self.family,
                f"aucun pair comparable pour {base} sans taux de change"
                + (f" — ecartes: {refused[:3]}" if refused else ""),
                ["comparable_peers"])

        try:
            a_ask = state.book.vwap_for_notional("ask", self.probe)
            a_bid = state.book.vwap_for_notional("bid", self.probe)
            a_spread = state.spread_bps
        except Exception as exc:
            return DetectionOutcome.insufficient(self.family, f"carnet A: {exc}")
        if a_ask is None or a_bid is None:
            return DetectionOutcome.insufficient(
                self.family, "profondeur insuffisante sur l'instrument de reference")

        out: List[Candidate] = []
        for peer in peers:
            try:
                b_ask = peer.book.vwap_for_notional("ask", self.probe)
                b_bid = peer.book.vwap_for_notional("bid", self.probe)
                b_spread = peer.spread_bps
            except Exception:
                continue
            if b_ask is None or b_bid is None:
                continue

            # Deux sens possibles, tous deux calcules sur des prix EXECUTABLES
            # (VWAP de traversee), jamais sur des tickers affiches.
            # Basis de reference : mid a mid, independant du sens et de la
            # taille. C'est cette serie dont on cherche l'ecart.
            key = tuple(sorted((state.instrument.inst_id, peer.instrument.inst_id)))
            try:
                basis_now = (peer.mid - state.mid) / state.mid * 10_000.0
            except ZeroDivisionError:
                continue
            hist = self._basis[key]
            hist.append(basis_now)
            if len(hist) < self.min_history:
                continue
            baseline = self._median(list(hist)[:-1])
            dislocation_bps = basis_now - baseline

            for buy_state, buy_px, sell_state, sell_px in (
                    (state, a_ask, peer, b_bid),
                    (peer, b_ask, state, a_bid)):
                if buy_px <= 0:
                    continue
                exec_edge_bps = (sell_px - buy_px) / buy_px * 10_000.0
                # Seuil ECONOMIQUE : sous la somme des demi-spreads, la
                # traversee des deux jambes consomme deja l'ecart.
                floor = (buy_state.spread_bps + sell_state.spread_bps) / 2.0
                if exec_edge_bps <= floor:
                    continue
                # ET la dislocation doit exister : un ecart executable qui
                # correspond au basis HABITUEL n'est pas une anomalie.
                sign = 1.0 if buy_state is state else -1.0
                if sign * dislocation_bps <= floor:
                    continue
                # L'edge brut retenu est la DISLOCATION, bornee par ce qui
                # est reellement executable — jamais le niveau du basis.
                edge_bps = min(exec_edge_bps, abs(dislocation_bps))
                out.append(self._candidate(state, buy_state, sell_state,
                                           buy_px, sell_px, edge_bps, floor,
                                           basis_now, baseline, dislocation_bps,
                                           exec_edge_bps, len(hist)))

        if not out:
            return DetectionOutcome.nothing(
                self.family,
                f"{len(peers)} pair(s) examine(s), aucun ecart executable "
                f"au-dela des demi-spreads")
        return DetectionOutcome.ok(self.family, out)

    def _candidate(self, ref: MarketState, buy: MarketState, sell: MarketState,
                   buy_px: float, sell_px: float, edge_bps: float,
                   floor_bps: float, basis_now: float, baseline: float,
                   dislocation_bps: float, exec_edge_bps: float,
                   n_history: int) -> Candidate:
        cap = min(buy.depth_usd("ask"), sell.depth_usd("bid"))
        same_settle = buy.instrument.settle_ccy == sell.instrument.settle_ccy
        return Candidate(
            ts_utc=ms_to_iso(ref.ts_ms), instrument=buy.instrument,
            opportunity_type="CROSS_MARKET_BASIS", family=self.family.value,
            candidate_id=self.new_candidate_id(),
            direction=Direction.LONG, gross_capture_bps=edge_bps,
            capacity_usd=cap, causal_reference_ts_ms=ref.ts_ms,
            expected_horizon_ms=None,
            required_execution="TAKER_BOTH_LEGS",
            provenance=Provenance("OKX", "cross_market", ms_to_iso(ref.ts_ms),
                                  buy.instrument.inst_id),
            invalidation_conditions={
                "max_spread_widening_bps": floor_bps * 2,
                "requires_both_legs": True},
            legs=[{"role": "BUY", "inst_id": buy.instrument.inst_id,
                   "inst_type": buy.instrument.inst_type.value,
                   "ct_type": buy.instrument.ct_type,
                   "settle_ccy": buy.instrument.settle_ccy,
                   "exec_price": buy_px, "depth_usd": buy.depth_usd("ask")},
                  {"role": "SELL", "inst_id": sell.instrument.inst_id,
                   "inst_type": sell.instrument.inst_type.value,
                   "ct_type": sell.instrument.ct_type,
                   "settle_ccy": sell.instrument.settle_ccy,
                   "exec_price": sell_px, "depth_usd": sell.depth_usd("bid")}],
            observed_state=ref.snapshot(),
            metadata={
                "basis_now_bps": basis_now,
                "basis_baseline_bps": baseline,
                "dislocation_bps": dislocation_bps,
                "executable_edge_bps": exec_edge_bps,
                "basis_history_n": n_history,
                "measures_dislocation_not_level": (
                    "l'edge retenu est l'ecart du basis a sa mediane recente. "
                    "Le NIVEAU du basis (prime de portage, peg USD/USDT) est "
                    "permanent et non capturable sans detention jusqu'a "
                    "convergence."),
                "half_spread_floor_bps": floor_bps,
                "quote_currencies": [buy.instrument.quote or buy.instrument.settle_ccy,
                                     sell.instrument.quote or sell.instrument.settle_ccy],
                "no_fx_conversion_applied": True,
                "probe_notional_usd": self.probe,
                "leg_risk": True,
                "same_settlement_currency": same_settle,
                "settlement_warning": (
                    None if same_settle else
                    f"jambes reglees en devises differentes "
                    f"({buy.instrument.settle_ccy} vs {sell.instrument.settle_ccy}) : "
                    "une position neutre en notionnel USD conserve une exposition "
                    "residuelle. Ce n'est PAS un arbitrage sans risque."),
                "prices_are_executable_vwap": True,
            })
