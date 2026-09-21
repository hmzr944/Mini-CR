"""Rafraichit le referentiel d'instruments OKX utilise par les mesures.

Aucune valeur de contrat n'est codee en dur nulle part dans les mesures : elles
lisent toutes ce fichier. ctVal lu de travers a deja coute un facteur 100 sur
la profondeur de carnet et un facteur 752 sur le flux signe.
"""
import json, os

from prism_v2.funding_feed import _http_json, OKX_BASE

from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
os.makedirs(SCRATCH, exist_ok=True)

out = {}
for kind in ("SWAP", "SPOT"):
    for row in (_http_json(
            f"{OKX_BASE}/api/v5/public/instruments?instType={kind}"
    ).get("data") or []):
        out[row["instId"]] = row
with open(f"{SCRATCH}/insts.json", "w") as fh:
    json.dump(out, fh)
print(f"{len(out)} instruments ecrits dans {SCRATCH}/insts.json")
