#!/usr/bin/env python3
"""CARRY sur les instruments REELLEMENT accessibles depuis la France.

    python3 -m prism_v2.scans.carry_eea

POURQUOI CE MODULE PLUTOT QUE `legal_carry.py`. Le module existant mesure le
carry sur OKX GLOBAL. Un resident francais n'y a pas acces aux perpetuels :
l'entite qui le sert est OKX Europe, dont les X-Perps ne sont PAS exposes par
l'API publique (486 instruments SWAP verifies, aucun X-Perp). Mesurer le
rendement d'un instrument qu'on ne peut pas traiter est l'erreur exacte que ce
projet documente depuis le debut. Ce module ne scanne donc que ce qui est
accessible et verifiable.

TROIS FAITS ETABLIS SUR SOURCE PRIMAIRE (eu.support.backpack.exchange) :
  - Backpack EU (Trek Labs Europe, CySEC 273/15) sert la FRANCE ;
  - elle offre SPOT et PERP, donc les deux jambes du carry sur un seul compte ;
  - le compte est en CROSS-MARGIN : « all eligible assets are used as
    collateral by default ». Le spot collateralise donc le short, et la
    question du « facteur 2 » entre rendement sur notionnel et rendement sur
    capital se resout du cote FAVORABLE. C'est verifie, pas suppose.

L'UNITE, QUI EST LE PIEGE DE CE MODULE. `legal_carry.py` annualise en
multipliant par 3 x 365 : il suppose un funding toutes les 8 h, ce qui est la
convention d'OKX. Backpack regle le funding TOUTES LES HEURES — mesure sur les
horodatages, pas lue dans une documentation. Appliquer le multiplicateur d'OKX
aux donnees de Backpack sous-estimerait d'un FACTEUR 8. L'intervalle est donc
mesure par symbole, et un symbole dont l'intervalle n'est pas determinable rend
UNKNOWN au lieu d'un nombre.

CE QUE CE MODULE NE PROMET PAS. Le net calcule est AVANT : l'inversion future
du funding (la stabilite mesure le passe), le risque de contrepartie, le risque
de liquidation (1 %/fill chez Backpack), la base spot-perp a la sortie, et
l'impot francais sur les plus-values crypto.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics as st
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

API = "https://api.backpack.exchange/api/v1"

#: Bareme Tier 1 (volume 30 j = 0 $), spot ET perp, source primaire
#: eu.support.backpack.exchange/exchange/trading-fees. En bps.
TAKER_BPS = 5.0
MAKER_BPS = 2.0

#: Au-dela de ce funding annualise, le marche est en STRESS, pas en carry.
#: Reprise a l'identique de `legal_carry.EXTREME_APR_CAP` : le seuil n'est pas
#: redeplace pour ce scan, sinon il ne voudrait plus rien dire.
EXTREME_APR_CAP = 50.0

#: Fiabilite minimale du signe du funding : |moyenne| / ecart-type.
MIN_STABILITY = 1.0

#: Nombre minimal de releves pour qu'une moyenne de funding ait un sens.
MIN_FUNDING_SAMPLES = 100

#: Volume 24 h minimal pour tenir une position a 1 000 EUR sans etre le marche.
MIN_VOL_USD = 5e6

#: Duree de detention du scenario central, en jours.
HOLD_DAYS = 60.0


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def measured_interval_hours(stamps: Sequence[str]) -> Optional[float]:
    """Intervalle de funding MESURE sur les horodatages. None si indeterminable.

    Ne jamais supposer 8 h parce qu'une autre venue fait ainsi : c'est un
    facteur 8 sur le resultat, et ce projet a deja paye une faute d'unite
    (maker_base_fee lu comme une fraction, facteur 10 000).
    """
    if len(stamps) < 3:
        return None
    ts = sorted(dt.datetime.fromisoformat(s) for s in stamps)
    deltas = [(ts[i + 1] - ts[i]).total_seconds() / 3600.0
              for i in range(len(ts) - 1)]
    deltas = [d for d in deltas if d > 0]
    if not deltas:
        return None
    med = st.median(deltas)
    return med if med > 0 else None


def annualised_apr_pct(mean_rate_per_period: float,
                       interval_hours: float) -> float:
    """Taux par periode -> pourcentage annualise, via l'intervalle MESURE."""
    if interval_hours <= 0:
        raise ValueError("intervalle de funding non strictement positif")
    periods_per_year = (24.0 / interval_hours) * 365.0
    return mean_rate_per_period * periods_per_year * 100.0


def stability(rates: Sequence[float]) -> float:
    """|moyenne| / ecart-type. 0 si degenere — jamais une valeur flatteuse."""
    if len(rates) < 2:
        return 0.0
    sd = st.pstdev(rates)
    if sd <= 0:
        return 0.0
    return abs(st.fmean(rates)) / sd


def round_trip_cost_bps(spot_spread_bps: float, perp_spread_bps: float,
                        taker_bps: float = TAKER_BPS) -> float:
    """Cout des QUATRE traversees d'un carry, en bps du notionnel.

    Un carry n'est pas un aller-retour mais deux : entrer le spot, entrer le
    perp, sortir le spot, sortir le perp. Chaque jambe paie son demi-spread et
    son frais taker, a l'entree comme a la sortie.

    Le demi-spread est LU au carnet, jamais estime depuis la bande — c'est la
    garde etablie par `backpack/passive.tape_mid_is_fit_for_level`.
    """
    if spot_spread_bps < 0 or perp_spread_bps < 0:
        raise ValueError("un spread ne peut pas etre negatif")
    par_jambe_spot = 0.5 * spot_spread_bps + taker_bps
    par_jambe_perp = 0.5 * perp_spread_bps + taker_bps
    return 2.0 * (par_jambe_spot + par_jambe_perp)


@dataclass(frozen=True)
class CarryRow:
    """Une ligne du livrable. Tout champ inconnu vaut None, jamais zero."""

    perp: str
    spot: str
    vol_usd: float
    interval_hours: Optional[float]
    n_samples: int
    raw_mean_rate: Optional[float]       # taux BRUT par periode, pour audit
    funding_apr: Optional[float]         # annualise, signe
    stability: float
    spot_spread_bps: Optional[float]
    perp_spread_bps: Optional[float]
    cost_bps: Optional[float]

    def gross_pct(self, days: float = HOLD_DAYS) -> Optional[float]:
        """Funding encaisse sur la periode. On est SHORT le perp : on encaisse
        quand le funding est POSITIF (les longs paient les shorts)."""
        if self.funding_apr is None:
            return None
        return self.funding_apr * days / 365.0

    def net_pct(self, days: float = HOLD_DAYS) -> Optional[float]:
        g = self.gross_pct(days)
        if g is None or self.cost_bps is None:
            return None
        return g - self.cost_bps / 100.0

    def net_pct_adverse(self, days: float = HOLD_DAYS) -> Optional[float]:
        """Scenario defavorable : le funding RETOMBE A ZERO des l'entree.

        On garde le cout, on perd le revenu. Ce n'est pas le pire cas — le
        funding peut s'INVERSER et coûter — mais c'est le premier scenario
        defavorable que la donnee autorise a chiffrer sans le supposer.
        """
        if self.cost_bps is None:
            return None
        return -self.cost_bps / 100.0

    def status(self) -> str:
        if (self.funding_apr is None or self.cost_bps is None
                or self.interval_hours is None):
            return "NON RESOLU"
        if self.n_samples < MIN_FUNDING_SAMPLES:
            return "NON RESOLU"
        if abs(self.funding_apr) > EXTREME_APR_CAP:
            return "REJETE (stress)"
        if self.vol_usd < MIN_VOL_USD:
            return "REJETE (illiquide)"
        if self.stability < MIN_STABILITY:
            return "REJETE (signe instable)"
        net = self.net_pct()
        if net is None or net <= 0:
            return "REJETE (net <= 0)"
        return "CANDIDAT"


def build_universe() -> List[Tuple[str, str, float]]:
    """Perpetuels ayant AUSSI un marche spot. Derive, jamais ecrit a la main.

    Un carry exige les deux jambes sur le meme compte. Un perp sans spot
    correspondant n'est pas un candidat, quel que soit son funding.
    """
    markets = _get(f"{API}/markets")
    tickers = {t["symbol"]: t for t in _get(f"{API}/tickers")}
    spot = {m["symbol"] for m in markets if m.get("marketType") == "SPOT"}
    out = []
    for m in markets:
        if m.get("marketType") != "PERP":
            continue
        sym = m["symbol"]
        base = m.get("baseSymbol")
        cand = f"{base}_USDC"
        if cand not in spot or sym not in tickers:
            continue
        out.append((sym, cand, float(tickers[sym]["quoteVolume"])))
    return sorted(out, key=lambda x: -x[2])


def _spread_bps(symbol: str) -> Optional[float]:
    """Spread LU au carnet. None si le marche est liste mais sans carnet.

    Certains marches figurent dans `/markets` sans repondre a `/depth` (404).
    Un carnet absent est un cout INCONNU, donc un net inconnu — jamais un
    spread de zero, qui rendrait le carry gratuit sur les marches morts.
    """
    try:
        d = _get(f"{API}/depth?symbol={symbol}")
    except Exception:
        return None
    bids, asks = d.get("bids") or [], d.get("asks") or []
    if not bids or not asks:
        return None
    bb = max(float(b[0]) for b in bids)
    ba = min(float(a[0]) for a in asks)
    if bb <= 0 or bb >= ba:
        return None
    return 1e4 * (ba - bb) / (0.5 * (bb + ba))


def measure(perp: str, spot: str, vol_usd: float, limit: int = 1000) -> CarryRow:
    hist = _get(f"{API}/fundingRates?symbol={perp}&limit={limit}")
    rates = [float(h["fundingRate"]) for h in hist]
    iv = measured_interval_hours([h["intervalEndTimestamp"] for h in hist])
    mean = st.fmean(rates) if rates else None
    apr = (annualised_apr_pct(mean, iv)
           if (mean is not None and iv) else None)
    ss, ps = _spread_bps(spot), _spread_bps(perp)
    cost = (round_trip_cost_bps(ss, ps)
            if (ss is not None and ps is not None) else None)
    return CarryRow(perp, spot, vol_usd, iv, len(rates), mean, apr,
                    stability(rates), ss, ps, cost)


def _f(v: Optional[float], w: int = 9, p: int = 2) -> str:
    return "INCONNU".rjust(w) if v is None else f"{v:>{w}.{p}f}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--markets", type=int, default=20)
    p.add_argument("--days", type=float, default=HOLD_DAYS)
    a = p.parse_args(argv)

    univers = build_universe()
    print(f"perpetuels AVEC jambe spot sur la meme venue : {len(univers)}")
    print(f"bareme Tier 1 : maker {MAKER_BPS:.0f} bps / taker {TAKER_BPS:.0f} bps "
          f"(spot ET perp, source primaire)")
    print(f"detention      : {a.days:.0f} jours")
    print(f"filtre d'arret : |funding annualise| > {EXTREME_APR_CAP:.0f} %/an "
          f"=> REJETE (stress)")
    print()
    head = (f"{'perp':<18}{'vol 24h $':>13}{'iv h':>6}{'n':>6}{'APR %':>9}"
            f"{'stab':>7}{'spr spot':>10}{'spr perp':>10}{'cout bps':>10}"
            f"{'net 60j %':>11}{'defav %':>9}  statut")
    print(head)
    print("-" * len(head))

    rows = []
    for perp, spot, vol in univers[:a.markets]:
        r = measure(perp, spot, vol)
        rows.append(r)
        print(f"{r.perp:<18}{r.vol_usd:>13,.0f}{_f(r.interval_hours, 6, 1)}"
              f"{r.n_samples:>6}{_f(r.funding_apr)}{r.stability:>7.2f}"
              f"{_f(r.spot_spread_bps, 10)}{_f(r.perp_spread_bps, 10)}"
              f"{_f(r.cost_bps, 10)}{_f(r.net_pct(a.days), 11)}"
              f"{_f(r.net_pct_adverse(a.days), 9)}  {r.status()}")

    cands = [r for r in rows if r.status() == "CANDIDAT"]
    print()
    print(f"CANDIDATS : {len(cands)}")
    for r in cands:
        net = r.net_pct(a.days)
        print(f"  {r.perp:<18} net {net:>6.2f} % / {a.days:.0f} j "
              f"= {net * 365.0 / a.days:>6.2f} %/an   "
              f"sur 1 000 EUR : {10.0 * net:>6.2f} EUR / {a.days:.0f} j")
    print()
    print("OKX Europe (X-Perps) : NON RESOLU — l'API publique OKX n'expose")
    print("aucun X-Perp (486 SWAP verifies). Aucune substitution par les")
    print("instruments OKX globaux : ils ne sont pas accessibles depuis la France.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
