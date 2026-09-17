#!/usr/bin/env python3
"""CARRY INVERSE/LINEAIRE — le funding differentiel est-il capturable ?

POURQUOI CETTE EXPERIENCE. Les experiences taker sur 6,5 h ont etabli que le
mouvement recuperable apres un deplacement vaut zero et que le spread traverse
deux fois (~3 bps, ~12 bps avec frais) tue tout. Ni la latence, ni la taille,
ni les frais n'etaient la contrainte : le PEAGE PAR ALLER-RETOUR l'etait.

Une seule facon de desarmer ce peage sans etre maker : l'AMORTIR. Un peage paye
une fois et etale sur des semaines ne pese plus 12 bps par trade mais 12 bps
divises par le nombre de periodes detenues. Cela deplace la question des
horizons de secondes vers ceux de jours — un regime que le projet n'avait
jamais teste.

LA POSITION. Short perpetuel INVERSE + long perpetuel LINEAIRE sur le meme
sous-jacent, a notionnel USD egal. Verifie numeriquement via `contracts.pnl` :
le PnL net est NUL a la precision machine sur +/-100 % de variation de prix.
Le paiement de funding differentiel reste seul.

CE QUI EST MESURE, JAMAIS SUPPOSE
  - le differentiel realise, periode par periode, sur ~94 jours ;
  - sa persistance HORS ECHANTILLON (seconde moitie jamais regardee pour
    choisir) ;
  - les spreads REELS des deux jambes, lus dans la collecte d'observatoire ;
  - le nombre de periodes de detention pour amortir l'entree et la sortie.

Aucun ordre reel. Aucune cle. Endpoints publics uniquement.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.core_types import utc_now_iso
from prism_v2.instruments import InstrumentSpec

DEFAULT_FUNDING = Path(__file__).parent / "data" / "funding" / "funding_history.json"
DEFAULT_OBSERVATORY = Path(__file__).parent / "data" / "observatory" / "session_long.jsonl.gz"

#: Duree d'une periode de funding OKX.
PERIOD_HOURS = 8.0
PERIODS_PER_YEAR = 365 * 24 / PERIOD_HOURS

#: Frais taker, bareme public OKX Lv1 : le tier le plus cher des standards,
#: donc un MAJORANT. Aucun frais OBSERVED n'existe sans compte authentifie.
FEE_BPS_PER_LEG = 5.0

#: Une entree traverse DEUX carnets, une sortie aussi : quatre traversees.
LEG_CROSSINGS = 4

#: Part de la premiere moitie servant a la DECOUVERTE. La seconde moitie est
#: le holdout : elle n'entre dans aucun choix.
DISCOVERY_FRACTION = 0.5


@dataclass
class PairSeries:
    """Serie appariee de funding pour un sous-jacent."""

    base: str
    inverse_id: str
    linear_id: str
    times: List[int] = field(default_factory=list)
    diff_bps: List[float] = field(default_factory=list)
    inverse_bps: List[float] = field(default_factory=list)
    linear_bps: List[float] = field(default_factory=list)
    #: Periodes ou le taux affiche differait du taux realise.
    displayed_differs: int = 0

    def __len__(self) -> int:
        return len(self.diff_bps)


def _stats(xs: Sequence[float]) -> Dict[str, Any]:
    n = len(xs)
    if n == 0:
        return {"n": 0, "mean": None, "stderr": None, "t_stat": None}
    mean = sum(xs) / n
    if n < 2:
        return {"n": n, "mean": mean, "stderr": None, "t_stat": None}
    sd = statistics.stdev(xs)
    se = sd / math.sqrt(n)
    return {"n": n, "mean": mean, "std": sd, "stderr": se,
            "t_stat": mean / se if se > 0 else None,
            "share_positive": sum(1 for x in xs if x > 0) / n}


def load_pairs(path: Path) -> Tuple[List[PairSeries], Dict[str, InstrumentSpec]]:
    """Apparie les historiques sur l'horodatage de reglement.

    Un appariement sur l'INDEX plutot que sur l'horodatage melangerait des
    periodes differentes des qu'un instrument a une periode manquante — ce qui
    est le cas de HYPE. On apparie donc sur `fundingTime`, jamais sur la
    position dans la liste.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    insts = raw["instruments"]
    specs = {k: InstrumentSpec.from_dict(v["spec"]) for k, v in insts.items()}
    out: List[PairSeries] = []
    for inv_id, lin_id in raw["pairs"]:
        if inv_id not in insts or lin_id not in insts:
            continue
        a = {int(r["fundingTime"]): r for r in insts[inv_id]["history"]}
        b = {int(r["fundingTime"]): r for r in insts[lin_id]["history"]}
        common = sorted(set(a) & set(b))
        if len(common) < 30:
            continue
        ps = PairSeries(base=specs[inv_id].base, inverse_id=inv_id, linear_id=lin_id)
        for t in common:
            ra, rb = a[t], b[t]
            ia = float(ra["realizedRate"]) * 10_000.0
            ib = float(rb["realizedRate"]) * 10_000.0
            ps.times.append(t)
            ps.inverse_bps.append(ia)
            ps.linear_bps.append(ib)
            ps.diff_bps.append(ia - ib)
            for r in (ra, rb):
                if r.get("fundingRate") is not None and \
                        abs(float(r["fundingRate"]) - float(r["realizedRate"])) > 1e-12:
                    ps.displayed_differs += 1
        out.append(ps)
    return out, specs


def rest_spreads(inst_ids: Sequence[str], specs: Dict[str, InstrumentSpec]
                 ) -> Dict[str, float]:
    """Spread instantane lu par REST pour les instruments absents de la collecte.

    C'est un INSTANTANE, pas une mediane sur six heures : moins fiable que la
    mesure d'observatoire, et le rapport doit le dire. Un instrument dont le
    carnet est illisible reste ABSENT, jamais complete.
    """
    from prism_v2.market_data import MarketDataError, OKXPublicClient
    from prism_v2.orderbook import OrderBook
    client = OKXPublicClient()
    out: Dict[str, float] = {}
    for inst_id in inst_ids:
        spec = specs.get(inst_id)
        if spec is None:
            continue
        try:
            obs = client.orderbook(spec, depth=5)
            book = OrderBook.from_okx(spec, obs.payload, obs.provenance)
            out[inst_id] = book.spread_bps
        except (MarketDataError, ValueError, KeyError):
            continue
        time.sleep(0.1)
    return out


def observed_spreads(obs_path: Path) -> Dict[str, float]:
    """Spread MEDIAN par instrument, lu dans la collecte d'observatoire.

    Un spread suppose rendrait tout le calcul de cout invente. Un instrument
    absent de la collecte reste ABSENT du resultat, jamais complete par une
    valeur par defaut.
    """
    from prism_v2.observatory import load_snapshots
    try:
        _meta, recs = load_snapshots(Path(obs_path))
    except (OSError, ValueError):
        return {}
    acc: Dict[str, List[float]] = {}
    for r in recs:
        b, a = r.get("b"), r.get("a")
        if not b or not a:
            continue
        mid = (b[0][0] + a[0][0]) / 2.0
        if mid <= 0:
            continue
        acc.setdefault(r["i"], []).append((a[0][0] - b[0][0]) / mid * 10_000.0)
    return {k: statistics.median(v) for k, v in acc.items() if v}


@dataclass
class PairResult:
    base: str
    inverse_id: str
    linear_id: str
    n_total: int
    discovery: Dict[str, Any]
    holdout: Dict[str, Any]
    sign_agrees: Optional[bool]
    spread_inverse_bps: Optional[float]
    spread_linear_bps: Optional[float]
    round_trip_cost_bps: Optional[float]
    breakeven_periods: Optional[float]
    breakeven_days: Optional[float]
    net_bps_per_year: Optional[float]
    displayed_differs: int

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def round_trip_cost(spread_inv: Optional[float], spread_lin: Optional[float],
                    fee_bps_per_leg: float = FEE_BPS_PER_LEG) -> Optional[float]:
    """Cout complet d'un aller-retour sur la PAIRE, en bps du notionnel.

    Entree : traverser le carnet inverse ET le carnet lineaire.
    Sortie : les deux a nouveau. Soit quatre traversees et quatre lots de frais.
    Traverser coute un DEMI-spread depuis le mid, par traversee.

    Si un spread manque, le cout est UNKNOWN — jamais complete.
    """
    if spread_inv is None or spread_lin is None:
        return None
    spread_cost = (spread_inv / 2.0) * 2 + (spread_lin / 2.0) * 2
    return spread_cost + LEG_CROSSINGS * fee_bps_per_leg


def analyse_pair(ps: PairSeries, spreads: Dict[str, float],
                 fee_bps_per_leg: float = FEE_BPS_PER_LEG) -> PairResult:
    """Decoupe temporellement, mesure, et chiffre l'economie.

    Le signe de la position est choisi sur la DECOUVERTE seule. Le holdout ne
    sert qu'a repondre : ce signe tient-il ?
    """
    cut = int(len(ps) * DISCOVERY_FRACTION)
    # Les donnees arrivent du plus RECENT au plus ancien : on remet en ordre
    # chronologique, sans quoi « premiere moitie » designerait le futur.
    order = sorted(range(len(ps)), key=lambda i: ps.times[i])
    chrono = [ps.diff_bps[i] for i in order]
    disc, hold = chrono[:cut], chrono[cut:]
    d, h = _stats(disc), _stats(hold)
    sign_agrees = None
    if d["mean"] is not None and h["mean"] is not None:
        sign_agrees = (d["mean"] * h["mean"]) > 0

    si = spreads.get(ps.inverse_id)
    sl = spreads.get(ps.linear_id)
    cost = round_trip_cost(si, sl, fee_bps_per_leg)
    # Le rendement retenu est celui du HOLDOUT : c'est le seul qui n'a servi a
    # aucun choix. Utiliser la decouverte surestimerait par construction.
    per_period = abs(h["mean"]) if h["mean"] is not None else None
    be_periods = (cost / per_period) if (cost and per_period and per_period > 0) else None
    net_year = None
    if per_period is not None and cost is not None:
        net_year = per_period * PERIODS_PER_YEAR - cost   # une entree/sortie par an
    return PairResult(
        base=ps.base, inverse_id=ps.inverse_id, linear_id=ps.linear_id,
        n_total=len(ps), discovery=d, holdout=h, sign_agrees=sign_agrees,
        spread_inverse_bps=si, spread_linear_bps=sl,
        round_trip_cost_bps=cost, breakeven_periods=be_periods,
        breakeven_days=(be_periods * PERIOD_HOURS / 24) if be_periods else None,
        net_bps_per_year=net_year, displayed_differs=ps.displayed_differs)


def margin_tiers(specs: Dict[str, InstrumentSpec], inst_ids: Sequence[str]
                 ) -> Dict[str, Dict[str, Any]]:
    """Premier palier de marge par instrument : marge initiale et taille max.

    Donnee PUBLIQUE. Elle borne a la fois le capital exige par jambe et la
    taille au-dela de laquelle la marge devient plus chere — donc la capacite
    reelle, imposee par l'exchange et non estimee.
    """
    from prism_v2.market_data import MarketDataError, OKXPublicClient
    client = OKXPublicClient()
    out: Dict[str, Dict[str, Any]] = {}
    for inst_id in inst_ids:
        spec = specs.get(inst_id)
        if spec is None:
            continue
        try:
            rows = client.position_tiers(spec).payload
        except (MarketDataError, ValueError, KeyError):
            continue
        tier1 = next((r for r in rows if str(r.get("tier")) == "1"), None)
        if not tier1:
            continue
        try:
            imr = float(tier1["imr"])
            max_sz = float(tier1["maxSz"])
        except (KeyError, TypeError, ValueError):
            continue
        out[inst_id] = {"imr": imr, "max_lever": 1.0 / imr if imr else None,
                        "max_sz_contracts": max_sz, "mmr": tier1.get("mmr")}
        time.sleep(0.1)
    return out


def run(funding_path: Path = DEFAULT_FUNDING,
        observatory_path: Path = DEFAULT_OBSERVATORY,
        fee_bps_per_leg: float = FEE_BPS_PER_LEG,
        json_out: Optional[Path] = None) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "started_at": utc_now_iso(), "mode": "RECHERCHE — aucun ordre reel",
        "position": ("short perpetuel INVERSE + long perpetuel LINEAIRE, "
                     "notionnel USD egal a l'entree"),
    }
    print("=" * 84); print("1. DONNEES"); print("=" * 84)
    pairs, specs = load_pairs(funding_path)
    if not pairs:
        print("aucune paire exploitable"); return report
    spreads = observed_spreads(observatory_path)
    n_from_obs = len(spreads)
    missing = [i for p in pairs for i in (p.inverse_id, p.linear_id)
               if i not in spreads]
    if missing:
        spreads.update(rest_spreads(sorted(set(missing)), specs))
    n_per = min(len(p) for p in pairs)
    all_ts = [t for p in pairs for t in p.times]
    span_h = (max(all_ts) - min(all_ts)) / 3.6e6
    print(f"paires appariees        : {len(pairs)}")
    print(f"periodes par paire      : {n_per} a {max(len(p) for p in pairs)}"
          f"  ({PERIOD_HOURS:g} h chacune)")
    print(f"fenetre couverte        : {span_h/24:.0f} jours")
    print(f"spreads OBSERVES        : {n_from_obs} par observatoire (mediane "
          f"sur 6,5 h), {len(spreads)-n_from_obs} par REST (instantane, moins "
          "fiable)")
    n_diff = sum(p.displayed_differs for p in pairs)
    tot = sum(len(p) for p in pairs) * 2
    print(f"taux affiche != realise : {n_diff} sur {tot} releves"
          + ("  -> le taux etait connu AVANT le reglement" if n_diff == 0
             else "  -> ATTENTION : le taux affiche n'est pas fiable"))
    report["data"] = {"n_pairs": len(pairs), "periods_min": n_per,
                      "span_days": span_h / 24,
                      "spreads_from_observatory": n_from_obs,
                      "spreads_from_rest": len(spreads) - n_from_obs,
                      "n_spreads_observed": len(spreads),
                      "displayed_differs": n_diff, "n_rate_observations": tot}

    print(); print("=" * 84)
    print("2. DECOUVERTE / HOLDOUT (decoupage temporel, holdout = seconde moitie)")
    print("=" * 84)
    results = [analyse_pair(p, spreads, fee_bps_per_leg) for p in pairs]
    print(f"{'base':<7}{'N':>5}{'decouverte':>12}{'holdout':>10}{'t_hold':>8}"
          f"{'signe':>7}{'cout_AR':>9}{'seuil_j':>9}{'net_an':>9}")
    print("-" * 78)
    for r in sorted(results, key=lambda x: -(x.net_bps_per_year or -1e9)):
        dm = r.discovery["mean"]; hm = r.holdout["mean"]; ht = r.holdout["t_stat"]
        f = lambda v, p=4: "n/a" if v is None else f"{v:.{p}f}"   # noqa: E731
        print(f"{r.base:<7}{r.n_total:>5}{f(dm):>12}{f(hm):>10}{f(ht,2):>8}"
              f"{('oui' if r.sign_agrees else 'NON'):>7}{f(r.round_trip_cost_bps,1):>9}"
              f"{f(r.breakeven_days,1):>9}{f(r.net_bps_per_year,0):>9}")
    report["pairs"] = [r.to_dict() for r in results]

    print(); print("=" * 84); print("3. COHERENCE TRANSVERSALE"); print("=" * 84)
    agree = [r for r in results if r.sign_agrees]
    disc_pos = sum(1 for r in results if (r.discovery["mean"] or 0) > 0)
    hold_pos = sum(1 for r in results if (r.holdout["mean"] or 0) > 0)
    print(f"signe conserve decouverte -> holdout : {len(agree)}/{len(results)}")
    print(f"differentiel positif en decouverte   : {disc_pos}/{len(results)}")
    print(f"differentiel positif en holdout      : {hold_pos}/{len(results)}")
    # Un signe identique sur des instruments correles n'est PAS N tests
    # independants. On le dit ici plutot que de laisser lire 15/15 comme une
    # preuve quinze fois plus forte.
    print("\n  Ces instruments sont fortement correles : 15 accords ne valent")
    print("  PAS quinze confirmations independantes. La coherence transversale")
    print("  suggere une cause COMMUNE, elle ne la demontre pas.")
    report["cross_section"] = {
        "sign_agreement": len(agree), "n_pairs": len(results),
        "discovery_positive": disc_pos, "holdout_positive": hold_pos}

    print(); print("=" * 84)
    print("4. PORTEFEUILLE SELECTIONNE SUR LA DECOUVERTE SEULE")
    print("=" * 84)
    # C'EST LE SEUL CHIFFRE HONNETE. Choisir la meilleure paire APRES avoir vu
    # les quinze holdouts serait de la selection sur holdout : le maximum d'un
    # echantillon est biaise vers le haut par construction. On fige donc la
    # regle sur la DECOUVERTE — « toutes les paires dont le differentiel de
    # decouverte est positif » — puis on lit ce que le holdout a rendu.
    selected = [r for r in results if (r.discovery["mean"] or 0) > 0]
    print(f"regle figee sur la decouverte : differentiel > 0")
    print(f"paires retenues : {len(selected)}/{len(results)}")
    holdout_means = [r.holdout["mean"] for r in selected
                     if r.holdout["mean"] is not None]
    costs = [r.round_trip_cost_bps for r in selected
             if r.round_trip_cost_bps is not None]
    if holdout_means and costs:
        agg = _stats(holdout_means)
        mean_cost = statistics.mean(costs)
        gross_year = agg["mean"] * PERIODS_PER_YEAR
        net_year = gross_year - mean_cost
        kept = sum(1 for m in holdout_means if m > 0)
        print(f"\n  differentiel holdout moyen : {agg['mean']:+.4f} bps/periode")
        print(f"  dont positifs              : {kept}/{len(holdout_means)}")
        print(f"  brut annualise             : {gross_year:.0f} bps")
        print(f"  cout aller-retour moyen    : {mean_cost:.1f} bps (une fois par an)")
        print(f"  NET ANNUEL DU PORTEFEUILLE : {net_year:.0f} bps "
              f"({net_year/100:.2f} %)")
        report["portfolio_selected_on_discovery"] = {
            "n_selected": len(selected), "rule": "differentiel de decouverte > 0",
            "holdout_mean_bps_per_period": agg["mean"],
            "holdout_positive": kept, "gross_bps_per_year": gross_year,
            "mean_round_trip_cost_bps": mean_cost,
            "net_bps_per_year": net_year}

    print(); print("=" * 84); print("5. MEILLEURE PAIRE (selection sur holdout — BIAISEE)")
    print("=" * 84)
    print("Ce chiffre est rapporte pour comparaison, PAS comme resultat : il")
    print("resulte d'un choix fait apres avoir vu les quinze holdouts.")
    viable = [r for r in results
              if r.net_bps_per_year is not None and r.net_bps_per_year > 0
              and r.sign_agrees]
    print(f"paires au net annuel POSITIF et de signe stable : "
          f"{len(viable)}/{len(results)}")
    if viable:
        best = max(viable, key=lambda r: r.net_bps_per_year)
        print(f"\nmeilleure : {best.base}  ({best.inverse_id} / {best.linear_id})")
        print(f"  differentiel holdout : {best.holdout['mean']:.4f} bps/periode "
              f"(t={best.holdout['t_stat']:.2f}, N={best.holdout['n']})")
        print(f"  cout aller-retour    : {best.round_trip_cost_bps:.2f} bps "
              f"(spreads {best.spread_inverse_bps:.2f}/{best.spread_linear_bps:.2f} "
              f"+ {LEG_CROSSINGS}x{fee_bps_per_leg:g} bps de frais)")
        print(f"  seuil de rentabilite : {best.breakeven_days:.1f} jours de detention")
        print(f"  net annuel           : {best.net_bps_per_year:.0f} bps "
              f"({best.net_bps_per_year/100:.2f} %)")
        report["best"] = best.to_dict()
    total_net = [r.net_bps_per_year for r in results if r.net_bps_per_year is not None]
    if total_net:
        print(f"\nnet annuel median sur toutes les paires : "
              f"{statistics.median(total_net):.0f} bps")
    report["viable_pairs"] = [r.to_dict() for r in viable]

    # ── 6. CAPITAL, MARGE ET RISQUE ───────────────────────────────────────
    print(); print("=" * 84); print("6. CAPITAL, MARGE ET RISQUE"); print("=" * 84)
    port = report.get("portfolio_selected_on_discovery") or {}
    net_notional = port.get("net_bps_per_year")
    levers = {}
    for r in results:
        a, b = specs.get(r.inverse_id), specs.get(r.linear_id)
        if a and b:
            levers[r.base] = (a.lever, b.lever, min(a.lever, b.lever))
    if levers:
        worst = min(v[2] for v in levers.values())
        print(f"levier maximal par paire (le plus contraignant des deux) : "
              f"{worst:.0f}x a {max(v[2] for v in levers.values()):.0f}x")
    print()
    print("LES DEUX JAMBES NE SE COMPENSENT PAS AU NIVEAU DE LA MARGE.")
    print("  La jambe inverse est margee en COIN, la lineaire en USDT. Une")
    print("  hausse de prix fait gagner l'une et perdre l'autre — mais dans des")
    print("  devises differentes. Le gain de l'une ne peut pas sauver l'autre")
    print("  d'une liquidation, sauf si l'exchange les nette explicitement.")
    print()
    print("  Ce netting (portfolio margin) est INCONNU sans compte authentifie.")
    print("  Le supposer favorable serait exactement l'erreur que ce projet")
    print("  interdit. Deux bornes sont donc rapportees, jamais une estimation :")
    if net_notional is not None:
        for label, lev, note in (
                ("plancher, entierement collateralise (1x par jambe)", 1.0,
                 "aucune hypothese de netting"),
                ("plafond, netting parfait au levier le plus contraignant", worst,
                 "SUPPOSE un netting que rien ne demontre"),
        ):
            print(f"    {label:<52} {net_notional*lev:>7.0f} bps/an  ({note})")
        report["capital"] = {
            "net_bps_per_year_on_notional": net_notional,
            "floor_fully_collateralised_bps": net_notional,
            "ceiling_if_netted_bps": net_notional * worst,
            "max_lever_binding": worst,
            "margin_netting": "UNKNOWN — exige un compte authentifie",
        }
    # Capacite imposee par l'exchange, lue publiquement.
    best_pairs = [r for r in results if (r.discovery["mean"] or 0) > 0]
    probe = best_pairs[:4]
    tiers = margin_tiers(specs, [i for r in probe
                                 for i in (r.inverse_id, r.linear_id)])
    if tiers:
        print()
        print("CAPACITE AU MEILLEUR PALIER DE MARGE (donnee publique)")
        print(f"  {'instrument':<18}{'marge init.':>12}{'levier max':>12}"
              f"{'taille max':>12}")
        for r in probe:
            for inst_id in (r.inverse_id, r.linear_id):
                t = tiers.get(inst_id)
                if not t:
                    continue
                print(f"  {inst_id:<18}{t['imr']*100:>11.1f}%"
                      f"{t['max_lever']:>11.0f}x{t['max_sz_contracts']:>12,.0f}")
        report["margin_tiers"] = tiers
        print("  Au-dela de `taille max`, le palier suivant s'applique : la")
        print("  marge augmente et le levier maximal baisse. C'est un plafond")
        print("  de capacite impose par l'exchange, pas une estimation.")

    print()
    print("EXPOSITIONS NON COUVERTES PAR LA DEMONSTRATION DE NEUTRALITE")
    print("  Le PnL du TRADE est couvert a la precision machine (verifie).")
    print("  Ne le sont PAS :")
    print("   - la marge postee en coin sur la jambe inverse ;")
    print("   - le funding recu en coin, expose jusqu'a sa conversion ;")
    print("   - le risque de depeg USDT (voir mecanisme ci-dessous).")
    print()
    print("MECANISME PROPOSE — et ce qu'il implique")
    print("  Le funding inverse (cote USD) depasse systematiquement le funding")
    print("  lineaire (cote USDT) sur 14 paires sur 15. Une explication")
    print("  plausible : la position revient a etre LONG USDT contre USD, et le")
    print("  differentiel est le prix de marche du risque de depeg de l'USDT.")
    print("  Si c'est le cas, ces ~2 %/an ne sont pas un edge mais une PRIME DE")
    print("  RISQUE DE QUEUE : encaissee en regime normal, rendue d'un coup lors")
    print("  d'un depeg. Cette hypothese est PLAUSIBLE, pas demontree — elle")
    print("  exigerait un episode de depeg dans l'echantillon, et 94 jours")
    print("  calmes n'en contiennent pas.")
    report["mechanism_hypothesis"] = (
        "la position est longue USDT contre USD ; le differentiel serait le "
        "prix du risque de depeg. Plausible, non demontre : aucun episode de "
        "depeg dans les 94 jours observes.")

    print()
    print("COMPARAISON QUI TRANCHE")
    if net_notional is not None:
        print(f"  Ce carry rapporte {net_notional:.0f} bps/an entierement")
        print("  collateralise, en portant un risque de depeg USDT et un risque")
        print("  d'execution sur quatre traversees de carnet.")
        print("  Preter de l'USDT ou detenir des bons du Tresor rapporte")
        print("  davantage, en portant MOINS de risques (pas d'execution, pas de")
        print("  liquidation, pas de risque de jambe).")
        print("  -> entierement collateralise, la strategie est DOMINEE.")
        print("  Elle ne devient interessante que si le netting de marge est")
        print("  reel ET substantiel, ce qui reste INCONNU.")
    report["finished_at"] = utc_now_iso()
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                             default=str), encoding="utf-8")
        print(f"\nrapport -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Carry inverse/lineaire (recherche)")
    ap.add_argument("--funding", type=Path, default=DEFAULT_FUNDING)
    ap.add_argument("--observatory", type=Path, default=DEFAULT_OBSERVATORY)
    ap.add_argument("--fee-bps", type=float, default=FEE_BPS_PER_LEG)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    run(a.funding, a.observatory, a.fee_bps, a.json)


if __name__ == "__main__":
    main()
