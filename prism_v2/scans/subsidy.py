"""SUBVENTION — la mesure, avec la vraie formule et des filtres declares.

    python -m prism_v2.scans.subsidy

CE QUE CE SCAN CORRIGE D'UNE MESURE ANTERIEURE DE LA MEME SESSION. Un premier
calcul, au prorata plat et sans filtre, a annonce 112,88 %/jour. Il etait faux
deux fois : il remplacait une liquidite qualifiante NULLE par 1 dollar (donc
une part de 99 %), et il ignorait que le score decroit en CARRE de la distance
au mid. Les deux erreurs allaient dans le meme sens, le favorable. Les filtres
ci-dessous sont ecrits AVANT la lecture du resultat.

FILTRES, declares d'avance :
  F0  bande qualifiante nulle (max_spread = 0) -> exclu. Un pool sans bande
      ne definit aucun ordre payable : c'est une donnee incomplete, pas une
      opportunite sans contrainte.
  F1  marche EXPIRE                      -> exclu
  F2  carnet a sens unique               -> exclu (pas de mid, pas de bande)
  F3  Q concurrent NUL                   -> exclu. Un marche que PERSONNE ne
      cote dans la bande n'offre pas 100 % du pool : il dit que le spread
      reel depasse la bande, donc qu'y coter des deux cotes revient a offrir
      une concession de 10 a 30 cents sur un contrat binaire.
  F4  spread reel > 2x la bande          -> exclu, meme raison, mesuree.

HYPOTHESE DE COTATION, declaree : on REJOINT le touch (s = spread/2). On
n'ameliore pas le prix. C'est le choix conservateur : pas de concession
payee pour la priorite de file, et le cout de neutralisation reste borne.
"""
from __future__ import annotations

import datetime as _dt

from prism_v2.subsidy.economics import (TAKER_FEE_RATES, UNKNOWNS,
                                        MarketEconomics)
from prism_v2.subsidy.scoring import epoch_share
from prism_v2.subsidy.venue import (attach_book, fetch_books,
                                    fetch_subsidised_markets, my_q_at)

CAPITAL_USD = 1_000.0
TARGET_PCT_PER_DAY = 2.72          # x5 en 60 jours


def main() -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    mk = fetch_subsidised_markets()
    print("=" * 78)
    print("SUBVENTION DE LIQUIDITE — MESURE")
    print("=" * 78)
    print(f"marches publiant un pool USDC : {len(mk)}")
    print(f"pool total publie             : "
          f"{sum(m.pool_usdc_per_day for m in mk):,.0f} USDC/jour")

    f0 = [m for m in mk if m.max_spread_cents > 0]
    print(f"F0  bande qualifiante publiee   : {len(f0)}"
          f"   (exclus : {len(mk) - len(f0)})")

    f1 = []
    for m in f0:
        if m.end_date_iso:
            try:
                if _dt.datetime.fromisoformat(
                        m.end_date_iso.replace("Z", "+00:00")) < now:
                    continue
            except ValueError:
                pass
        f1.append(m)
    print(f"F1  apres exclusion des expires : {len(f1)}")

    books = fetch_books([m.token_yes for m in f1])
    f2 = [m for m in f1 if attach_book(m, books)]
    print(f"F2  carnet a deux cotes         : {len(f2)}")
    f3 = [m for m in f2 if m.others_q and m.others_q > 0]
    print(f"F3  Q concurrent non nul        : {len(f3)}"
          f"   (exclus : {len(f2) - len(f3)})")
    f4 = [m for m in f3 if m.spread_cents <= 2 * m.max_spread_cents]
    print(f"F4  spread <= 2x la bande       : {len(f4)}"
          f"   (exclus : {len(f3) - len(f4)})")

    # allocation gloutonne, rendement marginal decroissant
    step = 25.0
    alloc = {i: 0.0 for i in range(len(f4))}

    def gain(i: int, cap: float) -> float:
        m = f4[i]
        q = my_q_at(m, cap, max(m.spread_cents / 2.0, m.tick * 100.0))
        if not q:
            return 0.0
        sh = epoch_share(q, m.others_q)
        return m.pool_usdc_per_day * sh if sh else 0.0

    spent = 0.0
    while True:
        best, bg, bn = None, 0.0, 0.0
        for i in range(len(f4)):
            nxt = alloc[i] + step
            if spent + step > CAPITAL_USD:
                continue
            d = gain(i, nxt) - gain(i, alloc[i])
            if d > bg:
                best, bg, bn = i, d, nxt
        if best is None:
            break
        spent += bn - alloc[best]
        alloc[best] = bn

    used = sorted([(i, a) for i, a in alloc.items() if a > 0],
                  key=lambda t: -gain(t[0], t[1]))
    econ = []
    for i, a in used:
        m = f4[i]
        q = my_q_at(m, a, max(m.spread_cents / 2.0, m.tick * 100.0))
        econ.append(MarketEconomics(
            question=m.question, pool_usdc_per_day=m.pool_usdc_per_day,
            share=epoch_share(q, m.others_q), capital_usd=a))

    print(f"\n{'alloue$':>9}{'pool$/j':>9}{'Q autres':>11}{'Q moi':>10}"
          f"{'part':>8}{'$/j':>8}  marche")
    for (i, a), e in zip(used, econ):
        m = f4[i]
        q = my_q_at(m, a, max(m.spread_cents / 2.0, m.tick * 100.0))
        print(f"{a:>9.0f}{m.pool_usdc_per_day:>9.0f}{m.others_q:>11,.0f}"
              f"{q:>10,.0f}{100*e.share:>7.1f}%{e.gross_usd_per_day():>8.2f}"
              f"  {m.question[:38]}")

    tot = sum(e.gross_usd_per_day() for e in econ)
    pct = 100.0 * tot / CAPITAL_USD
    print(f"\nmarches utilises : {len(used)}   capital place : {spent:,.0f} $")
    print("=" * 78)
    print(f"SUBVENTION BRUTE : {tot:,.2f} USDC/jour sur {CAPITAL_USD:,.0f} $"
          f"  =  {pct:,.2f} %/jour")
    print(f"cible            : {TARGET_PCT_PER_DAY:.2f} %/jour"
          f"   ->  ratio {pct/TARGET_PCT_PER_DAY:,.2f}x")
    print("=" * 78)
    print("STATUT : EDGE BRUT OBSERVE. Ce n'est pas un profit net.")
    print(f"{len(UNKNOWNS)} INCONNUES peuvent en retourner le signe :")
    for k, v in UNKNOWNS.items():
        print(f"  - {k} : {v[:104]}...")


if __name__ == "__main__":
    main()
