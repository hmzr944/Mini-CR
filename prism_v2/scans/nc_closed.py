"""Le residu de couverture se concentre-t-il quand le sous-jacent est OUVERT ?

Mecanisme teste : SKHYNIX et SAMSUNG suivent des actions cotees au KRX, ouvert
09:00-15:30 KST, soit 00:00-06:30 UTC, cinq jours sur sept. Pendant les 17,5 h
restantes et tout le week-end, le sous-jacent NE PEUT PAS BOUGER.

Si le residu de couverture entre les deux perpetuels se concentre aux heures
d'ouverture, alors detenir la paire uniquement a marche ferme serait une
structure differente : meme flux, risque de prix effondre.

C'est une hypothese mecanique, pas un ajustement de parametre : les heures
d'ouverture du KRX ne sont pas choisies sur le resultat.
"""
import datetime as dt, json, math, os as _os, statistics as st
_SCAN = _os.environ.get('PRISM_SCAN_DIR', '/tmp/prism_scans')
_os.makedirs(_SCAN, exist_ok=True)

from prism_v2.funding_feed import _http_json, OKX_BASE


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


A, B = "SKHYNIX", "SAMSUNG"
ca, cb = candles(f"{A}-USDT-SWAP"), candles(f"{B}-USDT-SWAP")
common = sorted(set(ca) & set(cb))
print(f"{A} / {B} : {len(common)} heures communes "
      f"({(common[-1]-common[0])/86_400_000:.0f} jours)\n")

ra = [(t2, math.log(ca[t2]/ca[t1])) for t1, t2 in zip(common, common[1:])]
rb = {t2: math.log(cb[t2]/cb[t1]) for t1, t2 in zip(common, common[1:])}
xs = [r for _, r in ra]
ys = [rb[t] for t, _ in ra]
mx, my = st.fmean(xs), st.fmean(ys)
cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/(len(xs)-1)
vy = st.pvariance(ys)*len(ys)/(len(ys)-1)
beta = cov/vy
print(f"beta global (SKHYNIX sur SAMSUNG) : {beta:.3f}")

def krx_ouvert(ms):
    d = dt.datetime.utcfromtimestamp(ms/1000)
    if d.weekday() >= 5:
        return False
    return 0 <= d.hour < 7            # 00:00-06:30 UTC = 09:00-15:30 KST

ouv = [x - beta*rb[t] for t, x in ra if krx_ouvert(t)]
fer = [x - beta*rb[t] for t, x in ra if not krx_ouvert(t)]
print(f"\n{'regime':<24}{'heures':>9}{'ecart-type residu':>20}{'p90 |residu|':>15}"
      f"{'annualise':>12}")
print("-"*80)
for nom, v in (("KRX OUVERT", ouv), ("KRX FERME (+ week-end)", fer)):
    if len(v) < 30:
        continue
    sd = st.stdev(v)
    ab = sorted(abs(x) for x in v)
    print(f"{nom:<24}{len(v):>9}{sd*100:>19.3f}%{ab[int(.9*len(ab))]*100:>14.3f}%"
          f"{sd*math.sqrt(24*365)*100:>11.0f}%")
if len(ouv) >= 30 and len(fer) >= 30:
    r = st.stdev(ouv)/st.stdev(fer)
    print(f"\nrapport des ecarts-types ouvert/ferme : {r:.2f}x")
    if r < 1.3:
        print("  -> le residu N'EST PAS concentre a l'ouverture. Fermer la")
        print("     position hors seance ne reduirait pas le risque de prix :")
        print("     le perpetuel bouge autant quand son sous-jacent ne peut pas.")
    else:
        print("  -> le residu EST concentre a l'ouverture ; detenir hors seance")
        print("     reduirait le risque de prix d'autant.")

# et le flux, lui, est-il paye pendant la fermeture ?
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
        if not d: break
        for r in d:
            try: out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
            except (KeyError, TypeError, ValueError): continue
        nb = min(int(r["fundingTime"]) for r in d)
        if after and nb >= after: break
        after = nb
        if len(d) < 100: break
    return out

fa, fb = funding(f"{A}-USDT-SWAP"), funding(f"{B}-USDT-SWAP")
ts = sorted(set(fa) & set(fb))
per = {y: (y-x)/3_600_000 for x, y in zip(ts, ts[1:]) if 0.5 <= (y-x)/3_600_000 <= 12.0}
do = [ (fa[t]-beta*fb[t])*24.0/p*10_000.0 for t,p in per.items() if krx_ouvert(t)]
df = [ (fa[t]-beta*fb[t])*24.0/p*10_000.0 for t,p in per.items() if not krx_ouvert(t)]
print(f"\ndifferentiel de funding couvert, par regime (bps/jour de notionnel)")
for nom, v in (("KRX OUVERT", do), ("KRX FERME", df)):
    if len(v) >= 20:
        print(f"  {nom:<14}{len(v):>5} periodes   moyenne {st.fmean(v):>7.2f}   "
              f"% positif {sum(1 for x in v if x>0)/len(v):>4.0%}")
print(f"\nseuil d'admission : ~84 bps/jour de notionnel")
