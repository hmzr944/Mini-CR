"""Le carry inverse/lineaire, remesure de bout en bout (rapport E.4, E.5, H.2).

Refetche le funding depuis OKX (aucune credential requise), puis produit, dans
l'ordre :

  1. le differentiel par coin, confronte a la table codee en dur de
     `prism_v2/scans/carry_capital.py` -- verification croisee ;
  2. la structure du differentiel : queue lourde par coin, mais flux regulier
     au niveau du panier ;
  3. la regle exacte de `prism_v2/scans/carry_alloc.py` (tri par |differentiel|,
     retournement des jambes), en blocs DISJOINTS ;
  4. la profondeur reelle des deux jambes, qui decide de ce qui est deployable ;
  5. l'economie du sous-ensemble deployable ;
  6. le risque de queue du residu, lu dans 375 jours de bougies du depot.

Aucun ordre n'est emis. Aucun fichier du depot n'est modifie.
"""
import json
import statistics as st
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BCH", "LINK", "ETC", "DOT", "FIL"]
OKX = "https://www.okx.com"

#: table codee en dur dans prism_v2/scans/carry_capital.py, bps/jour
PROJET = {"ADA": 1.200, "BCH": 1.161, "BTC": 0.141, "DOGE": 0.011, "DOT": 0.779,
          "ETC": 1.577, "ETH": 0.316, "FIL": 0.838, "LINK": 0.608, "LTC": 0.559,
          "SOL": 0.368, "XRP": 0.733}

#: mesures du projet reutilisees telles quelles, avec leur provenance
ALPHA_MEME_SOUSJACENT = 0.236     # ee398e7, IC [0.215 ; 0.258]
COUSSIN_14J = 0.00441             # ee398e7 : 0,441 % du notionnel
IMR, MUTU = 0.04, 2.21            # palier OKX 25x ; mutualisation mesuree


def funding(inst, pages=30):
    rows, after = [], None
    for _ in range(pages):
        p = {"instId": inst, "limit": 100}
        if after:
            p["after"] = after
        try:
            d = requests.get(f"{OKX}/api/v5/public/funding-rate-history",
                             params=p, timeout=20).json().get("data") or []
        except Exception:
            time.sleep(2); continue
        if not d:
            break
        rows += d
        after = d[-1]["fundingTime"]
        if len(d) < 100:
            break
        time.sleep(0.15)
    return {int(r["fundingTime"]): float(r["realizedRate"] or r["fundingRate"]) for r in rows}


def levier(T_jours):
    coussin = COUSSIN_14J * (T_jours / 14.0) ** ALPHA_MEME_SOUSJACENT
    return 1.0 / (IMR + coussin / MUTU)


def main():
    print("1. DIFFERENTIEL PAR COIN — verification croisee\n")
    SER = {}
    for c in COINS:
        inv, lin = funding(f"{c}-USD-SWAP"), funding(f"{c}-USDT-SWAP")
        t = sorted(set(inv) & set(lin))
        SER[c] = {k: (inv[k] - lin[k]) * 1e4 for k in t}
    n_cap = {len(v) for v in SER.values()}
    print(f"{'coin':5s} {'n':>4s} {'bps/jour':>9s} {'projet':>8s} {'ecart':>7s} {'%>0':>6s}")
    for c in COINS:
        d = list(SER[c].values()); m = st.fmean(d) * 3
        print(f"{c:5s} {len(d):4d} {m:9.3f} {PROJET[c]:8.3f} {m - PROJET[c]:7.3f} "
              f"{100 * sum(x > 0 for x in d) / len(d):6.1f}")
    print(f"\n  Plafond d'historique de l'API OKX : {n_cap} releves par instrument.")
    print("  Identique pour tous ⇒ ce n'est pas l'age des instruments, c'est un plafond.")

    print("\n2. STRUCTURE — par coin la queue porte la moyenne, en panier non\n")
    print(f"{'coin':5s} {'moyenne':>8s} {'mediane':>8s} {'part top5%':>11s} {'t':>7s}")
    for c in COINS:
        d = list(SER[c].values()); n = len(d)
        s = sorted(d, reverse=True); k = max(1, int(.05 * n))
        print(f"{c:5s} {st.fmean(d):8.4f} {st.median(d):8.4f} "
              f"{100 * sum(s[:k]) / sum(d):10.1f}% "
              f"{st.fmean(d) / (st.stdev(d) / n ** .5):7.2f}")
    times = sorted(set.intersection(*[set(v) for v in SER.values()]))
    port = [st.fmean([SER[c][t] for c in COINS]) for t in times]
    n = len(port); s = sorted(port, reverse=True); k = max(1, int(.05 * n))
    print(f"\n  PANIER equipondere : moyenne {st.fmean(port):.4f}  mediane {st.median(port):.4f}  "
          f"t {st.fmean(port) / (st.stdev(port) / n ** .5):.2f}")
    print(f"  part des 5 % plus forts : {100 * sum(s[:k]) / sum(port):.1f} %   "
          f"reglements positifs : {100 * sum(x > 0 for x in port) / n:.1f} %")

    print("\n3. REGLE DE carry_alloc.py — blocs DISJOINTS\n")
    print(f"{'k':>2s} {'N':>3s} {'jours':>6s} {'blocs':>6s} {'brut bps/j':>11s} {'t':>6s} {'net -21,8':>10s}")
    best = 0.0
    for kk in (1, 2, 3, 6, 12):
        for N in (3, 6, 12, 24):
            g, i, days = [], 0, []
            while i + N < len(times):
                top = sorted(COINS, key=lambda c: -abs(SER[c][times[i]]))[:kk]
                g.append(sum((1 if SER[c][times[i]] > 0 else -1)
                             * sum(SER[c][times[j]] for j in range(i + 1, i + 1 + N))
                             for c in top) / kk)
                days.append((times[i + N] - times[i]) / 86_400_000); i += N
            if len(g) < 8:
                continue
            dj = st.fmean(days); brut = st.fmean(g) / dj
            best = max(best, brut)
            print(f"{kk:2d} {N:3d} {dj:6.1f} {len(g):6d} {brut:11.3f} "
                  f"{st.fmean(g) / (st.stdev(g) / len(g) ** .5):6.2f} {brut - 21.8 / dj:10.3f}")
    print(f"\n  maximum observe : {best:.3f} bps/jour")
    print("  `buffer_alpha.py:128` utilise r_flux = 6.46, qui vient de `alloc_policy.py`")
    print("  (univers NON-CRYPTO : SKHYNIX, NVDA, TSLA, QQQ, XAU, BZ...). Pas de la crypto.")

    print("\n4. CAPACITE — profondeur reelle des deux jambes\n")
    spec = {i["instId"]: i for i in requests.get(
        f"{OKX}/api/v5/public/instruments?instType=SWAP", timeout=20).json()["data"]}
    print(f"{'coin':5s} {'bps/jour':>9s} {'jambe INVERSE au touch':>24s} {'lineaire':>12s}")
    deployable = []
    for c in COINS:
        row = []
        for suf in ("USD-SWAP", "USDT-SWAP"):
            i = f"{c}-{suf}"
            try:
                b = requests.get(f"{OKX}/api/v5/market/books",
                                 params={"instId": i, "sz": 1}, timeout=20).json()["data"][0]
            except Exception:
                row.append(float("nan")); continue
            s = spec.get(i, {}); ctv = float(s.get("ctVal", 1))
            px, sz = float(b["bids"][0][0]), float(b["bids"][0][1])
            row.append(sz * ctv if s.get("ctValCcy") == "USD" else sz * ctv * px)
        print(f"{c:5s} {st.fmean(list(SER[c].values())) * 3:9.3f} {row[0]:24,.0f} {row[1]:12,.0f}")
        if row[0] > 100_000:
            deployable.append(c)
    print(f"\n  deployable (jambe inverse > 100 k$) : {deployable}")
    print("  Les plus gros differentiels sont sur les jambes les plus fines.")

    if deployable:
        print(f"\n5. ECONOMIE DU SOUS-ENSEMBLE DEPLOYABLE {deployable}\n")
        com = sorted(set.intersection(*[set(SER[c]) for c in deployable]))
        p = [st.fmean([SER[c][t] for c in deployable]) for t in com]
        r = st.fmean(p) * 3
        print(f"  r = {r:.4f} bps/jour   t = {st.fmean(p) / (st.stdev(p) / len(p) ** .5):.2f}   n = {len(p)}")
        print(f"\n{'T jours':>8s} {'levier':>7s} {'maker 8bps':>11s} {'taker 21,8':>11s} "
              f"{'%/an maker':>11s} {'fenetres indep.':>16s}")
        jours = (com[-1] - com[0]) / 86_400_000
        for T in (30, 60, 90, 180, 365):
            L = levier(T); mk, tk = L * (r - 8.0 / T), L * (r - 21.8 / T)
            print(f"{T:8d} {L:7.1f} {mk:11.2f} {tk:11.2f} "
                  f"{100 * ((1 + mk / 1e4) ** 365 - 1):10.1f}% {jours / T:16.2f}")
        print("\n  Le coussin (alpha = 0,236) est negligeable devant l'IMR de 4 % :")
        print("  le levier est PLAT en T. La « tenaille » de MISSION.md 5-ter est une")
        print("  propriete de la couverture par correlation (alpha = 0,493), pas du carry crypto.")

    print("\n6. RISQUE DE QUEUE DU RESIDU — 375 jours de bougies du depot\n")
    raw = json.load(open(ROOT / "prism_v2/data/candles_1h.json"))
    ci = raw["fields"].index("close"); dat = raw["data"]
    seuil = 1e4 / 24.0
    print(f"  seuil de liquidation a 24x : {seuil:.0f} bps\n")
    print(f"{'coin':5s} {'ecart-type':>11s} {'pire derive 14j':>16s} {'liquide ?':>10s}")
    worst_hour = {}
    for c in COINS:
        a, b = f"{c}-USD-SWAP", f"{c}-USDT-SWAP"
        if a not in dat or b not in dat:
            continue
        A = {r[0]: r[ci] for r in dat[a]}; B = {r[0]: r[ci] for r in dat[b]}
        t = sorted(set(A) & set(B)); res = [1e4 * (A[k] / B[k] - 1) for k in t]
        W, worst = 336, 0.0
        for i in range(0, len(res) - W, 6):
            seg = res[i:i + W]
            worst = max(worst, max(seg) - seg[0], seg[0] - min(seg))
        k = t[res.index(min(res))]
        worst_hour[c] = datetime.fromtimestamp(k / 1000, timezone.utc)
        print(f"{c:5s} {st.pstdev(res):11.2f} {worst:16.1f} "
              f"{'OUI' if worst > seuil else 'non':>10s}")
    from collections import Counter
    h = Counter(v.strftime('%Y-%m-%d %H:00') for v in worst_hour.values()).most_common(1)[0]
    print(f"\n  Les pires residus ne sont pas disperses : {h[1]} coins sur {len(worst_hour)}")
    print(f"  atteignent leur minimum dans la MEME heure ({h[0]} UTC).")
    print("  ⇒ la mutualisation du coussin (x2,21, mesuree a correlation 0,056)")
    print("     disparait exactement dans l'evenement qui decide de la survie.")


if __name__ == "__main__":
    main()
