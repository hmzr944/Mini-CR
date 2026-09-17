#!/usr/bin/env python3
"""OPPORTUNITE — differentiel de funding inter-venues, en direct.

Cette famille a ete MESUREE ET FERMEE (prism_v2/FUNDING_ARB_PROTOCOL.md) :
142 actifs, 45 jours, deux regles de sortie, aucune configuration positive.
Elle est neanmoins implementee ici, et ce n'est pas une contradiction.

POURQUOI IMPLEMENTER UNE FAMILLE FERMEE.
Fermee veut dire « ne rapporte pas AUX NIVEAUX OBSERVES SUR 45 JOURS ». Cela
ne veut pas dire « ne rapportera jamais ». Un differentiel qui persisterait a
600 %/an au lieu de 100 % franchirait les couts. Le detecteur existe donc pour
SURVEILLER, avec le seuil economique exact, et pour declencher le jour ou la
condition change — une panne de venue, une cotation nouvelle, un squeeze.

Le comportement attendu, compte tenu de la mesure, est un REFUS quasi
systematique. Ce refus est le produit, pas un echec. Un detecteur qui
trouverait des candidates rentables tous les jours sur une famille mesuree
negative signalerait un bug, pas une opportunite.

LE HAIRCUT DE DECROISSANCE EST LE COEUR DE L'HONNETETE DE CE MODULE.
Le differentiel instantane SURESTIME massivement ce qu'on encaisse : a
100 %/an observe, on ne realise que 38,7 %/an sur 24 h et 13,7 % sur 168 h.
Utiliser le taux instantane comme capture attendue gonflerait la candidate
d'un facteur 2 a 7. Le tableau ci-dessous vient de ma propre mesure, sur la
fenetre DISCOVERY, et il est applique systematiquement.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.core_types import Direction, Provenance, utc_now_iso
from prism_v2.opportunity import (
    Candidate, DetectionResult, MarketContext, Opportunity,
)

FAMILY = "CROSS_VENUE_FUNDING"
HOURS_PER_YEAR = 24 * 365

#: SURFACE MESUREE. Pour chaque seuil de signal (%/an) et chaque horizon (h),
#: le differentiel ANNUALISE reellement realise. Source : mesure de ce depot,
#: FUNDING_ARB_PROTOCOL.md, fenetre DISCOVERY, 142 actifs, 45 jours.
#:
#: LIRE CETTE TABLE EN COLONNE EST LE POINT ECONOMIQUE CENTRAL. A 168 h, un
#: signal de 20 %/an realise 7,3 ; un signal de 500 %/an realise 5,5. Le
#: realise SATURE vers 11-14 %/an et REDESCEND sur les lectures extremes :
#: celles-ci sont majoritairement du bruit sur un seul paiement.
#:
#: Un haircut a ratio unique (realise/observe constant) predirait 61 bps sur
#: un signal a 234 %/an la ou la mesure en donne 26. Il fabriquerait donc des
#: opportunites exactement sur les lectures les plus spectaculaires — l'erreur
#: qui perd de l'argent en ayant l'air d'en trouver.
MEASURED_SURFACE: Dict[float, Dict[int, float]] = {
    0.20: {4: 0.266, 8: 0.240, 24: 0.188, 72: 0.123, 168: 0.073},
    0.50: {4: 0.472, 8: 0.413, 24: 0.307, 72: 0.191, 168: 0.107},
    1.00: {4: 0.667, 8: 0.533, 24: 0.387, 72: 0.250, 168: 0.137},
    2.00: {4: 0.700, 8: 0.239, 24: 0.188, 72: 0.186, 168: 0.116},
    5.00: {4: 0.269, 8: -0.334, 24: -0.063, 72: 0.063, 168: 0.055},
}
HORIZONS_MEASURED: Tuple[int, ...] = (4, 8, 24, 72, 168)
DECAY_SOURCE = ("prism_v2/FUNDING_ARB_PROTOCOL.md — DISCOVERY, 142 actifs, "
                "45 j ; differentiel realise par (seuil observe, horizon)")

#: Horizon par defaut : celui qui maximisait le brut dans la mesure.
DEFAULT_HORIZON_H = 168


def _interp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y0
    return y0 + (x - x0) * (y1 - y0) / (x1 - x0)


def realized_apr(observed_apr: float, horizon_h: int) -> float:
    """Differentiel annualise REELLEMENT attendu, par interpolation mesuree.

    Hors de la plage mesuree on BORNE, on n'extrapole jamais a la hausse :
    extrapoler une persistance favorable au-dela des donnees est precisement
    la facon dont un backtest se ment a lui-meme.

    Le resultat est en outre plafonne par le maximum jamais mesure a cet
    horizon. Aucune combinaison d'entrees ne peut donc produire une capture
    superieure a ce qui a ete observe.
    """
    if horizon_h <= 0:
        raise ValueError("horizon <= 0")
    obs = abs(observed_apr)
    thresholds = sorted(MEASURED_SURFACE)

    def at_threshold(t: float) -> float:
        row = MEASURED_SURFACE[t]
        hs = HORIZONS_MEASURED
        if horizon_h <= hs[0]:
            return row[hs[0]]
        if horizon_h >= hs[-1]:
            return row[hs[-1]]
        for h0, h1 in zip(hs, hs[1:]):
            if h0 <= horizon_h <= h1:
                return _interp(horizon_h, h0, h1, row[h0], row[h1])
        raise AssertionError("plage d'horizons incoherente")

    if obs <= thresholds[0]:
        # Sous le plus petit seuil mesure : on rabat proportionnellement,
        # jamais au-dessus de la valeur mesuree a ce seuil.
        base = at_threshold(thresholds[0])
        value = base * (obs / thresholds[0])
    elif obs >= thresholds[-1]:
        value = at_threshold(thresholds[-1])
    else:
        lo = max(t for t in thresholds if t <= obs)
        hi = min(t for t in thresholds if t >= obs)
        value = _interp(obs, lo, hi, at_threshold(lo), at_threshold(hi))

    ceiling = max(at_threshold(t) for t in thresholds)
    return min(value, ceiling)


def decay_factor(observed_apr: float, horizon_h: int) -> float:
    """Part du differentiel observe qui survit. Sert au rapport, pas au calcul."""
    obs = abs(observed_apr)
    if obs <= 0:
        return 0.0
    return realized_apr(obs, horizon_h) / obs


def expected_capture_bps(observed_apr: float, horizon_h: int) -> float:
    """Capture BRUTE attendue sur l'horizon, en bps du notionnel.

    Jamais le taux instantane : le realise mesure, ramene a la duree.
    Une valeur negative (le differentiel se retourne) est conservee telle
    quelle — la borner a zero cacherait une famille perdante.
    """
    return realized_apr(observed_apr, horizon_h) * (horizon_h / HOURS_PER_YEAR) * 10_000.0


@dataclass(frozen=True)
class VenueFunding:
    """Taux de funding d'une venue, avec sa cadence REELLE.

    `period_h` n'est jamais suppose : OKX paie toutes les 4 h sur 90 de ses
    142 instruments et toutes les 8 h sur les autres. Coder 8 h en dur
    divisait le taux horaire par deux sur 63 % de l'univers et fabriquait des
    differentiels spectaculaires qui n'existaient pas.
    """
    venue: str
    rate: float           # taux du DERNIER paiement, pour sa periode
    period_h: float
    ts_ms: int

    def __post_init__(self) -> None:
        if self.period_h <= 0:
            raise ValueError(f"{self.venue}: cadence de funding <= 0")

    @property
    def rate_per_hour(self) -> float:
        return self.rate / self.period_h

    @property
    def apr(self) -> float:
        return self.rate_per_hour * HOURS_PER_YEAR


class FundingSpreadOpportunity(Opportunity):
    """Detecte un differentiel de funding entre deux venues sur le meme actif.

    Le contexte doit porter, dans `ctx.funding`, les deux venues sous la forme
    {"long_venue": VenueFunding, "short_venue": VenueFunding}. Le detecteur ne
    fait AUCUN appel reseau : la collecte est separee de l'interpretation.
    """

    name = "funding_spread"
    requires = ("funding",)

    def __init__(self, min_abs_apr: float = 0.20,
                 horizon_h: int = DEFAULT_HORIZON_H):
        """`min_abs_apr` est un seuil de BRUIT, pas un seuil economique.

        Il evite d'emettre des milliers de candidates a 0,1 %/an qui seront
        toutes rejetees par l'economie. Le vrai filtre reste `evaluate()`,
        qui confronte la capture aux couts reels. Baisser ce seuil ne cree
        aucune opportunite ; il ne fait qu'allonger le journal.
        """
        if min_abs_apr < 0:
            raise ValueError("min_abs_apr negatif")
        self.min_abs_apr = min_abs_apr
        self.horizon_h = horizon_h

    def detect(self, ctx: MarketContext) -> DetectionResult:
        f = ctx.funding or {}
        a = f.get("long_venue")
        b = f.get("short_venue")
        if not isinstance(a, VenueFunding) or not isinstance(b, VenueFunding):
            return DetectionResult.insufficient(
                "les deux venues doivent fournir un funding date",
                missing=["funding.long_venue", "funding.short_venue"])

        diff_apr = b.apr - a.apr
        if abs(diff_apr) < self.min_abs_apr:
            return DetectionResult.ok([])

        # d > 0 : la venue `short_venue` paie davantage -> on y est SHORT et
        # on est LONG sur l'autre. Le sens de la candidate est celui de la
        # jambe portee sur l'instrument du contexte (`long_venue`).
        direction = Direction.LONG if diff_apr > 0 else Direction.SHORT
        gross = expected_capture_bps(diff_apr, self.horizon_h)

        capacity = ctx.extras.get("capacity_usd")
        cand = Candidate(
            ts_utc=ctx.ts_utc,
            instrument=ctx.instrument,
            opportunity_type=self.name,
            direction=direction,
            gross_capture_bps=gross,
            capacity_usd=capacity,
            provenance=Provenance(
                exchange=f"{a.venue}+{b.venue}",
                endpoint="funding-rate (public)",
                fetched_at=utc_now_iso(),
                inst_id=ctx.instrument.inst_id,
                extra={"decay_source": DECAY_SOURCE}),
            family=FAMILY,
            candidate_id=f"fund-{ctx.instrument.base}-{max(a.ts_ms, b.ts_ms)}",
            causal_reference_ts_ms=max(a.ts_ms, b.ts_ms),
            expected_horizon_ms=self.horizon_h * 3_600_000,
            required_execution="TAKER",
            invalidation_conditions={
                "differentiel_se_referme": "|d| < seuil avant l'entree",
                "une_jambe_non_remplie": "position directionnelle nue",
            },
            legs=[
                {"venue": a.venue, "side": direction.value,
                 "funding_apr": a.apr, "period_h": a.period_h},
                {"venue": b.venue,
                 "side": Direction.SHORT.value if direction is Direction.LONG
                         else Direction.LONG.value,
                 "funding_apr": b.apr, "period_h": b.period_h},
            ],
            observed_state={
                "diff_apr": diff_apr,
                "horizon_h": self.horizon_h,
                "decay_factor": decay_factor(diff_apr, self.horizon_h),
                "realized_apr_attendu": realized_apr(diff_apr, self.horizon_h),
                "capture_si_aucune_decroissance_bps":
                    abs(diff_apr) * (self.horizon_h / HOURS_PER_YEAR) * 10_000.0,
            },
            metadata={
                "haircut_applique": True,
                "decay_source": DECAY_SOURCE,
                "famille_statut": "FERMEE par mesure — detecteur en veille",
            },
        )
        return DetectionResult.ok([cand])
