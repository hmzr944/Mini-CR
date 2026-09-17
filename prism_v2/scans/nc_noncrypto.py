"""Le funding est-il structurellement plus grand la ou rien ne l'arbitre ?

HYPOTHESE A FALSIFIER. Les flux couverts mesures jusqu'ici (carry, basis,
inter-venues, options) atterrissent tous entre 2 et 16 %/an. J'ai conclu que
c'etait la condition de non-arbitrage. Si c'est vrai, alors un instrument que
RIEN n'arbitre devrait echapper a cette borne.

OKX cote trois classes que PRISM n'avait jamais inventoriees :
  - ACTIONS et ETF tokenises (TSLA, NVDA, QQQ, SOXL...) : sous-jacent reel,
    mais marche ferme 17,5 h sur 24 et tout le week-end ;
  - MATIERES PREMIERES (XAU, XAG, CL, BZ) : sous-jacent reel, marche ouvert ;
  - PRE-IPO / prive (SPCX, ZHIPU, UNITREE, LITE) : AUCUN sous-jacent
    negociable. Ni spot, ni emprunt, ni livraison. Rien ne peut l'arbitrer.

La cadence est lue PERIODE PAR PERIODE : OKX la change pendant le stress, et
c'est exactement la que les taux sont extremes.
"""
import json, os as _os, time, statistics as st
import urllib.request

_SCAN = _os.environ.get('PRISM_SCAN_DIR', '/tmp/prism_scans')
_os.makedirs(_SCAN, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE

CLASSES = {
    "action":  ["TSLA", "NVDA", "INTC", "MU", "MSTR", "QQQ", "SOXL", "MRVL",
                "CRCL", "NBIS", "AAOI", "CRWV", "SNDK", "SAMSUNG", "SKHYNIX"],
    "matiere":  ["XAU", "XAG", "CL", "BZ"],
    "pre-IPO":  ["SPCX", "ZHIPU", "UNITREE", "LITE", "SNXX", "KORU", "SKHY"],
    "crypto":   ["BTC", "ETH", "SOL", "XRP", "DOGE"],      # temoin
}


def hist(inst, pages=10):
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


tick = {t["instId"]: t for t in
        (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or [])}

print(f"{'classe':<10}{'actif':<10}{'n':>6}{'jours':>7}{'moy bps/j':>12}"
      f"{'|moy|':>9}{'p90 |d|':>10}{'max |d|':>10}{'demi-spr':>10}{'vol 24h USD':>15}")
print("-" * 99)
res = []
for cls, bases in CLASSES.items():
    for b in bases:
        iid = f"{b}-USDT-SWAP"
        h = hist(iid)
        if len(h) < 60:
            continue
        ts = sorted(h)
        per = {}
        for a, c in zip(ts, ts[1:]):
            ph = (c - a) / 3_600_000
            if 0.5 <= ph <= 12.0:
                per[c] = ph
        if len(per) < 60:
            continue
        daily = [h[t] * 24.0 / ph * 10_000.0 for t, ph in per.items()]
        days = (max(per) - min(per)) / 86_400_000
        ab = sorted(abs(x) for x in daily)
        n = len(ab)
        t = tick.get(iid)
        try:
            bid_px, ask_px = float(t["bidPx"]), float(t["askPx"])
            hs = (ask_px - bid_px) / (ask_px + bid_px) * 10_000.0
            vol = float(t.get("volCcy24h") or 0) * float(t.get("last") or 0)
        except (TypeError, ValueError, KeyError):
            hs, vol = float("nan"), 0.0
        m = st.fmean(daily)
        res.append((cls, b, n, days, m, ab[int(.9*n)], ab[-1], hs, vol))
        print(f"{cls:<10}{b:<10}{n:>6}{days:>7.0f}{m:>12.2f}{abs(m):>9.2f}"
              f"{ab[int(.9*n)]:>10.1f}{ab[-1]:>10.1f}{hs:>10.2f}{vol:>15,.0f}")

print()
for cls in CLASSES:
    v = [r for r in res if r[0] == cls]
    if not v:
        continue
    print(f"{cls:<10} n={len(v):>2}  |moyenne| mediane {st.median([abs(r[4]) for r in v]):>8.2f} bps/j"
          f"   p90 median {st.median([r[5] for r in v]):>8.1f}"
          f"   demi-spread median {st.median([r[7] for r in v if r[7]==r[7]]):>6.2f} bps")
print(f"\nseuil d'admission a la nouvelle cible : ~84 bps/jour de notionnel")
json.dump([list(r) for r in res], open(_os.path.join(_SCAN, 'noncrypto.json'),'w'))
