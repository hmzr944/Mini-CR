"""Rejeu complet : tous les instruments, carnet reconstruit + bande de transactions.

Produit deux series par instrument, toutes horodatees par l'exchange :
  quotes[inst] = [(ts, bid, ask, bid_usd, ask_usd, depth5_bid_usd, depth5_ask_usd)]
  trades[inst] = [(ts, px, sz_usd, side)]      side = sens de l'AGRESSEUR

Le carnet vient du moteur incrementiel (chainage prevSeqId -> seqId, fail
closed sur trou). Le canal books5 est ecarte : 5 niveaux ne sont pas un
snapshot du canal 400 niveaux.
"""
import json, glob, sys, pickle
from collections import defaultdict

from prism_v2.l2book import L2Book
from prism_v2.instruments import parse_okx_instrument
from prism_v2.orderbook import usd_notional

import os as _os
#: Repertoire de travail des mesures. Les fichiers intermediaires (panneau,
#: bande) n'ont pas leur place dans le depot : ils se recalculent.
SCRATCH = _os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
_os.makedirs(SCRATCH, exist_ok=True)



RAW = json.load(open(f"{SCRATCH}/insts.json"))

books, quotes, trades = {}, defaultdict(list), defaultdict(list)
specs, skipped = {}, set()
n_book = n_trade = 0

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
            if inst in skipped:
                continue
            sp = parse_okx_instrument(RAW[inst]) if inst in RAW else None
            if sp is None:
                skipped.add(inst)
                continue
            specs[inst] = sp
        d = r.get("data") or {}
        if isinstance(d, list):
            d = d[0] if d else {}
        if ch == "trades":
            try:
                px, sz = float(d["px"]), float(d["sz"])
                trades[inst].append((int(d["ts"]), px,
                                     usd_notional(sp, sz, px), d["side"]))
                n_trade += 1
            except (KeyError, ValueError, TypeError):
                pass
            continue
        b = books.get(inst)
        if b is None:
            b = books[inst] = L2Book(sp)
        action = r.get("action")
        if action is None:
            try:
                action = "snapshot" if int(d.get("prevSeqId", -1)) == -1 else "update"
            except (TypeError, ValueError):
                action = None
        if not b.apply(action, d, r.get("local_recv_ts_ms")):
            continue
        n_book += 1
        ob = b.book()
        if ob is None:
            continue
        d5b = sum(l.notional_usd for l in ob.bids[:5])
        d5a = sum(l.notional_usd for l in ob.asks[:5])
        quotes[inst].append((ob.ts_ms, ob.bids[0].price, ob.asks[0].price,
                             ob.bids[0].notional_usd, ob.asks[0].notional_usd,
                             d5b, d5a))

for i in quotes:
    quotes[i].sort(key=lambda x: x[0])
for i in trades:
    trades[i].sort(key=lambda x: x[0])

print(f"carnets appliques {n_book:,} | transactions {n_trade:,}")
if skipped:
    print(f"instruments hors referentiel OKX (delistes ?), exclus : {sorted(skipped)}")
print(f"\n{'instrument':<18}{'cotations':>11}{'trades':>9}{'trous':>7}"
      f"{'demi-spread bps':>17}{'span min':>10}")
print("-" * 72)
for i in sorted(quotes):
    q = quotes[i]
    hs = sorted((a - b) / (a + b) * 10_000.0 for _, b, a, *_ in q)
    print(f"{i:<18}{len(q):>11}{len(trades[i]):>9}"
          f"{books[i].sequence_gaps:>7}{hs[len(hs)//2]:>17.2f}"
          f"{(q[-1][0]-q[0][0])/60000:>10.1f}")
pickle.dump({"quotes": dict(quotes), "trades": dict(trades)},
            open(f"{SCRATCH}/tape.pkl", "wb"))
print(f"\necrit : {SCRATCH}/tape.pkl")
