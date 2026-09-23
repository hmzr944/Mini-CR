"""FALSIFICATION de SKHYNIX avant toute exploitation.

Le funding moyen ressort a 16,55 bps/jour, soit 604 %/an. Encaisser ce flux
demande d'etre COURT le perpetuel. La question n'est donc pas « le funding
est-il grand » mais :

    qu'est-il arrive au PRIX pendant qu'on encaissait ce funding ?

Un perpetuel dont le funding est massivement positif est un perpetuel qui cote
au-dessus de son sous-jacent. Si rien ne le ramene, le vendeur gagne le funding
et perd sur le prix. C'est exactement ainsi que la non-arbitrage epingle un
flux : par le prix, pas par le taux.

Quatre verifications, dans cet ordre :
  1. le funding est-il PERSISTANT ou concentre sur quelques jours ? (lecon KAITO)
  2. quel a ete le PnL de prix d'une position courte sur la meme fenetre ?
  3. le total funding + prix est-il positif ?
  4. une couverture par un perpetuel correle change-t-elle le resultat ?
"""
import datetime as dt, json, math, os as _os, statistics as st
from prism_v2.scans import scan_dir as _scan_dir
_SCAN = _scan_dir()
_os.makedirs(_SCAN, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE

NAMES = ["SKHYNIX", "SAMSUNG", "MU", "SOXL", "NVDA", "UNITREE", "BZ", "CL"]


def candles(inst, pages=25):
    rows, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after:
            u += f"&after={after}"
        try:
            d = _http_json(u).get("data") or []
        except Exception:
            break
        if not d:
            break
        for r in d:
            try:
                rows[int(r[0])] = float(r[4])
            except (ValueError, IndexError):
                continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after:
            break
        after = nb
    return rows


def funding(inst, pages=10):
    out, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            u += f"&after={after}"
        try:
            d = _http_json(u).get("data") or []
        except Exception:
            break
        if not d:
            break
        for r in d:
            try:
                out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
        nb = min(int(r["fundingTime"]) for r in d)
        if after and nb >= after:
            break
        after = nb
        if len(d) < 100:
            break
    return out


def d8(ms):
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")


print(f"{'actif':<10}{'periodes':>9}{'jours':>7}{'funding cumule':>16}"
      f"{'% jours positifs':>18}{'prix debut':>12}{'prix fin':>11}{'var prix %':>12}")
print("-" * 95)
DATA = {}
for nm in NAMES:
    iid = f"{nm}-USDT-SWAP"
    f, c = funding(iid), candles(iid)
    if len(f) < 60 or len(c) < 200:
        print(f"{nm:<10}  donnees insuffisantes (f={len(f)}, c={len(c)})")
        continue
    ts = sorted(f)
    per = {}
    for a, b in zip(ts, ts[1:]):
        ph = (b - a) / 3_600_000
        if 0.5 <= ph <= 12.0:
            per[b] = ph
    if not per:
        continue
    # funding CUMULE encaisse par un SHORT : + somme des taux payes
    cum = sum(f[t] for t in per) * 10_000.0
    pos = sum(1 for t in per if f[t] > 0) / len(per)
    ct = sorted(c)
    p0, p1 = c[ct[0]], c[ct[-1]]
    DATA[nm] = {"f": f, "per": per, "c": c}
    print(f"{nm:<10}{len(per):>9}{(max(per)-min(per))/86_400_000:>7.0f}"
          f"{cum:>15.0f}b{pos:>17.0%}{p0:>12,.2f}{p1:>11,.2f}"
          f"{(p1/p0-1)*100:>11.1f}%")

print("\n\nPnL COMPLET D'UNE POSITION COURTE : funding encaisse + PnL de prix")
print("(notionnel constant, 1 000 USD ; strictement causal : on porte, on n'anticipe rien)\n")
print(f"{'actif':<10}{'funding USD':>13}{'prix USD':>11}{'TOTAL USD':>12}"
      f"{'bps/jour net':>14}")
print("-" * 62)
N = 1000.0
for nm, D in DATA.items():
    per, f, c = D["per"], D["f"], D["c"]
    ct = sorted(c)
    lo, hi = min(per), max(per)
    inside = [t for t in ct if lo <= t <= hi]
    if len(inside) < 50:
        continue
    fund_usd = sum(f[t] for t in per) * N
    px_usd = -(c[inside[-1]] / c[inside[0]] - 1.0) * N     # short : perd si ca monte
    days = (hi - lo) / 86_400_000
    tot = fund_usd + px_usd
    print(f"{nm:<10}{fund_usd:>13.2f}{px_usd:>11.2f}{tot:>12.2f}"
          f"{tot / N * 10_000 / days:>14.2f}")
json.dump({k: {"per": {str(a): b for a, b in v["per"].items()}} for k, v in DATA.items()},
          open(_os.path.join(_SCAN, 'skhynix_meta.json'),'w'))
