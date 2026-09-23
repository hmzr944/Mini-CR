"""Panneau SYNCHRONE de 15 perpetuels inverses, cadence 250 ms, 6,5 heures.

Pourquoi cela change quelque chose. Toutes les mesures precedentes du projet
portaient sur UN instrument a la fois, ou sur des instruments echantillonnes
a des instants differents. L'observatoire, lui, echantillonne quinze
instruments sur la MEME horloge a 250 ms. C'est la seule donnee du depot qui
autorise une mesure transversale : un ecart entre instruments a un instant
donne, et non une prediction dans le temps sur un seul.

Les quinze sont des perpetuels inverses regles en USD. Aucune devise n'est
traversee : un ecart entre deux d'entre eux est un ecart reel, pas un
mouvement de peg.

Ce script ne fait que construire le panneau et le mettre en cache. Les prix
retenus sont le meilleur bid et le meilleur ask de chaque instantane, plus le
flux signe agrege sur l'intervalle (bu = USD achetes a l'agression, su = USD
vendus).
"""
import sys, pickle, statistics as st
from pathlib import Path

import os as _os
#: Repertoire de travail des mesures. Les fichiers intermediaires (panneau,
#: bande) n'ont pas leur place dans le depot : ils se recalculent.
from prism_v2.scans import scan_dir as _scan_dir
SCRATCH = _scan_dir()
_os.makedirs(SCRATCH, exist_ok=True)

from prism_v2.observatory import load_snapshots
from prism_v2.instruments import parse_okx_instrument
import json as _json


RAWI = _json.load(open(f"{SCRATCH}/insts.json"))
CT_VAL = {}
for _i, _r in RAWI.items():
    if _i.endswith("-USD-SWAP"):
        _sp = parse_okx_instrument(_r)
        if _sp is not None and _sp.is_inverse:
            CT_VAL[_i] = _sp.ct_val * _sp.ct_mult

meta, snaps = load_snapshots(Path('prism_v2/data/observatory/session_long.jsonl.gz'))
print(f"instantanes lus : {len(snaps):,}   cadence {meta.get('snapshot_ms')} ms")

by_inst = {}
for s in snaps:
    if not s.get("ok"):
        continue
    b, a = s.get("b") or [], s.get("a") or []
    if not b or not a:
        continue
    bid_px, bid_sz = b[0][0], b[0][1]
    ask_px, ask_sz = a[0][0], a[0][1]
    if not (ask_px > bid_px > 0):
        continue
    t = s.get("t") or {}
    # CORRECTION DES DONNEES DEJA COLLECTEES. Les champs bu/su ont ete ecrits
    # comme px * sz alors que sz compte des CONTRATS de ctVal USD sur ces
    # inverses. On retablit le notionnel reel : usd = bu / px * ctVal.
    # Le prix retenu est le milieu de l'intervalle ; a l'interieur d'une barre
    # de 250 ms l'ecart de prix est de l'ordre du tick, soit une erreur
    # residuelle inferieure au point de base. C'est une reconstitution, pas
    # une mesure : elle est signalee comme telle.
    ct = CT_VAL.get(s["i"])
    px = (bid_px + ask_px) / 2.0
    k = (ct / px) if ct else 1.0
    by_inst.setdefault(s["i"], []).append(
        (s["ts"], bid_px, ask_px, bid_sz, ask_sz,
         t.get("bu", 0.0) * k, t.get("su", 0.0) * k))

for i in by_inst:
    by_inst[i].sort(key=lambda x: x[0])

# Controle de grandeur : le volume reconstitue doit retrouver l'ordre de
# grandeur du volume 24 h publie par OKX pour le meme instrument.
from prism_v2.funding_feed import _http_json, OKX_BASE

REF = {}
for _t in (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or []):
    if _t["instId"].endswith("-USD-SWAP"):
        try:
            REF[_t["instId"]] = float(_t.get("volCcy24h") or 0) * float(_t.get("last") or 0)
        except (TypeError, ValueError):
            pass

print(f"\n{'instrument':<16}{'points':>10}{'span min':>10}{'demi-spread bps':>17}"
      f"{'volume USD/h':>15}{'x vol OKX 24h':>12}")
print("-" * 80)
for i in sorted(by_inst):
    v = by_inst[i]
    hs = sorted((a - b) / (a + b) * 10_000.0 for _, b, a, *_ in v)
    span_h = (v[-1][0] - v[0][0]) / 3_600_000
    vol = sum(x[5] + x[6] for x in v) / max(span_h, 1e-9)
    ref = REF.get(i)
    print(f"{i:<16}{len(v):>10,}{(v[-1][0]-v[0][0])/60000:>10.1f}"
          f"{hs[len(hs)//2]:>17.2f}{vol:>15,.0f}"
          f"{(vol*24/ref if ref else float('nan')):>12.2f}")

pickle.dump(by_inst, open(f"{SCRATCH}/panel.pkl", "wb"))
print(f"\npanneau ecrit : {SCRATCH}/panel.pkl")
