#!/usr/bin/env python3
"""Collecte FORWARD du funding inverse/lineaire, et la commite.

POURQUOI CE SCRIPT EXISTE. L'API OKX ne rend que ~286 releves de funding par
instrument, soit 95 jours, et ce plafond est le meme pour tous les instruments
(verifie : 15 paires x 2 jambes, toutes s'arretent a 286). A 30 jours de
detention, 95 jours d'historique ne valent que 3,2 fenetres independantes : le
carry ne PEUT PAS etre valide hors echantillon avec les donnees disponibles.

La seule facon d'en obtenir est de collecter vers l'avant. Chaque jour non
collecte est un jour de validation perdu DEFINITIVEMENT -- l'API ne le rendra
jamais. C'est la seule action du projet dont le cout d'omission croit avec le
temps.

CE QU'IL FAIT. Il lit le funding des 15 paires, fusionne avec ce qui est deja
stocke (idempotent : un releve deja present n'est pas duplique), et ecrit un
JSONL par paire dans prism_v2/data/funding_forward/. Ces fichiers sont
VERSIONNES -- c'est le seul endroit ou la donnee survit a la destruction d'un
conteneur.

Il ne passe aucun ordre et ne lit aucune cle.

    python3 tools/collect_funding_forward.py [--commit]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

OKX = "https://www.okx.com"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "prism_v2" / "data" / "funding_forward"


def universe() -> list[str]:
    d = requests.get(f"{OKX}/api/v5/public/instruments",
                     params={"instType": "SWAP"}, timeout=25).json().get("data") or []
    live = [i["instId"] for i in d if i.get("state") == "live"]
    inv = {i.split("-")[0] for i in live if i.endswith("-USD-SWAP")}
    lin = {i.split("-")[0] for i in live if i.endswith("-USDT-SWAP")}
    return sorted(inv & lin)


def fetch(inst: str, pages: int = 30) -> dict[int, float]:
    out, after = {}, None
    for _ in range(pages):
        p = {"instId": inst, "limit": 100}
        if after:
            p["after"] = after
        try:
            d = requests.get(f"{OKX}/api/v5/public/funding-rate-history",
                             params=p, timeout=20).json().get("data") or []
        except Exception:
            time.sleep(2); continue
        if not d:
            break
        for r in d:
            out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
        after = d[-1]["fundingTime"]
        if len(d) < 100:
            break
        time.sleep(0.12)
    return out


def merge(inst: str, fresh: dict[int, float]) -> tuple[int, int]:
    """Fusionne dans le JSONL de l'instrument. Rend (deja_connus, nouveaux)."""
    path = OUT / f"{inst}.jsonl"
    known: dict[int, float] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                ts, rate = json.loads(line)
                known[int(ts)] = float(rate)
            except Exception:
                continue
    before = len(known)
    known.update(fresh)
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"[{ts},{known[ts]!r}]\n" for ts in sorted(known)))
    return before, len(known) - before


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="commite et pousse les releves nouveaux")
    args = ap.parse_args()

    coins = universe()
    print(f"{len(coins)} paires inverse/lineaire : {coins}")
    total_new = 0
    for c in coins:
        for suf in ("USD-SWAP", "USDT-SWAP"):
            inst = f"{c}-{suf}"
            fresh = fetch(inst)
            if not fresh:
                print(f"  {inst:18s} AUCUNE DONNEE"); continue
            before, new = merge(inst, fresh)
            total_new += new
            print(f"  {inst:18s} {before:5d} connus  +{new:4d} nouveaux")
    print(f"\n{total_new} releves nouveaux.")

    if not args.commit:
        return 0
    if total_new == 0:
        print("rien de neuf — pas de commit.")
        return 0
    subprocess.run(["git", "add", str(OUT)], cwd=ROOT, check=True)
    msg = (f"Collecte forward du funding : +{total_new} releves\n\n"
           "Genere par tools/collect_funding_forward.py. L'API OKX ne rend que\n"
           "95 jours ; ces releves sont la seule facon d'obtenir un hors\n"
           "echantillon sur le carry, et un jour non collecte est perdu.\n")
    r = subprocess.run(["git", "commit", "-m", msg], cwd=ROOT,
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
