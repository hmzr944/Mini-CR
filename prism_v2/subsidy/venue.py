"""Acces PUBLIC a Polymarket, et reconstruction du Q des concurrents.

Aucune cle, aucun ordre, aucun compte. Trois lectures seulement : les marches
echantillonnes, leur carnet, leur bande de recompense.

LE POINT DELICAT : le Q des concurrents. La part du pool vaut
Q_moi / (Q_moi + Q_autres), et `Q_autres` n'est pas publie. On le RECONSTRUIT
depuis le carnet avec la formule du programme, ce qui impose une hypothese
explicite et une seule :

  HYPOTHESE D'AGREGATION. Le carnet publie une taille par NIVEAU de prix, pas
  par ordre. La regle `min_size` porte pourtant sur l'ORDRE. Un niveau de
  200 parts peut donc etre un ordre qualifiant de 200, ou dix ordres de 20
  dont aucun ne qualifie si min_size vaut 50. On traite chaque niveau comme
  UN ordre, ce qui SUREVALUE le Q des concurrents et donc SOUS-EVALUE ma
  part. L'erreur va dans le sens conservateur, et c'est pourquoi elle est
  acceptable ; l'inverse ne le serait pas.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from prism_v2.subsidy.scoring import (QualifyingOrder, order_score,
                                      two_sided_score)

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

#: USDC sur Polygon. Le pool est libelle dans cet actif ; un pool libelle
#: ailleurs ne serait pas comparable et doit etre rejete, pas converti a la
#: volee avec un taux suppose.
USDC_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _post(url: str, payload, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"User-Agent": "curl/8",
                         "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


@dataclass
class SubsidisedMarket:
    """Un marche qui publie un pool, avec sa bande et son carnet."""

    question: str
    condition_id: str
    token_yes: str
    token_no: str
    pool_usdc_per_day: float
    max_spread_cents: float
    min_size: float
    tick: float
    end_date_iso: Optional[str]
    maker_fee: float
    #: renseignes par `attach_book`
    mid: Optional[float] = None
    spread_cents: Optional[float] = None
    others_q: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None


def fetch_subsidised_markets() -> List[SubsidisedMarket]:
    """Les marches actifs qui publient un pool en USDC. Aucun filtre de choix.

    Le CHOIX des marches n'appartient pas a cette fonction : elle rend tout ce
    que la venue publie. Filtrer ici melangerait la lecture et la decision, et
    rendrait le biais de selection invisible — la faute que le depot s'est
    deja reprochee sur son univers crypto.
    """
    out: List[SubsidisedMarket] = []
    for m in _get(f"{CLOB}/sampling-markets")["data"]:
        if not m.get("accepting_orders") or m.get("closed"):
            continue
        rw = m.get("rewards") or {}
        pool = 0.0
        for r in (rw.get("rates") or []):
            if (r.get("asset_address") or "").lower() != USDC_POLYGON.lower():
                continue
            pool += float(r.get("rewards_daily_rate") or 0)
        if pool <= 0:
            continue
        try:
            mx = float(rw.get("max_spread"))
        except (TypeError, ValueError):
            continue
        toks = m.get("tokens") or []
        if len(toks) != 2:
            continue
        out.append(SubsidisedMarket(
            question=m["question"], condition_id=m["condition_id"],
            token_yes=toks[0]["token_id"], token_no=toks[1]["token_id"],
            pool_usdc_per_day=pool, max_spread_cents=mx,
            min_size=float(rw.get("min_size") or 0),
            tick=float(m.get("minimum_tick_size") or 0.01),
            end_date_iso=m.get("end_date_iso"),
            maker_fee=float(m.get("maker_base_fee") or 0)))
    return out


def fetch_books(token_ids: Sequence[str], batch: int = 150) -> Dict[str, dict]:
    books: Dict[str, dict] = {}
    ids = list(token_ids)
    for i in range(0, len(ids), batch):
        chunk = [{"token_id": t} for t in ids[i:i + batch]]
        try:
            for b in _post(f"{CLOB}/books", chunk):
                books[b["asset_id"]] = b
        except Exception:
            continue
        time.sleep(0.12)
    return books


def _levels(book: dict, side: str) -> List[Tuple[float, float]]:
    return [(float(l["price"]), float(l["size"]))
            for l in (book.get(side) or [])]


def competitor_q(book: dict, max_spread_cents: float, min_size: float
                 ) -> Tuple[Optional[float], Optional[float],
                            Optional[float], Optional[float]]:
    """(Q_autres, mid, spread_cents, best_bid) reconstruits depuis le carnet.

    Rend (None, ...) quand un cote est vide : un carnet a sens unique n'a pas
    de mid, et en fabriquer un reviendrait a inventer le prix de reference sur
    lequel toute la bande est calculee.
    """
    bids = _levels(book, "bids")
    asks = _levels(book, "asks")
    if not bids or not asks:
        return None, None, None, None
    top_bid = max(bids)[0]
    top_ask = min(asks)[0]
    mid = (top_bid + top_ask) / 2.0
    spread_c = (top_ask - top_bid) * 100.0
    orders = [QualifyingOrder(abs(p - mid) * 100.0, s, True)
              for p, s in bids] + \
             [QualifyingOrder(abs(p - mid) * 100.0, s, False)
              for p, s in asks]
    q = two_sided_score(orders, mid, max_spread_cents, min_size)
    return q, mid, spread_c, top_bid


def attach_book(m: SubsidisedMarket, books: Dict[str, dict]) -> bool:
    """Renseigne mid/spread/Q concurrent. False si le carnet ne le permet pas."""
    b = books.get(m.token_yes)
    if not b:
        return False
    q, mid, spread_c, top_bid = competitor_q(b, m.max_spread_cents,
                                             m.min_size)
    if q is None:
        return False
    asks = _levels(b, "asks")
    top_ask = min(asks)[0] if asks else None
    m.others_q, m.mid, m.spread_cents, m.best_bid, m.best_ask = \
        q, mid, spread_c, top_bid, top_ask
    return True


def my_q_at(m: SubsidisedMarket, capital_usd: float,
            distance_cents: float) -> Optional[float]:
    """Le Q que j'obtiendrais avec `capital_usd` cote des DEUX cotes.

    Le capital se partage en deux moities : une au bid, une a l'ask. La taille
    en PARTS depend du prix — c'est la conversion que ce module refuse de
    faire a la louche, puisqu'un dollar achete huit fois plus de parts a 0,12
    qu'a 0,95.
    """
    if m.mid is None or m.mid <= 0:
        return None
    half = capital_usd / 2.0
    bid_px = max(m.tick, m.mid - distance_cents / 100.0)
    ask_px = min(1.0 - m.tick, m.mid + distance_cents / 100.0)
    if bid_px <= 0 or ask_px <= 0:
        return None
    shares_bid = half / bid_px
    # le cote vendeur d'un binaire s'obtient en achetant le complement, dont
    # le prix vaut 1 - ask_px : c'est lui qui consomme le capital.
    comp = max(m.tick, 1.0 - ask_px)
    shares_ask = half / comp
    if shares_bid < m.min_size or shares_ask < m.min_size:
        return 0.0        # sous la taille minimale : le programme ne paie rien
    orders = [QualifyingOrder(distance_cents, shares_bid, True),
              QualifyingOrder(distance_cents, shares_ask, False)]
    return two_sided_score(orders, m.mid, m.max_spread_cents, m.min_size)
