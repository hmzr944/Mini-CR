#!/usr/bin/env python3
"""PROPAGATION OKX -> Backpack EU. Reste-t-il quelque chose apres le delai ?

    python3 -m prism_v2.scans.propagation collecte --minutes 9 --out FICHIER
    python3 -m prism_v2.scans.propagation analyse  --in FICHIER

LA QUESTION, ET POURQUOI ELLE PASSE AVANT LE PROTOCOLE COMPLET. Les
liquidations ne sont publiees que par OKX, sur des instruments qu'un resident
francais ne peut pas traiter. La venue traitable, Backpack EU, n'expose aucun
flux de liquidations (404 verifie). Toute exploitation suppose donc que
l'information TRAVERSE d'une venue a l'autre assez lentement pour qu'on puisse
agir. Si elle ne traverse pas, ou traverse plus vite que le delai de reaction,
le protocole complet est inutile — et cela se mesure en une heure.

CE QUE CE MODULE SEPARE, PARCE QUE LES CONFONDRE EST LA FAUTE CENTRALE :

    OBSERVE     le mid Backpack bouge-t-il apres la liquidation ?
    CAPTURABLE  en reste-t-il apres 0,5 / 1 / 2 / 5 s de delai ?
    NET         en reste-t-il apres le spread traverse et les frais ?

Un mouvement OBSERVE reel et entierement resorbe avant 0,5 s est un vrai
phenomene et une opportunite nulle.

LE SENS EST FIXE PAR LA LIQUIDATION, JAMAIS PAR LE RESULTAT. Une liquidation
de position LONGUE force une VENTE : la pression attendue est BAISSIERE, donc
la position a prendre est SHORT. Ce signe est decide avant de lire le prix.
Choisir apres coup le cote qui aurait gagne fabriquerait un edge a partir de
n'importe quel bruit, et c'est la faute la plus facile a commettre ici.

LE TEMOIN EST OBLIGATOIRE. Pour chaque episode, on mesure aussi une fenetre
TEMOIN prise loin de toute liquidation. Un mid bouge tout le temps : sans
temoin, on mesurerait la volatilite ordinaire et on l'appellerait propagation.
C'est exactement la faute que le test a blanc a deja attrapee une fois dans ce
depot, sur un marche haussier lu comme un edge.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import random

from prism_v2.backpack.ws import WebSocket
from prism_v2.tail import Event, group_into_episodes, quantile

OKX = "https://www.okx.com/api/v5"

#: Delais de reaction testes, en secondes. FIGES avant toute lecture.
DELAIS_S: Tuple[float, ...] = (0.5, 1.0, 2.0, 5.0)

#: Horizon de sortie apres l'entree. Une entree sans sortie n'est pas un trade.
SORTIE_S = 30.0

#: Ecart de regroupement en episodes, identique au protocole gele.
GAP_S = 60.0

#: Frais taker Backpack EU Tier 1, aller-retour (entree + sortie), en bps.
FRAIS_AR_BPS = 10.0

#: Fraicheur maximale d'un point de prix pour qu'il decrive l'instant demande.
#: Le flux publie ~35 msg/s : 2 s est large, et un trou plus long est un trou.
MAX_AGE_S = 2.0

#: Un temoin doit etre a AU MOINS cette distance de tout episode, sinon il
#: mesure la queue de l'evenement qu'il est cense servir de reference.
TEMOIN_ECART_MIN_S = 120.0

#: Nombre de temoins tires par episode. Plusieurs valent mieux qu'un : un
#: temoin unique herite de la volatilite de son propre instant.
TEMOINS_PAR_EPISODE = 3

#: UNIVERS GELE LE 23 SEPTEMBRE 2026, AVANT TOUTE LECTURE DE RESULTAT.
#:
#: DERIVE, jamais ecrit a la main : intersection des familles OKX SWAP -USDT
#: et des perpetuels Backpack, triee par le volume 24 h **en USD de
#: Backpack** — la venue ou l'on traite, et la seule unite homogene.
#:
#: UN PREMIER TRI ETAIT FAUX ET A ETE JETE. Il classait sur `volCcy24h` d'OKX,
#: libelle dans la DEVISE DU CONTRAT : il comparait 23 milliards de PUMP a un
#: volume exprime en BTC, et sortait un univers d'ou BTC, ETH et SOL etaient
#: absents. Meme faute d'unite que melanger CAPITAL et NOTIONNEL, que le
#: registre interdit deja par typage.
#:
#: Les 20 retenues couvrent 93 % du volume perpetuel de Backpack.
PAIRES = [
    ("BTC-USDT", "BTC_USDC_PERP"), ("SOL-USDT", "SOL_USDC_PERP"),
    ("ETH-USDT", "ETH_USDC_PERP"), ("HYPE-USDT", "HYPE_USDC_PERP"),
    ("UNI-USDT", "UNI_USDC_PERP"), ("ZEC-USDT", "ZEC_USDC_PERP"),
    ("XRP-USDT", "XRP_USDC_PERP"), ("ARB-USDT", "ARB_USDC_PERP"),
    ("NEAR-USDT", "NEAR_USDC_PERP"), ("BNB-USDT", "BNB_USDC_PERP"),
    ("SUI-USDT", "SUI_USDC_PERP"), ("PENGU-USDT", "PENGU_USDC_PERP"),
    ("AAVE-USDT", "AAVE_USDC_PERP"), ("ASTER-USDT", "ASTER_USDC_PERP"),
    ("SEI-USDT", "SEI_USDC_PERP"), ("AVAX-USDT", "AVAX_USDC_PERP"),
    ("LTC-USDT", "LTC_USDC_PERP"), ("DOGE-USDT", "DOGE_USDC_PERP"),
    ("TAO-USDT", "TAO_USDC_PERP"), ("ENA-USDT", "ENA_USDC_PERP"),
]


def _get(url: str, tries: int = 3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            return json.loads(urllib.request.urlopen(req, timeout=25).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.0 * (i + 1))


def mesure_decalage_horloges() -> Dict[str, float]:
    """Ecart entre l'horloge de chaque venue et l'horloge locale, en ms.

    CONTROLE OBLIGATOIRE : les horodatages viennent de DEUX venues. Un
    decalage de 500 ms entre elles deplacerait tous les delais mesures d'un
    delai entier, et un resultat nul se lirait comme une absence de
    propagation alors qu'il serait une erreur d'horloge.
    """
    out: Dict[str, float] = {}
    try:
        r = _get("https://api.backpack.exchange/api/v1/time")
        out["backpack_ms"] = time.time() * 1000.0 - float(r)
    except Exception:
        out["backpack_ms"] = float("nan")
    try:
        r = _get(f"{OKX}/public/time")
        out["okx_ms"] = time.time() * 1000.0 - float(r["data"][0]["ts"])
    except Exception:
        out["okx_ms"] = float("nan")
    out["ecart_relatif_ms"] = out["backpack_ms"] - out["okx_ms"]
    return out


# ----------------------------------------------------------------- collecte

def _flux_backpack(symbols: Sequence[str], fh, stop: threading.Event) -> None:
    """Ecrit chaque mise a jour du toucher. Une coupure est INSCRITE, pas comblee."""
    try:
        with WebSocket() as ws:
            ws.subscribe([f"bookTicker.{s}" for s in symbols])
            for msg in ws.messages():
                if stop.is_set():
                    return
                try:
                    d = json.loads(msg)["data"]
                    fh.write(json.dumps({
                        "k": "bp", "ts": float(d["E"]) / 1e6, "sym": d["s"],
                        "bid": float(d["b"]), "ask": float(d["a"])}) + "\n")
                except Exception:
                    continue
    except Exception as e:
        fh.write(json.dumps({"k": "trou", "ts": time.time(),
                             "raison": str(e)[:200]}) + "\n")


def collecte(out: Path, minutes: float) -> None:
    fin = time.time() + minutes * 60.0
    stop = threading.Event()
    with out.open("a", buffering=1) as fh:
        fh.write(json.dumps({"k": "horloges", "ts": time.time(),
                             **mesure_decalage_horloges()}) + "\n")
        t = threading.Thread(target=_flux_backpack,
                             args=([b for _, b in PAIRES], fh, stop),
                             daemon=True)
        t.start()
        vus = set()
        while time.time() < fin:
            for fam, _ in PAIRES:
                try:
                    d = _get(f"{OKX}/public/liquidation-orders?instType=SWAP"
                             f"&state=filled&instFamily={fam}&limit=100")
                except Exception:
                    continue
                for blk in d.get("data", []):
                    for x in blk.get("details", []):
                        cle = (fam, x["ts"], x["sz"], x["posSide"])
                        if cle in vus:
                            continue
                        vus.add(cle)
                        fh.write(json.dumps({
                            "k": "liq", "ts": float(x["ts"]) / 1000.0,
                            "fam": fam, "sz": float(x["sz"]),
                            "pos_side": x["posSide"], "px": float(x["bkPx"])}) + "\n")
                time.sleep(0.15)
            time.sleep(2.0)
        stop.set()


# ------------------------------------------------------------------ analyse

class Prix:
    """Serie de mids d'un symbole, lue par temps. Aucune interpolation."""

    def __init__(self, points: List[Tuple[float, float, float]]):
        self.pts = sorted(points, key=lambda p: p[0])
        self.ts = [p[0] for p in self.pts]

    def _idx_avant(self, at: float) -> Optional[int]:
        from bisect import bisect_left
        i = bisect_left(self.ts, at) - 1
        return i if i >= 0 else None

    def mid(self, at: float) -> Optional[float]:
        i = self._idx_avant(at)
        if i is None or at - self.ts[i] > MAX_AGE_S:
            return None
        b, a = self.pts[i][1], self.pts[i][2]
        return 0.5 * (b + a)

    def spread_bps(self, at: float) -> Optional[float]:
        i = self._idx_avant(at)
        if i is None or at - self.ts[i] > MAX_AGE_S:
            return None
        b, a = self.pts[i][1], self.pts[i][2]
        m = 0.5 * (b + a)
        return 1e4 * (a - b) / m if m > 0 and a > b else None


def mouvement_bps(mid_ref: float, mid_apres: float, short: bool) -> float:
    """Mouvement compte DANS LE SENS impose par la liquidation.

    `short=True` (liquidation d'un LONG, donc vente forcee, pression baissiere)
    gagne quand le prix BAISSE. Le sens n'est jamais choisi apres coup.
    """
    if mid_ref <= 0:
        raise ValueError("mid de reference non strictement positif")
    d = (mid_ref - mid_apres) if short else (mid_apres - mid_ref)
    return 1e4 * d / mid_ref


def charge(path: Path):
    liqs, prix, horloges, trous = [], {}, {}, 0
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            k = d.get("k")
            if k == "liq":
                liqs.append(d)
            elif k == "bp":
                prix.setdefault(d["sym"], []).append((d["ts"], d["bid"], d["ask"]))
            elif k == "horloges":
                horloges = d
            elif k == "trou":
                trous += 1
    return liqs, {s: Prix(v) for s, v in prix.items()}, horloges, trous


def fenetre_prix(serie: "Prix") -> Optional[Tuple[float, float]]:
    return (serie.ts[0], serie.ts[-1]) if serie.ts else None


def temoins(serie: "Prix", episodes_ts: Sequence[float], besoin_s: float,
            rng: random.Random, n: int = TEMOINS_PAR_EPISODE) -> List[float]:
    """Instants de reference PRIS DANS la fenetre, loin de tout episode.

    LA VERSION PRECEDENTE PRENAIT UN DECALAGE FIXE DE -600 s. Sur une collecte
    de 9 minutes, ce temoin tombait AVANT le debut de la serie de prix : il
    rendait INCONNU sur toutes les lignes, et l'absence de temoin se lisait
    comme une absence de mesure. Un temoin doit vivre dans la meme fenetre que
    ce qu'il controle.
    """
    f = fenetre_prix(serie)
    if f is None:
        return []
    t0, t1 = f
    haut = t1 - besoin_s
    if haut <= t0:
        return []
    out: List[float] = []
    for _ in range(40 * n):
        if len(out) >= n:
            break
        c = rng.uniform(t0, haut)
        if all(abs(c - e) > TEMOIN_ECART_MIN_S for e in episodes_ts):
            out.append(c)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collecte"); c.add_argument("--out", type=Path, required=True)
    c.add_argument("--minutes", type=float, default=9.0)
    a = sub.add_parser("analyse"); a.add_argument("--in", dest="inp", type=Path, required=True)
    ns = p.parse_args(argv)

    if ns.cmd == "collecte":
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        collecte(ns.out, ns.minutes)
        print(f"collecte terminee -> {ns.out}", flush=True)
        return 0

    liqs, prix, horloges, trous = charge(ns.inp)
    bp2fam = {b: f for f, b in PAIRES}
    fam2bp = {f: b for f, b in PAIRES}

    print("CONTROLE D'HORLOGE")
    print(f"  Backpack - local        : {horloges.get('backpack_ms', float('nan')):>8.0f} ms")
    print(f"  OKX - local             : {horloges.get('okx_ms', float('nan')):>8.0f} ms")
    print(f"  ecart RELATIF des venues: {horloges.get('ecart_relatif_ms', float('nan')):>8.0f} ms")
    print(f"  trous de flux           : {trous}")
    print()

    evs = [Event(ts=l["ts"], instrument=l["fam"], size_usd=l["sz"]) for l in liqs]
    eps = group_into_episodes(evs, GAP_S) if evs else []
    # Sens de l'episode : majorite des positions liquidees. Decide AVANT le prix.
    sens: Dict[Tuple[str, float], bool] = {}
    for e in eps:
        dedans = [l for l in liqs if l["fam"] == e.instrument
                  and e.start_ts <= l["ts"] <= e.end_ts]
        n_long = sum(1 for l in dedans if l["pos_side"] == "long")
        sens[(e.instrument, e.start_ts)] = n_long >= len(dedans) - n_long

    besoin = max(DELAIS_S) + SORTIE_S
    retenus = []
    for e in eps:
        sym = fam2bp.get(e.instrument)
        f = fenetre_prix(prix[sym]) if sym in prix else None
        if f and f[0] <= e.end_ts <= f[1] - besoin:
            retenus.append(e)
    print(f"liquidations : {len(liqs)}   episodes (ecart {GAP_S:.0f}s) : {len(eps)}")
    print(f"  dont DANS la fenetre de prix, marge de sortie comprise : {len(retenus)}")
    print(f"  ecartes : {len(eps) - len(retenus)} — la backlog OKX couvre des")
    print(f"  heures anterieures a la collecte ; les mesurer supposerait un prix.")
    eps = retenus
    print(f"symboles avec flux de prix : {sorted(prix)}")
    print()

    head = (f"{'delai':>7}{'n':>6}{'OBSERVE med':>13}{'TEMOIN med':>13}"
            f"{'EXCES':>9}{'spread':>9}{'NET median':>12}")
    print(head); print("-" * len(head))

    rng = random.Random(20260923)   # graine FIGEE : le tirage des temoins
    for delai in DELAIS_S:          # ne doit pas varier d'une lecture a l'autre
        obs, tem, spr = [], [], []
        for e in eps:
            sym = fam2bp.get(e.instrument)
            if sym not in prix:
                continue
            short = sens[(e.instrument, e.start_ts)]
            serie = prix[sym]
            m0 = serie.mid(e.end_ts)
            m1 = serie.mid(e.end_ts + delai)
            m2 = serie.mid(e.end_ts + delai + SORTIE_S)
            s = serie.spread_bps(e.end_ts + delai)
            if None in (m0, m1, m2, s):
                continue
            obs.append(mouvement_bps(m0, m1, short))          # avant l'entree
            # capturable : de l'ENTREE (apres delai) a la SORTIE
            cap = mouvement_bps(m1, m2, short)
            spr.append(s)
            for c in temoins(serie, [x.end_ts for x in eps], besoin, rng):
                a = serie.mid(c)
                b = serie.mid(c + SORTIE_S)
                if a is not None and b is not None:
                    tem.append(mouvement_bps(a, b, short))
            obs[-1] = cap                                      # on garde le capturable
        if len(obs) < 5:
            print(f"{delai:>6.1f}s{len(obs):>6}{'INCONNU':>13}{'INCONNU':>13}"
                  f"{'INCONNU':>9}{'INCONNU':>9}{'INCONNU':>12}")
            continue
        mo, mt = st.median(obs), (st.median(tem) if len(tem) >= 5 else None)
        ms = st.median(spr)
        exces = (mo - mt) if mt is not None else None
        net = (exces - ms - FRAIS_AR_BPS) if exces is not None else None
        f = lambda v, w=13: "INCONNU".rjust(w) if v is None else f"{v:>{w}.2f}"
        print(f"{delai:>6.1f}s{len(obs):>6}{f(mo)}{f(mt)}{f(exces,9)}"
              f"{f(ms,9)}{f(net,12)}")

    print()
    print("OBSERVE  : mouvement de l'ENTREE (apres delai) a la sortie, dans le")
    print("           sens impose par la liquidation.")
    print("TEMOIN   : meme mesure sur une fenetre SANS liquidation.")
    print("EXCES    : observe moins temoin. C'est le seul terme attribuable.")
    print(f"NET      : exces moins spread traverse moins {FRAIS_AR_BPS:.0f} bps de frais.")
    print()
    print("Un EXCES positif mais un NET negatif signifie : la propagation")
    print("EXISTE et n'est pas une opportunite. Les deux enonces tiennent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
