#!/usr/bin/env python3
"""POLYMARKET — contrats binaires, et l'equation maker qui y change de signe.

POURQUOI CETTE VENUE. La mesure de markout sur OKX a etabli que coter
passivement perd 1,34 bps par fill AVANT tout frais : l'adverse selection y
depasse le demi-spread encaisse d'un facteur 2. La conclusion portait sur la
STRUCTURE TARIFAIRE — il faudrait etre PAYE pour coter. Polymarket est la
seule venue identifiee ou c'est le cas.

CE QUI EST OBSERVE, PAS SUPPOSE. Le champ `feeSchedule` de chaque marche
publie ses propres parametres :

    {"exponent": 1, "rate": 0.05, "takerOnly": true, "rebateRate": 0.25}

`takerOnly` confirme que le maker ne paie aucun frais ; `rebateRate` donne sa
part du pool. Ces valeurs sont lues par instrument, jamais codees en dur.

UN CONTRAT BINAIRE N'EST PAS UN PERPETUEL. Il regle a 0 ou 1 dollar, sa taille
est en PARTS et son prix vit dans [0, 1]. Le forcer dans `InstrumentSpec` —
concu pour ct_val, ct_type et devise de reglement — produirait des calculs
faux sous une apparence correcte. C'est exactement l'erreur que le contrat
d'instrument du projet interdit. D'ou un type DISTINCT.

UNITE DE COMPTE : le DOLLAR PAR PART. Un « bps » n'a pas de sens stable ici,
puisque le meme mouvement de 0,01 $ represente 1 % du notionnel a p = 0,5 et
20 % a p = 0,05. Tout est donc exprime en dollars par part, ramenes au
notionnel de 1 $ a l'echeance.

Aucun ordre reel. Aucune cle. Endpoints publics uniquement.
"""
from __future__ import annotations

import enum
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

USER_AGENT = "prism-v2-research/1.0"
REQUEST_PAUSE_S = 0.12


class PolymarketError(RuntimeError):
    """Echec de recuperation. On ne substitue jamais une valeur par defaut."""


def _get(url: str, params: Optional[Dict[str, Any]] = None,
         timeout: float = 30.0) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        raise PolymarketError(f"{url}: {exc}") from exc


class Side(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def maker_side(self) -> "Side":
        """Le maker prend TOUJOURS le cote oppose au taker.

        C'est ce qui rend ce jeu de donnees superieur a une simulation : chaque
        trade EST un fill maker reel, il n'y a rien a deviner.
        """
        return Side.SELL if self is Side.BUY else Side.BUY


@dataclass(frozen=True)
class BinaryMarket:
    """Contrat a resultat binaire. Type DISTINCT d'un InstrumentSpec.

    Ne porte deliberement ni ct_val, ni ct_type, ni devise de reglement : ces
    notions n'ont pas de sens ici, et les simuler inviterait a reutiliser par
    erreur la mecanique des perpetuels.
    """

    condition_id: str
    question: str
    token_ids: Tuple[str, ...]
    #: Parametres de frais PUBLIES par le marche lui-meme.
    fees_enabled: bool
    fee_rate: Optional[float]
    rebate_rate: Optional[float]
    taker_only: Optional[bool]
    fee_exponent: Optional[float]
    #: Recompenses de liquidite, distinctes du rebate.
    rewards_daily_rate: Optional[float]
    rewards_min_size: Optional[float]
    rewards_max_spread: Optional[float]
    volume_24h: Optional[float]
    liquidity: Optional[float]
    spread: Optional[float]

    @property
    def maker_pays_fees(self) -> Optional[bool]:
        """None quand l'information manque — jamais suppose favorable."""
        if self.taker_only is None:
            return None
        return not self.taker_only

    @property
    def rebate_is_observed(self) -> bool:
        """Le rebate est-il LU sur le marche, ou absent ?"""
        return (self.fees_enabled and self.fee_rate is not None
                and self.rebate_rate is not None)

    def maker_rebate_per_share(self, price: float) -> Optional[float]:
        """Rebate maker en DOLLARS PAR PART, au prix `price`.

        Formule publiee : frais equivalents = rate * p * (1 - p) par part
        (exposant 1), dont le maker recoit `rebate_rate`.

        Le terme p(1-p) est maximal a p = 0,5 et s'annule aux extremes : la
        remuneration du maker est donc la plus FORTE la ou l'issue est la plus
        incertaine. C'est precisement la tension a mesurer, car c'est aussi la
        que l'adverse selection est a priori la pire.
        """
        if not self.rebate_is_observed:
            return None
        if not 0.0 < price < 1.0:
            return 0.0
        exponent = self.fee_exponent if self.fee_exponent is not None else 1.0
        base = (price * (1.0 - price)) ** exponent
        return self.rebate_rate * self.fee_rate * base


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_markets(limit: int = 100, min_volume_24h: float = 0.0,
                 require_fees: bool = True) -> List[BinaryMarket]:
    """Marches actifs, tries par volume. Un marche sans frais est ECARTE
    quand `require_fees` : sans frais taker, il n'y a pas de pool a redistribuer
    et le terme rebate vaut zero par construction."""
    rows = _get(f"{GAMMA}/markets",
                {"limit": limit, "active": "true", "closed": "false",
                 "order": "volume24hr", "ascending": "false"})
    out: List[BinaryMarket] = []
    for m in rows:
        sched = m.get("feeSchedule") or {}
        fees_enabled = bool(m.get("feesEnabled"))
        if require_fees and not fees_enabled:
            continue
        vol = _f(m.get("volume24hr")) or 0.0
        if vol < min_volume_24h:
            continue
        try:
            tokens = tuple(json.loads(m.get("clobTokenIds") or "[]"))
        except json.JSONDecodeError:
            continue
        if not tokens:
            continue
        rewards = (m.get("clobRewards") or [{}])[0]
        out.append(BinaryMarket(
            condition_id=m.get("conditionId", ""), question=m.get("question", ""),
            token_ids=tokens, fees_enabled=fees_enabled,
            fee_rate=_f(sched.get("rate")), rebate_rate=_f(sched.get("rebateRate")),
            taker_only=sched.get("takerOnly"),
            fee_exponent=_f(sched.get("exponent")),
            rewards_daily_rate=_f(rewards.get("rewardsDailyRate")),
            rewards_min_size=_f(m.get("rewardsMinSize")),
            rewards_max_spread=_f(m.get("rewardsMaxSpread")),
            volume_24h=vol, liquidity=_f(m.get("liquidity")),
            spread=_f(m.get("spread"))))
    return out


@dataclass(frozen=True)
class Trade:
    ts: int
    price: float
    size: float
    taker_side: Side
    asset: str

    @property
    def maker_side(self) -> Side:
        return self.taker_side.maker_side


def load_trades(condition_id: str, pages: int = 4,
                page_size: int = 500) -> List[Trade]:
    """Trades REELLEMENT executes. Chacun est un fill maker du cote oppose."""
    out: List[Trade] = []
    for page in range(pages):
        try:
            rows = _get(f"{DATA}/trades",
                        {"market": condition_id, "limit": page_size,
                         "offset": page * page_size})
        except PolymarketError:
            break
        if not rows:
            break
        for r in rows:
            px, sz, ts = _f(r.get("price")), _f(r.get("size")), r.get("timestamp")
            side = r.get("side")
            if px is None or sz is None or ts is None or side not in ("BUY", "SELL"):
                continue
            out.append(Trade(ts=int(ts), price=px, size=sz,
                             taker_side=Side(side), asset=r.get("asset", "")))
        if len(rows) < page_size:
            break
        time.sleep(REQUEST_PAUSE_S)
    out.sort(key=lambda t: t.ts)
    return out


#: Intervalles acceptes par l'endpoint. « 1w » et « 1m » ne le sont PAS —
#: ils rendent une erreur, et un defaut invalide produisait silencieusement
#: zero point de prix, donc zero fill mesurable.
VALID_INTERVALS = ("1h", "6h", "1d", "1w" "max")


def load_price_history(token_id: str, interval: str = "max",
                       fidelity: int = 1) -> List[Tuple[int, float]]:
    """Serie de prix mediane, servant de reference de valeur.

    Utiliser le prix des trades comme reference melangerait le rebond
    bid-ask avec la derive reelle : le markout serait alors mesure contre
    une reference qui bouge avec le flux qu'on cherche a evaluer.
    """
    try:
        body = _get(f"{CLOB}/prices-history",
                    {"market": token_id, "interval": interval,
                     "fidelity": fidelity})
    except PolymarketError:
        return []
    pts = [(int(p["t"]), float(p["p"])) for p in (body.get("history") or [])
           if p.get("t") is not None and p.get("p") is not None]
    pts.sort()
    return pts


# ══════════════════════════════════════════════════════════════════════════
#: Horizons de markout, en SECONDES. Bien plus longs que sur un perpetuel :
#: un marche de prediction se reevalue sur des heures et des nouvelles, pas
#: sur des millisecondes. Ils sont aussi BORNES PAR LA DONNEE : la serie de
#: prix publique a un pas median de 600 s, et mesurer en dessous comparerait
#: un point a lui-meme.
MARKOUT_HORIZONS_S = (600, 3_600, 21_600)

#: Anciennete maximale toleree pour une reference de prix. Au-dela, la
#: reference ne decrit plus le marche a l'instant du fill.
MAX_STALENESS_S = 1_200


@dataclass
class MakerFill:
    """Un fill maker REEL, et ce qui lui est arrive ensuite.

    Contrairement a la mesure OKX, rien n'est simule : chaque trade execute
    implique un maker de l'autre cote.
    """

    ts: int
    condition_id: str
    maker_side: Side
    price: float
    size: float
    mid_at_fill: Optional[float]
    half_spread: Optional[float]            # dollars par part
    markout: Dict[int, float] = field(default_factory=dict)
    rebate: Optional[float] = None          # dollars par part

    def net_per_share(self, horizon_s: int) -> Optional[float]:
        """Demi-spread + markout + rebate. Le maker ne paie AUCUN frais.

        None des qu'un terme manque : on ne complete jamais par zero.
        """
        m = self.markout.get(horizon_s)
        if m is None or self.half_spread is None or self.rebate is None:
            return None
        return self.half_spread + m + self.rebate

    def net_without_rebate(self, horizon_s: int) -> Optional[float]:
        """La meme chose SANS le rebate : ce que vaudrait la cotation sur une
        venue qui ne remunere pas le maker. C'est la comparaison directe avec
        le resultat OKX."""
        m = self.markout.get(horizon_s)
        if m is None or self.half_spread is None:
            return None
        return self.half_spread + m


class PriceSeries:
    """Serie de prix, interrogeable sans jamais rendre une valeur future."""

    def __init__(self, points: Sequence[Tuple[int, float]]):
        self._ts = [p[0] for p in points]
        self._px = [p[1] for p in points]

    def __len__(self) -> int:
        return len(self._ts)

    def at(self, ts: int,
           max_staleness_s: int = MAX_STALENESS_S) -> Optional[float]:
        """Dernier prix d'horodatage <= ts, s'il n'est pas trop ancien.

        Une reference trop vieille ferait mesurer un markout contre un prix
        qui n'existait plus : on refuse plutot que d'extrapoler.
        """
        from bisect import bisect_right
        if not self._ts:
            return None
        i = bisect_right(self._ts, ts) - 1
        if i < 0 or ts - self._ts[i] > max_staleness_s:
            return None
        return self._px[i]


def measure_maker_fills(market: BinaryMarket, trades: Sequence[Trade],
                        series: PriceSeries,
                        horizons: Sequence[int] = MARKOUT_HORIZONS_S,
                        ) -> List[MakerFill]:
    """Markout de chaque fill maker reel, en dollars par part.

    CAUSALITE : le demi-spread se mesure au prix de reference a l'instant du
    fill ; le markout est la derive POSTERIEURE de cette reference. Aucune
    information future n'entre dans le premier terme.
    """
    out: List[MakerFill] = []
    if not len(series) or not trades:
        return out
    last_ts = series._ts[-1]
    max_h = max(horizons)
    for t in trades:
        if t.ts + max_h > last_ts:
            continue                        # l'issue n'est pas observable
        mid = series.at(t.ts)
        if mid is None:
            continue
        # Maker acheteur : il gagne si la reference est AU-DESSUS de son prix.
        sign = 1.0 if t.maker_side is Side.BUY else -1.0
        half = sign * (mid - t.price)
        f = MakerFill(ts=t.ts, condition_id=market.condition_id,
                      maker_side=t.maker_side, price=t.price, size=t.size,
                      mid_at_fill=mid, half_spread=half,
                      rebate=market.maker_rebate_per_share(t.price))
        for h in horizons:
            m = series.at(t.ts + h)
            if m is not None:
                f.markout[h] = sign * (m - mid)
        if f.markout:
            out.append(f)
    return out


# ══════════════════════════════════════════════════════════════════════════
def load_book(token_id: str) -> Optional[Dict[str, Any]]:
    """Carnet complet pour un token. None si illisible — jamais reconstruit."""
    try:
        b = _get(f"{CLOB}/book", {"token_id": token_id})
    except PolymarketError:
        return None
    bids = [(float(x["price"]), float(x["size"])) for x in (b.get("bids") or [])
            if x.get("price") and x.get("size")]
    asks = [(float(x["price"]), float(x["size"])) for x in (b.get("asks") or [])
            if x.get("price") and x.get("size")]
    if not bids or not asks:
        return None
    bids.sort(key=lambda p: -p[0])
    asks.sort(key=lambda p: p[0])
    ts = b.get("timestamp")
    return {"token_id": token_id, "ts": int(ts) if ts else None,
            "bids": bids[:10], "asks": asks[:10]}


def observe(token_ids: Sequence[str], duration_s: float, out_path,
            snapshot_s: float = 10.0) -> Dict[str, Any]:
    """Observatoire Polymarket : carnets et trades, horodatage local.

    MEME DISCIPLINE QUE L'OBSERVATOIRE OKX. Un carnet illisible est ecrit
    `ok: false` SANS niveaux : on n'ecrit jamais un carnet perime en le
    faisant passer pour frais.

    POURQUOI CETTE COLLECTE EXISTE. La serie de prix publique a un pas de
    600 s. Mesurer un demi-spread contre une reference aussi ancienne donnait
    un ecart median de 5,5 fois le spread publie : la mesure etait dominee par
    la derive, pas par la position de l'ordre dans le carnet. Il n'existe pas
    de raccourci historique — il faut le carnet A L'INSTANT du fill.
    """
    import gzip
    from pathlib import Path as _P

    path = _P(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stats = {"snapshots": 0, "invalid": 0, "errors": 0,
             "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    deadline = time.monotonic() + duration_s
    next_flush = time.monotonic() + 30.0
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"_meta": True, "token_ids": list(token_ids),
                             "snapshot_s": snapshot_s,
                             "started_at": stats["started_at"]}) + "\n")
        while time.monotonic() < deadline:
            cycle = time.monotonic()
            for tid in token_ids:
                book = load_book(tid)
                now_ms = int(time.time() * 1000)
                if book is None:
                    stats["invalid"] += 1
                    fh.write(json.dumps({"i": tid, "ok": False,
                                         "recv": now_ms}) + "\n")
                    continue
                stats["snapshots"] += 1
                fh.write(json.dumps({
                    "i": tid, "ok": True, "recv": now_ms, "ts": book["ts"],
                    "b": book["bids"], "a": book["asks"]},
                    separators=(",", ":")) + "\n")
                time.sleep(REQUEST_PAUSE_S)
            if time.monotonic() >= next_flush:
                next_flush = time.monotonic() + 30.0
                fh.flush()
                stats["heartbeat_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                stats["elapsed_s"] = round(duration_s - (deadline - time.monotonic()), 1)
                _P(str(path) + ".stats.json").write_text(
                    json.dumps(stats, indent=1), encoding="utf-8")
            wait = snapshot_s - (time.monotonic() - cycle)
            if wait > 0:
                time.sleep(wait)
    stats["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _P(str(path) + ".stats.json").write_text(json.dumps(stats, indent=1),
                                             encoding="utf-8")
    return stats
