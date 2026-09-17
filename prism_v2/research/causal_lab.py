"""Laboratoire causal — mesurer la CONSEQUENCE d'un phenomene, pas la predire.

LA QUESTION. Le projet a etabli qu'une capture brute egale au deplacement
observe PRIS EN ENTIER est un majorant, jamais une capture (defaut n8). Ce
module mesure la grandeur manquante :

    FRACTION DE REVERSION = mouvement recuperable / mouvement observe

CAUSALITE. La DECISION a T0 n'utilise que des donnees <= T0 : le deplacement
observe se mesure entre T0-lookback et T0. Les mesures posterieures servent a
caracteriser la CONSEQUENCE ex-post du phenomene ; elles n'entrent jamais dans
la decision. C'est la distinction entre mesurer un effet et le predire.

L'ENTREE n'est jamais a T0 : elle est a T0 + latence, sur le carnet
REELLEMENT disponible a cet instant. Le mouvement recuperable se mesure donc
depuis le prix d'entree, pas depuis le prix de l'evenement — sinon on
s'attribuerait le trajet parcouru pendant la latence.

CE QUI EST REFUSE PLUTOT QU'ESTIME
  - deplacement observe nul ou sous la resolution : pas de denominateur ;
  - fenetre ne couvrant pas l'horizon : aucun chiffre ;
  - carnet invalide a l'entree ou a la sortie : aucun chiffre.
"""
from __future__ import annotations

import enum
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Grille temporelle par defaut, en millisecondes. Elle couvre le regime de
#: microstructure (10 ms) jusqu'au regime de portage court (15 min).
DEFAULT_GRID_MS: Tuple[int, ...] = (
    10, 25, 50, 100, 250, 500, 1_000, 2_000, 5_000,
    10_000, 30_000, 60_000, 300_000, 900_000)

#: Les deux paris possibles apres un deplacement observe. Tester uniquement la
#: reversion reviendrait a supposer la reponse : la continuation est sa
#: negation, et elle doit etre soumise au meme protocole.
BET_REVERSION = "REVERSION"
BET_CONTINUATION = "CONTINUATION"


class CaptureOutcome(str, enum.Enum):
    MEASURED = "MEASURED"
    NO_DISPLACEMENT = "NO_DISPLACEMENT"          # denominateur nul ou sous la resolution
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"            # la fenetre ne couvre pas l'horizon
    NO_BOOK = "NO_BOOK"                          # carnet absent ou invalide
    UNRESOLVABLE_DELTA = "UNRESOLVABLE_DELTA"    # delta sous la cadence d'instantane


@dataclass
class SnapshotSeries:
    """Serie d'instantanes d'observatoire pour UN instrument.

    `at(ts)` ne peut pas retourner un instantane futur : c'est structurel,
    exactement comme `EventTimeline.book_at`.
    """

    inst_id: str
    _ts: List[int] = field(default_factory=list)
    _recs: List[Dict[str, Any]] = field(default_factory=list)
    #: (calculee, valeur) — memorisation de la cadence, voir cadence_ms().
    _cadence_cached: Optional[Tuple[bool, Optional[float]]] = None

    @classmethod
    def from_records(cls, inst_id: str,
                     records: Sequence[Dict[str, Any]]) -> "SnapshotSeries":
        pairs = [(r["ts"], r) for r in records
                 if r.get("i") == inst_id and r.get("ok") and r.get("ts")]
        pairs.sort(key=lambda p: p[0])
        s = cls(inst_id=inst_id)
        s._ts = [p[0] for p in pairs]
        s._recs = [p[1] for p in pairs]
        return s

    def __len__(self) -> int:
        return len(self._recs)

    @property
    def first_ts(self) -> Optional[int]:
        return self._ts[0] if self._ts else None

    @property
    def last_ts(self) -> Optional[int]:
        return self._ts[-1] if self._ts else None

    def covers(self, ts: int) -> bool:
        return bool(self._ts) and self._ts[0] <= ts <= self._ts[-1]

    def at(self, ts: int) -> Optional[Dict[str, Any]]:
        if not self._ts:
            return None
        i = bisect_right(self._ts, ts) - 1
        return self._recs[i] if i >= 0 else None

    def index_at(self, ts: int) -> int:
        return bisect_right(self._ts, ts) - 1

    def cadence_ms(self) -> Optional[float]:
        """Intervalle median entre instantanes : borne de resolution.

        MEMORISE. La serie est immuable, donc cette valeur l'est aussi. La
        recalculer a chaque appel triait les 93 600 ecarts d'une collecte de
        six heures une fois par mesure : 440 des 450 secondes d'un profil,
        pour un resultat identique a chaque fois.
        """
        if self._cadence_cached is None:
            if len(self._ts) < 2:
                self._cadence_cached = (False, None)
            else:
                gaps = sorted(b - a for a, b in zip(self._ts, self._ts[1:]))
                n = len(gaps)
                self._cadence_cached = (
                    True,
                    gaps[n // 2] if n % 2
                    else (gaps[n // 2 - 1] + gaps[n // 2]) / 2)
        return self._cadence_cached[1]

    def is_resolvable(self, delta_ms: int) -> bool:
        c = self.cadence_ms()
        return c is not None and delta_ms >= c

    # ── grandeurs derivees d'un instantane ────────────────────────────────
    @staticmethod
    def mid(rec: Optional[Dict[str, Any]]) -> Optional[float]:
        if not rec or not rec.get("b") or not rec.get("a"):
            return None
        return (rec["b"][0][0] + rec["a"][0][0]) / 2.0

    @staticmethod
    def spread_bps(rec: Optional[Dict[str, Any]]) -> Optional[float]:
        m = SnapshotSeries.mid(rec)
        if m is None or m <= 0:
            return None
        return (rec["a"][0][0] - rec["b"][0][0]) / m * 10_000.0

    def mid_at(self, ts: int) -> Optional[float]:
        return self.mid(self.at(ts))


@dataclass(frozen=True)
class CaptureMeasurement:
    """Consequence mesuree d'un deplacement. Aucun champ n'est une prediction."""

    inst_id: str
    t0_ms: int
    lookback_ms: int
    latency_ms: int
    horizon_ms: int
    outcome: CaptureOutcome
    #: Deplacement OBSERVE entre T0-lookback et T0, en bps signes.
    observed_move_bps: Optional[float] = None
    #: Sens teste : +1 si l'on parie sur un mouvement a la hausse.
    reversion_sign: Optional[int] = None
    #: Quel pari a ete teste : REVERSION ou CONTINUATION.
    bet: str = BET_REVERSION
    #: Derive pendant la latence, en bps SIGNES dans le sens du pari
    #: (negatif = le prix a fui avant l'entree).
    latency_drift_bps: Optional[float] = None
    #: Mouvement recuperable depuis le prix d'ENTREE, dans le sens du pari.
    recoverable_bps: Optional[float] = None
    #: recoverable / |observed| — le coeur de la mesure.
    capture_fraction: Optional[float] = None
    #: Excursions depuis l'entree, dans le sens du pari, sur [entree, sortie].
    mfe_bps: Optional[float] = None
    mae_bps: Optional[float] = None
    time_to_peak_ms: Optional[int] = None
    #: Le prix a-t-il depasse son point de depart (sur-reversion) ?
    overshoot: Optional[bool] = None
    #: Le prix a-t-il continue dans le sens du deplacement (pas de reversion) ?
    continuation: Optional[bool] = None
    spread_at_entry_bps: Optional[float] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["outcome"] = self.outcome.value
        return d


def measure_capture(series: SnapshotSeries, t0_ms: int, lookback_ms: int,
                    latency_ms: int, horizon_ms: int,
                    min_displacement_bps: float = 0.0,
                    bet: str = BET_REVERSION) -> CaptureMeasurement:
    """Mesure la consequence d'un deplacement observe a T0.

    Etapes, dans cet ordre strict :
      1. deplacement observe sur [T0-lookback, T0]  — passe uniquement ;
      2. sens du pari = INVERSE du deplacement (hypothese de reversion) ;
      3. entree a T0+latence, sur le carnet reellement disponible ;
      4. sortie a T0+latence+horizon ;
      5. mouvement recuperable depuis le prix d'ENTREE ;
      6. fraction = recuperable / |deplacement observe|.
    """
    base = dict(inst_id=series.inst_id, t0_ms=t0_ms, lookback_ms=lookback_ms,
                latency_ms=latency_ms, horizon_ms=horizon_ms)
    entry_ts = t0_ms + latency_ms
    exit_ts = entry_ts + horizon_ms

    if not series.is_resolvable(horizon_ms):
        return CaptureMeasurement(
            outcome=CaptureOutcome.UNRESOLVABLE_DELTA, **base,
            detail=f"horizon {horizon_ms}ms sous la cadence d'instantane "
                   f"({series.cadence_ms()}ms) : mesurerait un instantane "
                   "contre lui-meme")
    if not series.covers(t0_ms - lookback_ms) or not series.covers(exit_ts):
        return CaptureMeasurement(
            outcome=CaptureOutcome.OUTSIDE_WINDOW, **base,
            detail="la fenetre collectee ne couvre pas [T0-lookback, sortie]")

    ref_mid = series.mid_at(t0_ms - lookback_ms)
    t0_mid = series.mid_at(t0_ms)
    entry_rec = series.at(entry_ts)
    entry_mid = series.mid(entry_rec)
    exit_mid = series.mid_at(exit_ts)
    if None in (ref_mid, t0_mid, entry_mid, exit_mid) or ref_mid <= 0:
        return CaptureMeasurement(outcome=CaptureOutcome.NO_BOOK, **base,
                                  detail="carnet absent ou invalide a un instant requis")

    observed = (t0_mid - ref_mid) / ref_mid * 10_000.0
    if abs(observed) <= max(min_displacement_bps, 1e-9):
        return CaptureMeasurement(
            outcome=CaptureOutcome.NO_DISPLACEMENT, **base,
            observed_move_bps=observed,
            detail=f"deplacement {observed:.4f} bps : pas de denominateur "
                   "exploitable (on ne divise pas par un mouvement nul)")

    # REVERSION : si le prix a monte, on teste la baisse.
    # CONTINUATION : on parie que le mouvement se poursuit.
    if bet == BET_CONTINUATION:
        sign = 1 if observed > 0 else -1
    else:
        sign = -1 if observed > 0 else 1

    # Ce que la latence a deja coute AVANT l'entree, dans le sens du pari.
    drift = sign * (entry_mid - t0_mid) / t0_mid * 10_000.0

    # Mouvement recuperable, mesure depuis le prix d'ENTREE.
    recoverable = sign * (exit_mid - entry_mid) / entry_mid * 10_000.0
    fraction = recoverable / abs(observed)

    # Excursions sur [entree, sortie], dans le sens du pari.
    i0, i1 = series.index_at(entry_ts), series.index_at(exit_ts)
    mfe = mae = 0.0
    t_peak: Optional[int] = None
    for i in range(max(0, i0), min(i1, len(series) - 1) + 1):
        rec = series._recs[i]
        m = series.mid(rec)
        if m is None:
            continue
        move = sign * (m - entry_mid) / entry_mid * 10_000.0
        if move > mfe:
            mfe, t_peak = move, rec["ts"] - entry_ts
        if move < mae:
            mae = move

    # Sur-reversion : le prix a depasse son point de depart d'avant le mouvement.
    overshoot = (sign * (exit_mid - ref_mid) / ref_mid * 10_000.0) > 0
    # Continuation : le prix a poursuivi le deplacement au lieu de revenir.
    continuation = recoverable < 0

    return CaptureMeasurement(
        outcome=CaptureOutcome.MEASURED, **base, bet=bet,
        observed_move_bps=observed, reversion_sign=sign,
        latency_drift_bps=drift, recoverable_bps=recoverable,
        capture_fraction=fraction, mfe_bps=mfe, mae_bps=mae,
        time_to_peak_ms=t_peak, overshoot=overshoot, continuation=continuation,
        spread_at_entry_bps=series.spread_bps(entry_rec))


# ══════════════════════════════════════════════════════════════════════════
def _quantile(vals: Sequence[float], q: float) -> Optional[float]:
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * (len(v) - 1)))]


def summarise_captures(measurements: Sequence[CaptureMeasurement]) -> Dict[str, Any]:
    """Synthese d'une serie de mesures. Les refus sont comptes, pas caches."""
    by_outcome: Dict[str, int] = {}
    for m in measurements:
        by_outcome[m.outcome.value] = by_outcome.get(m.outcome.value, 0) + 1
    done = [m for m in measurements if m.outcome is CaptureOutcome.MEASURED]
    fr = [m.capture_fraction for m in done if m.capture_fraction is not None]
    rec = [m.recoverable_bps for m in done if m.recoverable_bps is not None]
    out: Dict[str, Any] = {
        "n_attempts": len(measurements),
        "by_outcome": dict(sorted(by_outcome.items(), key=lambda kv: -kv[1])),
        "n_measured": len(done),
    }
    if not fr:
        out["capture_fraction"] = None
        out["note"] = "aucune mesure exploitable : aucune conclusion"
        return out
    n = len(fr)
    mean = sum(fr) / n
    var = sum((x - mean) ** 2 for x in fr) / (n - 1) if n > 1 else 0.0
    out["capture_fraction"] = {
        "n": n, "mean": mean, "std": math.sqrt(var),
        "median": _quantile(fr, 0.5), "p05": _quantile(fr, 0.05),
        "p25": _quantile(fr, 0.25), "p75": _quantile(fr, 0.75),
        "p95": _quantile(fr, 0.95),
        "share_positive": sum(1 for x in fr if x > 0) / n,
    }
    rmean = sum(rec) / len(rec)
    rvar = sum((x - rmean) ** 2 for x in rec) / (len(rec) - 1) if len(rec) > 1 else 0.0
    out["recoverable_bps"] = {
        "n": len(rec), "mean": rmean, "std": math.sqrt(rvar),
        "median": _quantile(rec, 0.5), "p05": _quantile(rec, 0.05),
        "p95": _quantile(rec, 0.95),
        # Erreur standard de la moyenne : ce qui permet de dire si la moyenne
        # se distingue de zero. Sans elle, une moyenne positive ne prouve rien.
        "stderr": math.sqrt(rvar / len(rec)) if len(rec) > 1 else None,
    }
    out["continuation_share"] = (
        sum(1 for m in done if m.continuation) / len(done))
    out["overshoot_share"] = sum(1 for m in done if m.overshoot) / len(done)
    return out
