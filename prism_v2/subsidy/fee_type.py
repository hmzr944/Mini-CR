"""Le taux de frais TAKER d'un marche, resolu de facon AUTORITAIRE.

POURQUOI CE MODULE REMPLACE UNE DEVINETTE. subsidy_fills.py deduisait la
categorie tarifaire par mots-cles dans le libelle du marche : 5 a 6 marches
sur 10 restaient sans categorie, et leur cout de neutralisation restait
INCONNU — ce qui rendait le net du portefeuille inconnu. Or l'API gamma publie
le champ `feeType` PAR MARCHE, et `feesEnabled`. Ce n'est pas une deduction :
c'est la venue qui declare la categorie. On l'utilise.

LE FAIT QUI DECIDE DU CHOIX DE MARCHE. `feesEnabled = false` (feeType nul) =
marche SANS frais taker — geopolitique et evenements mondiaux. Neutraliser un
inventaire n'y coute que le spread. C'est la, et seulement la, que le net du
recolteur a une chance structurelle d'etre positif.

Bareme V2 (30 mars 2026), taux verifies contre la formule
fee = parts * taux * p * (1 - p) : a p = 0,50, taux 0,04 -> 1,00 $/100 parts,
ce qui reconcilie le bareme public « max 1,00 $/100 parts ».
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Dict, Iterable, List, Optional

GAMMA = "https://gamma-api.polymarket.com"

#: feeType publie -> taux taker, en fraction. Un feeType absent ou vide, ou
#: feesEnabled = False, vaut ZERO : la venue declare le marche sans frais.
#: Une categorie INCONNUE (feeType present mais absent de cette table) rend
#: None : on refuse de deviner, et le net devient inconnu plutot que faux.
FEE_TYPE_RATES: Dict[str, float] = {
    "politics_fees": 0.04,
    "sports_fees_v2": 0.03,
    "sports_fees_v3": 0.03,
    "crypto_fees_v2": 0.07,
    "finance_prices_fees": 0.04,
    "culture_fees": 0.05,
    "tech_fees": 0.04,
    "mentions_fees": 0.04,
    "weather_fees": 0.05,
    "economics_fees": 0.05,
    "geopolitics_fees": 0.00,
}


def rate_for(fee_type: Optional[str], fees_enabled: Optional[bool]) -> Optional[float]:
    """Taux taker d'un marche, ou None si la categorie est inconnue.

    Ordre : feesEnabled explicitement False -> 0 (marche sans frais) ; feeType
    absent -> 0 ; feeType connu -> son taux ; feeType present mais hors table
    -> None (inconnu, jamais devine).
    """
    if fees_enabled is False:
        return 0.0
    if not fee_type:
        return 0.0
    return FEE_TYPE_RATES.get(fee_type)


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.2 * (i + 1))


def fetch_fee_rates(max_markets: int = 1_500) -> Dict[str, Optional[float]]:
    """{condition_id: taux taker} pour les marches actifs, depuis gamma.

    La cle est le condition_id, qui joint avec les marches subventionnes lus
    cote CLOB. Un condition_id absent de la carte laisse l'appelant sur None,
    donc sur un cout inconnu — jamais sur zero.
    """
    out: Dict[str, Optional[float]] = {}
    for off in range(0, max_markets, 500):
        try:
            ms = _get(f"{GAMMA}/markets?closed=false&active=true&limit=500"
                      f"&offset={off}&order=volumeNum&ascending=false")
        except Exception:
            break
        if not ms:
            break
        for m in ms:
            cid = m.get("conditionId")
            if not cid:
                continue
            out[cid] = rate_for(m.get("feeType"), m.get("feesEnabled"))
        if len(ms) < 500:
            break
        time.sleep(0.15)
    return out


def fetch_fee_rates_for(condition_ids: Iterable[str],
                        batch: int = 20) -> Dict[str, Optional[float]]:
    """{condition_id -> taux taker} pour un ENSEMBLE CIBLE de marches.

    Le balayage general de gamma plafonne et ne recoupe qu'une fraction des
    marches subventionnes lus cote CLOB. Mais on n'a besoin des frais que pour
    les marches que l'allocation retient reellement : on les demande donc un a
    un par le filtre `condition_ids`, ce qui garantit la couverture la ou elle
    decide du net. Un condition_id que gamma ne renvoie pas reste ABSENT de la
    carte — l'appelant le lira comme un cout inconnu, jamais comme zero.
    """
    ids = [c for c in condition_ids if c]
    out: Dict[str, Optional[float]] = {}
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        qs = "&".join("condition_ids=" + urllib.parse.quote(c) for c in chunk)
        try:
            ms = _get(f"{GAMMA}/markets?{qs}&limit={batch}")
        except Exception:
            continue
        for m in ms or []:
            cid = m.get("conditionId")
            if cid:
                out[cid] = rate_for(m.get("feeType"), m.get("feesEnabled"))
        time.sleep(0.15)
    return out
