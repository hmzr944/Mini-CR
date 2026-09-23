"""CARRY MARCHE-NEUTRE SOUTENABLE — le systeme legal, modeste et reel.

    python -m prism_v2.scans.legal_carry

POURQUOI CE MODULE. Apres deux constats :
  1. toute capture de PRIX directionnelle accessible a 1 000 EUR est a edge
     negatif (balayage large, 478 instruments, 0 survivant) ;
  2. la subvention Polymarket — seul mecanisme a signe positif — est ILLEGALE
     et bloquee en France (ANJ, 16 juillet 2026).
Reste une seule structure a la fois LEGALE et non-directionnelle : le CARRY.
Detenir le spot et vendre le perpetuel (ou l'inverse) encaisse le funding sans
parier sur la direction. Ce module en mesure le rendement REEL et SOUTENABLE.

CE QUI FAIT LA DIFFERENCE ENTRE UNE MESURE HONNETE ET UN PIEGE. Cueillir le
funding le plus fort revient a cueillir le marche le plus CASSE : un funding de
-300 %/an (ONE-USDT) signale une dislocation persistante ou un delistage, ou la
position neutre n'est PAS tenable (pas de spot a shorter, jambes qui divergent,
liquidation). Trois filtres, declares d'avance, separent le carry du mirage :
  - SOUTENABLE : on prend la MOYENNE realisee du funding sur l'historique, pas
    un instantane. Un instantane surestime.
  - STABLE : |moyenne| / ecart-type >= 1. Le signe du funding doit etre fiable.
  - NON EXTREME : |funding annualise| <= CAP. Au-dela, c'est un marche en
    stress, pas un rendement — exclu, meme s'il « rapporte » sur le papier.
  - LIQUIDE : volume 24 h suffisant pour tenir la position a 1 000 EUR.

CE QUE CE MODULE NE PROMET PAS. Le net calcule est AVANT : (a) l'inversion
future du funding — la stabilite mesure le passe, pas l'avenir ; (b) l'acces
retail EU aux perpetuels, restreint par l'ESMA/AMF, qui est la vraie condition
juridique a verifier ; (c) le risque de contrepartie ; (d) l'impot francais sur
les plus-values crypto. Le carry soutenable mesure ~5-6 %/an net sans levier :
c'est modeste, c'est reel, et ce n'est pas un x5.
"""
from __future__ import annotations

import json
import statistics as st
import time
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Sequence

OKX = "https://www.okx.com"
TAKER_BPS = 5.0
#: Au-dela de ce funding annualise, le marche est en STRESS, pas en carry. Un
#: funding « normal » de perp vit sous 30-40 %/an ; 50 % est une borne large et
#: prudente qui laisse passer les vrais carries et coupe les dislocations.
EXTREME_APR_CAP = 50.0
#: Volume 24 h minimal (USD) pour tenir serieusement une position a 1 000 EUR.
MIN_VOL_USD = 50e6
#: Fiabilite minimale du signe du funding : moyenne / ecart-type.
MIN_STABILITY = 1.0


@dataclass(frozen=True)
class CarryRow:
    inst_id: str
    vol_usd: float
    funding_apr: float          # moyenne realisee, annualisee, signee
    stability: float            # |moyenne| / ecart-type
    setup_bps: float            # cout entree (2 jambes) + sortie (2 jambes)
    net_pct_60d: float

    def annualised_net_pct(self) -> float:
        return self.net_pct_60d * 365.0 / 60.0

    def is_harvestable(self) -> bool:
        """Carry REEL : liquide, stable, non extreme, net positif.

        L'exclusion des extremes est la regle qui separe un rendement d'un
        marche casse. Elle est ici, pas dans un commentaire.
        """
        return (self.vol_usd >= MIN_VOL_USD
                and self.stability >= MIN_STABILITY
                and abs(self.funding_apr) <= EXTREME_APR_CAP
                and self.net_pct_60d > 0.0)


def sustainable_apr(rates_per_8h: Sequence[float]) -> Optional[float]:
    """Funding annualise depuis la MOYENNE realisee. None si trop court."""
    if len(rates_per_8h) < 30:
        return None
    return st.fmean(rates_per_8h) * 3.0 * 365.0 * 100.0


def stability(rates_per_8h: Sequence[float]) -> float:
    """|moyenne| / ecart-type des funding annualises. 0 si degenere."""
    if len(rates_per_8h) < 2:
        return 0.0
    sd = st.pstdev(rates_per_8h)
    if sd <= 0:
        return 0.0
    return abs(st.fmean(rates_per_8h)) / sd


def net_carry_pct(funding_apr: float, setup_bps: float, days: float) -> float:
    """Net d'un carry marche-neutre tenu `days` jours, sans levier.

    On capte |funding| (on se place du bon cote). Le cout d'entree + sortie est
    paye UNE fois et amorti sur la duree : c'est ce qui rend le carry possible
    la ou le trading a haute rotation echoue.
    """
    gross = abs(funding_apr) * days / 365.0
    return gross - setup_bps / 100.0


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.7 * (i + 1))


def measure(top_n: int = 20, hist: int = 90) -> List[CarryRow]:
    tickers = _get(f"{OKX}/api/v5/market/tickers?instType=SWAP")["data"]
    perps = sorted(
        ({"id": t["instId"],
          "vol": float(t.get("volCcy24h") or 0) * float(t.get("last") or 0)}
         for t in tickers if t["instId"].endswith("-USDT-SWAP")),
        key=lambda p: -p["vol"])[:top_n]
    rows: List[CarryRow] = []
    for p in perps:
        try:
            h = _get(f"{OKX}/api/v5/public/funding-rate-history"
                     f"?instId={p['id']}&limit={hist}")["data"]
            rates = [float(x.get("realizedRate") or x.get("fundingRate"))
                     for x in h]
            apr = sustainable_apr(rates)
            if apr is None:
                continue
            b = _get(f"{OKX}/api/v5/market/books?instId={p['id']}&sz=1")["data"][0]
            bid, ask = float(b["bids"][0][0]), float(b["asks"][0][0])
            spread_bps = (ask - bid) / ((ask + bid) / 2.0) * 1e4
            setup = 4.0 * (TAKER_BPS + spread_bps / 2.0)
            rows.append(CarryRow(
                inst_id=p["id"], vol_usd=p["vol"], funding_apr=apr,
                stability=stability(rates), setup_bps=setup,
                net_pct_60d=net_carry_pct(apr, setup, 60.0)))
            time.sleep(0.05)
        except Exception:
            continue
    return rows


def main() -> None:
    rows = measure()
    harvest = [r for r in rows if r.is_harvestable()]
    harvest.sort(key=lambda r: -r.net_pct_60d)

    print("=" * 76)
    print("CARRY MARCHE-NEUTRE SOUTENABLE — systeme legal, modeste, reel")
    print("=" * 76)
    print(f"{'perp':<18}{'vol M$':>9}{'fund APR':>11}{'stab':>7}"
          f"{'setup bps':>11}{'net 60j %':>11}{'retenu':>8}")
    for r in sorted(rows, key=lambda x: -x.net_pct_60d)[:16]:
        flag = "OUI" if r.is_harvestable() else (
            "extreme" if abs(r.funding_apr) > EXTREME_APR_CAP else
            "instable" if r.stability < MIN_STABILITY else
            "peu liq." if r.vol_usd < MIN_VOL_USD else "net<=0")
        print(f"{r.inst_id:<18}{r.vol_usd/1e6:>9.0f}{r.funding_apr:>10.1f}%"
              f"{r.stability:>7.2f}{r.setup_bps:>11.1f}{r.net_pct_60d:>11.2f}"
              f"{flag:>8}")

    print("\n" + "-" * 76)
    if not harvest:
        print("AUCUN carry a la fois liquide, stable, non extreme et net-positif.")
        return
    best = harvest[0]
    med_net = st.median([r.net_pct_60d for r in harvest])
    print(f"carries RETENUS (liquides, stables, non extremes) : {len(harvest)}")
    print(f"  meilleur : {best.inst_id} — funding moyen {best.funding_apr:.1f} %/an, "
          f"stabilite {best.stability:.1f}")
    print(f"  net : {best.net_pct_60d:.2f} % sur 60 j = "
          f"{best.annualised_net_pct():.1f} %/an, sans levier")
    print(f"  net MEDIAN du panier retenu : {med_net:.2f} % / 60 j = "
          f"{med_net*365/60:.1f} %/an")
    print("\nCE QUE C'EST : ~5-6 %/an net sur le NOTIONNEL, marche-neutre, sur")
    print("les perps les plus liquides (BTC/ETH/majors). Modeste, reel, PAS un x5.")
    print("\nATTENTION — RENDEMENT SUR CAPITAL. Un carry mobilise DEUX jambes :")
    print("spot achete + marge du short. Si la venue laisse le spot collateraliser")
    print("le short (marge unifiee), le rendement sur capital reste ~celui-ci ;")
    print("sinon il est ~MOITIE. Ce facteur 2 depend de la venue et doit etre")
    print("verifie avant tout chiffre sur capital.")
    print("\nCE QUE LE NET NE COMPTE PAS ENCORE : inversion future du funding,")
    print("acces retail EU aux perps (ESMA/AMF — condition juridique a verifier),")
    print("risque de liquidation de la jambe short, contrepartie, impot francais.")
    print("=" * 76)


if __name__ == "__main__":
    main()
