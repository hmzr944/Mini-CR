"""Un grand ecart ne vaut rien si son SIGNE ne persiste pas.

KAITO montre |differentiel| au-dessus du seuil 18,2 % du temps, et une moyenne
signee de -2,81 bps/jour. Les deux ensemble decrivent un ecart symetrique et
evenementiel, pas un flux : on ne peut pas detenir une position dans les deux
sens a la fois.

Le test economique, strictement causal : on decide sur le differentiel DEJA
PAYE a t, on prend la position qui l'encaisse, on la tient N periodes, on
encaisse ce qui est reellement paye de t a t+N, on paie l'aller-retour une
fois. Le taux de t+1 n'est jamais consulte pour decider d'entrer.
"""
import json, math, os as _os, time, statistics as st, urllib.request
_SCAN_DIR = _os.environ.get('PRISM_SCAN_DIR', '/tmp/prism_scans')
_os.makedirs(_SCAN_DIR, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE
from prism_v2.long_test import t_test_one_sided, benjamini_hochberg
HL = "https://api.hyperliquid.xyz/info"


def hp(b):
    r = urllib.request.Request(HL, data=json.dumps(b).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())


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
            out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
        nb = min(int(r["fundingTime"]) for r in d)
        if after and nb >= after:
            break
        after = nb
        if len(d) < 100:
            break
    return out


def hl_hist(name):
    out, start = {}, int(time.time() * 1000) - 60 * 86_400_000
    for _ in range(30):
        try:
            d = hp({"type": "fundingHistory", "coin": name, "startTime": start})
        except Exception:
            break
        if not d:
            break
        for r in d:
            out[int(r["time"]) // 3_600_000 * 3_600_000] = float(r["fundingRate"])
        nt = max(int(r["time"]) for r in d)
        if nt <= start:
            break
        start = nt + 1
        if len(d) < 500:
            break
    return out


# cout d'aller-retour mesure : demi-spreads reels des deux venues, x2, + frais
def cout(base):
    try:
        t = _http_json(f"{OKX_BASE}/api/v5/market/ticker?instId={base}-USDT-SWAP")["data"][0]
        b, a = float(t["bidPx"]), float(t["askPx"])
        ho = (a - b) / (a + b) * 10_000.0
    except Exception:
        return None
    hh = None
    for u, c in zip(META[0]["universe"], META[1]):
        if u["name"] == base:
            px = c.get("impactPxs")
            if px and len(px) == 2:
                hb, ha = float(px[0]), float(px[1])
                if ha > hb > 0:
                    hh = (ha - hb) / (ha + hb) * 10_000.0
            break
    if hh is None:
        return None
    return 2 * (ho + hh) + 2 * 5.0 + 2 * 4.5     # spreads x2 + taker OKX + taker HL


META = hp({"type": "metaAndAssetCtxs"})
CANDIDATS = ["KAITO", "SOPH", "ZORA", "MOVE", "MINA", "PURR", "SAND", "STABLE", "2Z"]
SEUILS = [20.0, 37.0, 60.0, 100.0]
HOLD = [1, 3, 6, 12]

print(f"{'actif':<8}{'cout A/R':>10}{'seuil':>8}{'tenue':>7}{'entrees':>9}"
      f"{'brut bps':>10}{'net bps':>10}{'t':>7}{'net total':>11}")
print("-" * 80)
pvals, nets = {}, {}
for base in CANDIDATS:
    c = cout(base)
    if c is None:
        continue
    o = okx_hist(f"{base}-USDT-SWAP")
    h = hl_hist(base)
    if len(o) < 80 or len(h) < 200:
        continue
    ts = sorted(o)
    per = {}
    for a, b in zip(ts, ts[1:]):
        ph = (b - a) / 3_600_000
        if 0.5 <= ph <= 12.0:
            per[b] = ph
    seq = []
    for t in sorted(per):
        ph = per[t]
        hs = [h[k] for k in range(int(t - ph * 3_600_000), int(t), 3_600_000) if k in h]
        if len(hs) < max(1, ph * 0.7):
            continue
        # differentiel PAYE sur cette periode, en bps de notionnel (pas par jour)
        seq.append((t, (o[t] - sum(hs) / len(hs) * ph) * 10_000.0))
    if len(seq) < 100:
        continue
    for seuil_j in SEUILS:
        for N in HOLD:
            gains, last = [], -1
            for i, (t, d) in enumerate(seq):
                if i <= last or i + N >= len(seq):
                    continue
                ph = per[t]
                if abs(d) * 24.0 / ph < seuil_j:     # seuil exprime en bps/jour
                    continue
                sgn = 1.0 if d > 0 else -1.0         # on prend le sens qui encaisse
                gains.append(sgn * sum(x[1] for x in seq[i + 1:i + 1 + N]) - c)
                last = i + N
            if len(gains) < 15:
                continue
            m = st.fmean(gains)
            tt, p = t_test_one_sided(gains) if len(gains) >= 30 else (float("nan"), 1.0)
            key = f"{base}@{seuil_j:.0f}/{N}"
            pvals[key] = p
            nets[key] = gains
            print(f"{base:<8}{c:>10.1f}{seuil_j:>8.0f}{N:>7}{len(gains):>9}"
                  f"{m + c:>10.2f}{m:>10.2f}{tt:>7.2f}{sum(gains):>11.1f}")

if pvals:
    surv = benjamini_hochberg(pvals, q=0.10)
    pos = [(k, st.fmean(v)) for k, v in nets.items() if st.fmean(v) > 0]
    print(f"\n{len(pvals)} tests (actif x seuil x tenue), Benjamini-Hochberg q=0,10")
    print(f"cellules a moyenne NETTE positive : {len(pos)}/{len(pvals)}")
    for k, m in sorted(pos, key=lambda x: -x[1]):
        print(f"  {k:<20}{m:>+9.2f} bps/entree   "
              f"{'SURVIT BH' if surv[k] else 'ne survit pas a BH'}")
    if not pos:
        print("  AUCUNE. Le signe du differentiel ne persiste pas assez")
        print("  pour payer l'aller-retour, a aucun seuil ni aucune tenue.")
