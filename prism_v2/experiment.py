#!/usr/bin/env python3
"""EXPERIENCE FINALE — DISCOVERY -> PROOF -> PAPER, sur donnees d'observatoire.

QUESTION UNIQUE : existe-t-il, dans les donnees accessibles, une inefficience
assez persistante et assez capturable APRES couts, latence, impact et
contraintes d'execution pour produire un PnL net positif HORS ECHANTILLON ?

PROTOCOLE, dans cet ordre strict et non contournable :
  1. DISCOVERY     balayage des configurations, mesure de la fraction de
                   reversion. Chaque configuration est un ESSAI COMPTE.
  2. DEVELOPMENT   choix de la configuration. C'est le SEUL segment ou l'on a
                   le droit de choisir.
  3. VALIDATION    confirmation, sans rien rechoisir.
  4. FINAL_HOLDOUT ouvert UNE FOIS, a la fin.

La sortie principale n'est pas un PnL : c'est l'ENTONNOIR — a quelle etape
precise l'edge meurt. C'est l'information reutilisable.

Aucun ordre reel. Aucune cle. Endpoints publics uniquement.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.contracts import usd_notional
from prism_v2.core_types import Direction, Provenance, utc_now_iso
from prism_v2.instruments import InstrumentSpec
from prism_v2.observatory import load_snapshots
from prism_v2.orderbook import Level, OrderBook
from prism_v2.research.causal_lab import (
    BET_CONTINUATION, BET_REVERSION, CaptureOutcome, SnapshotSeries,
    measure_capture,
)
from prism_v2.research.protocol import DataProtocol, Split
from prism_v2.research.trials import (
    TrialLedger, deflated_sharpe, probability_of_backtest_overfitting,
    sharpe_ratio,
)
from prism_v2.research.validation import Condition, ProofStandard, Verdict

#: Espace de configurations balaye. Chaque combinaison est un ESSAI compte
#: dans la correction pour tests multiples. Le seuil est exprime en multiples
#: du spread median observe : il s'ADAPTE a l'instrument au lieu d'etre pose.
LOOKBACKS_MS = (2_000, 5_000, 15_000)
HORIZONS_MS = (2_000, 5_000, 15_000, 30_000)
#: Seuils en multiples du spread median. Les grandes valeurs sont
#: INDISPENSABLES : le plancher de cout d'un aller-retour est d'environ 10 bps,
#: et un evenement dont l'amplitude est de 2 bps ne peut PAS le couvrir, quelle
#: que soit la qualite de la prevision. Ne balayer que de petits seuils
#: reviendrait a tester uniquement des cas structurellement perdants.
THRESHOLD_SPREADS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)

#: Les deux paris. Tester seulement la reversion supposerait la reponse.
BETS = (BET_REVERSION, BET_CONTINUATION)

#: Notionnel de sonde. Petit, pour rester dans la profondeur reellement
#: observee (10 niveaux enregistres par l'observatoire).
PROBE_NOTIONAL_USD = 250.0

#: Majorant des frais : bareme public OKX Lv1 taker, le tier le plus cher des
#: standards. Ce n'est PAS une mesure — aucun frais OBSERVED n'existe sans
#: compte authentifie.
FEE_BPS_PER_LEG = 5.0

#: Latence appliquee a l'entree. Mesuree a la collecte (delai de transport),
#: jamais supposee nulle.
DEFAULT_LATENCY_MS = 250


def _book(spec: InstrumentSpec, rec: Dict[str, Any]) -> Optional[OrderBook]:
    """Instantane compact -> OrderBook, pour reutiliser la machinerie de couts."""
    try:
        bids = [Level(p, s, usd_notional(spec, s, p)) for p, s in rec["b"]]
        asks = [Level(p, s, usd_notional(spec, s, p)) for p, s in rec["a"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not bids or not asks:
        return None
    return OrderBook(instrument=spec, bids=bids, asks=asks, ts_utc="",
                     provenance=Provenance("OKX", "observatory", "", spec.inst_id),
                     seq_id=str(rec.get("seq")), raw_depth=max(len(bids), len(asks)),
                     ts_ms=rec.get("ts"), local_recv_ts_ms=rec.get("recv"))


@dataclass
class EventResult:
    """Un evenement mesure de bout en bout. Chaque cout est nomme."""

    ts_ms: int
    inst_id: str
    observed_bps: float
    recoverable_bps: float
    capture_fraction: float
    spread_cost_bps: Optional[float]
    impact_cost_bps: Optional[float]
    fee_bps: float
    net_bps: Optional[float]
    resolved: bool
    why: str = ""


@dataclass
class ConfigResult:
    """Resultat d'UNE configuration sur UN segment. C'est un essai compte."""

    inst_id: str
    lookback_ms: int
    horizon_ms: int
    threshold_spreads: float
    split: str
    bet: str = BET_REVERSION
    n_events: int = 0
    n_resolved: int = 0
    gross_bps: List[float] = field(default_factory=list)
    net_bps: List[float] = field(default_factory=list)
    fractions: List[float] = field(default_factory=list)
    #: Horodatage de chaque evenement retenu, pour pouvoir dedoublonner entre
    #: configurations qui balayent les memes instants.
    event_ts: List[int] = field(default_factory=list)
    #: Contexte par evenement. Sans lui, decouper par regime a posteriori
    #: serait impossible et « ca marche en moyenne » resterait indiscutable.
    event_spread_bps: List[float] = field(default_factory=list)
    event_displacement_bps: List[float] = field(default_factory=list)
    refusals: Dict[str, int] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return (f"{self.inst_id}|lb{self.lookback_ms}|h{self.horizon_ms}"
                f"|thr{self.threshold_spreads}|{self.bet}")

    def mean(self, xs: Sequence[float]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "split": self.split, "n_events": self.n_events,
            "n_resolved": self.n_resolved,
            "mean_gross_bps": self.mean(self.gross_bps),
            "mean_net_bps": self.mean(self.net_bps),
            "mean_capture_fraction": self.mean(self.fractions),
            "sharpe": sharpe_ratio(self.net_bps) if len(self.net_bps) > 1 else None,
            "refusals": dict(sorted(self.refusals.items(), key=lambda kv: -kv[1])),
        }


def median_spread_bps(series: SnapshotSeries, lo: int, hi: int) -> Optional[float]:
    vals = []
    for rec in series._recs:
        if lo <= rec["ts"] < hi:
            s = series.spread_bps(rec)
            if s is not None and s > 0:
                vals.append(s)
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def run_config(spec: InstrumentSpec, series: SnapshotSeries, lo: int, hi: int,
               lookback_ms: int, horizon_ms: int, threshold_spreads: float,
               split: str, latency_ms: int,
               notional_usd: float = PROBE_NOTIONAL_USD,
               bet: str = BET_REVERSION) -> ConfigResult:
    """Balaye un segment, declenche AU FRANCHISSEMENT, mesure net de couts.

    Le declenchement se fait au franchissement du seuil, PAS sur une grille
    reguliere : le test synthetique a montre qu'echantillonner sur grille
    attenue l'effet d'environ 30 %, parce qu'on mesure alors majoritairement
    des instants situes au milieu d'un mouvement deja entame.
    """
    res = ConfigResult(inst_id=spec.inst_id, lookback_ms=lookback_ms,
                       horizon_ms=horizon_ms, threshold_spreads=threshold_spreads,
                       split=split, bet=bet)
    med_spread = median_spread_bps(series, lo, hi)
    if med_spread is None:
        res.refusals["spread median inconnu"] = 1
        return res
    threshold_bps = med_spread * threshold_spreads

    last_event_ts = -10**18
    # Anti-chevauchement : deux evenements distants de moins d'un horizon
    # partagent leur issue. Les compter separement gonflerait N sans ajouter
    # d'information — c'est exactement le defaut CORRELATED_OBSERVATIONS.
    cooldown_ms = horizon_ms

    for rec in series._recs:
        ts = rec["ts"]
        if not (lo <= ts < hi):
            continue
        if ts - last_event_ts < cooldown_ms:
            continue
        if ts - lookback_ms < lo or ts + latency_ms + horizon_ms >= hi:
            continue
        ref = series.mid_at(ts - lookback_ms)
        now = series.mid(rec)
        if ref is None or now is None or ref <= 0:
            continue
        disp = (now - ref) / ref * 10_000.0
        if abs(disp) < threshold_bps:
            continue

        last_event_ts = ts
        res.n_events += 1
        m = measure_capture(series, ts, lookback_ms, latency_ms, horizon_ms,
                            min_displacement_bps=threshold_bps, bet=bet)
        if m.outcome is not CaptureOutcome.MEASURED:
            res.refusals[m.outcome.value] = res.refusals.get(m.outcome.value, 0) + 1
            continue

        entry_rec = series.at(ts + latency_ms)
        exit_rec = series.at(ts + latency_ms + horizon_ms)
        eb, xb = _book(spec, entry_rec or {}), _book(spec, exit_rec or {})
        if eb is None or xb is None:
            res.refusals["carnet illisible"] = res.refusals.get("carnet illisible", 0) + 1
            continue
        # Le sens du pari decide quel cote on prend a l'entree et a la sortie.
        entry_side = "ask" if m.reversion_sign > 0 else "bid"
        exit_side = "bid" if m.reversion_sign > 0 else "ask"
        try:
            spread_cost = (eb.crossing_cost_bps(entry_side)
                           + xb.crossing_cost_bps(exit_side))
            imp_in = eb.slippage_vs_touch_bps(entry_side, notional_usd)
            imp_out = xb.slippage_vs_touch_bps(exit_side, notional_usd)
        except Exception:
            res.refusals["couts non calculables"] = \
                res.refusals.get("couts non calculables", 0) + 1
            continue
        # Un impact non mesurable (profondeur enregistree epuisee) est INCONNU,
        # jamais nul : l'evenement est compte comme NON RESOLU.
        if imp_in is None or imp_out is None:
            res.refusals["impact UNKNOWN (profondeur epuisee)"] = \
                res.refusals.get("impact UNKNOWN (profondeur epuisee)", 0) + 1
            continue
        impact = imp_in + imp_out
        fee = FEE_BPS_PER_LEG * 2
        net = m.recoverable_bps - spread_cost - impact - fee

        res.n_resolved += 1
        res.event_ts.append(ts)
        res.event_spread_bps.append(m.spread_at_entry_bps or 0.0)
        res.event_displacement_bps.append(abs(m.observed_move_bps))
        res.gross_bps.append(m.recoverable_bps)
        res.net_bps.append(net)
        res.fractions.append(m.capture_fraction)
    return res



# ══════════════════════════════════════════════════════════════════════════
def _stats(xs: Sequence[float]) -> Dict[str, Any]:
    n = len(xs)
    if not n:
        return {"n": 0, "mean": None, "stderr": None, "t_stat": None}
    mean = sum(xs) / n
    if n < 2:
        return {"n": n, "mean": mean, "stderr": None, "t_stat": None}
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    se = (var / n) ** 0.5
    return {"n": n, "mean": mean, "stderr": se,
            "t_stat": mean / se if se > 0 else None}


def regime_analysis(configs: Sequence[ConfigResult]) -> Dict[str, Any]:
    """Decoupe les resultats par regime observable.

    « Ca marche en moyenne » n'est pas un resultat robuste : un effet
    entierement porte par un seul regime est une dependance de regime, pas une
    relation. Le decoupage est fait sur des grandeurs OBSERVABLES A LA
    DECISION (heure, spread, amplitude du deplacement), jamais sur l'issue.
    """
    rows: List[Tuple[int, float, float, float]] = []
    seen: set = set()
    for c in configs:
        for ts, sp, disp, net in zip(c.event_ts, c.event_spread_bps,
                                     c.event_displacement_bps, c.net_bps):
            k = (c.inst_id, ts)
            if k in seen:
                continue                      # meme dedoublonnage qu'ailleurs
            seen.add(k)
            rows.append((ts, sp, disp, net))
    if len(rows) < 6:
        return {"n": len(rows),
                "note": "echantillon trop faible pour decouper par regime"}

    out: Dict[str, Any] = {"n_events": len(rows), "overall": _stats(
        [r[3] for r in rows])}

    def terciles(key_idx: int, label: str) -> Dict[str, Any]:
        vals = sorted(r[key_idx] for r in rows)
        lo = vals[len(vals) // 3]
        hi = vals[2 * len(vals) // 3]
        buckets = {"bas": [], "moyen": [], "haut": []}
        for r in rows:
            v = r[key_idx]
            buckets["bas" if v <= lo else ("haut" if v >= hi else "moyen")
                    ].append(r[3])
        return {"cutoffs": [lo, hi],
                **{k: _stats(v) for k, v in buckets.items()}}

    out["by_spread"] = terciles(1, "spread")
    out["by_displacement"] = terciles(2, "amplitude")
    by_hour: Dict[int, List[float]] = {}
    for ts, _sp, _d, net in rows:
        by_hour.setdefault(time.gmtime(ts / 1000).tm_hour, []).append(net)
    out["by_hour_utc"] = {str(h): _stats(v)
                          for h, v in sorted(by_hour.items()) if len(v) >= 5}
    # Un effet concentre sur un seul regime est nomme comme tel.
    sub = [v for k, v in out["by_spread"].items()
           if isinstance(v, dict) and v.get("mean") is not None]
    if sub:
        pos = [v for v in sub if v["mean"] > 0]
        out["concentrated_in_one_spread_regime"] = len(pos) == 1
    return out


def decay_analysis(configs: Sequence[ConfigResult], n_buckets: int = 6
                   ) -> Dict[str, Any]:
    """Le resultat se deplace-t-il dans le temps ?

    Une inefficience qui s'eteint pendant la fenetre d'observation n'est pas
    la meme chose qu'une inefficience persistante, et le systeme ne doit pas
    les confondre.
    """
    rows: List[Tuple[int, float]] = []
    seen: set = set()
    for c in configs:
        for ts, net in zip(c.event_ts, c.net_bps):
            k = (c.inst_id, ts)
            if k in seen:
                continue
            seen.add(k)
            rows.append((ts, net))
    if len(rows) < n_buckets * 3:
        return {"n": len(rows),
                "note": f"moins de {n_buckets * 3} evenements : decroissance "
                        "non mesurable"}
    rows.sort(key=lambda r: r[0])
    lo, hi = rows[0][0], rows[-1][0]
    width = max(1, (hi - lo) // n_buckets)
    buckets: Dict[int, List[float]] = {}
    for ts, net in rows:
        buckets.setdefault(min(n_buckets - 1, (ts - lo) // width), []).append(net)
    series = [{"bucket": int(b), "from_min": round((b * width) / 60000, 1),
               **_stats(v)} for b, v in sorted(buckets.items())]
    means = [s["mean"] for s in series if s["mean"] is not None]
    trend = None
    if len(means) >= 3:
        n = len(means)
        xs = list(range(n))
        mx, my = sum(xs) / n, sum(means) / n
        den = sum((x - mx) ** 2 for x in xs)
        trend = (sum((x - mx) * (y - my) for x, y in zip(xs, means)) / den
                 if den else None)
    return {"n_events": len(rows), "buckets": series, "slope_per_bucket": trend,
            "note": ("pente negative = le resultat se degrade pendant la "
                     "fenetre. Une fenetre de quelques heures ne permet PAS "
                     "de distinguer une decroissance d'une fluctuation.")}


# ══════════════════════════════════════════════════════════════════════════
def run(obs_path: Path, latency_ms: int = DEFAULT_LATENCY_MS,
        max_instruments: int = 15,
        json_out: Optional[Path] = None) -> Dict[str, Any]:
    t_start = time.time()
    report: Dict[str, Any] = {"started_at": utc_now_iso(),
                              "observatory_file": str(obs_path),
                              "mode": "DISCOVERY+PAPER — aucun ordre reel"}

    # ── 1. DONNEES ─────────────────────────────────────────────────────────
    print("=" * 84); print("1. DONNEES"); print("=" * 84)
    meta, recs = load_snapshots(obs_path)
    if not recs:
        print("aucun instantane valide : rien a conclure")
        return {**report, "verdict": Verdict.INSUFFICIENT_DATA.value}
    specs_by_id = {d["inst_id"]: InstrumentSpec.from_dict(d)
                   for d in (meta.get("instruments") or [])}
    inst_ids = sorted({r["i"] for r in recs})[:max_instruments]
    ts_all = [r["ts"] for r in recs]
    start_ms, end_ms = min(ts_all), max(ts_all)
    dur_h = (end_ms - start_ms) / 3_600_000.0
    print(f"fichier tronque (collecte en cours ou interrompue) : {meta.get('truncated')}")
    print(f"instantanes valides : {len(recs):,} | instruments : {len(inst_ids)}")
    print(f"fenetre : {dur_h:.2f} h  ({(end_ms-start_ms)/1000:.0f} s)")
    report["data"] = {"n_snapshots": len(recs), "n_instruments": len(inst_ids),
                      "window_hours": dur_h, "truncated": meta.get("truncated"),
                      "snapshot_ms": meta.get("snapshot_ms")}

    # ── 2. PROTOCOLE ───────────────────────────────────────────────────────
    print(); print("=" * 84); print("2. PROTOCOLE DE DONNEES (decoupage temporel immuable)")
    print("=" * 84)
    proto = DataProtocol(start_ms, end_ms)
    for s in (Split.DISCOVERY, Split.DEVELOPMENT, Split.VALIDATION,
              Split.FINAL_HOLDOUT):
        lo, hi = proto.bounds(s)
        print(f"  {s.value:<15} {(hi-lo)/60000:7.1f} min"
              f"   {'(peut servir a choisir)' if s.may_inform_choices else '(ne choisit rien)'}")
    report["protocol"] = proto.to_dict()

    seriess = {i: SnapshotSeries.from_records(i, recs) for i in inst_ids}
    seriess = {i: s for i, s in seriess.items() if len(s) > 100 and i in specs_by_id}
    print(f"  instruments exploitables : {len(seriess)}")

    # ── 3. DISCOVERY : balayage, chaque configuration est un ESSAI ─────────
    print(); print("=" * 84)
    print("3. DISCOVERY — balayage des configurations (chaque essai est compte)")
    print("=" * 84)
    ledger = TrialLedger()
    disc_lo, disc_hi = proto.bounds(Split.DISCOVERY)
    discovery: Dict[str, ConfigResult] = {}
    for inst_id, series in seriess.items():
        spec = specs_by_id[inst_id]
        for lb in LOOKBACKS_MS:
            for hz in HORIZONS_MS:
                for thr in THRESHOLD_SPREADS:
                    for bet in BETS:
                        r = run_config(spec, series, disc_lo, disc_hi, lb, hz,
                                       thr, Split.DISCOVERY.value, latency_ms,
                                       bet=bet)
                        discovery[r.key] = r
                        ledger.record(r.key,
                                      sharpe_ratio(r.net_bps)
                                      if len(r.net_bps) > 1 else None,
                                      r.n_resolved,
                                      "RESOLVED" if r.n_resolved else "NO_DATA")
    n_ev = sum(r.n_events for r in discovery.values())
    n_res = sum(r.n_resolved for r in discovery.values())
    print(f"configurations essayees : {len(discovery):,}")
    print(f"evenements declenches   : {n_ev:,}")
    print(f"evenements resolus      : {n_res:,}")
    print(f"essais comptes          : {ledger.n_trials:,}"
          f" | dispersion des Sharpe : {ledger.sharpe_dispersion()}")
    refus: Dict[str, int] = {}
    for r in discovery.values():
        for k, v in r.refusals.items():
            refus[k] = refus.get(k, 0) + v
    if refus:
        print("refus (aucun chiffre fabrique a la place) :")
        for k, v in sorted(refus.items(), key=lambda kv: -kv[1])[:6]:
            print(f"   {v:>7,}  {k}")
    by_bet: Dict[str, Dict[str, Any]] = {}
    for b in BETS:
        nets = [x for r in discovery.values() if r.bet == b for x in r.net_bps]
        gross = [x for r in discovery.values() if r.bet == b for x in r.gross_bps]
        by_bet[b] = {"n": len(nets),
                     "mean_net_bps": sum(nets) / len(nets) if nets else None,
                     "mean_gross_bps": sum(gross) / len(gross) if gross else None,
                     "share_gross_positive":
                         (sum(1 for x in gross if x > 0) / len(gross))
                         if gross else None}
    print("\npar PARI (la continuation est la negation de la reversion) :")
    for b, st in by_bet.items():
        if st["n"]:
            print(f"  {b:<13} N={st['n']:>6,}  brut {st['mean_gross_bps']:+.4f} bps"
                  f"  net {st['mean_net_bps']:+.4f} bps"
                  f"  part brut>0 {st['share_gross_positive']:.1%}")
    report["by_bet"] = by_bet
    report["discovery"] = {
        "n_configurations": len(discovery), "n_events": n_ev,
        "n_resolved": n_res, "refusals": refus,
        "n_trials": ledger.n_trials,
        "trial_sharpe_dispersion": ledger.sharpe_dispersion()}

    # Fraction de reversion agregee — la grandeur que le projet cherchait.
    # DEDOUBLONNAGE OBLIGATOIRE : 144 configurations balayent les MEMES
    # instants. Empiler leurs mesures compterait le meme evenement des dizaines
    # de fois et multiplierait artificiellement la significativite. On ne
    # retient qu'une mesure par (instrument, instant).
    seen_events: set = set()
    all_fr: List[float] = []
    all_gross: List[float] = []
    for r in discovery.values():
        for ts, fr, gr in zip(r.event_ts, r.fractions, r.gross_bps):
            k = (r.inst_id, ts)
            if k in seen_events:
                continue
            seen_events.add(k)
            all_fr.append(fr)
            all_gross.append(gr)
    if all_fr:
        mean_fr = sum(all_fr) / len(all_fr)
        var = sum((x - mean_fr) ** 2 for x in all_fr) / max(1, len(all_fr) - 1)
        stderr = (var / len(all_fr)) ** 0.5
        gm = sum(all_gross) / len(all_gross)
        gvar = sum((x - gm) ** 2 for x in all_gross) / max(1, len(all_gross) - 1)
        gse = (gvar / len(all_gross)) ** 0.5
        print(f"\nFRACTION DE REVERSION mesuree : {mean_fr:+.4f} "
              f"+/- {stderr:.4f} (N={len(all_fr):,})")
        print(f"  t = {mean_fr/stderr if stderr else float('nan'):+.2f}"
              "   (|t| < 3 : indiscernable de zero)")
        print(f"MOUVEMENT RECUPERABLE brut   : {gm:+.4f} +/- {gse:.4f} bps")
        print("  NB : l'echantillonnage AU FRANCHISSEMENT est non biaise ; "
              "mesurer sur grille sous-estimerait d'environ 30 %.")
        report["reversion_fraction"] = {
            "mean": mean_fr, "stderr": stderr, "n": len(all_fr),
            "n_before_dedup": sum(len(r.fractions) for r in discovery.values()),
            "dedup_note": ("une mesure par (instrument, instant) : les "
                           "configurations balayent les memes evenements"),
            "t_stat": mean_fr / stderr if stderr else None,
            "gross_recoverable_bps_mean": gm, "gross_stderr": gse}
    else:
        report["reversion_fraction"] = None

    # ── 4. DEVELOPMENT : SEUL segment ou l'on a le droit de choisir ────────
    print(); print("=" * 84)
    print("4. DEVELOPMENT — selection de la configuration")
    print("=" * 84)
    dev_lo, dev_hi = proto.bounds(Split.DEVELOPMENT)
    dev: Dict[str, ConfigResult] = {}
    for key, dr in discovery.items():
        if dr.n_resolved < 5:
            continue
        spec = specs_by_id[dr.inst_id]
        r = run_config(spec, seriess[dr.inst_id], dev_lo, dev_hi, dr.lookback_ms,
                       dr.horizon_ms, dr.threshold_spreads,
                       Split.DEVELOPMENT.value, latency_ms, bet=dr.bet)
        dev[key] = r
        ledger.record(key + "|dev", sharpe_ratio(r.net_bps)
                      if len(r.net_bps) > 1 else None, r.n_resolved, "DEV")
    ranked = sorted(
        [r for r in dev.values() if r.n_resolved >= 5 and r.mean(r.net_bps) is not None],
        key=lambda r: -(r.mean(r.net_bps) or -1e9))
    print(f"configurations avec >= 5 evenements en DEVELOPMENT : {len(ranked)}")
    if ranked:
        print(f"\n{'configuration':<46}{'N':>5}{'net_bps':>10}{'sharpe':>9}")
        print("-" * 72)
        for r in ranked[:8]:
            sh = sharpe_ratio(r.net_bps)
            print(f"  {r.key:<44}{r.n_resolved:>5}{r.mean(r.net_bps):>10.3f}"
                  f"{(f'{sh:.3f}' if sh is not None else 'n/a'):>9}")
    selected = ranked[0] if ranked and (ranked[0].mean(ranked[0].net_bps) or 0) > 0 else None
    if selected is None:
        print("\nAUCUNE configuration n'est nette positive en DEVELOPMENT.")
        print("Le holdout ne sera PAS ouvert : il n'y a rien a valider.")
    else:
        print(f"\nselectionnee : {selected.key} "
              f"(net {selected.mean(selected.net_bps):+.3f} bps, "
              f"N={selected.n_resolved})")
    report["development"] = {
        "n_candidates": len(ranked),
        "top": [r.to_dict() for r in ranked[:10]],
        "selected": selected.key if selected else None}

    # ── 5. VALIDATION ──────────────────────────────────────────────────────
    print(); print("=" * 84); print("5. VALIDATION (aucun nouveau choix)"); print("=" * 84)
    val_result = None
    if selected is not None:
        vlo, vhi = proto.bounds(Split.VALIDATION)
        val_result = run_config(specs_by_id[selected.inst_id],
                                seriess[selected.inst_id], vlo, vhi,
                                selected.lookback_ms, selected.horizon_ms,
                                selected.threshold_spreads,
                                Split.VALIDATION.value, latency_ms,
                                bet=selected.bet)
        print(json.dumps(val_result.to_dict(), indent=1, ensure_ascii=False))
        report["validation"] = val_result.to_dict()
    else:
        print("sans configuration selectionnee, rien a valider.")
        report["validation"] = None

    # ── 6. FINAL HOLDOUT — ouvert UNE FOIS ────────────────────────────────
    print(); print("=" * 84); print("6. FINAL HOLDOUT"); print("=" * 84)
    hold_result = None
    val_ok = (val_result is not None and val_result.n_resolved >= 5
              and (val_result.mean(val_result.net_bps) or -1) > 0)
    if selected is not None and val_ok:
        proto.open_holdout(f"validation finale de {selected.key}")
        hlo, hhi = proto.bounds(Split.FINAL_HOLDOUT)
        hold_result = run_config(specs_by_id[selected.inst_id],
                                 seriess[selected.inst_id], hlo, hhi,
                                 selected.lookback_ms, selected.horizon_ms,
                                 selected.threshold_spreads,
                                 Split.FINAL_HOLDOUT.value, latency_ms,
                                 bet=selected.bet)
        print(json.dumps(hold_result.to_dict(), indent=1, ensure_ascii=False))
        report["final_holdout"] = hold_result.to_dict()
    else:
        why = ("aucune configuration selectionnee" if selected is None
               else "la configuration selectionnee n'a pas survecu a VALIDATION")
        print(f"HOLDOUT NON OUVERT — {why}.")
        print("Un holdout ouvert pour rattraper un echec anterieur n'est plus "
              "un holdout.")
        report["final_holdout"] = {"opened": False, "why": why}

    # ── 7. TESTS MULTIPLES ────────────────────────────────────────────────
    print(); print("=" * 84); print("7. COMPTABILITE DES ESSAIS"); print("=" * 84)
    disp = ledger.sharpe_dispersion()
    print(f"essais totaux : {ledger.n_trials:,}  (tout essai compte, "
          "y compris abandonne ou perdant)")
    print(f"dispersion des Sharpe entre essais : "
          f"{disp if disp is None else round(disp, 4)}")
    ds = None
    if selected is not None and len(selected.net_bps) > 2:
        d = deflated_sharpe(selected.net_bps, ledger.n_trials, disp)
        if d is not None:
            ds = d.to_dict()
            print(f"\nSharpe observe   : {d.observed_sharpe:+.4f}")
            print(f"Seuil de CHANCE  : {d.benchmark_sharpe:+.4f}"
                  f"   (ce qu'on obtiendrait par hasard sur {d.n_trials:,} essais)")
            print(f"Sharpe deflate   : p = {d.p_value}"
                  f" | significatif : {d.significant}")
            print(f"  {d.note}")
    matrix = [r.net_bps for r in discovery.values() if len(r.net_bps) >= 32]
    pbo = None
    if len(matrix) >= 2:
        n = min(len(m) for m in matrix)
        pbo = probability_of_backtest_overfitting([m[:n] for m in matrix], n_blocks=8)
        if pbo:
            print(f"\nPBO = {pbo['pbo']:.3f} sur {pbo['n_configurations']} "
                  f"configurations, {pbo['n_partitions']} partitions")
            print(f"  {pbo['note']}")
    else:
        print("\nPBO non calculable : moins de 2 configurations avec >= 32 "
              "evenements. Ce n'est pas un succes, c'est une absence de mesure.")
    report["multiple_testing"] = {"n_trials": ledger.n_trials,
                                  "sharpe_dispersion": disp,
                                  "deflated_sharpe": ds, "pbo": pbo}

    # ── 7bis. REGIMES ET DECROISSANCE ─────────────────────────────────────
    print(); print("=" * 84)
    print("7bis. REGIMES ET DECROISSANCE"); print("=" * 84)
    reg = regime_analysis(list(discovery.values()))
    dec = decay_analysis(list(discovery.values()))
    if reg.get("n_events"):
        ov = reg["overall"]
        print(f"ensemble : net {ov['mean']:+.4f} bps "
              f"(N={ov['n']:,}, t={ov['t_stat']:+.2f})" if ov["t_stat"] is not None
              else f"ensemble : N={ov['n']}")
        for label, key in (("par spread", "by_spread"),
                           ("par amplitude", "by_displacement")):
            b = reg.get(key, {})
            cells = []
            for name in ("bas", "moyen", "haut"):
                st = b.get(name, {})
                cells.append(f"{name}={st.get('mean'):+.3f}"
                             if st.get("mean") is not None else f"{name}=n/a")
            print(f"  {label:<16} " + "  ".join(cells))
        if reg.get("concentrated_in_one_spread_regime"):
            print("  ATTENTION : effet concentre sur UN SEUL regime de spread "
                  "— dependance de regime, pas relation")
    else:
        print(reg.get("note"))
    if dec.get("buckets"):
        print(f"decroissance : pente {dec['slope_per_bucket']} bps/tranche "
              f"sur {len(dec['buckets'])} tranches")
        print(f"  {dec['note']}")
    else:
        print(dec.get("note"))
    report["regimes"] = reg
    report["decay"] = dec

    # ── 8. ENTONNOIR : OU MEURT L'EDGE ────────────────────────────────────
    print(); print("=" * 84); print("8. OU MEURT L'EDGE"); print("=" * 84)
    n_gross_pos = sum(1 for r in discovery.values() for g in r.gross_bps if g > 0)
    n_net_pos = sum(1 for r in discovery.values() for x in r.net_bps if x > 0)
    stages: List[Tuple[str, int]] = [
        ("evenements declenches", n_ev),
        ("mesurables causalement", n_res),
        ("mouvement recuperable > 0", n_gross_pos),
        ("net de couts > 0", n_net_pos),
        ("configurations nettes positives en DEVELOPMENT",
         sum(1 for r in ranked if (r.mean(r.net_bps) or 0) > 0)),
        ("survit a VALIDATION", 1 if val_ok else 0),
        ("positif sur FINAL_HOLDOUT",
         1 if (hold_result and (hold_result.mean(hold_result.net_bps) or -1) > 0) else 0),
    ]
    base = max(1, n_ev)
    for label, n in stages:
        print(f"  {n:>9,}  {100.0*n/base:6.2f}%   {label}")
    report["funnel"] = [{"stage": s, "n": n, "share_of_events": n / base}
                        for s, n in stages]

    # ── 9. VERDICT ────────────────────────────────────────────────────────
    print(); print("=" * 84); print("9. VERDICT"); print("=" * 84)
    ps = ProofStandard()
    ps.assess(Condition.CAUSALITY, True,
              "decision a T0 sur donnees <= T0 ; entree a T0+latence ; "
              "book_at() ne peut pas retourner le futur")
    ps.assess(Condition.NO_LEAKAGE, True,
              "segments temporels disjoints, holdout le plus recent")
    ps.assess(Condition.REALISTIC_FILL, True,
              "carnets d'entree et de sortie DISTINCTS ; impact non mesurable "
              "= evenement non resolu, jamais cout nul")
    ps.assess(Condition.COSTS, True,
              f"spread et impact OBSERVES sur carnet ; frais MAJORANT "
              f"{FEE_BPS_PER_LEG} bps/jambe (aucun frais OBSERVED sans compte)")
    ps.assess(Condition.NO_CRITICAL_UNKNOWN, False,
              "slippage d'execution, probabilite de fill et latence "
              "aller-retour restent UNKNOWN : non mesurables sans ordres reels")
    ps.assess(Condition.EXECUTION_REALISM, True,
              "traversee du carnet reel aux deux instants")
    n_sel = selected.n_resolved if selected else 0
    ps.assess(Condition.ENOUGH_TRADES, n_sel >= 30,
              f"{n_sel} evenements resolus sur la configuration selectionnee "
              f"(minimum 30)")
    ps.assess(Condition.NET_POSITIVE,
              bool(selected and (selected.mean(selected.net_bps) or -1) > 0),
              f"net moyen {selected.mean(selected.net_bps):+.4f} bps" if selected
              else "aucune configuration nette positive")
    ps.assess(Condition.OUT_OF_SAMPLE,
              bool(hold_result and (hold_result.mean(hold_result.net_bps) or -1) > 0),
              "holdout non ouvert" if hold_result is None else
              f"holdout net {hold_result.mean(hold_result.net_bps)}")
    ps.assess(Condition.TEMPORAL_STABILITY, bool(val_ok),
              "positif en DEVELOPMENT et en VALIDATION" if val_ok
              else "non confirme sur un second segment")
    ps.assess(Condition.CAPACITY, None,
              f"non mesuree : l'observatoire n'enregistre que "
              f"{meta.get('depth_levels')} niveaux ; au-dela l'impact est UNKNOWN")
    ps.assess(Condition.RISK_MEASURED, bool(selected and len(selected.net_bps) > 1),
              "dispersion des resultats par evenement disponible" if selected
              else "aucun echantillon")
    ps.assess(Condition.DRAWDOWN_MEASURED, None,
              "non mesure : les evenements ne forment pas une serie de "
              "positions consecutives dans ce protocole")
    ps.assess(Condition.MULTIPLE_TESTING,
              bool(ds and ds.get("significant")),
              (f"Sharpe deflate p={ds.get('p_value')}" if ds
               else f"{ledger.n_trials} essais ; deflation non concluante"))
    verdict = ps.verdict()
    report["proof"] = ps.to_dict()
    report["verdict"] = verdict.value
    print(f"\n{'CONDITION':<66}{'OK'}")
    print("-" * 72)
    for c in Condition:
        print(f"  {c.value[:62]:<64}{'oui' if ps.satisfied.get(c) else 'NON'}")
    print(f"\nconditions satisfaites : {ps.to_dict()['n_satisfied']}/{len(Condition)}")
    print(f"\n>>> VERDICT : {verdict.value}")
    report["finished_at"] = utc_now_iso()
    report["runtime_s"] = round(time.time() - t_start, 1)
    if json_out:
        Path(json_out).write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                             default=str), encoding="utf-8")
        print(f"\nrapport -> {json_out}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Experience finale (PAPER uniquement)")
    ap.add_argument("--observatory", type=Path, required=True)
    ap.add_argument("--latency-ms", type=int, default=DEFAULT_LATENCY_MS)
    ap.add_argument("--instruments", type=int, default=15)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    run(a.observatory, latency_ms=a.latency_ms, max_instruments=a.instruments,
        json_out=a.json)


if __name__ == "__main__":
    main()
