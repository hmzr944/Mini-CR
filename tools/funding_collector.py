#!/usr/bin/env python3
"""Collecteur funding rates — construit le dataset du futur edge V34 carry.
OKX ne conserve que ~3 mois d'historique : on enregistre nous-mêmes, chaque
heure, le funding courant + prochain des 26 perps. Après 4-6 semaines, ce
dataset permettra de tester le premier edge orthogonal réellement neuf
(carry/contrarian funding) — impossible aujourd'hui faute de données.
Usage : tâche horaire (append CSV, ~30 requêtes légères)."""
import sys, csv, time
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import requests
import backtest_v33 as bt

OUT = ROOT / "backtest_data" / "funding_log.csv"
OUT.parent.mkdir(exist_ok=True)
rows = []
for sym in bt.SYMBOLS:
    inst = f"{sym}-SWAP"
    try:
        r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
                         params={"instId": inst}, timeout=10).json()
        d = (r.get("data") or [{}])[0]
        rows.append([datetime.utcnow().isoformat(), sym,
                     d.get("fundingRate", ""), d.get("nextFundingRate", ""),
                     d.get("fundingTime", "")])
    except Exception:
        pass
    time.sleep(0.15)
new = not OUT.exists()
with open(OUT, "a", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    if new:
        w.writerow(["ts_utc", "sym", "funding_rate", "next_funding_rate", "funding_time"])
    w.writerows(rows)
print(f"{len(rows)}/26 funding enregistrés -> {OUT.name}")
