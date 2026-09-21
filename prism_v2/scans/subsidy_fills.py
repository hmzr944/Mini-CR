"""LE FLUX SUBI, MESURE — et la distance de cotation qui optimise le NET.

    python -m prism_v2.scans.subsidy_fills

POURQUOI CETTE MESURE FERME LA DERNIERE INCONNUE. `MarketEconomics` rend
`net_usd_per_day()` a None tant que `measured_fills_per_day` est absent, et
subsidy_capacity.py a laisse le cout de neutralisation LIBRE faute de cette
entree : a 60 jours, l'hypothese couvrait seule un facteur 1,5 a 5,5 sur le
resultat. Cette entree est la seule des six inconnues qui se mesure SANS
CAPITAL — la bande des echanges est publique.

LE VRAI ARBITRAGE DU METIER, ET POURQUOI UN BALAYAGE ETAIT NECESSAIRE. Le
score vaut ((v-s)/v)^2 : il est MAXIMAL au mid. Mais c'est au mid qu'on se
fait remplir le plus. La subvention et le cout croissent donc ENSEMBLE quand
on se rapproche, et le net n'est monotone dans aucun des deux sens. Choisir
une distance « raisonnable » a la main aurait produit un chiffre sans savoir
s'il etait le bon ; on balaie donc la distance et on lit l'optimum NET.

REPLIER LE COMPLEMENT. Un binaire a deux jetons et la bande publique melange
les deux. Acheter NO a p, c'est vendre YES a 1-p : ignorer cette equivalence
diviserait le flux mesure par deux environ, et SOUS-ESTIMERAIT le cout. Tous
les echanges sont donc replies en equivalent YES avant simulation.

SENS DES ERREURS RESIDUELLES, declare d'avance :
  - file d'attente entierement perdue (tape.py) -> SOUS-estime les remplissages ;
  - neutralisation immediate au meilleur complement -> cout PLANCHER ;
  - pool suppose constant sur la fenetre -> neutre.
Le net produit ici est donc une BORNE SUPERIEURE. Un net negatif tue la
famille ; un net positif ne la valide pas.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.request
from typing import Dict, List, Optional, Sequence, Tuple

from prism_v2.subsidy.economics import TAKER_FEE_RATES, Fill, MarketEconomics
from prism_v2.subsidy.tape import RestingQuote, Trade, fills_per_day, simulate
from prism_v2.subsidy.venue import (SubsidisedMarket, attach_book, fetch_books,
                                    fetch_subsidised_markets, my_q_at)

CAPITAL_USD = 1_000.0
STEP_USD = 25.0
#: distances balayees, en cents autour du mid. 0,5 est le pas minimal utile
#: (le tick vaut 0,1 ou 1 cent selon le marche).
DISTANCES_CENTS = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
#: fenetre de bande exigee. fills_per_day refuse sous une heure ; on vise 24 h.
MIN_WINDOW_S = 24 * 3_600.0
#: nombre de marches dont on va chercher la bande. Chaque bande est un appel
#: reseau ; on se limite aux marches que l'allocation retient reellement.
MAX_TAPES = 14

_UA = {"User-Agent": "curl/8", "Accept": "application/json"}


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            return json.loads(urllib.request.urlopen(req, timeout=45).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def eligible_markets() -> List[SubsidisedMarket]:
    """Memes filtres que scans/subsidy.py : pool reel, bande reelle, ouvert."""
    now = _dt.datetime.now(_dt.timezone.utc)
    out = []
    for m in fetch_subsidised_markets():
        if m.max_spread_cents <= 0 or m.pool_usdc_per_day <= 0:
            continue
        if m.end_date_iso:
            try:
                if _dt.datetime.fromisoformat(
                        m.end_date_iso.replace("Z", "+00:00")) < now:
                    continue
            except ValueError:
                pass
        out.append(m)
    return out


def fetch_tape(condition_id: str, token_yes: str,
               limit: int = 1_000) -> Tuple[List[Trade], float]:
    """La bande publique, repliee en equivalent YES.

    Rend (echanges, duree_observee_s). Un echange sur le jeton NO a p devient
    un echange YES a 1-p de sens INVERSE : le preneur qui achete NO vend YES.
    """
    raw = _get(f"https://data-api.polymarket.com/trades"
               f"?market={condition_id}&limit={limit}")
    trades: List[Trade] = []
    for t in raw or []:
        try:
            px, sz = float(t["price"]), float(t["size"])
            ts = float(t["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        taker_buy = str(t.get("side", "")).upper() == "BUY"
        if str(t.get("asset")) != str(token_yes):
            px, taker_buy = 1.0 - px, not taker_buy     # repli du complement
        if not 0.0 < px < 1.0 or sz <= 0:
            continue
        trades.append(Trade(ts=ts, price=px, size=sz, taker_is_buy=taker_buy))
    if not trades:
        return [], 0.0
    span = max(t.ts for t in trades) - min(t.ts for t in trades)
    return trades, span


#: Mots-cles -> categorie tarifaire. La correspondance est GROSSIERE et elle
#: est assumee : l'API du carnet ne publie pas la categorie, et le taux taker
#: en depend entierement (0 % en geopolitique, 7 % en crypto). Un marche non
#: reconnu reste a None — donc son cout de neutralisation reste INCONNU et
#: n'est pas compte comme nul.
_CATEGORIES = (
    ("geopolitical", ("war", "invade", "nato", "nuclear", "ceasefire",
                      "treaty", "sanction", "hostage", "troops", "missile")),
    ("politics", ("election", "president", "senate", "governor", "house",
                  "prime minister", "chancellor", "parliament", "vote",
                  "democrat", "republican", "cabinet", "impeach", "seats")),
    ("crypto", ("bitcoin", "btc", "ethereum", "eth", "solana", "crypto",
                "token", "coin", "hyperliquid", "fdv")),
    ("finance", ("fed", "rate", "inflation", "gdp", "treasury", "yield",
                 "recession", "ipo", "market cap", "s&p", "nasdaq")),
    ("sports", ("win the", "super bowl", "world cup", "nba", "nfl", "mlb",
                "premier league", "champions league", "yankees")),
    ("weather", ("temperature", "hurricane", "sea ice", "volcano",
                 "hottest", "climate")),
    ("tech", ("openai", "agi", "anthropic", "gpu", "ai ", "discord",
              "millennium prize")),
)


def taker_rate_for(question: str) -> Optional[float]:
    """Le taux taker de la categorie deduite du libelle, ou None.

    None n'est PAS zero : c'est l'aveu que la categorie n'a pas ete
    identifiee, et `Fill.neutralisation_cost_usd` propage alors None plutot
    que de chiffrer une protection gratuite.
    """
    q = (question or "").lower()
    for cat, keys in _CATEGORIES:
        if any(k in q for k in keys):
            return TAKER_FEE_RATES.get(cat)
    return None


def _queue_ahead(book: dict, price: float, is_bid: bool,
                 tol: float = 1e-9) -> float:
    """Parts deja posees a ce prix : on se place DERRIERE elles."""
    side = "bids" if is_bid else "asks"
    tot = 0.0
    for lv in (book.get(side) or []):
        try:
            p, s = float(lv["price"]), float(lv["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(p - price) <= tol:
            tot += s
    return tot


def allocate(ms: Sequence[SubsidisedMarket], capital: float,
             distance_cents: float, step: float = STEP_USD
             ) -> List[Tuple[SubsidisedMarket, float]]:
    """Allocation gloutonne a distance FIXEE. Concavite -> glouton = optimal.

    Indexee par POSITION et non par marche : `SubsidisedMarket` est mutable
    (attach_book y ecrit le carnet), donc non hachable — et le rendre hachable
    pour le confort d'un dictionnaire reviendrait a pretendre qu'il est
    immuable alors qu'il ne l'est pas.
    """
    alloc = [0.0] * len(ms)

    def gain(m: SubsidisedMarket, c: float) -> float:
        if c <= 0 or m.others_q is None:
            return 0.0
        q = my_q_at(m, c, distance_cents)
        if not q:
            return 0.0
        return m.pool_usdc_per_day * q / (q + m.others_q)

    spent = 0.0
    while spent < capital:
        best_i, best_d = -1, 0.0
        for i, m in enumerate(ms):
            d = gain(m, alloc[i] + step) - gain(m, alloc[i])
            if d > best_d:
                best_d, best_i = d, i
        if best_i < 0:
            break
        alloc[best_i] += step
        spent += step
    return [(ms[i], c) for i, c in enumerate(alloc) if c > 0]


def evaluate(distance_cents: float, ms: Sequence[SubsidisedMarket],
             books: Dict[str, dict], tapes: Dict[str, Tuple[List[Trade], float]]
             ) -> Optional[Dict[str, float]]:
    """Gross, cout de neutralisation et NET a une distance donnee."""
    alloc = allocate(ms, CAPITAL_USD, distance_cents)
    rows: List[MarketEconomics] = []
    for m, cap in alloc:
        if m.mid is None or m.others_q is None:
            continue
        tape, span = tapes.get(m.condition_id, ([], 0.0))
        if span < MIN_WINDOW_S:
            continue                      # bande trop courte : on n'extrapole pas
        q = my_q_at(m, cap, distance_cents)
        if not q:
            continue
        share = q / (q + m.others_q)
        bid_px = max(m.tick, m.mid - distance_cents / 100.0)
        ask_px = min(1.0 - m.tick, m.mid + distance_cents / 100.0)
        half = cap / 2.0
        book = books.get(m.token_yes) or {}
        t0 = min(t.ts for t in tape)
        quotes = [
            RestingQuote(price=bid_px, size=half / bid_px, is_bid=True,
                         queue_ahead=_queue_ahead(book, bid_px, True),
                         placed_ts=t0),
            RestingQuote(price=ask_px, size=half / max(m.tick, 1.0 - ask_px),
                         is_bid=False,
                         queue_ahead=_queue_ahead(book, ask_px, False),
                         placed_ts=t0),
        ]
        fills: List[Fill] = []
        n_fills = 0.0
        for qt in quotes:
            simulate(qt, tape)
            fpd = fills_per_day(qt, span)
            if fpd is None:
                continue
            n_fills += fpd
            for f in qt.fills:
                # LE PRIX PAYE EST LE MIEN, PAS CELUI DE L'IMPRESSION. Un
                # ordre au repos s'execute a SA limite : si mon bid dort a
                # 0,40 et qu'un preneur vend a 0,35, l'impression sort a 0,35
                # mais je paie 0,40. Utiliser f.price — l'erreur precedente —
                # me faisait acheter au prix agressif du preneur, donc
                # systematiquement mieux que la realite, et produisait un
                # cout de neutralisation NEGATIF : un arbitrage somme-a-1 qui
                # n'existe pas (mesure : minimum 1,0010 sur 999 paires).
                comp = (1.0 - m.best_bid) if (qt.is_bid and m.best_bid
                                              is not None) else (
                    (1.0 - m.best_ask) if m.best_ask is not None else None)
                fills.append(Fill(price_paid=qt.price, complement_ask=comp,
                                  shares=f.size,
                                  taker_rate=taker_rate_for(m.question)))
        rows.append(MarketEconomics(
            question=m.question, pool_usdc_per_day=m.pool_usdc_per_day,
            share=share, capital_usd=cap, fills=fills,
            measured_fills_per_day=(n_fills if fills else 0.0)))
    if not rows:
        return None
    gross = sum(r.gross_usd_per_day() or 0.0 for r in rows)
    costs = [r.neutralisation_cost_per_day() for r in rows]
    n_unknown = sum(1 for c in costs if c is None)
    # UN COUT INCONNU NE S'ADDITIONNE PAS A ZERO. La version precedente
    # sommait les couts CONNUS et les retranchait d'un brut calcule sur TOUS
    # les marches : cinq marches sur onze contribuaient au revenu sans
    # contribuer au cout. C'est exactement l'interdit du mandat — une valeur
    # inconnue silencieusement remplacee par zero — et il produisait
    # 6,575 %/jour. Le net d'un portefeuille dont une jambe n'est pas chiffree
    # est INCONNU, pas optimiste.
    cost = None if n_unknown else sum(costs)
    net = None if cost is None else gross - cost
    return {"distance": distance_cents, "gross": gross, "cost": cost,
            "net": net, "markets": float(len(rows)),
            "unknown_cost": float(n_unknown),
            "fills": sum(r.measured_fills_per_day or 0.0 for r in rows)}


def main() -> None:
    ms = eligible_markets()
    print(f"marches subventionnes eligibles : {len(ms)}")
    toks = [t for m in ms for t in (m.token_yes, m.token_no)]
    books = fetch_books(toks)
    live = [m for m in ms if attach_book(m, books)]
    print(f"dont carnet lisible             : {len(live)}")

    # quels marches l'allocation retient-elle, toutes distances confondues ?
    wanted = set()
    for d in DISTANCES_CENTS:
        wanted |= {m.condition_id for m, _c in allocate(live, CAPITAL_USD, d)}
    by_id = {m.condition_id: m for m in live}
    order = sorted(wanted, key=lambda c: -by_id[c].pool_usdc_per_day)[:MAX_TAPES]
    print(f"marches retenus par l'allocation : {len(wanted)}, "
          f"bandes recuperees : {len(order)}")

    tapes: Dict[str, Tuple[List[Trade], float]] = {}
    for cid in order:
        m = by_id[cid]
        try:
            tapes[cid] = fetch_tape(cid, m.token_yes)
        except Exception as exc:
            print(f"  bande indisponible ({exc}) : {m.question[:40]}")
            tapes[cid] = ([], 0.0)
        time.sleep(0.25)
    usable = sum(1 for t, s in tapes.values() if s >= MIN_WINDOW_S)
    print(f"bandes couvrant >= 24 h          : {usable} / {len(tapes)}")
    if not usable:
        print("\nAUCUNE bande ne couvre 24 h : le flux n'est pas mesurable "
              "ici, et le net RESTE INCONNU. Ne pas lire cela comme un cout "
              "nul.")
        return

    print(f"\n{'dist c':>7}{'marches':>9}{'brut $/j':>10}{'cout $/j':>10}"
          f"{'NET $/j':>10}{'%/jour':>9}{'rempl./j':>10}{'cout inconnu':>14}")
    best = None
    for d in DISTANCES_CENTS:
        r = evaluate(d, live, books, tapes)
        if r is None:
            print(f"{d:>7.1f}{'—':>9}{'aucun marche exploitable':>53}")
            continue
        if r["net"] is None:
            print(f"{d:>7.1f}{r['markets']:>9.0f}{r['gross']:>10.2f}"
                  f"{'INCONNU':>10}{'INCONNU':>10}{'—':>9}"
                  f"{r['fills']:>10.1f}{r['unknown_cost']:>14.0f}")
            continue
        pct = 100.0 * r["net"] / CAPITAL_USD
        print(f"{d:>7.1f}{r['markets']:>9.0f}{r['gross']:>10.2f}"
              f"{r['cost']:>10.2f}{r['net']:>10.2f}{pct:>9.3f}"
              f"{r['fills']:>10.1f}{r['unknown_cost']:>14.0f}")
        if best is None or r["net"] > best["net"]:
            best = r

    print("\n" + "=" * 74)
    if best is None:
        print("AUCUN RESULTAT : le net reste INCONNU.")
        return
    pct = 100.0 * best["net"] / CAPITAL_USD
    print(f"DISTANCE OPTIMALE EN NET : {best['distance']:.1f} cents du mid")
    print(f"  brut {best['gross']:.2f} $/j  -  cout {best['cost']:.2f} $/j"
          f"  =  NET {best['net']:.2f} $/j  ({pct:.3f} %/jour)")
    print(f"  part du brut mangee par la neutralisation : "
          f"{(100.0 * best['cost'] / best['gross']) if best['gross'] else 0:.1f} %")
    print(f"\nCIBLE : 2.720 %/jour")
    if pct > 0:
        print(f"ECART : facteur {2.720 / pct:.2f}")
        print(f"\n60 jours composes a ce net : "
              f"{CAPITAL_USD * (1.0 + pct / 100.0) ** 60:,.0f} EUR depuis 1 000")
    else:
        print("NET NEGATIF : la famille est tuee par son propre cout.")
    print("\nSTATUT : BORNE SUPERIEURE. File d'attente supposee entierement")
    print("perdue, neutralisation au meilleur complement, pool constant.")
    print("=" * 74)


if __name__ == "__main__":
    main()
