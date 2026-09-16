"""Agents de recherche. Chacun a un role etroit et ne peut pas en sortir.

    AGENT 1 MarketObserver   : releve l'etat, n'interprete rien
    AGENT 2 PhenomenonAgent  : regroupe les releves en etats recurrents
    AGENT 3 RelationAgent    : MESURE ce qui suit un phenomene — la decouverte
    AGENT 4 MechanismAgent   : propose une explication economique
    AGENT 6 CaptureResearch  : etudie l'executabilite des survivants

L'agent 5 (falsification) vit dans falsification.py : il doit rester
separement adressable pour qu'aucun chemin ne puisse l'eviter.

AUCUN agent ne decide d'acheter ou de vendre. Aucun n'appelle economics,
risk ou execution : ils produisent des objets de recherche que le noyau
consomme ensuite. C'est ce qui empeche un agent de contourner les gardes.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..market_state import MarketState
from .hypothesis import (
    EconomicMechanism, Hypothesis, MultipleTestingAccount, Phenomenon, Relation,
)
from .observation import FEATURE_SPACE, Observation, ObservationLog, observe_state

#: Horizons sondes, en ms. Points d'OBSERVATION couvrant du tres court au
#: court terme. Les elargir ajoute des mesures, cela ne change aucun resultat.
DEFAULT_HORIZONS_MS: Tuple[int, ...] = (1_000, 5_000, 15_000, 30_000)

#: Quantile definissant un etat "extreme". Convention de LECTURE : on regarde
#: les deciles. Ce n'est pas un seuil regle sur des resultats — le seuil
#: NUMERIQUE est mesure sur l'echantillon, jamais pose a l'avance.
EXTREME_QUANTILE = 0.10

#: Occurrences minimales pour qu'un etat merite le nom de phenomene.
MIN_OCCURRENCES = 20


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] * (1 - (pos - lo)) + sorted_values[hi] * (pos - lo)


def _hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════
class MarketObserverAgent:
    """AGENT 1 — releve l'etat sans imposer de strategie."""

    name = "MARKET_OBSERVER"

    def __init__(self, venue: str = "OKX"):
        self.venue = venue
        self.log = ObservationLog()

    def observe(self, states: Dict[str, MarketState]) -> List[Observation]:
        out: List[Observation] = []
        for state in states.values():
            obs = observe_state(state, venue=self.venue)
            self.log.add(obs)
            out.append(obs)
        return out


# ══════════════════════════════════════════════════════════════════════════
class PhenomenonAgent:
    """AGENT 2 — regroupe les observations en etats recurrents.

    Un phenomene = « la primitive F est dans son decile extreme ». Le seuil
    numerique est MESURE sur l'echantillon observe : rien n'est pose d'avance,
    et le meme code sur un autre marche produira d'autres seuils.
    """

    name = "PHENOMENON_AGENT"

    def __init__(self, quantile: float = EXTREME_QUANTILE,
                 min_occurrences: int = MIN_OCCURRENCES):
        self.quantile = quantile
        self.min_occurrences = min_occurrences

    def discover(self, log: ObservationLog) -> List[Phenomenon]:
        found: List[Phenomenon] = []
        for inst_id in log.instruments():
            series = log.series(inst_id)
            if len(series) < self.min_occurrences * 2:
                continue
            window = (series[0].ts_ms, series[-1].ts_ms)
            for feature in FEATURE_SPACE:
                values = [o.values[feature] for o in series if feature in o.values]
                if len(values) < self.min_occurrences * 2:
                    continue
                ordered = sorted(values)
                if ordered[0] == ordered[-1]:
                    continue        # primitive constante : aucun etat a distinguer
                for condition, q in (("low", self.quantile),
                                     ("high", 1.0 - self.quantile)):
                    threshold = _quantile(ordered, q)
                    if condition == "low":
                        n_occ = sum(1 for v in values if v <= threshold)
                    else:
                        n_occ = sum(1 for v in values if v >= threshold)
                    if n_occ < self.min_occurrences:
                        continue
                    found.append(Phenomenon(
                        phenomenon_id=_hash(inst_id, feature, condition, window),
                        feature=feature, condition=condition, threshold=threshold,
                        inst_id=inst_id, venue=series[0].venue,
                        n_occurrences=n_occ, n_observations=len(values),
                        window_ms=window))
        return found


# ══════════════════════════════════════════════════════════════════════════
class RelationAgent:
    """AGENT 3 — MESURE ce qui suit un phenomene. C'est ici que se joue la
    decouverte : le balayage est MECANIQUE sur tout l'espace de primitives
    croise avec tous les horizons. Aucune intuition n'oriente la recherche.

    CAUSALITE : la condition a T n'utilise que des donnees <= T. L'issue est
    mesuree de T a T+H. C'est une estimation statistique legitime, PAS une
    decision : aucune decision n'est prise a T avec l'issue. Le red team
    verifie ensuite qu'on n'en tire pas abusivement une prevision.

    HONNETETE : la p-value est un test t bilateral approximatif. Elle ne sert
    QU'A la correction pour tests multiples. Elle n'affirme aucune verite.
    """

    name = "RELATION_AGENT"

    def __init__(self, horizons_ms: Sequence[int] = DEFAULT_HORIZONS_MS,
                 min_samples: int = MIN_OCCURRENCES):
        self.horizons_ms = tuple(horizons_ms)
        self.min_samples = min_samples

    @staticmethod
    def _forward_move_bps(series: List[Observation], mids: Dict[int, float],
                          idx: int, horizon_ms: int,
                          tolerance_ms: Optional[int] = None) -> Optional[float]:
        """Deplacement du mid entre l'observation `idx` et idx+horizon.

        DEUX DEFAUTS CORRIGES APRES REVUE ADVERSARIALE :

        1. TOLERANCE D'HORIZON. On retenait la derniere observation <= cible,
           quelle que soit sa distance. Si la plus proche precedait la cible
           de 10 s sur un horizon de 30 s, on mesurait un mouvement de 20 s en
           l'appelant 30 s. L'issue est desormais REFUSEE si l'observation la
           plus proche s'ecarte de plus de `tolerance_ms` de la cible.

        2. ECRASEMENT PAR UNE VALEUR MANQUANTE. `best = mids.get(ts)` remettait
           `best` a None quand un horodatage tardif n'avait pas de mid, perdant
           une valeur valide trouvee juste avant.
        """
        t0 = series[idx].ts_ms
        m0 = mids.get(t0)
        if m0 is None or m0 <= 0:
            return None
        target = t0 + horizon_ms
        tol = tolerance_ms if tolerance_ms is not None else max(1, horizon_ms // 4)
        best: Optional[float] = None
        best_ts: Optional[int] = None
        for j in range(idx + 1, len(series)):
            ts = series[j].ts_ms
            if ts > target:
                break
            m = mids.get(ts)
            if m is not None and m > 0:      # ne jamais ecraser par un trou
                best, best_ts = m, ts
        if best is None or best_ts is None:
            return None
        if abs(target - best_ts) > tol:      # horizon reellement couvert ?
            return None
        return (best - m0) / m0 * 10_000.0

    @staticmethod
    def _welch_p(conditional: Sequence[float],
                 baseline: Sequence[float]) -> Optional[float]:
        """p-value bilaterale de Welch entre l'echantillon conditionnel et la
        reference.

        DEFAUT CORRIGE : la version precedente traitait la moyenne de reference
        comme une CONSTANTE CONNUE. Cela ignorait sa propre variance, sous-
        estimait l'erreur standard, et produisait donc des p-values trop
        petites — c'est-a-dire trop de « decouvertes ». Welch tient compte des
        deux variances et de deux tailles d'echantillon differentes.

        Reste une approximation normale de la queue : suffisante pour ORDONNER
        des candidates en vue d'une correction de tests multiples, insuffisante
        pour affirmer quoi que ce soit seule.
        """
        n1, n2 = len(conditional), len(baseline)
        if n1 < 3 or n2 < 3:
            return None
        m1 = sum(conditional) / n1
        m2 = sum(baseline) / n2
        v1 = sum((v - m1) ** 2 for v in conditional) / (n1 - 1)
        v2 = sum((v - m2) ** 2 for v in baseline) / (n2 - 1)
        se = math.sqrt(v1 / n1 + v2 / n2)
        if se <= 0:
            return None
        t = (m1 - m2) / se
        return math.erfc(abs(t) / math.sqrt(2.0))

    def measure(self, log: ObservationLog, phenomena: Sequence[Phenomenon],
                mids_by_inst: Dict[str, Dict[int, float]],
                accounting: MultipleTestingAccount,
                sample: str = "DISCOVERY") -> List[Relation]:
        """Balaye phenomenes x horizons et mesure l'issue conditionnelle."""
        relations: List[Relation] = []
        by_inst: Dict[str, List[Phenomenon]] = {}
        for p in phenomena:
            by_inst.setdefault(p.inst_id, []).append(p)

        for inst_id, phens in by_inst.items():
            series = log.series(inst_id)
            mids = mids_by_inst.get(inst_id, {})
            if len(series) < self.min_samples * 2 or not mids:
                continue
            for horizon in self.horizons_ms:
                # Issue pre-calculee une fois par horizon : la meme serie sert
                # a toutes les primitives, ce qui EST une source de tests
                # multiples — comptabilisee explicitement.
                forwards: List[Optional[float]] = [
                    self._forward_move_bps(series, mids, i, horizon)
                    for i in range(len(series))]
                usable = [(i, f) for i, f in enumerate(forwards) if f is not None]
                if len(usable) < self.min_samples * 2:
                    continue
                all_moves = [f for _, f in usable]
                for phen in phens:
                    cond_idx = set()
                    for i, _ in usable:
                        v = series[i].values.get(phen.feature)
                        if v is None:
                            continue
                        if ((phen.condition == "low" and v <= phen.threshold) or
                                (phen.condition == "high" and v >= phen.threshold)):
                            cond_idx.add(i)
                    conditional = [f for i, f in usable if i in cond_idx]
                    baseline = [f for i, f in usable if i not in cond_idx]
                    if len(conditional) < self.min_samples or len(baseline) < 3:
                        continue
                    base_mean = sum(baseline) / len(baseline)
                    mean = sum(conditional) / len(conditional)
                    ordered = sorted(conditional)
                    n = len(ordered)
                    median = (ordered[n // 2] if n % 2
                              else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)
                    var = (sum((v - mean) ** 2 for v in conditional) / (n - 1)
                           if n > 1 else 0.0)
                    p_value = self._welch_p(conditional, baseline)
                    accounting.record(p_value)
                    relations.append(Relation(
                        relation_id=_hash(phen.phenomenon_id, horizon, sample),
                        phenomenon_id=phen.phenomenon_id, feature=phen.feature,
                        condition=phen.condition, inst_id=inst_id,
                        horizon_ms=horizon, n=n, mean_forward_bps=mean,
                        median_forward_bps=median,
                        std_forward_bps=math.sqrt(var),
                        baseline_mean_bps=base_mean, n_baseline=len(baseline),
                        p_value=p_value, sample=sample))
        return relations


# ══════════════════════════════════════════════════════════════════════════
class MechanismAgent:
    """AGENT 4 — propose une explication economique. Jamais une verite.

    L'etiquette repond a : « quelle histoire economique rendrait cette
    relation plausible ? ». Si aucune ne s'impose, le mecanisme est
    UNEXPLAINED — et c'est un resultat, pas un echec : une relation sans
    mecanisme est le profil type d'un artefact statistique.
    """

    name = "MECHANISM_AGENT"

    #: Correspondance primitive -> mecanisme plausible, avec sa justification.
    MAPPING: Dict[str, Tuple[EconomicMechanism, str]] = {
        "n_forced_flow_10s": (
            EconomicMechanism.FORCED_FLOW,
            "des liquidations sont des ventes/achats contraints : elles "
            "deplacent le prix sans information nouvelle"),
        "depth_change_ratio_bid_5s": (
            EconomicMechanism.LIQUIDITY_WITHDRAWAL,
            "un retrait de profondeur elargit le spread et renforce l'impact"),
        "depth_change_ratio_ask_5s": (
            EconomicMechanism.LIQUIDITY_WITHDRAWAL,
            "un retrait de profondeur elargit le spread et renforce l'impact"),
        "aggressive_imbalance_5s": (
            EconomicMechanism.INVENTORY_IMBALANCE,
            "un flux agressif desequilibre charge l'inventaire des teneurs, "
            "qui le repercutent sur leurs cotations"),
        "trade_to_book_ratio_5s": (
            EconomicMechanism.TEMPORARY_MARKET_IMPACT,
            "un flux consommant le carnet plus vite qu'il ne se reconstitue "
            "produit un impact temporaire"),
        "microprice_deviation_bps": (
            EconomicMechanism.DELAYED_ADJUSTMENT,
            "le mid peut retarder sur la pression reelle du carnet"),
        "depth_imbalance": (
            EconomicMechanism.INVENTORY_IMBALANCE,
            "un carnet desequilibre traduit une pression d'inventaire"),
        "funding_rate": (
            EconomicMechanism.FUNDING_BASIS_INTERACTION,
            "le funding tire le perpetuel vers son sous-jacent"),
        "spread_bps": (
            EconomicMechanism.LIQUIDITY_FRAGMENTATION,
            "un spread anormal signale une liquidite fragmentee ou retiree"),
        "executable_vs_mid_bps": (
            EconomicMechanism.TEMPORARY_DISLOCATION,
            "un ecart entre mid affiche et prix executable est une dislocation"),
        "displacement_bps_5s": (
            EconomicMechanism.TEMPORARY_MARKET_IMPACT,
            "un deplacement recent peut comporter une part temporaire"),
        "realized_vol_bps_30s": (
            EconomicMechanism.UNEXPLAINED,
            "la volatilite est un contexte, pas un mecanisme de capture"),
        "trade_intensity_5s": (
            EconomicMechanism.UNEXPLAINED,
            "l'intensite seule ne dit rien du sens ni de la capturabilite"),
    }

    def propose(self, relation: Relation, phenomenon: Phenomenon) -> Hypothesis:
        mechanism, rationale = self.MAPPING.get(
            relation.feature,
            (EconomicMechanism.UNEXPLAINED,
             "aucun mecanisme economique documente ne relie cette primitive a "
             "une capture : profil typique d'un artefact statistique"))
        return Hypothesis.propose(
            relation=relation, phenomenon=phenomenon, mechanism=mechanism,
            rationale=rationale, venues=(phenomenon.venue,))


# ══════════════════════════════════════════════════════════════════════════
@dataclass
class CaptureStudy:
    """Etude d'executabilite d'une hypothese survivante."""

    hypothesis_id: str
    inst_id: str
    theoretical_edge_bps: float       # l'exces mesure, avant toute friction
    executable_depth_usd: Optional[float]
    spread_cost_bps: Optional[float]
    impact_cost_bps: Optional[float]
    fee_cost_bps: Optional[float]
    fee_quality: str
    latency_ms: Optional[float]
    required_execution: str
    capacity_ok: Optional[bool]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class CaptureResearchAgent:
    """AGENT 6 — etudie l'executabilite des SEULES hypotheses survivantes.

    Ne calcule aucun PnL et n'autorise aucune execution : il prepare le
    dossier que le noyau economique tranchera. La separation est ce qui
    empeche un agent de contourner risk/execution.
    """

    name = "CAPTURE_RESEARCH_AGENT"

    def study(self, h: Hypothesis, state: Optional[MarketState],
              fee_bps: Optional[float], fee_quality: str,
              notional_usd: float, latency_ms: Optional[float]) -> CaptureStudy:
        notes: List[str] = []
        spread = impact = depth = None
        capacity_ok: Optional[bool] = None
        if state is not None:
            try:
                spread = state.book.crossing_cost_bps("ask") * 2
                depth = min(state.depth_usd("bid"), state.depth_usd("ask"))
                imp = state.book.slippage_vs_touch_bps("ask", notional_usd)
                impact = (imp * 2) if imp is not None else None
                capacity_ok = depth >= notional_usd
            except Exception as exc:
                notes.append(f"carnet inexploitable: {exc}")
        else:
            notes.append("aucun etat de marche : executabilite NON EVALUEE")
        if fee_bps is None:
            notes.append("frais UNKNOWN : aucune conclusion economique possible")
        if latency_ms is None:
            notes.append("latence UNKNOWN")
        # Un maker est exige quand la capture consiste a encaisser le spread.
        required = "MAKER" if h.relation.feature in (
            "spread_bps", "depth_change_ratio_bid_5s",
            "depth_change_ratio_ask_5s") else "TAKER"
        if required == "MAKER":
            notes.append("capture en MAKER : fill non garanti et adverse "
                         "selection UNKNOWN — ne peut pas passer en EXECUTION")
        return CaptureStudy(
            hypothesis_id=h.hypothesis_id, inst_id=h.relation.inst_id,
            theoretical_edge_bps=abs(h.relation.excess_bps),
            executable_depth_usd=depth, spread_cost_bps=spread,
            impact_cost_bps=impact, fee_cost_bps=fee_bps, fee_quality=fee_quality,
            latency_ms=latency_ms, required_execution=required,
            capacity_ok=capacity_ok, notes=notes)
