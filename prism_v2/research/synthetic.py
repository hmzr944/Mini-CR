"""Donnees synthetiques a verite connue — tester l'INSTRUMENT, pas le marche.

POURQUOI. L'audit de cloture a etabli que le projet n'avait jamais verifie la
propriete la plus elementaire de son moteur de decouverte : *s'il existait un
phenomene, le moteur le trouverait-il ?* Sans cette verification, « 0
survivant » est ambigu — absence d'edge, ou moteur aveugle ?

CE MODULE fabrique des series ou la reponse est CONNUE D'AVANCE :

  - `random_walk`        : aucune relation. Le moteur doit ne rien trouver.
                           Ce qu'il trouve quand meme est son taux de FAUX
                           POSITIFS.
  - `planted_reversion`  : apres un deplacement, une fraction CONNUE du
                           mouvement est rendue. Le moteur doit retrouver
                           cette fraction.

LIMITE A ENONCER. Une serie synthetique n'est pas un marche : elle n'a ni
micro-structure realiste, ni regimes, ni flux. Reussir ici ne prouve PAS que
le moteur trouverait un edge reel. Echouer ici prouve en revanche qu'il ne le
trouverait pas. Le test est donc NECESSAIRE, jamais suffisant.

Aucune de ces series ne doit jamais entrer dans une evaluation economique.
Chaque enregistrement porte `synthetic: True`.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class SyntheticSpec:
    """Parametres d'une serie synthetique. Tous explicites, aucun devine."""

    inst_id: str = "SYNTH-USD-SWAP"
    n_snapshots: int = 20_000
    cadence_ms: int = 250
    start_price: float = 50_000.0
    #: Ecart-type du pas de marche aleatoire, en bps par instantane.
    step_vol_bps: float = 2.0
    #: Demi-spread en bps.
    half_spread_bps: float = 1.0
    #: Taille affichee a chaque niveau, en contrats.
    level_size: float = 50.0
    n_levels: int = 10
    seed: int = 12345


def _book(mid: float, spec: SyntheticSpec) -> Dict[str, List[List[float]]]:
    half = mid * spec.half_spread_bps / 10_000.0
    tick = max(half / 2.0, mid * 1e-6)
    bids = [[round(mid - half - i * tick, 6), spec.level_size]
            for i in range(spec.n_levels)]
    asks = [[round(mid + half + i * tick, 6), spec.level_size]
            for i in range(spec.n_levels)]
    return {"b": bids, "a": asks}


def _emit(mids: Sequence[float], spec: SyntheticSpec,
          start_ts: int = 1_700_000_000_000) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, mid in enumerate(mids):
        rec: Dict[str, Any] = {"i": spec.inst_id, "ok": True,
                               "ts": start_ts + i * spec.cadence_ms,
                               "seq": 1_000_000 + i, "synthetic": True}
        rec.update(_book(mid, spec))
        out.append(rec)
    return out


def random_walk(spec: SyntheticSpec = SyntheticSpec()) -> List[Dict[str, Any]]:
    """Marche aleatoire pure. AUCUNE relation exploitable n'existe.

    Tout ce que le moteur y « decouvre » est un faux positif, par definition.
    """
    rng = random.Random(spec.seed)
    mid = spec.start_price
    mids = [mid]
    for _ in range(spec.n_snapshots - 1):
        mid *= 1.0 + rng.gauss(0.0, spec.step_vol_bps) / 10_000.0
        mids.append(mid)
    return _emit(mids, spec)


def planted_reversion(fraction: float, lookback_ms: int, horizon_ms: int,
                      trigger_bps: float = 6.0,
                      spec: SyntheticSpec = SyntheticSpec()
                      ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Marche aleatoire PLUS une reversion de fraction connue.

    Mecanique plantee : des que le deplacement sur `lookback_ms` depasse
    `trigger_bps`, une fraction `fraction` de ce deplacement est rendue,
    repartie uniformement sur les `horizon_ms` suivants.

    Retourne (instantanes, verite_terrain). La verite terrain est ce que le
    moteur DOIT retrouver ; elle n'est jamais passee au moteur.
    """
    if not 0.0 <= fraction <= 2.0:
        raise ValueError("fraction hors du domaine plausible [0, 2]")
    rng = random.Random(spec.seed)
    lb = max(1, lookback_ms // spec.cadence_ms)
    hz = max(1, horizon_ms // spec.cadence_ms)

    mids = [spec.start_price]
    # Correction en attente, en bps par instantane, cumulee par evenement.
    pending = [0.0] * (spec.n_snapshots + hz + 2)
    trigger_idx: List[int] = []
    n_planted = 0
    for i in range(1, spec.n_snapshots):
        drift = pending[i]
        mid = mids[-1] * (1.0 + (rng.gauss(0.0, spec.step_vol_bps) + drift) / 10_000.0)
        mids.append(mid)
        if i >= lb:
            disp = (mids[i] - mids[i - lb]) / mids[i - lb] * 10_000.0
            if abs(disp) >= trigger_bps and pending[i + 1] == 0.0:
                trigger_idx.append(i)
                # On rend `fraction` du deplacement, dans le sens INVERSE,
                # etale sur l'horizon.
                per_step = -disp * fraction / hz
                for k in range(1, hz + 1):
                    if i + k < len(pending):
                        pending[i + k] += per_step
                n_planted += 1
    truth = {
        "planted_fraction": fraction, "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms, "trigger_bps": trigger_bps,
        "n_planted_events": n_planted, "inst_id": spec.inst_id,
        "trigger_indices": trigger_idx,
        "note": ("verite terrain : la fraction de reversion attendue est "
                 f"{fraction}. Le moteur ne la recoit jamais."),
    }
    return _emit(mids, spec), truth


# ══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class RecoveryResult:
    """Le moteur a-t-il retrouve ce qui etait plante ?"""

    planted_fraction: float
    measured_fraction: Optional[float]
    measured_stderr: Optional[float]
    n_measured: int
    absolute_error: Optional[float]
    within_tolerance: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def recovery_test(fraction: float, lookback_ms: int = 5_000,
                  horizon_ms: int = 5_000, latency_ms: int = 250,
                  tolerance: float = 0.25,
                  spec: SyntheticSpec = SyntheticSpec(),
                  max_events: int = 4_000) -> RecoveryResult:
    """Plante une reversion connue et verifie que le laboratoire la retrouve.

    `tolerance` est une erreur ABSOLUE sur la fraction. Elle est large a
    dessein : on teste que l'instrument n'est pas aveugle ni biaise, pas qu'il
    est precis au centieme.
    """
    from .causal_lab import CaptureOutcome, SnapshotSeries, measure_capture

    recs, truth = planted_reversion(fraction, lookback_ms, horizon_ms,
                                    spec=spec)
    series = SnapshotSeries.from_records(spec.inst_id, recs)
    step = max(spec.cadence_ms, lookback_ms // 2)
    t = (series.first_ts or 0) + lookback_ms + spec.cadence_ms
    end = (series.last_ts or 0) - latency_ms - horizon_ms
    measured: List[float] = []
    n = 0
    while t < end and n < max_events:
        m = measure_capture(series, t, lookback_ms, latency_ms, horizon_ms,
                            min_displacement_bps=truth["trigger_bps"])
        if m.outcome is CaptureOutcome.MEASURED and m.capture_fraction is not None:
            measured.append(m.capture_fraction)
        t += step
        n += 1
    if len(measured) < 10:
        return RecoveryResult(
            planted_fraction=fraction, measured_fraction=None,
            measured_stderr=None, n_measured=len(measured),
            absolute_error=None, within_tolerance=False,
            detail=f"seulement {len(measured)} mesures : echantillon trop "
                   "faible pour conclure quoi que ce soit")
    mean = sum(measured) / len(measured)
    var = sum((x - mean) ** 2 for x in measured) / (len(measured) - 1)
    stderr = math.sqrt(var / len(measured))
    err = abs(mean - fraction)
    return RecoveryResult(
        planted_fraction=fraction, measured_fraction=mean,
        measured_stderr=stderr, n_measured=len(measured),
        absolute_error=err, within_tolerance=err <= tolerance,
        detail=(f"plante {fraction:.3f}, mesure {mean:.3f} "
                f"+/- {stderr:.3f} (N={len(measured)})"))


def false_positive_test(lookback_ms: int = 5_000, horizon_ms: int = 5_000,
                        latency_ms: int = 250,
                        spec: SyntheticSpec = SyntheticSpec(),
                        max_events: int = 4_000) -> Dict[str, Any]:
    """Marche aleatoire : la fraction mesuree doit etre indiscernable de zero.

    Si elle ne l'est pas, l'instrument fabrique de l'edge a partir de bruit —
    et tout resultat positif sur donnees reelles serait suspect.
    """
    from .causal_lab import CaptureOutcome, SnapshotSeries, measure_capture

    recs = random_walk(spec)
    series = SnapshotSeries.from_records(spec.inst_id, recs)
    step = max(spec.cadence_ms, lookback_ms // 2)
    t = (series.first_ts or 0) + lookback_ms + spec.cadence_ms
    end = (series.last_ts or 0) - latency_ms - horizon_ms
    measured: List[float] = []
    n = 0
    while t < end and n < max_events:
        m = measure_capture(series, t, lookback_ms, latency_ms, horizon_ms,
                            min_displacement_bps=6.0)
        if m.outcome is CaptureOutcome.MEASURED and m.capture_fraction is not None:
            measured.append(m.capture_fraction)
        t += step
        n += 1
    if len(measured) < 10:
        return {"n": len(measured), "conclusive": False,
                "detail": "echantillon insuffisant"}
    mean = sum(measured) / len(measured)
    var = sum((x - mean) ** 2 for x in measured) / (len(measured) - 1)
    stderr = math.sqrt(var / len(measured))
    t_stat = mean / stderr if stderr > 0 else float("inf")
    return {"n": len(measured), "mean_fraction": mean, "stderr": stderr,
            "t_stat": t_stat, "conclusive": True,
            "indistinguishable_from_zero": abs(t_stat) < 3.0,
            "detail": (f"marche aleatoire : fraction {mean:+.4f} "
                       f"+/- {stderr:.4f}, t = {t_stat:+.2f}")}
