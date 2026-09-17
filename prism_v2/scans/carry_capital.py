"""Le carry inverse/lineaire, evalue en rendement du CAPITAL REEL.

CE QUE CE SCRIPT TRANCHE. CARRY_FINDING.md laissait ouverte « l'inconnue
decisive » : un facteur 20 sur le rendement du capital, selon qu'OKX nette ou
non les marges d'une paire delta-neutre. Le document annoncait qu'il fallait un
compte authentifie pour le savoir. Il n'en fallait pas : le bareme de marge est
public, et surtout la question etait mal posee. Le plancher qu'il retenait —
« entierement collateralise », levier 1x — n'est pas un plancher mais un choix
de financement.

Le vrai capital immobilise est, jambe par jambe :

    marge initiale du palier  +  de quoi survivre au mouvement de prix
                                 sur la duree de detention

Jambe par jambe, parce que l'inverse est marge en coin et la lineaire en USDT :
le gain de l'une ne peut pas empecher la liquidation de l'autre. C'est le seul
point ou CARRY_FINDING.md avait entierement raison, et il est conserve.

Le coussin est LU dans 375 jours de bougies horaires, sur le maximum ATTEINT
dans la fenetre — une liquidation se declenche au plus bas traverse, pas au
prix de sortie.

Aucun horizon n'est choisi sur le resultat : la courbe complete est rendue.
"""
import json
import os
import statistics as st
from pathlib import Path

from prism_v2.capital_efficiency import Objective
from prism_v2.core_types import Direction
from prism_v2.flow_census import Flow, Leg, horizon_curve, render
from prism_v2.funding_feed import OKX_BASE, _http_json
from prism_v2.instruments import parse_okx_instrument
from prism_v2.margin import load_schedules

SCRATCH = os.environ.get("PRISM_SCAN_DIR", "/tmp/prism_scans")

#: Differentiel de funding inverse - lineaire, moyenne sur 92 jours, en bps
#: par jour de notionnel. Mesure par prism_v2/scans/funding_pairs (crible
#: inverse/lineaire). Une valeur positive remunere SHORT inverse + LONG
#: lineaire : le funding positif fait payer les longs.
DIFFERENTIEL_BPS_JOUR = {
    "ADA": 1.200, "BCH": 1.161, "BTC": 0.141, "DOGE": 0.011, "DOT": 0.779,
    "ETC": 1.577, "ETH": 0.316, "FIL": 0.838, "LINK": 0.608, "LTC": 0.559,
    "SOL": 0.368, "SUI": 0.959, "UNI": 0.498, "XRP": 0.733,
}
TAKER_BPS = 5.0
OBJECTIF = Objective(capital_eur=1_000.0, multiple=10.0, days=365.0)


def main() -> None:
    raw = json.load(open(Path("prism_v2/data/candles_1h.json")))["data"]
    schedules = load_schedules()
    tick = {t["instId"]: t for t in
            (_http_json(f"{OKX_BASE}/api/v5/market/tickers?instType=SWAP"
                        ).get("data") or [])}
    specs = {}
    for row in (_http_json(f"{OKX_BASE}/api/v5/public/instruments?instType=SWAP"
                           ).get("data") or []):
        sp = parse_okx_instrument(row)
        if sp is not None:
            specs[sp.inst_id] = sp

    seuil = OBJECTIF.required_bps_per_day()
    print(f"objectif : x{OBJECTIF.multiple:.0f} en {OBJECTIF.days:.0f} jours "
          f"= {seuil:.2f} bps/jour de capital\n")
    print(f"{'actif':<7}{'flux bps/j':>12}{'A/R bps':>10}{'levier 1j':>11}"
          f"{'net/j capital 1j':>18}{'BORNE grille':>14}{'x objectif':>12}")
    print("-" * 84)

    resume = []
    for base, rate in sorted(DIFFERENTIEL_BPS_JOUR.items()):
        inv_id, lin_id = f"{base}-USD-SWAP", f"{base}-USDT-SWAP"
        if inv_id not in raw or lin_id not in raw:
            continue
        si, sl = specs.get(inv_id), specs.get(lin_id)
        ti, tl = tick.get(inv_id), tick.get(lin_id)
        if not (si and sl and ti and tl):
            continue
        try:
            bi, ai = float(ti["bidPx"]), float(ti["askPx"])
            bl, al = float(tl["bidPx"]), float(tl["askPx"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (ai > bi > 0 and al > bl > 0):
            continue
        hs = ((ai - bi) / (ai + bi) + (al - bl) / (al + bl)) * 10_000.0
        cost = 4 * TAKER_BPS + 2 * hs      # 4 traversees + 2 fois les spreads

        # prix : (ts, close) ; le coussin lit le parcours complet de la bougie
        prices = {}
        for iid in (inv_id, lin_id):
            rows = raw[iid]
            pts = []
            for ts, high, low, close in rows:
                pts.append((ts, high))
                pts.append((ts + 1, low))
            prices[iid] = pts
        entry = {inv_id: (bi + ai) / 2.0, lin_id: (bl + al) / 2.0}
        # notionnel egal des deux cotes, ~10 000 USD
        n_inv = 10_000.0 / (si.ct_val * si.ct_mult)
        n_lin = 10_000.0 / (sl.ct_val * sl.ct_mult * entry[lin_id])
        flow = Flow(name=f"carry {base} (short inverse / long lineaire)",
                    legs=[Leg(si, Direction.SHORT, n_inv),
                          Leg(sl, Direction.LONG, n_lin)],
                    rate_bps_per_day=rate, round_trip_bps=cost,
                    evidence="OBSERVE",
                    source="differentiel funding 92 j, 277 periodes",
                    note=f"demi-spreads cumules {hs:.2f} bps")
        curve = horizon_curve(flow, prices, schedules, entry)
        if not curve:
            print(f"{base:<7}  bareme ou prix manquants")
            continue
        un = next((e for e in curve if abs(e.horizon_days - 1.0) < 1e-9), None)
        borne = max(curve, key=lambda e: e.net_bps_per_day_capital)
        resume.append((base, flow, curve, borne))
        print(f"{base:<7}{rate:>12.3f}{cost:>10.1f}"
              f"{(un.leverage if un else float('nan')):>11.1f}"
              f"{(un.net_bps_per_day_capital if un else float('nan')):>18.1f}"
              f"{borne.net_bps_per_day_capital:>14.2f}"
              f"{borne.net_bps_per_day_capital / seuil:>12.3f}")

    if not resume:
        return
    best = max(resume, key=lambda r: r[3].net_bps_per_day_capital)
    print(f"\n{'='*84}\nCOURBE COMPLETE DU MEILLEUR CAS : {best[0]}\n")
    print(render(best[1], best[2], seuil))
    print(f"\n{'='*84}")
    tous = [r[3].net_bps_per_day_capital for r in resume]
    print(f"borne superieure sur {len(resume)} actifs : "
          f"max {max(tous):+.2f} | mediane {st.median(tous):+.2f} bps/jour de capital")
    print(f"objectif : {seuil:.2f} bps/jour")
    print(f"actifs dont MEME LA BORNE atteint l'objectif : "
          f"{sum(1 for x in tous if x >= seuil)}/{len(tous)}")


if __name__ == "__main__":
    main()
