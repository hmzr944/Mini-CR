"""Lecture PUBLIQUE de Backpack. Aucune cle, aucun ordre, aucun compte.

Quatre lectures : l'univers des marches, leurs volumes 24 h, un carnet, une
bande d'echanges.

L'UNIVERS EST DERIVE, JAMAIS ECRIT A LA MAIN. Une garde d'architecture du
depot a deja refuse un univers d'instruments code en dur, et elle avait
raison : un univers choisi a la main est un choix de resultat deguise en
donnee. Les marches viennent donc de `/markets`, filtres sur leur type
declare par la venue.

CE QUE CETTE LECTURE N'ETABLIT PAS. `api.eu.backpack.exchange` renvoie un
corps IDENTIQUE au md5 pres a `api.backpack.exchange` : l'hote EEA n'expose
pas de carnet distinct. Tout ce qui est mesure ici decrit donc le carnet
GLOBAL, et non l'univers reellement accessible depuis l'entite EEA — qui
annonce publiquement « 40+ paires » contre les 103 perpetuels vus ici. Aucune
conclusion tiree de ce module ne vaut pour un compte francais tant que cette
correspondance n'est pas etablie.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from prism_v2.subsidy.tape import Trade

API = "https://api.backpack.exchange/api/v1"

#: Type de marche declare par la venue pour un perpetuel.
PERP_MARKET_TYPE = "PERP"

#: Plafond d'echanges rendu par `/trades` en un appel.
TAPE_MAX_LIMIT = 1_000


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


@dataclass(frozen=True)
class PerpMarket:
    """Un perpetuel et son activite sur 24 h, telle que la venue la publie."""

    symbol: str
    quote_volume_24h_usd: float
    trades_24h: int

    def share_notional_per_day_usd(self, share: float) -> float:
        """Volume quotidien qu'il faut traiter pour detenir `share` du marche.

        C'est le seuil du programme de market making exprime en dollars, et
        c'est le chiffre qui dit si un capital donne peut seulement CONCOURIR.
        """
        if not 0.0 < share < 1.0:
            raise ValueError("une part se situe strictement entre 0 et 1")
        return share * self.quote_volume_24h_usd


def fetch_perp_markets() -> List[PerpMarket]:
    """Univers des perpetuels, joint a leurs volumes 24 h.

    Un marche sans ticker est OMIS plutot que compte a volume nul : un volume
    absent n'est pas un volume de zero, et le confondre ferait remonter en
    tete du classement « marches minces » les marches simplement non cotes.
    """
    markets = _get(f"{API}/markets")
    tickers = _get(f"{API}/tickers")
    by_symbol: Dict[str, dict] = {t["symbol"]: t for t in tickers}
    out: List[PerpMarket] = []
    for m in markets:
        if m.get("marketType") != PERP_MARKET_TYPE:
            continue
        t = by_symbol.get(m["symbol"])
        if t is None:
            continue
        out.append(PerpMarket(m["symbol"], float(t["quoteVolume"]),
                              int(t["trades"])))
    return sorted(out, key=lambda x: x.quote_volume_24h_usd)


@dataclass(frozen=True)
class Book:
    best_bid: float
    best_ask: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.best_bid + self.best_ask)

    @property
    def spread_bps(self) -> float:
        return 1e4 * (self.best_ask - self.best_bid) / self.mid


def fetch_book(symbol: str) -> Optional[Book]:
    """Meilleure limite de chaque cote. None si un cote est vide ou croise."""
    d = _get(f"{API}/depth?symbol={symbol}")
    bids, asks = d.get("bids") or [], d.get("asks") or []
    if not bids or not asks:
        return None
    # `/depth` rend les bids en ordre CROISSANT : le meilleur est le dernier.
    best_bid = max(float(b[0]) for b in bids)
    best_ask = min(float(a[0]) for a in asks)
    if best_bid >= best_ask:
        return None
    return Book(best_bid, best_ask)


def fetch_tape(symbol: str, limit: int = TAPE_MAX_LIMIT) -> List[Trade]:
    """Bande publique des echanges, traduite dans le type du depot.

    `isBuyerMaker` donne le sens du PRENEUR : si l'acheteur etait maker, le
    preneur etait vendeur. C'est la seule traduction de ce module, et tout le
    reste de la mesure en depend — une inversion ici echangerait demi-spread
    encaisse et markout, et retournerait le signe du resultat.
    """
    raw = _get(f"{API}/trades?symbol={symbol}&limit={min(limit, TAPE_MAX_LIMIT)}")
    return [Trade(ts=float(r["timestamp"]) / 1000.0,
                  price=float(r["price"]),
                  size=float(r["quantity"]),
                  taker_is_buy=not bool(r["isBuyerMaker"]))
            for r in raw]


def tape_window_seconds(trades: Sequence[Trade]) -> Optional[float]:
    """Duree couverte par la bande. None sous deux echanges."""
    if len(trades) < 2:
        return None
    ts = [t.ts for t in trades]
    return max(ts) - min(ts)
