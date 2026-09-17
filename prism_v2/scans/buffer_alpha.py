"""Le coussin croit-il en racine de T, ou moins ? Une seule question.

ENJEU. Le levier disponible vaut 1/(marge + coussin). Si le coussin croit en
T^0,5 (marche aleatoire du residu), le levier s'effondre exactement a la duree
ou le cout d'aller-retour finit de s'amortir, et le plafond de la famille reste
sous 30 bps/jour. S'il croit moins vite — residu qui revient — la tenaille
s'ouvre et le plafond monte.

Ce n'est pas une recherche de signal. C'est la mesure de la variable qui fixe
le denominateur de PNL / CAPITAL / TEMPS.

METHODE. Pour chaque duree T, le coussin est le quantile du PIRE CUMUL ATTEINT
dans une fenetre de T (une liquidation se declenche au plus bas traverse, pas
au prix de sortie). On regresse log(coussin) sur log(T) : la pente est alpha.

HONNETETE DECLAREE D'AVANCE. 100 jours de donnees ne portent que ~5 fenetres
independantes a 20 jours. On pool les 16 paires (correlation mediane des
residus 0,056) et on rapporte le nombre de fenetres DISJOINTES, pas le nombre
de fenetres glissantes. Si l'intervalle sur alpha ne permet pas de trancher
entre 0,35 et 0,50, la mesure est declaree non concluante.
"""
import json, math, os, statistics as st
from prism_v2.funding_feed import _http_json, OKX_BASE

SCAN = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")
PAIRES = [("SKHYNIX","SAMSUNG"),("SKHYNIX","MU"),("SKHYNIX","SOXL"),
          ("SAMSUNG","MU"),("MU","SOXL"),("NVDA","MRVL"),("NVDA","SOXL"),
          ("MRVL","MU"),("INTC","MU"),("TSLA","NVDA"),("MSTR","CRCL"),
          ("AAOI","NBIS"),("CRWV","NBIS"),("QQQ","SOXL"),("XAU","XAG"),
          ("BZ","CL")]
NAMES = sorted({x for p in PAIRES for x in p})
DUREES_J = [1, 2, 3, 5, 7, 10, 14, 20]
QUANTILE = 0.95        # tenable avec le nombre de fenetres disjointes disponibles


def candles(inst, pages=25):
    rows, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100"
        if after: u += f"&after={after}"
        try: d = _http_json(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: rows[int(r[0])] = float(r[4])
            except (ValueError, IndexError): continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after: break
        after = nb
    return rows


C = {n: candles(f"{n}-USDT-SWAP") for n in NAMES}
grid = sorted(set.intersection(*[set(C[n]) for n in NAMES if C[n]]))
R = {n: [math.log(C[n][b]/C[n][a]) for a, b in zip(grid, grid[1:])] for n in NAMES}
print(f"{len(NAMES)} instruments | {len(grid)} heures communes "
      f"({(grid[-1]-grid[0])/86_400_000:.0f} jours)\n")

RESID = {}
for a, b in PAIRES:
    ra, rb = R[a], R[b]
    ma, mb = st.fmean(ra), st.fmean(rb)
    cov = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))/(len(ra)-1)
    vb = st.pvariance(rb)*len(rb)/(len(rb)-1)
    if vb <= 0: continue
    beta = cov/vb
    RESID[(a, b)] = [x - beta*y for x, y in zip(ra, rb)]

keys = list(RESID)
n_h = len(RESID[keys[0]])


def pires(serie, w, disjointes=False):
    """Pire cumul ATTEINT dans une fenetre de w heures."""
    out = []
    pas = w if disjointes else 1
    for i in range(0, len(serie)-w, pas):
        c, lo = 0.0, 0.0
        for j in range(i, i+w):
            c += serie[j]
            if c < lo: lo = c
        out.append(-lo)
    return out


print(f"{'duree':>7}{'heures':>8}{'fen. glissantes':>17}{'fen. DISJOINTES':>17}"
      f"{'coussin q95':>13}{'racine(T) attendu':>19}")
print("-"*82)
pts = []
base = None
for T in DUREES_J:
    w = T*24
    if w >= n_h - 10:
        print(f"{T:>6}j  fenetre plus longue que l'echantillon — exclu")
        continue
    tous, disj = [], 0
    for k in keys:
        tous.extend(pires(RESID[k], w))
        disj += max(0, (n_h - w)//w)
    if not tous: continue
    tous.sort()
    q = tous[min(len(tous)-1, int(QUANTILE*len(tous)))]
    if base is None: base = (T, q)
    attendu = base[1]*math.sqrt(T/base[0])
    pts.append((T, q, disj))
    print(f"{T:>6}j{w:>8}{len(tous):>17,}{disj:>17}{q*100:>12.2f}%"
          f"{attendu*100:>18.2f}%")

if len(pts) < 4:
    print("\nECHANTILLON INSUFFISANT — rien n'est conclu.")
    raise SystemExit

# --- regression log-log : alpha = pente ---
xs = [math.log(p[0]) for p in pts]
ys = [math.log(p[1]) for p in pts]
mx, my = st.fmean(xs), st.fmean(ys)
sxx = sum((x-mx)**2 for x in xs)
alpha = sum((x-mx)*(y-my) for x, y in zip(xs, ys))/sxx
inter = my - alpha*mx
resid = [y - (inter+alpha*x) for x, y in zip(xs, ys)]
se = math.sqrt(sum(r*r for r in resid)/max(1, len(pts)-2)/sxx)
print(f"\nEXPOSANT MESURE : alpha = {alpha:.3f}  (erreur-type {se:.3f})")
print(f"  intervalle a 95 % : [{alpha-1.96*se:.3f} ; {alpha+1.96*se:.3f}]")
print(f"  marche aleatoire  : alpha = 0,500")
print(f"  R2 = {1 - sum(r*r for r in resid)/sum((y-my)**2 for y in ys):.4f}")

# --- consequence economique, avec la marge REELLE ---
IMR, r_flux, c_tak = 0.0567, 6.46, 21.8
MUTU = 2.21
print(f"\nCONSEQUENCE, marge reelle {IMR:.2%}, flux {r_flux} bps/j, "
      f"aller-retour {c_tak} bps")
print(f"{'duree':>7}{'coussin mesure':>16}{'mutualise':>12}{'levier':>8}"
      f"{'R bps/jour':>12}{'EUR/jour':>11}")
print("-"*68)
best = None
for T, q, _ in pts:
    cm = q/MUTU
    L = 1.0/(IMR+cm)
    Rv = L*(r_flux - c_tak/T)
    if best is None or Rv > best[1]: best = (T, Rv)
    print(f"{T:>6}j{q*100:>15.2f}%{cm*100:>11.2f}%{L:>8.2f}{Rv:>12.1f}"
          f"{Rv/10_000*1000:>11.2f}")

print(f"\nplafond mesure : {best[1]:.1f} bps/jour a {best[0]} j = "
      f"{best[1]/10_000*1000:.2f} EUR/jour")
print(f"cible          : 272.0 bps/jour = 20.00 EUR/jour  -> facteur "
      f"{272.0/best[1]:.1f} manquant")
print(f"\nCRITERE D'ABANDON DECLARE D'AVANCE : alpha >= 0,45 -> mecanisme abandonne")
if alpha >= 0.45:
    print(f"  -> alpha = {alpha:.3f} : MECANISME ABANDONNE.")
elif alpha+1.96*se >= 0.45 and alpha-1.96*se <= 0.35:
    print(f"  -> intervalle [{alpha-1.96*se:.3f} ; {alpha+1.96*se:.3f}] ne tranche "
          f"pas entre 0,35 et 0,50 : NON CONCLUANT, mecanisme abandonne.")
else:
    print(f"  -> alpha = {alpha:.3f} < 0,45 : le residu revient. Le plafond "
          f"ci-dessus est la nouvelle borne.")
json.dump({"alpha": alpha, "se": se, "points": pts, "best": best},
          open(os.path.join(SCAN, "alpha.json"), "w"))
