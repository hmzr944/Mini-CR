"""Comptabilite des essais — empecher la selection multiple de creer un edge.

LE PROBLEME. Si l'on essaie N hypotheses et que l'on retient la meilleure, son
Sharpe apparent est gonfle par la SELECTION, meme si aucune n'a d'edge. Avec
N essais de Sharpe vrai nul, le maximum attendu croit comme sqrt(2 ln N).

CE MODULE fournit :
  - `expected_max_sharpe` : le Sharpe qu'on obtiendrait PAR CHANCE avec N essais ;
  - `deflated_sharpe` : la probabilite que le Sharpe observe depasse ce seuil,
    en tenant compte de l'asymetrie et de l'aplatissement des rendements ;
  - `min_track_record_length` : le nombre d'observations necessaire pour
    conclure ;
  - `probability_of_backtest_overfitting` : par CSCV, la frequence a laquelle
    la meilleure configuration en echantillon finit sous la mediane hors
    echantillon.

Aucun de ces outils ne rend un resultat vrai. Ils disent quand il ne l'est
PAS. Stdlib seule.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence

#: Constante d'Euler-Mascheroni, utilisee par l'esperance du maximum.
EULER_MASCHERONI = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float, tol: float = 1e-10) -> float:
    """Quantile de la loi normale, par bissection sur la CDF.

    Methode volontairement elementaire : elle est evidemment correcte et ne
    depend d'aucune table. La precision atteinte (1e-10) depasse largement ce
    que la qualite des donnees justifie.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p doit etre dans ]0,1[ (recu {p})")
    lo, hi = -40.0, 40.0
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _moments(xs: Sequence[float]) -> Dict[str, float]:
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": xs[0] if n else 0.0, "std": 0.0,
                "skew": 0.0, "kurtosis": 3.0}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    std = math.sqrt(var)
    if std <= 0:
        return {"n": n, "mean": mean, "std": 0.0, "skew": 0.0, "kurtosis": 3.0}
    m3 = sum((x - mean) ** 3 for x in xs) / n
    m4 = sum((x - mean) ** 4 for x in xs) / n
    return {"n": n, "mean": mean, "std": std,
            "skew": m3 / std ** 3, "kurtosis": m4 / std ** 4}


def sharpe_ratio(returns: Sequence[float]) -> Optional[float]:
    """Sharpe NON annualise, sur la frequence des observations fournies.

    Annualiser un Sharpe mesure sur quelques heures multiplierait le bruit par
    la racine du nombre de periodes : on ne le fait pas.
    """
    m = _moments(returns)
    if m["n"] < 2 or m["std"] <= 0:
        return None
    return m["mean"] / m["std"]


def expected_max_sharpe(n_trials: int, trial_sharpe_std: float) -> Optional[float]:
    """Sharpe maximal attendu PAR CHANCE sur `n_trials` essais de Sharpe nul.

    C'est le seuil que la meilleure hypothese doit depasser pour signifier
    autre chose que « nous avons cherche longtemps ».
    """
    if n_trials < 2 or trial_sharpe_std <= 0:
        return None
    g = EULER_MASCHERONI
    a = norm_ppf(1.0 - 1.0 / n_trials)
    b = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return trial_sharpe_std * ((1.0 - g) * a + g * b)


@dataclass(frozen=True)
class DeflatedSharpe:
    observed_sharpe: float
    benchmark_sharpe: float
    n_observations: int
    n_trials: int
    skew: float
    kurtosis: float
    p_value: Optional[float]
    significant: bool
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def deflated_sharpe(returns: Sequence[float], n_trials: int,
                    trial_sharpe_std: Optional[float] = None,
                    alpha: float = 0.05) -> Optional[DeflatedSharpe]:
    """Sharpe deflate : le Sharpe observe survit-il au nombre d'essais ?

    `trial_sharpe_std` est l'ecart-type des Sharpe des essais REELLEMENT
    effectues. S'il est absent, on ne peut PAS deflater : la fonction retourne
    None plutot que d'inventer une dispersion.
    """
    m = _moments(returns)
    n = m["n"]
    if n < 3 or m["std"] <= 0:
        return None
    sr = m["mean"] / m["std"]
    if trial_sharpe_std is None or n_trials < 2:
        return DeflatedSharpe(
            observed_sharpe=sr, benchmark_sharpe=0.0, n_observations=n,
            n_trials=n_trials, skew=m["skew"], kurtosis=m["kurtosis"],
            p_value=None, significant=False,
            note="dispersion des essais inconnue : deflation IMPOSSIBLE, "
                 "le Sharpe observe ne peut pas etre interprete")
    sr0 = expected_max_sharpe(n_trials, trial_sharpe_std) or 0.0
    denom = 1.0 - m["skew"] * sr + (m["kurtosis"] - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return DeflatedSharpe(
            observed_sharpe=sr, benchmark_sharpe=sr0, n_observations=n,
            n_trials=n_trials, skew=m["skew"], kurtosis=m["kurtosis"],
            p_value=None, significant=False,
            note="variance du Sharpe non definie (moments extremes)")
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    p = norm_cdf(z)
    return DeflatedSharpe(
        observed_sharpe=sr, benchmark_sharpe=sr0, n_observations=n,
        n_trials=n_trials, skew=m["skew"], kurtosis=m["kurtosis"],
        p_value=p, significant=p > 1.0 - alpha,
        note=(f"seuil de chance sur {n_trials} essais : {sr0:.4f} ; "
              f"observe : {sr:.4f}"))


def min_track_record_length(returns: Sequence[float], target_sharpe: float = 0.0,
                            alpha: float = 0.05) -> Optional[float]:
    """Nombre d'observations necessaires pour distinguer SR de `target_sharpe`."""
    m = _moments(returns)
    if m["n"] < 3 or m["std"] <= 0:
        return None
    sr = m["mean"] / m["std"]
    if sr <= target_sharpe:
        return None
    z = norm_ppf(1.0 - alpha)
    denom = 1.0 - m["skew"] * sr + (m["kurtosis"] - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return None
    return 1.0 + denom * (z / (sr - target_sharpe)) ** 2


def probability_of_backtest_overfitting(
        matrix: Sequence[Sequence[float]], n_blocks: int = 8) -> Optional[Dict[str, Any]]:
    """PBO par validation croisee combinatoire symetrique (CSCV).

    `matrix[k]` est la serie de rendements de la configuration k, toutes de
    meme longueur. On decoupe en `n_blocks`, on forme toutes les partitions
    moitie/moitie, on retient la meilleure configuration DANS l'echantillon et
    on regarde son rang HORS echantillon. PBO = frequence a laquelle elle
    tombe sous la mediane.

    PBO eleve = la selection en echantillon n'apprend rien d'utile.
    """
    if not matrix or len(matrix) < 2:
        return None
    n_cfg = len(matrix)
    length = min(len(r) for r in matrix)
    if length < n_blocks * 2:
        return None
    if n_blocks % 2 or n_blocks < 4:
        raise ValueError("n_blocks doit etre pair et >= 4")
    size = length // n_blocks
    blocks = [[r[i * size:(i + 1) * size] for i in range(n_blocks)]
              for r in matrix]

    def sr_of(chunks: Sequence[Sequence[float]]) -> float:
        flat = [x for c in chunks for x in c]
        s = sharpe_ratio(flat)
        return s if s is not None else float("-inf")

    logits: List[float] = []
    n_under = 0
    total = 0
    for is_idx in combinations(range(n_blocks), n_blocks // 2):
        oos_idx = [i for i in range(n_blocks) if i not in is_idx]
        is_sr = [sr_of([blocks[k][i] for i in is_idx]) for k in range(n_cfg)]
        oos_sr = [sr_of([blocks[k][i] for i in oos_idx]) for k in range(n_cfg)]
        best = max(range(n_cfg), key=lambda k: is_sr[k])
        ranked = sorted(range(n_cfg), key=lambda k: oos_sr[k])
        rank = ranked.index(best) + 1          # 1 = pire, n_cfg = meilleur
        w = rank / (n_cfg + 1.0)
        total += 1
        if w <= 0.5:
            n_under += 1
        w = min(max(w, 1e-9), 1 - 1e-9)
        logits.append(math.log(w / (1 - w)))
    if not total:
        return None
    return {
        "pbo": n_under / total,
        "n_partitions": total,
        "n_configurations": n_cfg,
        "median_logit": sorted(logits)[len(logits) // 2],
        "note": ("PBO = frequence a laquelle la meilleure configuration en "
                 "echantillon tombe sous la mediane hors echantillon. "
                 "PBO >= 0.5 : la selection n'apprend rien."),
    }


@dataclass
class TrialLedger:
    """Compte IMMUABLE de tous les essais. Un essai abandonne reste compte."""

    trials: List[Dict[str, Any]] = field(default_factory=list)

    def record(self, hypothesis_id: str, sharpe: Optional[float],
               n_obs: int, status: str, **extra: Any) -> None:
        self.trials.append({"trial_number": len(self.trials) + 1,
                            "hypothesis_id": hypothesis_id, "sharpe": sharpe,
                            "n_observations": n_obs, "status": status, **extra})

    @property
    def n_trials(self) -> int:
        return len(self.trials)

    #: Un Sharpe calcule sur une poignee d'observations n'est pas une mesure :
    #: il peut valoir des centaines. L'inclure dans la dispersion gonflerait le
    #: seuil de chance au point de rendre la deflation vide de sens.
    MIN_OBS_FOR_DISPERSION = 30

    def sharpe_dispersion(self, min_obs: Optional[int] = None) -> Optional[float]:
        """Ecart-type des Sharpe des essais SUFFISAMMENT observes.

        Requis pour deflater. Retourne None si moins de deux essais
        atteignent le seuil : on ne deflate pas avec une dispersion inventee.
        """
        floor = self.MIN_OBS_FOR_DISPERSION if min_obs is None else min_obs
        vals = [t["sharpe"] for t in self.trials
                if t["sharpe"] is not None
                and (t.get("n_observations") or 0) >= floor]
        if len(vals) < 2:
            return None
        return _moments(vals)["std"]

    def n_trials_with_enough_observations(self, min_obs: Optional[int] = None) -> int:
        floor = self.MIN_OBS_FOR_DISPERSION if min_obs is None else min_obs
        return sum(1 for t in self.trials
                   if (t.get("n_observations") or 0) >= floor)

    def to_dict(self) -> Dict[str, Any]:
        return {"n_trials": self.n_trials,
                "n_trials_with_enough_observations":
                    self.n_trials_with_enough_observations(),
                "min_obs_for_dispersion": self.MIN_OBS_FOR_DISPERSION,
                "sharpe_dispersion": self.sharpe_dispersion(),
                "by_status": {s: sum(1 for t in self.trials if t["status"] == s)
                              for s in sorted({t["status"] for t in self.trials})},
                "note": "tout essai compte, y compris abandonne ou perdant"}
