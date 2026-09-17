"""Existe-t-il un flux assez GRAND, sur une paire couvrable sans emprunt ?

Le critere vient du calcul, pas d'un gout : pour atteindre 63,28 bps/jour de
capital a une detention d'UN jour, avec le levier que le coussin de survie
laisse (environ 6x sur une paire de perpetuels), il faut

    r - c > 63,28 / 6 ~ 10,5 bps/jour      soit   r > ~37 bps/jour de notionnel

Ce balayage cherche donc, sur TOUS les sous-jacents qu'OKX cote a la fois en
inverse et en lineaire, le differentiel de funding entre les deux jambes.

Pourquoi cette paire et pas spot/perp : couvrir avec du spot exige d'emprunter
le coin pour le vendre a decouvert, et la mesure precedente a montre que les
actifs a funding extreme sont precisement les non-empruntables. Inverse contre
lineaire ne demande AUCUN emprunt : deux perpetuels, deux marges.
"""
import json, math, os as _os, time, statistics as st, urllib.request
_SCAN_DIR = _os.environ.get('PRISM_SCAN_DIR', '/tmp/prism_scans')
_os.makedirs(_SCAN_DIR, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE

rows = (_http_json(f"{OKX_BASE}/api/v5/public/instruments?instType=SWAP").get("data") or [])
inv = {r["instId"].split("-")[0] for r in rows if r["instId"].endswith("-USD-SWAP")}
lin = {r["instId"].split("-")[0] for r in rows if r["instId"].endswith("-USDT-SWAP")}
both = sorted(inv & lin)
print(f"{len(inv)} inverses, {len(lin)} lineaires, {len(both)} sous-jacents cotes DANS LES DEUX")
print(f"sous-jacents couvrables sans emprunt : {both}\n")


def hist(inst, pages=12):
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
        time.sleep(0.03)
    return out


SEUIL = 37.0
print(f"{'actif':<8}{'periodes':>10}{'jours':>7}{'diff moy bps/j':>16}"
      f"{'|diff| p90':>12}{'|diff| max':>12}{'% > 37 bps/j':>14}")
print("-" * 79)
res = []
for base in both:
    a, b = hist(f"{base}-USD-SWAP"), hist(f"{base}-USDT-SWAP")
    common = sorted(set(a) & set(b))
    if len(common) < 50:
        continue
    gaps = [y - x for x, y in zip(common, common[1:])]
    per_h = st.median(gaps) / 3_600_000 if gaps else 8.0
    if per_h <= 0:
        continue
    # differentiel par periode -> bps/jour de notionnel
    diff = [(a[t] - b[t]) * 10_000.0 * 24.0 / per_h for t in common]
    days = (max(common) - min(common)) / 86_400_000
    ab = sorted(abs(d) for d in diff)
    n = len(ab)
    over = sum(1 for d in ab if d > SEUIL) / n
    res.append((base, n, days, st.fmean(diff), ab[int(.9*n)], ab[-1], over))
    print(f"{base:<8}{n:>10}{days:>7.0f}{st.fmean(diff):>16.3f}"
          f"{ab[int(.9*n)]:>12.2f}{ab[-1]:>12.2f}{over:>13.2%}")

print(f"\nseuil d'admission : {SEUIL:.1f} bps/jour de notionnel")
big = [r for r in res if r[5] > SEUIL]
print(f"actifs ayant DEJA atteint le seuil au moins une fois : {len(big)}/{len(res)}")
pers = [r for r in res if r[4] > SEUIL]
print(f"actifs le depassant au moins 10 % du temps          : {len(pers)}/{len(res)}")
if res:
    print(f"\n|differentiel| median sur tous les actifs : "
          f"{st.median([r[4] for r in res]):.2f} bps/jour (p90 par actif)")
json.dump([list(r) for r in res], open(_os.path.join(_SCAN_DIR, 'funding_pairs.json'), 'w'))
