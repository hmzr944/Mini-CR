#!/usr/bin/env python3
"""Collecte CARNET + BANDE sur une seule connexion, une seule horloge.

    python3 -m prism_v2.backpack.ws_collect --out FICHIER --minutes 9

CE QUE CE MODULE CORRIGE. La mesure de cotation passive melangeait deux
sources : le carnet venait d'un sondage REST a 5 s, la bande d'un appel REST
separe. Deux horloges, deux latences, et un carnet perime de plusieurs
secondes au moment du fill. C'est la cause mesuree des exclusions par
`tape_mid_is_fit_for_level` : le demi-spread « mesure » suivait la cadence de
sondage et non le marche.

    sondage /depth 5 s     ecart median entre carnets   4 989 ms
    WebSocket bookTicker                                    4 ms

Ici les deux flux viennent de la MEME connexion WebSocket, donc de la meme
horloge d'exchange. Un desalignement carnet/bande ne peut plus provenir de la
mesure.

DEUX HORODATAGES SONT CONSERVES POUR CHAQUE MESSAGE, et c'est delibere :

    ts_ex     `E`, horodatage de l'exchange, en microsecondes
    ts_loc    l'instant de RECEPTION local

Le premier sert aux mesures. Le second permet d'auditer apres coup la latence
de transport et de detecter une derive : si `ts_loc - ts_ex` se met a deriver,
les deux flux restent coherents entre eux mais la fenetre de collecte ne
decrit plus ce qu'on croit. Conserver les deux coute un champ et evite de
devoir refaire une collecte pour repondre a une question d'horloge.

CE QUE CE MODULE NE PRETEND PAS. Le `bookTicker` donne le MEILLEUR bid/ask et
sa taille. Il ne demontre ni la position dans la file, ni le taux de
remplissage, ni l'adverse selection subie. Les tailles au toucher permettent
de poser un ordre DERRIERE la file observee (`subsidy.tape.simulate`), ce qui
est un majorant defendable — pas une preuve de capture.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.backpack.ws import WebSocket

#: Flux souscrits par symbole. Les deux sur la MEME connexion.
STREAMS = ("bookTicker", "trade")


def collect(symbols: Sequence[str], out: Path, seconds: float) -> dict:
    """Ecrit carnet et bande jusqu'a `seconds`. Rend le compte par type.

    Une coupure est INSCRITE dans le fichier, jamais comblee : le rejeu doit
    voir le trou plutot que de lire une continuite qui n'a pas eu lieu.
    """
    fin = time.time() + seconds
    n = {"book": 0, "trade": 0, "trou": 0}
    with out.open("a", buffering=1) as fh:
        try:
            with WebSocket() as ws:
                ws.subscribe([f"{s}.{sym}" for sym in symbols for s in STREAMS])
                for msg in ws.messages():
                    if time.time() >= fin:
                        break
                    try:
                        d = json.loads(msg)
                        data = d["data"]
                        e = data.get("e")
                        if e == "bookTicker":
                            fh.write(json.dumps({
                                "k": "book", "ts_ex": float(data["E"]) / 1e6,
                                "ts_loc": time.time(), "sym": data["s"],
                                "bid": float(data["b"]), "ask": float(data["a"]),
                                "bid_sz": float(data["B"]),
                                "ask_sz": float(data["A"])}) + "\n")
                            n["book"] += 1
                        elif e == "trade":
                            # `m` vrai = l'acheteur etait MAKER, donc le preneur
                            # etait VENDEUR. L'inversion ici echangerait
                            # demi-spread et markout : elle est testee.
                            fh.write(json.dumps({
                                "k": "trade", "ts_ex": float(data["E"]) / 1e6,
                                "ts_loc": time.time(), "sym": data["s"],
                                "price": float(data["p"]),
                                "size": float(data["q"]),
                                "taker_is_buy": not bool(data["m"])}) + "\n")
                            n["trade"] += 1
                    except Exception:
                        continue
        except Exception as exc:
            fh.write(json.dumps({"k": "trou", "ts_loc": time.time(),
                                 "raison": str(exc)[:200]}) + "\n")
            n["trou"] += 1
    return n


def audit_horloge(path: Path) -> dict:
    """Latence de transport observee, et sa derive. Controle obligatoire.

    `ts_loc - ts_ex` melange latence reseau et decalage d'horloge ; il n'est
    pas decompose. Ce qui compte ici est sa STABILITE : une derive signalerait
    que la fenetre de collecte ne decrit pas ce qu'on croit.
    """
    import statistics as st

    lat: List[float] = []
    par_type = {"book": 0, "trade": 0, "trou": 0}
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
            if k in par_type:
                par_type[k] += 1
            if "ts_ex" in d and "ts_loc" in d:
                lat.append((d["ts_loc"] - d["ts_ex"]) * 1000.0)
    if not lat:
        return {"n": 0, **par_type}
    moitie = len(lat) // 2
    return {
        "n": len(lat), **par_type,
        "latence_mediane_ms": st.median(lat),
        "latence_p95_ms": sorted(lat)[int(0.95 * len(lat))],
        "derive_ms": (st.median(lat[moitie:]) - st.median(lat[:moitie]))
        if moitie else 0.0,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    from prism_v2.scans.propagation import PAIRES

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--minutes", type=float, default=9.0)
    a = p.parse_args(argv)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    symbols = [b for _, b in PAIRES]
    n = collect(symbols, a.out, a.minutes * 60.0)
    print(f"carnet {n['book']:,}   bande {n['trade']:,}   trous {n['trou']}",
          flush=True)
    print(json.dumps(audit_horloge(a.out), indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
