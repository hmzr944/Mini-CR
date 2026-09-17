"""Crible inter-venues, avec la cadence lue PERIODE PAR PERIODE.

CORRECTION. La premiere version prenait la cadence mediane de l'historique
d'un instrument et l'appliquait a toutes ses periodes. Or OKX CHANGE la
cadence d'un instrument pendant les episodes de stress : KAITO est passe de
4 h a 1 h du 8 au 13 aout 2026. Annualiser un taux horaire comme s'il etait
quadri-horaire le multiplie par quatre — et c'est precisement pendant ces
episodes que les taux sont extremes, donc l'erreur frappe la ou elle compte.

Le projet avait deja paye cette classe d'erreur (8 h suppose partout). La
lecon n'avait ete qu'a moitie apprise : la cadence n'est pas constante entre
instruments, et elle n'est pas constante NON PLUS dans le temps pour un meme
instrument.

Ici chaque periode porte sa propre duree, mesuree sur l'ecart avec la periode
precedente.
"""
import json, math, os as _os, time, statistics as st, urllib.request
_SCAN_DIR = _os.environ.get('PRISM_SCAN_DIR', '/tmp/prism_scans')
_os.makedirs(_SCAN_DIR, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE
HL = "https://api.hyperliquid.xyz/info"


def hp(b):
    r = urllib.request.Request(HL, data=json.dumps(b).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


meta = hp({"type": "metaAndAssetCtxs"})
hl_names = [u["name"] for u in meta[0]["universe"] if not u.get("isDelisted")]
okx = {r["instId"].split("-")[0] for r in
       (_http_json(f"{OKX_BASE}/api/v5/public/instruments?instType=SWAP").get("data") or [])
       if r["instId"].endswith("-USDT-SWAP")}
common = sorted(set(hl_names) & okx)
START = int(time.time() * 1000) - 60 * 86_400_000
SEUIL = 37.0


def okx_hist(inst):
    out, after = {}, None
    for _ in range(8):
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


def hl_hist(name):
    out, start = {}, START
    for _ in range(30):
        try:
            d = hp({"type": "fundingHistory", "coin": name, "startTime": start})
        except Exception:
            break
        if not d:
            break
        for r in d:
            try:
                out[int(r["time"]) // 3_600_000 * 3_600_000] = float(r["fundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
        nt = max(int(r["time"]) for r in d)
        if nt <= start:
            break
        start = nt + 1
        if len(d) < 500:
            break
    return out


res, changed = [], []
for i, base in enumerate(common):
    o = okx_hist(f"{base}-USDT-SWAP")
    if len(o) < 80:
        continue
    h = hl_hist(base)
    if len(h) < 200:
        continue
    ts = sorted(o)
    pers = {}
    for a, b in zip(ts, ts[1:]):
        ph = (b - a) / 3_600_000
        if 0.5 <= ph <= 12.0:
            pers[b] = ph
    if not pers:
        continue
    uniq = sorted({round(v, 1) for v in pers.values()})
    if len(uniq) > 1:
        changed.append((base, uniq))
    diffs = []
    for t, ph in pers.items():
        hs = [h[k] for k in range(int(t - ph * 3_600_000), int(t), 3_600_000) if k in h]
        if len(hs) < max(1, ph * 0.7):
            continue
        diffs.append((o[t] * 24.0 / ph - sum(hs) / len(hs) * 24.0) * 10_000.0)
    if len(diffs) < 60:
        continue
    ab = sorted(abs(d) for d in diffs)
    n = len(ab)
    res.append((base, n, st.fmean(diffs), ab[n // 2], ab[int(.9 * n)], ab[-1],
                sum(1 for d in ab if d > SEUIL) / n))
    if (i + 1) % 40 == 0:
        print(f"  ...{i+1}/{len(common)}", flush=True)

res.sort(key=lambda r: -r[4])
print(f"\n{len(res)} actifs exploitables")
print(f"instruments dont la cadence OKX a CHANGE dans la fenetre : {len(changed)}")
for b, u in changed[:12]:
    print(f"    {b:<10} cadences vues : {u} h")
print(f"\n{'actif':<10}{'n':>6}{'diff moy':>11}{'|d| med':>10}{'|d| p90':>10}"
      f"{'|d| max':>10}{'% > 37':>9}")
print("-" * 66)
for r in res[:15]:
    print(f"{r[0]:<10}{r[1]:>6}{r[2]:>11.2f}{r[3]:>10.2f}{r[4]:>10.2f}"
          f"{r[5]:>10.2f}{r[6]:>8.1%}")
print(f"\nseuil : {SEUIL} bps/jour de notionnel")
print(f"p90 au-dessus du seuil : {sum(1 for r in res if r[4] > SEUIL)}/{len(res)}")
print(f"mediane des p90        : {st.median([r[4] for r in res]):.2f} bps/jour")
json.dump([list(r) for r in res], open(_os.path.join(_SCAN_DIR, 'xvenue_funding.json'), 'w'))
