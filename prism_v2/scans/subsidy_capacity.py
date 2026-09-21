"""CAPACITE de la subvention : le rendement survit-il a son propre succes ?

    python -m prism_v2.scans.subsidy_capacity

POURQUOI CETTE MESURE DECIDE DE L'OBJECTIF. La part du pool vaut
Q_moi / (Q_moi + Q_autres) et tend vers 1 : passe un seuil, le capital
supplementaire n'achete plus rien. Un rendement qui ne survit pas a sa propre
croissance ne COMPOSE pas, et l'objectif « 1 000 -> 5 000 en 60 jours » est
une question de composition avant d'etre une question de rendement.

Le rendement MOYEN flatte ; c'est le rendement MARGINAL — ce que rapporte le
millier de dollars suivant — qui dit si la trajectoire tient.

PAS D'ALLOCATION CONSTANT. Une premiere version de cette mesure changeait le
pas de 25 a 100 dollars au-dela de 3 000, et la courbe cessait d'etre
monotone : 2,56 %/jour a 3 000 puis 3,78 a 5 000. Ce n'etait pas un effet de
capacite, c'etait mon pas. Le pas est desormais constant et l'allocation
gloutonne est comparable d'un point a l'autre.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional, Tuple

from prism_v2.subsidy.scoring import epoch_share
from prism_v2.subsidy.venue import (SubsidisedMarket, attach_book,
                                    fetch_books, fetch_subsidised_markets,
                                    my_q_at)

STEP_USD = 25.0
LEVELS = (250, 500, 1_000, 2_000, 3_000, 5_000)


def eligible_markets() -> List[SubsidisedMarket]:
    """Les memes quatre filtres que scans/subsidy.py, declares la-bas."""
    now = _dt.datetime.now(_dt.timezone.utc)
    out = []
    for m in fetch_subsidised_markets():
        if m.max_spread_cents <= 0:
            continue
        if m.end_date_iso:
            try:
                if _dt.datetime.fromisoformat(
                        m.end_date_iso.replace("Z", "+00:00")) < now:
                    continue
            except ValueError:
                pass
        out.append(m)
    books = fetch_books([m.token_yes for m in out])
    out = [m for m in out if attach_book(m, books)]
    return [m for m in out if m.others_q and m.others_q > 0
            and m.spread_cents is not None
            and m.spread_cents <= 2 * m.max_spread_cents]


def gain_usd(m: SubsidisedMarket, capital: float) -> float:
    q = my_q_at(m, capital, max(m.spread_cents / 2.0, m.tick * 100.0))
    if not q:
        return 0.0
    share = epoch_share(q, m.others_q)
    return m.pool_usdc_per_day * share if share else 0.0


def allocate(markets: List[SubsidisedMarket], capital: float,
             step: float = STEP_USD) -> Tuple[float, int]:
    """Allocation gloutonne a rendement marginal decroissant."""
    alloc: Dict[int, float] = {i: 0.0 for i in range(len(markets))}
    spent = 0.0
    while spent + step <= capital:
        best, best_gain, best_next = None, 0.0, 0.0
        for i, m in enumerate(markets):
            nxt = alloc[i] + step
            d = gain_usd(m, nxt) - gain_usd(m, alloc[i])
            if d > best_gain:
                best, best_gain, best_next = i, d, nxt
        if best is None:
            break
        spent += best_next - alloc[best]
        alloc[best] = best_next
    used = [(i, a) for i, a in alloc.items() if a > 0]
    return sum(gain_usd(markets[i], a) for i, a in used), len(used)


def main() -> None:
    mk = eligible_markets()
    print("=" * 74)
    print("CAPACITE DE LA SUBVENTION — rendement contre capital")
    print("=" * 74)
    print(f"marches eligibles : {len(mk)}   pas d'allocation : {STEP_USD:.0f} $\n")
    print(f"{'capital $':>11}{'brut $/j':>11}{'%/jour':>10}{'marches':>9}"
          f"{'marginal %/j':>15}")
    prev_g = prev_c = 0.0
    rows = []
    for cap in LEVELS:
        g, n = allocate(mk, float(cap))
        marg = ((g - prev_g) / (cap - prev_c) * 100.0) if cap > prev_c else 0.0
        rows.append((cap, g, n, marg))
        print(f"{cap:>11,}{g:>11.2f}{100*g/cap:>9.2f}%{n:>9}{marg:>14.2f}%")
        prev_g, prev_c = g, cap
    print()
    print("Le rendement MOYEN flatte ; le MARGINAL dit si la trajectoire")
    print("tient. Cible : 2,72 %/jour. STATUT : BRUT — aucun cout de")
    print("neutralisation n'est retranche, et le flux subi reste INCONNU.")


if __name__ == "__main__":
    main()
