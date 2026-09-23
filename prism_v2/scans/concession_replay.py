"""Qui recoit la valeur quand quelqu'un DOIT agir ?

LA FORME FERMEE. Cinq representations ont echoue, toutes de structure
identique : une valeur est PUBLIEE comme un taux, on immobilise du capital, on
attend. Ce n'est pas la persistance qui manquait, c'est l'unite economique.

CE QUI CHANGE ICI. L'observable n'est plus un taux affiche mais une CONTRAINTE
revelee : un ordre qui traverse plusieurs niveaux de carnet revele un agresseur
insensible au prix. Il paie une CONCESSION pour le droit d'agir maintenant, et
cette concession est recue par le cote passif.

POURQUOI CE N'EST PAS MON ETUDE MAKER. Celle-ci mesurait les remplissages AU
TOUCH, moyennes sur tous les trades : -3,34 bps, selection adverse > demi-
spread sur 12/13 instruments. Elle n'a jamais regarde les niveaux 2..N. Le
touch est l'endroit ou l'INFORMATION vous frappe ; la profondeur est l'endroit
ou l'URGENCE vous frappe. Ce sont deux economies differentes.

CE QUI EST MESURE, ET RIEN D'AUTRE :
  concession  = VWAP de la rafale - touch AVANT la rafale
  recuperation = mid(t+d) - VWAP, du point de vue du cote PASSIF
  net         = recuperation - frais maker
La concession seule n'est jamais presentee comme un gain : elle n'est acquise
que si le prix revient. Sinon elle etait le prix juste de l'information.

GARDE-FOUS. Carnet reconstruit par le moteur incrementiel (chainage
prevSeqId -> seqId, fail closed). Etat du carnet lu STRICTEMENT AVANT la
rafale. Horodatages exchange. Rafales agregees par (instrument, ts) : un ordre
agressif produit plusieurs impressions.
"""
import glob, json, math, os, statistics as st
from bisect import bisect_left, bisect_right
from collections import defaultdict

from prism_v2.l2book import L2Book
from prism_v2.instruments import parse_okx_instrument
from prism_v2.contracts import usd_notional
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
os.makedirs(SCAN, exist_ok=True)
MAKER_BPS = 2.0
HORIZONS_MS = [1_000, 5_000, 30_000, 300_000]

RAW = json.load(open(f"{SCAN}/insts.json")) if os.path.exists(f"{SCAN}/insts.json") else None
if RAW is None:
    from prism_v2.funding_feed import _http_json, OKX_BASE
    RAW = {}
    for k in ("SWAP", "SPOT"):
        for x in (_http_json(f"{OKX_BASE}/api/v5/public/instruments?instType={k}").get("data") or []):
            RAW[x["instId"]] = x
    json.dump(RAW, open(f"{SCAN}/insts.json", "w"))

books, specs, skip = {}, {}, set()
quotes = defaultdict(list)     # inst -> (ts, bid, ask)
bursts = defaultdict(list)     # inst -> (ts, side, vwap, usd, niveaux, touch, mid)
pending = {}                   # (inst, ts) -> agregat de la rafale en cours
n_book = n_trade = 0


def flush(key):
    """Cloture une rafale et l'enregistre avec l'etat du carnet ANTERIEUR."""
    b = pending.pop(key, None)
    if b is None or b["usd"] <= 0:
        return
    inst, ts = key
    vwap = b["pv"] / b["sz"]
    # Concession lue depuis le CARNET (depend de l'ordonnancement carnet/trade)
    conc = ((vwap - b["touch"]) / b["mid"] * 10_000.0 if b["side"] == "buy"
            else (b["touch"] - vwap) / b["mid"] * 10_000.0)
    # Concession lue depuis les IMPRESSIONS SEULES : un acheteur remonte le
    # carnet, donc sa premiere impression est la moins chere. Cette mesure ne
    # depend d'aucun ordonnancement entre les canaux `books` et `trades`, et
    # c'est elle qui tranche si les deux divergent.
    first = b["pmin"] if b["side"] == "buy" else b["pmax"]
    conc_p = ((vwap - first) / b["mid"] * 10_000.0 if b["side"] == "buy"
              else (first - vwap) / b["mid"] * 10_000.0)
    span = (b["pmax"] - b["pmin"]) / b["mid"] * 10_000.0
    bursts[inst].append((ts, b["side"], vwap, b["usd"], b["niv"], conc,
                         b["mid"], conc_p, span, b["n"]))


for f in sorted(glob.glob('prism_v2/data/events/*.jsonl')):
    for line in open(f):
        if '"books"' not in line and '"trades"' not in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        inst, ch = r.get("inst_id"), r.get("channel")
        if ch not in ("books", "trades"):
            continue
        sp = specs.get(inst)
        if sp is None:
            if inst in skip:
                continue
            sp = parse_okx_instrument(RAW[inst]) if inst in RAW else None
            if sp is None:
                skip.add(inst); continue
            specs[inst] = sp
        d = r.get("data") or {}
        if isinstance(d, list):
            d = d[0] if d else {}
        if ch == "trades":
            try:
                ts = int(d["ts"]); px = float(d["px"]); sz = float(d["sz"])
                side = d["side"]
            except (KeyError, ValueError, TypeError):
                continue
            bk = books.get(inst)
            ob = bk.book() if bk is not None else None
            if ob is None:
                continue
            key = (inst, ts)
            b = pending.get(key)
            if b is None:
                # etat du carnet STRICTEMENT AVANT la rafale
                touch = ob.asks[0].price if side == "buy" else ob.bids[0].price
                mid = (ob.bids[0].price + ob.asks[0].price) / 2.0
                lv = ob.asks if side == "buy" else ob.bids
                b = pending[key] = {"side": side, "pv": 0.0, "sz": 0.0,
                                    "usd": 0.0, "touch": touch, "mid": mid,
                                    "lv": lv, "niv": 0, "n": 0,
                                    "pmin": px, "pmax": px}
            if b["side"] != side:
                continue
            b["pv"] += px * sz; b["sz"] += sz; b["n"] += 1
            if px < b["pmin"]: b["pmin"] = px
            if px > b["pmax"]: b["pmax"] = px
            b["usd"] += usd_notional(sp, sz, px)
            # combien de niveaux le prix atteint a-t-il traverse ?
            n = 0
            for L in b["lv"]:
                n += 1
                if (side == "buy" and L.price >= px) or (side == "sell" and L.price <= px):
                    break
            b["niv"] = max(b["niv"], n)
            n_trade += 1
            continue
        bk = books.get(inst)
        if bk is None:
            bk = books[inst] = L2Book(sp)
        action = r.get("action")
        if action is None:
            try:
                action = "snapshot" if int(d.get("prevSeqId", -1)) == -1 else "update"
            except (TypeError, ValueError):
                action = None
        if not bk.apply(action, d, r.get("local_recv_ts_ms")):
            continue
        n_book += 1
        ob = bk.book()
        if ob is None:
            continue
        quotes[inst].append((ob.ts_ms, ob.bids[0].price, ob.asks[0].price))
        # une mise a jour de carnet cloture toute rafale plus ancienne
        for key in [k for k in pending if k[0] == inst and k[1] < ob.ts_ms]:
            flush(key)

for key in list(pending):
    flush(key)
for i in quotes:
    quotes[i].sort(key=lambda x: x[0])
for i in bursts:
    bursts[i].sort(key=lambda x: x[0])

print(f"carnets appliques {n_book:,} | impressions {n_trade:,} | "
      f"rafales {sum(len(v) for v in bursts.values()):,} "
      f"sur {len(bursts)} instruments")
json.dump({"n_book": n_book, "n_trade": n_trade,
           "bursts": {k: len(v) for k, v in bursts.items()}},
          open(f"{SCAN}/concession2_meta.json", "w"))
import pickle
pickle.dump({"quotes": dict(quotes), "bursts": dict(bursts)},
            open(f"{SCAN}/concession2.pkl", "wb"))
print(f"ecrit : {SCAN}/concession2.pkl")
