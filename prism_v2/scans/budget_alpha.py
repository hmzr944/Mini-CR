"""Le budget des instruments a haut sigma est-il structure, ou du bruit ?

CE QUI A ETE DECOUVERT. Le budget economique — mouvement quotidien rapporte au
cout d'un aller-retour — vaut 13,4 sur BTC-USDT-SWAP (rang 2085 sur 3419) et
150 a 474 dans le haut de la distribution. PRISM n'a jamais mesure que des
instruments du quintile le plus pauvre : ses quatre fermetures de forme
reposent donc sur un echantillon biaise, et ce biais est le mien.

CE QUI EST TESTE ICI, ET CE N'EST PAS UNE STRATEGIE. Un budget eleve dit qu'il
y a de la place, pas que le mouvement soit exploitable. Sur une marche
aleatoire, aucun budget ne sert a rien : le deplacement croit en racine du
temps et tout horizon paie le meme cout pour la meme esperance nulle.

On mesure donc l'exposant alpha du deplacement avec le temps, exactement comme
pour le coussin de survie : deplacement ~ T^alpha.
  alpha = 0,5  marche aleatoire, inexploitable par construction
  alpha < 0,5  retour : le prix revient, il y a de la place
  alpha > 0,5  tendance : le prix persiste

Aucune regle d'entree, aucune position, aucun signal. Une propriete du
processus, mesuree, comparee entre les deux populations.

TEMOIN OBLIGATOIRE. Les memes instruments que PRISM a deja etudies servent de
groupe de controle : si alpha y vaut aussi 0,5, la difference de budget ne
tient pas a la structure du mouvement.
"""
import json, math, os, statistics as st, time, urllib.request
from collections import defaultdict

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
FEES = {"OKX": 5.0, "MEXC": 2.0, "Bitget": 6.0, "Hyperliquid": 4.5}
MIN_VOL = 1_000_000.0          # tradabilite : 1 M USD/jour minimum
DUREES_H = [1, 2, 4, 8, 16, 32, 64, 128]


def get(u, timeout=25):
    r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def okx_candles(inst, pages=12):
    rows, after = {}, None
    for _ in range(pages):
        u = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after: u += f"&after={after}"
        try: d = get(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: rows[int(r[0])] = float(r[4])
            except (ValueError, IndexError): continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after: break
        after = nb
    return rows


def mexc_candles(sym, limit=1000):
    try:
        d = get(f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=60m&limit={limit}")
        return {int(r[0]): float(r[4]) for r in d}
    except Exception:
        return {}


def bitget_candles(sym, limit=1000):
    try:
        d = get(f"https://api.bitget.com/api/v2/mix/market/candles?symbol={sym}"
                f"&productType=USDT-FUTURES&granularity=1H&limit={limit}").get("data") or []
        return {int(r[0]): float(r[4]) for r in d}
    except Exception:
        return {}


rows = json.load(open(f"{SCAN}/univers.json"))
E = []
for v, s, hs, sig, vol in rows:
    if v not in FEES or vol < MIN_VOL: continue
    E.append((sig / (2*(FEES[v]+hs)), v, s, hs, sig, vol))
E.sort(key=lambda x: -x[0])
HAUT = E[:30]
TEMOIN = [x for x in E if x[2] in
          ("BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","XRP-USDT-SWAP",
           "DOGE-USDT-SWAP","ADA-USDT-SWAP","LTC-USDT-SWAP","BCH-USDT-SWAP",
           "LINK-USDT-SWAP","DOT-USDT-SWAP","FIL-USDT-SWAP","ETC-USDT-SWAP")]
print(f"haut budget : {len(HAUT)} instruments (budget {HAUT[-1][0]:.0f} a {HAUT[0][0]:.0f})")
print(f"temoin      : {len(TEMOIN)} instruments deja etudies par PRISM "
      f"(budget {min(x[0] for x in TEMOIN):.0f} a {max(x[0] for x in TEMOIN):.0f})\n")


def alpha_of(px_map):
    """Exposant du deplacement moyen absolu avec l'horizon."""
    if len(px_map) < 400: return None
    ts = sorted(px_map)
    p = [px_map[t] for t in ts]
    pts = []
    for h in DUREES_H:
        if h >= len(p) // 4: continue
        d = [abs(math.log(p[i+h]/p[i])) for i in range(0, len(p)-h)
             if p[i] > 0 and p[i+h] > 0]
        if len(d) < 50: continue
        pts.append((h, st.fmean(d)))
    if len(pts) < 5: return None
    xs = [math.log(x[0]) for x in pts]; ys = [math.log(max(x[1], 1e-12)) for x in pts]
    mx, my = st.fmean(xs), st.fmean(ys)
    sxx = sum((x-mx)**2 for x in xs)
    if sxx <= 0: return None
    a = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/sxx
    r = [y-(my+a*(x-mx)) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(v*v for v in r)/max(1, len(pts)-2)/sxx)
    return a, se, len(pts)


def fetch(v, s):
    if v == "OKX": return okx_candles(s)
    if v == "MEXC": return mexc_candles(s)
    if v == "Bitget": return bitget_candles(s)
    return {}


for nom, grp in (("HAUT BUDGET", HAUT), ("TEMOIN (deja etudies)", TEMOIN)):
    print(f"\n=== {nom} ===")
    print(f"{'venue':<11}{'symbole':<22}{'budget':>8}{'n bougies':>11}"
          f"{'alpha':>8}{'+/-':>7}")
    print("-"*68)
    alphas = []
    for b, v, s, hs, sig, vol in grp:
        c = fetch(v, s)
        A = alpha_of(c)
        if A is None:
            print(f"{v:<11}{s:<22}{b:>8.0f}{len(c):>11}   donnees insuffisantes")
            continue
        a, se, npt = A
        alphas.append(a)
        print(f"{v:<11}{s:<22}{b:>8.0f}{len(c):>11}{a:>8.3f}{se:>7.3f}")
        time.sleep(0.05)
    if alphas:
        print(f"  -> alpha median {st.median(alphas):.3f} | "
              f"moyenne {st.fmean(alphas):.3f} | n={len(alphas)}")
        print(f"     part a alpha < 0,45 : "
              f"{sum(1 for a in alphas if a < 0.45)/len(alphas):.0%}")
        globals()[f"A_{nom[:4]}"] = alphas

ha = globals().get("A_HAUT", []); te = globals().get("A_TEMO", [])
print(f"\n{'='*68}")
if ha and te:
    print(f"alpha median HAUT BUDGET : {st.median(ha):.3f}")
    print(f"alpha median TEMOIN      : {st.median(te):.3f}")
    print(f"marche aleatoire         : 0,500")
    print(f"\nKILL CONDITION : alpha >= 0,45 sur le haut budget -> le budget est")
    print(f"du bruit de marche aleatoire, HARD LIMIT DEMONTRE.")
    if st.median(ha) >= 0.45:
        print(f"  -> alpha = {st.median(ha):.3f} : KILL CONDITION ATTEINTE.")
    else:
        print(f"  -> alpha = {st.median(ha):.3f} < 0,45 : structure presente.")
json.dump({"haut": ha, "temoin": te}, open(f"{SCAN}/budget_alpha.json", "w"))
