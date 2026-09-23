#!/usr/bin/env python3
"""Coter sur Backpack : le demi-spread paie-t-il le markout ?

LA QUESTION, ET POURQUOI C'EST LA BONNE. L'arithmetique de `conditions.py` a
ferme la forme taker : net = n * L * (e - c), coup 10,77 a 12,48 bps, plus
grande magnitude brute jamais mesuree 6,50 bps, donc (e - c) < 0 partout. Le
barème EEA desormais public (2 bps maker / 5 bps taker) confirme ce cout sur
des frais MESURES et non plus supposes, et le plafond de levier retail a 2x
(ESMA, 25 fevrier 2026) interdit a L de rien sauver.

Il reste une seule facon de retourner le signe : rendre `c` NEGATIF, c'est-a-
dire etre PAYE pour coter. C'est la famille subvention, la seule du projet
dont un net mesure ait jamais ete positif, et Backpack en publie l'equivalent
sur une venue qui opere une entite EEA.

CE QUE CE SCAN MESURE, ET CE QU'IL NE MESURE PAS.

  MESURE  : le PnL d'un fill passif avant frais, decompose en demi-spread
            encaisse et markout, sur la bande publique.
  MESURE  : le volume quotidien qu'exige le seuil de part du programme, donc
            si un capital donne peut seulement CONCOURIR.
  INCONNU : le rebate maker par palier du programme. Non publie au niveau
            requis. Le scan rend donc un FRAIS D'EQUILIBRE — une condition
            sur la venue — et jamais un net qui supposerait ce taux.
  INCONNU : l'allocation des paliers 2 et 3. Seul le palier 1 est publie
            (BTC, ETH, SOL a 23 333 $/symbole).
  INCONNU : si le programme couvre l'entite EEA. La documentation est sur le
            support global, et `api.eu.backpack.exchange` rend un corps
            identique au md5 pres au global.

Trois inconnues nommees, aucune defaussee en zero. Le scan ne conclut donc
pas a un profit ; il rend la condition que la venue doit remplir pour qu'il
en existe un.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.backpack.passive import (MARKOUT_HORIZONS_S, PassiveFill,
                                       MM_LIQUIDITY_BAND_BPS,
                                       half_spread_at_touch_bps,
                                       simulate_passive_fills,
                                       tape_mid_is_fit_for_level)
from prism_v2.backpack.venue import (PerpMarket, fetch_book, fetch_perp_markets,
                                     fetch_tape, tape_window_seconds)

#: Seuil de part de volume sous lequel le programme declare un market maker
#: NON eligible aux recompenses sur un marche donne.
MM_MIN_VOLUME_SHARE = 0.015

#: Taille moyenne d'ordre minimale exigee par le programme, en dollars.
MM_MIN_AVG_ORDER_USD = 2_000.0

#: Fenetre minimale sous laquelle un taux de fill extrapole n'a pas de sens.
#: Reprise de `subsidy.tape.fills_per_day`, meme raison : un marche mince
#: passe des heures sans echange, et une fenetre courte tombe sur zero ou sur
#: une rafale.
MIN_TAPE_WINDOW_S = 3_600.0

#: Nombre d'echanges minimal pour qu'une mediane de markout veuille dire
#: quelque chose. En dessous, le scan rend INCONNU plutot qu'un chiffre.
MIN_FILLS_FOR_MEDIAN = 30

#: Fraicheur exigee de chaque cote pour estimer une DERIVE depuis la bande.
#: Le defaut du module (3 600 s) a ete mesure comme produisant des markouts
#: exactement nuls — l'estimateur rendait deux fois la meme paire d'echanges.
#: 60 s les supprime entierement sur les trois marches testes.
SCAN_STALENESS_S = 60.0


class MarketVerdict:
    """Ce que la bande d'un marche dit, et ce qu'elle laisse inconnu."""

    def __init__(self, market: PerpMarket, fills: List[PassiveFill],
                 window_s: Optional[float], spread_bps: Optional[float]):
        self.market = market
        self.fills = fills
        self.window_s = window_s
        self.spread_bps = spread_bps

    def half_spread_bps(self) -> Optional[float]:
        """Demi-spread encaisse, LU au carnet et jamais tire de la bande.

        Voir `tape_mid_is_fit_for_level` : le terme de niveau tire de la bande
        etait un artefact de fraicheur. Il est desormais lu, ou inconnu.
        """
        if self.spread_bps is None:
            return None
        return half_spread_at_touch_bps(self.spread_bps)

    def tape_estimator_is_fit(self) -> Optional[bool]:
        """L'estimateur de bande decrit-il seulement le carnet de ce marche ?"""
        if self.spread_bps is None or len(self.fills) < MIN_FILLS_FOR_MEDIAN:
            return None
        implied = statistics.median(
            2.0 * abs(f.half_spread_bps) for f in self.fills)
        return tape_mid_is_fit_for_level(implied, self.spread_bps)

    def median_breakeven_fee_bps(self, horizon_s: float) -> Optional[float]:
        """Frais maker median que le marche supporte. None si un terme manque.

        Le demi-spread vient du CARNET, le markout de la BANDE. Si l'un des
        deux est inconnu, la somme l'est aussi : une jambe non chiffree ne
        devient jamais zero — c'est l'interdit explicite du mandat, et c'est
        precisement la faute qui avait produit « 6,575 %/jour » sur la famille
        subvention.
        """
        hs = self.half_spread_bps()
        mo = self.median_markout_bps(horizon_s)
        if hs is None or mo is None:
            return None
        return hs + mo

    def median_markout_bps(self, horizon_s: float) -> Optional[float]:
        vals = [f.markout_bps.get(horizon_s) for f in self.fills]
        vals = [v for v in vals if v is not None]
        if len(vals) < MIN_FILLS_FOR_MEDIAN:
            return None
        return statistics.median(vals)

    def rotations_per_day_for_share(self, notional_usd: float) -> float:
        """Rotations quotidiennes exigees par le seuil de part du programme."""
        need = self.market.share_notional_per_day_usd(MM_MIN_VOLUME_SHARE)
        return need / notional_usd

    def can_meet_avg_order_size(self, notional_usd: float) -> bool:
        """Le capital permet-il l'ordre moyen minimal exige ?

        Le programme exclut un market maker dont la taille moyenne d'ordre est
        inferieure a 2 000 $. C'est une contrainte de CAPITAL, distincte de
        celle de volume, et elle mord dans l'autre sens : elle punit le petit
        compte meme la ou le volume est atteignable.
        """
        return notional_usd >= MM_MIN_AVG_ORDER_USD


def measure(market: PerpMarket,
            horizons_s: Sequence[float] = MARKOUT_HORIZONS_S) -> MarketVerdict:
    tape = fetch_tape(market.symbol)
    window = tape_window_seconds(tape)
    book = fetch_book(market.symbol)
    if window is None or window < MIN_TAPE_WINDOW_S:
        return MarketVerdict(market, [], window,
                             book.spread_bps if book else None)
    fills = simulate_passive_fills(tape, horizons_s=horizons_s,
                                   max_staleness_s=SCAN_STALENESS_S)
    return MarketVerdict(market, fills, window,
                         book.spread_bps if book else None)


def _fmt(v: Optional[float], width: int = 10, places: int = 2) -> str:
    return "INCONNU".rjust(width) if v is None else f"{v:>{width}.{places}f}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--markets", type=int, default=12,
                   help="nombre de marches les plus MINCES a mesurer")
    p.add_argument("--notional-usd", type=float, default=2_000.0,
                   help="notionnel cotable (capital x levier)")
    p.add_argument("--horizon-s", type=float, default=300.0,
                   help="horizon de markout rapporte dans le tableau")
    a = p.parse_args(argv)

    universe = fetch_perp_markets()
    print(f"univers PERP avec ticker : {len(universe)}")
    print(f"notionnel cotable suppose : {a.notional_usd:,.0f} $")
    print(f"horizon de markout        : {a.horizon_s:.0f} s")
    print(f"bande de liquidite du programme : {MM_LIQUIDITY_BAND_BPS:.0f} bps du mid")
    print()

    head = (f"{'marche':<20}{'vol 24h $':>12}{'spr carnet':>11}{'fills':>7}"
            f"{'demi-spr':>10}{'markout':>10}{'equilibre':>11}{'bande?':>8}")
    print(head)
    print("-" * len(head))

    for m in universe[:a.markets]:
        v = measure(m)
        fit = v.tape_estimator_is_fit()
        fit_txt = "?" if fit is None else ("oui" if fit else "NON")
        print(f"{m.symbol:<20}{m.quote_volume_24h_usd:>12,.0f}"
              f"{_fmt(v.spread_bps, 11, 2)}{len(v.fills):>7}"
              f"{_fmt(v.half_spread_bps())}"
              f"{_fmt(v.median_markout_bps(a.horizon_s))}"
              f"{_fmt(v.median_breakeven_fee_bps(a.horizon_s), 11)}"
              f"{fit_txt:>8}")

    print()
    print("equilibre > 0 : le fill supporte un frais maker jusqu'a ce niveau.")
    print("equilibre < 0 : perdant meme a frais NUL ; il faut un REBATE d'au")
    print("                moins cette magnitude pour atteindre le neutre.")
    print()
    print(f"CONTRAINTE DE TAILLE D'ORDRE : le programme exclut une taille")
    print(f"moyenne sous {MM_MIN_AVG_ORDER_USD:,.0f} $. Au notionnel suppose "
          f"({a.notional_usd:,.0f} $), "
          f"{'FRANCHIE' if a.notional_usd >= MM_MIN_AVG_ORDER_USD else 'NON FRANCHIE'}"
          f" — et un seul ordre de cette taille immobilise tout le compte.")
    print()
    print("INCONNUS NON DEFAUSSES EN ZERO : rebate maker par palier,")
    print("allocation des paliers 2 et 3, couverture de l'entite EEA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
