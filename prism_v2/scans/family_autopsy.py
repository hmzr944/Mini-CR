"""Autopsie des 9 familles sur un generateur d'etats de marche varies.

    python3 -m prism_v2.scans.family_autopsy

Les etats sont SYNTHETIQUES et volontairement genereux : spreads de 0,1 a
500 bps, retraits de profondeur jusqu'a 99 %, desequilibres de flux extremes,
deplacements jusqu'a 200 bps. Si un detecteur ne se declenche pas ici, ce n'est
pas le marche qui est calme -- c'est le detecteur qui ne peut pas.
"""
from __future__ import annotations

import random

from prism_v2.core_types import Provenance
from prism_v2.family_audit import render, run_funnel
from prism_v2.instruments import InstrumentSpec
from prism_v2.market_state import MarketState, TradePrint
from prism_v2.orderbook import OrderBook

T0 = 1_789_000_000_000
N_STATES = 4_000
SEED = 20260921


def _spec() -> InstrumentSpec:
    from tests.v2.fixtures import BTC_INVERSE      # meme spec que la suite de tests
    return BTC_INVERSE


def _prov() -> Provenance:
    from tests.v2.fixtures import PROV
    return PROV


def generate_states(n: int = N_STATES, seed: int = SEED):
    """Etats varies : le but est de COUVRIR l'espace, pas de l'imiter."""
    from tests.v2.fixtures import okx_book_payload
    rng = random.Random(seed)
    spec, prov = _spec(), _prov()
    for _ in range(n):
        px = rng.uniform(10.0, 120_000.0)
        spread_bps = 10 ** rng.uniform(-1, 2.7)          # 0,1 a ~500 bps
        # elargissement courant par rapport au passe : de 0,2x (resserrement)
        # a 20x (choc de liquidite). Sans cette variation, DEPTH_WITHDRAWAL
        # n'a rien a detecter et son silence ne prouve rien.
        widen = 10 ** rng.uniform(-0.7, 1.3)
        past_spread_bps = spread_bps / widen
        ask = px * (1 + spread_bps / 1e4)
        bsz, asz = 10 ** rng.uniform(-2, 4), 10 ** rng.uniform(-2, 4)
        book = OrderBook.from_okx(spec, okx_book_payload(
            [[f"{px:.8f}", f"{bsz:.6f}"]], [[f"{ask:.8f}", f"{asz:.6f}"]],
            ts=str(T0 + 30_000)), prov)
        # historique de mid : derive jusqu'a +/- 200 bps sur 30 s
        drift = rng.uniform(-200, 200)
        mids = [(T0 + i * 1000, px * (1 + drift / 1e4 * (i / 30.0)))
                for i in range(31)]
        # profondeur : retrait jusqu'a 99 %
        d0 = 10 ** rng.uniform(2, 6)
        shrink = rng.uniform(0.01, 1.5)
        depths = [(T0 + i * 1000, d0, d0) for i in range(25)] + \
                 [(T0 + i * 1000, d0 * shrink, d0 * shrink) for i in range(25, 31)]
        # flux agressif : desequilibre de 0 a 100 %, intensite variable
        n_tr = rng.randint(1, 40)
        buy_share = rng.random()
        trades = []
        for i in range(n_tr):
            ts = T0 + 25_000 + int(rng.random() * 5_000)
            notion = 10 ** rng.uniform(1, 5)
            trades.append(TradePrint(ts_ms=ts, price=px, size_native=notion / px,
                                     notional_usd=notion,
                                     is_buy=rng.random() < buy_share))
        spreads = [(T0 + i * 1000, past_spread_bps) for i in range(26, 30)]
        yield MarketState(instrument=spec, ts_ms=T0 + 30_000, book=book,
                          mid_history=mids, depth_history=depths,
                          spread_history=spreads,
                          trades=trades, forced_flow=[], window_ms=60_000)


def detectors():
    from prism_v2.detectors.microstructure import (
        AggressiveFlowDetector, BookImbalanceDetector, DepthWithdrawalDetector,
        ShortHorizonReversionDetector, SpreadDislocationDetector)
    return [BookImbalanceDetector(), DepthWithdrawalDetector(),
            AggressiveFlowDetector(), ShortHorizonReversionDetector(),
            SpreadDislocationDetector()]


def main() -> None:
    dets = detectors()
    print(f"{len(dets)} detecteurs de microstructure, {N_STATES} etats generes "
          f"(graine {SEED})\n")
    funnels = run_funnel(dets, generate_states())
    print(render(funnels))
    print("\nMOTIFS DE NON-DECLENCHEMENT, par famille :")
    for f in sorted(funnels.values(), key=lambda x: -x.detector_nothing):
        if not f.nothing_reasons:
            continue
        print(f"\n  {f.family.value}")
        for r, n in f.nothing_reasons.most_common(4):
            print(f"    {n:6d} ({100*n/max(f.ran,1):5.1f} %)  {r}")
    print("\nVERDICT DE LA GARDE ANTI-DETECTEUR-MUET "
          "(sanity.check_firing_rate, jusqu'ici jamais appelee) :")
    for f in funnels.values():
        v = f.verdict()
        print(f"  {f.family.value:26s} {v.status:10s} {v.detail[:96]}")


if __name__ == "__main__":
    main()
