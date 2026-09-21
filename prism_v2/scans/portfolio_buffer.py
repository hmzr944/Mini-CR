"""Le coussin de survie se PARTAGE-T-IL entre positions ? Mesure, pas postulat.

DEUX CHOSES QUE J'AVAIS MANQUEES, et qui changent l'arithmetique du capital.

1. MARGE DANS LA MEME DEVISE. Le carry crypto oppose un perpetuel INVERSE
   (marge en coin) a un LINEAIRE (marge en USDT) : les jambes ne se
   compensent pas, chacune doit survivre seule. Les paires non-crypto sont
   DEUX perpetuels lineaires margeS en USDT : dans un compte en marge croisee,
   la perte de l'une est compensee par le gain de l'autre, dans la MEME
   devise. Le coussin se dimensionne alors sur le RESIDU de la paire, pas sur
   chaque jambe. C'est un facteur, pas un detail.

2. MUTUALISATION ENTRE POSITIONS. Pour N positions dont les residus sont
   decorreles, le mouvement adverse du PORTEFEUILLE croit en racine de N, pas
   en N. Le coussin par unite de notionnel baisse donc en 1/racine(N) et le
   levier effectif monte en racine(N). Encore faut-il que les residus soient
   reellement decorreles : c'est ce qui est mesure ici, jamais suppose.

Ce script ne cherche aucun signal. Il demande si la MACHINE A CAPITAL peut
faire mieux avec les memes flux — ce que la section 12 du mandat exige de
verifier avant toute amelioration de prediction.
"""
import json, math, os, statistics as st
from prism_v2.funding_feed import _http_json, OKX_BASE

from prism_v2.scans import scan_dir as _scan_dir
SCAN = _scan_dir()
PAIRES = [("SKHYNIX","SAMSUNG"),("SKHYNIX","MU"),("SKHYNIX","SOXL"),
          ("SAMSUNG","MU"),("MU","SOXL"),("NVDA","MRVL"),("NVDA","SOXL"),
          ("MRVL","MU"),("INTC","MU"),("TSLA","NVDA"),("MSTR","CRCL"),
          ("AAOI","NBIS"),("CRWV","NBIS"),("QQQ","SOXL"),("XAU","XAG"),
          ("BZ","CL")]
NAMES = sorted({x for p in PAIRES for x in p})
HOLD_DAYS = 7.0            # DECLARE d'avance, pas choisi sur le resultat
QUANTILE = 0.99
TAKER = 5.0


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


def funding(inst, pages=10):
    out, after = {}, None
    for _ in range(pages):
        u = f"{OKX_BASE}/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after: u += f"&after={after}"
        try: d = _http_json(u).get("data") or []
        except Exception: break
        if not d: break
        for r in d:
            try: out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
            except (KeyError, TypeError, ValueError): continue
        nb = min(int(r["fundingTime"]) for r in d)
        if after and nb >= after: break
        after = nb
        if len(d) < 100: break
    return out


C = {n: candles(f"{n}-USDT-SWAP") for n in NAMES}
F = {n: funding(f"{n}-USDT-SWAP") for n in NAMES}
tick = {t["instId"]: t for t in (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP").get("data") or [])}
print(f"{len(NAMES)} instruments, {len(PAIRES)} paires candidates\n")

grid = sorted(set.intersection(*[set(C[n]) for n in NAMES if C[n]]))
print(f"grille horaire commune a TOUS : {len(grid)} heures "
      f"({(grid[-1]-grid[0])/86_400_000:.0f} jours)")
if len(grid) < 500:
    print("ECHANTILLON INSUFFISANT — rien n'est conclu.")
    raise SystemExit

R = {n: [math.log(C[n][b]/C[n][a]) for a, b in zip(grid, grid[1:])] for n in NAMES}

def demi_spread(n):
    t = tick.get(f"{n}-USDT-SWAP")
    try:
        b, a = float(t["bidPx"]), float(t["askPx"])
        return (a - b) / (a + b) * 10_000.0
    except (TypeError, ValueError, KeyError):
        return None

rows = []
resid = {}
for a, b in PAIRES:
    ra, rb = R[a], R[b]
    ma, mb = st.fmean(ra), st.fmean(rb)
    cov = sum((x-ma)*(y-mb) for x, y in zip(ra, rb))/(len(ra)-1)
    vb = st.pvariance(rb)*len(rb)/(len(rb)-1)
    if vb <= 0: continue
    beta = cov/vb
    res = [x - beta*y for x, y in zip(ra, rb)]
    hs_a, hs_b = demi_spread(a), demi_spread(b)
    if hs_a is None or hs_b is None: continue
    fa, fb = F.get(a, {}), F.get(b, {})
    ts = sorted(set(fa) & set(fb))
    per = {y: (y-x)/3_600_000 for x, y in zip(ts, ts[1:]) if 0.5 <= (y-x)/3_600_000 <= 12.0}
    if len(per) < 60: continue
    flux = st.fmean([(fa[t]-beta*fb[t])*24.0/p*10_000.0 for t, p in per.items()])
    cout = 4*TAKER + 2*(hs_a + hs_b)
    resid[(a, b)] = res
    rows.append({"paire": f"{a}/{b}", "beta": beta, "flux": flux, "cout": cout,
                 "sd_h": st.stdev(res)})

print(f"paires exploitables : {len(rows)}\n")
print(f"{'paire':<20}{'beta':>7}{'flux bps/j':>12}{'cout A/R':>10}"
      f"{'sd residu/h':>13}")
print("-"*62)
for r in rows:
    print(f"{r['paire']:<20}{r['beta']:>7.3f}{r['flux']:>12.2f}"
          f"{r['cout']:>10.1f}{r['sd_h']*100:>12.3f}%")

# ── correlation des residus : la mutualisation est-elle reelle ? ──────────
keys = [tuple(r["paire"].split("/")) for r in rows]
M = [resid[k] for k in keys]
n = len(M)
print(f"\nCORRELATION DES RESIDUS entre paires ({n}x{n})")
cors = []
for i in range(n):
    for j in range(i+1, n):
        x, y = M[i], M[j]
        mx, my = st.fmean(x), st.fmean(y)
        sx, sy = st.stdev(x), st.stdev(y)
        if sx <= 0 or sy <= 0: continue
        c = sum((p-mx)*(q-my) for p, q in zip(x, y))/((len(x)-1)*sx*sy)
        cors.append(abs(c))
if cors:
    cors.sort()
    print(f"  |correlation| mediane {st.median(cors):.3f} | p90 {cors[int(.9*len(cors))]:.3f} | max {max(cors):.3f}")

# ── coussin individuel vs coussin de portefeuille, MESURE ────────────────
W = int(HOLD_DAYS*24)
def pire(serie, w):
    """Pire perte cumulee atteinte dans une fenetre glissante de w heures."""
    out = []
    for i in range(len(serie)-w):
        c, lo = 0.0, 0.0
        for k in range(i, i+w):
            c += serie[k]
            lo = min(lo, c)
        out.append(-lo)
    return sorted(out)

indiv = []
for k in keys:
    p = pire(resid[k], W)
    if p: indiv.append(p[int(QUANTILE*len(p))-1])
port_serie = [st.fmean([resid[k][i] for k in keys]) for i in range(len(M[0]))]
pp = pire(port_serie, W)
port = pp[int(QUANTILE*len(pp))-1] if pp else None

print(f"\nCOUSSIN DE SURVIE a {HOLD_DAYS:.0f} jours, quantile {QUANTILE}")
print(f"  moyenne des coussins individuels  : {st.fmean(indiv)*100:>7.2f} % du notionnel")
print(f"  coussin du PORTEFEUILLE equipondere: {port*100:>7.2f} % du notionnel")
gain = st.fmean(indiv)/port if port and port > 0 else float("nan")
print(f"  GAIN DE MUTUALISATION             : {gain:>7.2f}x")
print(f"  (racine de N = {math.sqrt(n):.2f}x si les residus etaient independants)")

IMR = 0.02*2          # deux jambes, palier 1 typique sur ces instruments
flux_moy = st.fmean([r["flux"] for r in rows])
cout_moy = st.fmean([r["cout"] for r in rows])
net_notionnel = flux_moy - cout_moy/HOLD_DAYS
for nom, cous in (("une paire seule", st.fmean(indiv)), ("portefeuille", port)):
    cap = IMR + cous
    L = 1.0/cap
    print(f"\n{nom:<18} capital {cap*100:>6.2f} % du notionnel  levier {L:>5.2f}x"
          f"  ->  {net_notionnel*L:>8.2f} bps/jour de CAPITAL")
print(f"\nflux moyen {flux_moy:.2f} bps/j | cout moyen {cout_moy:.1f} bps | "
      f"net sur notionnel {net_notionnel:.2f} bps/j")
print(f"SEUIL QUI MORD : 272 bps/jour de capital")
json.dump({"rows": rows, "gain": gain, "port": port,
           "indiv": st.fmean(indiv)}, open(os.path.join(SCAN, "portbuf.json"), "w"))
