#!/usr/bin/env python3
"""Collecteur de CARNET. La donnee qui manque au projet depuis le debut.

POURQUOI. `LIMITS.md` section 6 nomme deja ce manque : « Le code est deja la ;
il manque uniquement des donnees. » Le scan `backpack_mm.py` vient de le
redemontrer a ses depens — faute d'historique de carnet, il a tire un
demi-spread de la bande des echanges, obtenu un artefact de fraicheur, et
rendu INCONNU une fois la garde posee.

Un NIVEAU ne se tire pas de la bande. Il se lit au carnet, et pour le lire
dans le passe il faut l'avoir enregistre. C'est tout ce que fait ce module.

CE QU'IL ENREGISTRE, ET POURQUOI CES CHAMPS-LA.

  - `bid` / `ask` : le mid et le spread REELS a cet instant, donc le
    demi-spread qu'une cotation au toucher encaisse.
  - `bid_sz` / `ask_sz` : la taille AU TOUCHER, qui est la file devant un
    ordre qui arrive. `subsidy.tape.RestingQuote` l'attend sous le nom
    `queue_ahead` : sans elle, la simulation de remplissage doit supposer la
    file, et c'est exactement l'hypothese qui rend un resultat positif sans
    valeur.

CE QU'IL N'ENREGISTRE PAS, ET QUI EST UNE LIMITE REELLE. Un sondage
periodique n'est pas un flux d'evenements : entre deux instantanes, le carnet
a pu bouger et revenir. Le markout mesure sur ces instantanes est donc une
derive AUX INSTANTS SONDES, pas la trajectoire du mid. Sur les horizons vises
(60 s et plus) l'ecart est acceptable ; sous la cadence de sondage il n'y a
rien a mesurer, et le replay refuse de le faire.

L'INTERRUPTION NE CORROMPT RIEN. Chaque instantane est une ligne JSON close,
ecrite et videe immediatement. Un collecteur tue laisse un fichier lisible
jusqu'a sa derniere ligne complete. Aucun etat n'est garde en memoire.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.backpack.venue import API, _get, fetch_perp_markets

#: Cadence de sondage. Choisie, pas mesuree : elle borne par le bas tout
#: horizon de markout exploitable, et elle est declaree ici pour cette raison.
DEFAULT_POLL_S = 5.0

#: Marge de securite entre deux requetes, pour ne pas marteler la venue.
INTER_REQUEST_SLEEP_S = 0.05


def snapshot(symbol: str) -> Optional[dict]:
    """Un instantane du toucher. None si un cote manque ou si le carnet croise.

    Rendre un instantane incomplet serait pire que n'en rendre aucun : il
    entrerait dans une serie de mids en s'y faisant passer pour une mesure.
    """
    d = _get(f"{API}/depth?symbol={symbol}")
    bids, asks = d.get("bids") or [], d.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(bids, key=lambda b: float(b[0]))
    best_ask = min(asks, key=lambda a: float(a[0]))
    bid_px, ask_px = float(best_bid[0]), float(best_ask[0])
    if bid_px >= ask_px:
        return None
    return {"ts": time.time(), "sym": symbol,
            "bid": bid_px, "ask": ask_px,
            "bid_sz": float(best_bid[1]), "ask_sz": float(best_ask[1])}


def collect(symbols: Sequence[str], out_path: Path, duration_s: float,
            poll_s: float = DEFAULT_POLL_S) -> int:
    """Sonde `symbols` pendant `duration_s`. Rend le nombre d'instantanes ecrits.

    Une erreur reseau sur un symbole n'interrompt pas la collecte des autres :
    l'instantane manquant est simplement absent, jamais remplace par le
    precedent — repeter un carnet perime fabriquerait un markout nul.
    """
    deadline = time.time() + duration_s
    written = 0
    with out_path.open("a") as fh:
        while time.time() < deadline:
            cycle_start = time.time()
            for sym in symbols:
                try:
                    snap = snapshot(sym)
                except Exception:
                    snap = None
                if snap is not None:
                    fh.write(json.dumps(snap) + "\n")
                    written += 1
                fh.flush()
                time.sleep(INTER_REQUEST_SLEEP_S)
            elapsed = time.time() - cycle_start
            if elapsed < poll_s:
                time.sleep(poll_s - elapsed)
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True,
                   help="fichier JSONL d'instantanes (ajout)")
    p.add_argument("--minutes", type=float, default=60.0)
    p.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S)
    p.add_argument("--markets", type=int, default=12,
                   help="nombre de marches les plus MINCES a suivre")
    p.add_argument("--symbols", nargs="*", default=None,
                   help="symboles explicites ; sinon derives du volume")
    a = p.parse_args(argv)

    if a.symbols:
        symbols: List[str] = list(a.symbols)
    else:
        symbols = [m.symbol for m in fetch_perp_markets()[:a.markets]]

    a.out.parent.mkdir(parents=True, exist_ok=True)
    print(f"collecte de {len(symbols)} marches pendant {a.minutes:.0f} min "
          f"a {a.poll_s:.0f} s -> {a.out}", flush=True)
    for s in symbols:
        print(f"  {s}", flush=True)
    n = collect(symbols, a.out, a.minutes * 60.0, a.poll_s)
    print(f"instantanes ecrits : {n}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
