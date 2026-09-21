#!/usr/bin/env python3
"""Collecte FORWARD du touch et du flux agresseur, pour mesurer le remplissage
passif et la selection adverse -- l'etape 2 de OBJECTIF.md.

CE QUE CETTE MESURE DECIDE. Le carry est NEGATIF en taker a court horizon et
POSITIF en maker. Le signe du livre entier depend donc d'une seule quantite
jamais mesuree : la probabilite qu'un ordre pose au touch soit rempli, et ce
que le prix fait APRES ce remplissage.

RAPPORT_FINAL.md le dit deja : « la probabilite de remplissage passif est
INCONNUE -- PRISM n'a pas de modele de file d'attente. Toute economie maker
produite ici est donc une BORNE SUPERIEURE, file supposee gagnee a chaque
transaction. » Ce script sert a remplacer cette borne par une mesure.

CE QU'IL ENREGISTRE, toutes les INTERVAL_S secondes pendant WINDOW_MIN minutes :
  - le meilleur bid/ask et les tailles au touch (l'etat de la file DEVANT nous) ;
  - le flux agresseur arrive depuis le releve precedent, par cote et par prix
    (ce qui aurait consomme la file, donc nous aurait remplis) ;
  - le mid, pour calculer le markout a posteriori (la selection adverse).

Un passage quotidien echantillonne une heure differente a chaque fois ; sur
plusieurs semaines on couvre la journee sans jamais tenir de connexion longue.

Il ne passe aucun ordre et ne lit aucune cle.

    python3 tools/collect_touch_forward.py [--minutes N] [--commit]
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

OKX = "https://www.okx.com"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "prism_v2" / "data" / "touch_forward"

#: Les jambes du livre candidat. Volontairement court : mesurer peu et bien.
INSTRUMENTS = ["ADA-USD-SWAP", "ADA-USDT-SWAP",
               "BCH-USD-SWAP", "BCH-USDT-SWAP",
               "ETC-USD-SWAP", "ETC-USDT-SWAP",
               "SOL-USD-SWAP", "SOL-USDT-SWAP"]
INTERVAL_S = 3.0
WINDOW_MIN = 8


def _get(path: str, **params):
    for _ in range(3):
        try:
            return requests.get(f"{OKX}{path}", params=params, timeout=12).json()
        except Exception:
            time.sleep(1)
    return {}


def snapshot(inst: str, since_trade_id: str | None):
    """(état du touch, trades agresseurs depuis le dernier relevé, dernier id)."""
    b = (_get("/api/v5/market/books", instId=inst, sz=1).get("data") or [{}])[0]
    if not b.get("bids") or not b.get("asks"):
        return None, [], since_trade_id
    bid, bid_sz = float(b["bids"][0][0]), float(b["bids"][0][1])
    ask, ask_sz = float(b["asks"][0][0]), float(b["asks"][0][1])

    t = _get("/api/v5/market/trades", instId=inst, limit=100).get("data") or []
    fresh, newest = [], since_trade_id
    for r in t:                      # OKX rend du plus recent au plus ancien
        tid = r.get("tradeId")
        if since_trade_id is not None and tid == since_trade_id:
            break
        fresh.append([int(r["ts"]), float(r["px"]), float(r["sz"]), r["side"]])
    if t:
        newest = t[0].get("tradeId")
    return {"bid": bid, "bidSz": bid_sz, "ask": ask, "askSz": ask_sz}, fresh, newest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=WINDOW_MIN)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    path = OUT / f"{stamp}.jsonl"
    last: dict[str, str | None] = {i: None for i in INSTRUMENTS}
    deadline = time.time() + args.minutes * 60
    rows = 0

    with path.open("w") as fh:
        while time.time() < deadline:
            ts = int(time.time() * 1000)
            for inst in INSTRUMENTS:
                book, trades, last[inst] = snapshot(inst, last[inst])
                if book is None:
                    continue
                fh.write(json.dumps({"ts": ts, "i": inst, **book,
                                     "agg": trades}, separators=(",", ":")) + "\n")
                rows += 1
            fh.flush()
            time.sleep(INTERVAL_S)

    print(f"{rows} relevés sur {args.minutes:.0f} min, {len(INSTRUMENTS)} jambes "
          f"-> {path.relative_to(ROOT)}")
    print("Ce fichier ne dit encore rien : il faut plusieurs semaines de fenêtres")
    print("à des heures différentes avant que le taux de remplissage soit estimable.")

    if not args.commit or rows == 0:
        return 0
    subprocess.run(["git", "add", str(OUT)], cwd=ROOT, check=True)
    msg = (f"Collecte forward du touch : fenêtre {stamp}, {rows} relevés\n\n"
           "Généré par tools/collect_touch_forward.py. Sert à mesurer la\n"
           "probabilité de remplissage passif et la sélection adverse, seule\n"
           "inconnue qui décide du signe du carry (étape 2 de OBJECTIF.md).\n")
    r = subprocess.run(["git", "commit", "-m", msg], cwd=ROOT,
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
