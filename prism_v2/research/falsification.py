"""AGENT 5 — FALSIFICATION / RED TEAM : detruire les hypotheses.

Cet agent n'a pas pour but de valider. Il a pour but de TUER. Une hypothese
qui lui survit n'est pas vraie : elle est seulement encore debout.

Il ne peut JAMAIS etre desactive pour obtenir un meilleur resultat. Un test
architectural le verifie : aucun parametre du pipeline ne permet de le
contourner, et `FalsificationAgent.attack()` est le seul chemin vers le
statut SURVIVED_FALSIFICATION.

Les controles les plus mordants ne sont pas les evidents (look-ahead) mais
les silencieux :
  - OBSERVATIONS CORRELEES : echantillonner toutes les 500 ms un effet a
    horizon 30 s produit 60 fenetres qui se recouvrent. Le N nominal est
    60x trop optimiste. On calcule le N EFFECTIF non chevauchant.
  - EFFET SOUS LE SPREAD : un effet reel mais plus petit que le spread n'est
    pas capturable. C'est l'illusion la plus frequente.
  - PAS D'EXCES SUR LA REFERENCE : un mouvement moyen egal a celui du reste
    du temps n'est pas un edge, c'est la derive du marche.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .hypothesis import (
    Hypothesis, HypothesisStatus, MultipleTestingAccount, Relation,
)


class RejectionReason(str, enum.Enum):
    LOOK_AHEAD = "LOOK_AHEAD"
    DATA_LEAKAGE = "DATA_LEAKAGE"
    MULTIPLE_TESTING = "MULTIPLE_TESTING"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    CORRELATED_OBSERVATIONS = "CORRELATED_OBSERVATIONS"
    NO_EXCESS_OVER_BASELINE = "NO_EXCESS_OVER_BASELINE"
    EFFECT_BELOW_SPREAD = "EFFECT_BELOW_SPREAD"
    DENOMINATION_MISMATCH = "DENOMINATION_MISMATCH"
    UNSTABLE_OUT_OF_SAMPLE = "UNSTABLE_OUT_OF_SAMPLE"
    SELECTION_BIAS = "SELECTION_BIAS"
    REGIME_DEPENDENCE = "REGIME_DEPENDENCE"
    CAPACITY_ILLUSION = "CAPACITY_ILLUSION"
    LATENCY_ILLUSION = "LATENCY_ILLUSION"
    TRANSACTION_COST_OMISSION = "TRANSACTION_COST_OMISSION"
    STALE_QUOTES = "STALE_QUOTES"
    DUPLICATE_LIQUIDITY = "DUPLICATE_LIQUIDITY"
    UNREALISTIC_FILL = "UNREALISTIC_FILL"
    SURVIVORSHIP_BIAS = "SURVIVORSHIP_BIAS"


class FalsificationVerdict(str, enum.Enum):
    REJECTED = "REJECTED"
    SURVIVED = "SURVIVED"

    @property
    def survived(self) -> bool:
        return self is FalsificationVerdict.SURVIVED


#: N effectif minimal (fenetres NON chevauchantes) sous lequel aucune
#: conclusion n'est formulee. Seuil d'HONNETETE statistique, jamais abaisse
#: pour rendre un resultat presentable.
MIN_EFFECTIVE_N = 30

#: Part maximale de l'effet total qu'un seul sous-intervalle peut porter
#: avant qu'on parle de dependance de regime plutot que de relation.
MAX_REGIME_CONCENTRATION = 0.80


@dataclass
class FalsificationResult:
    verdict: FalsificationVerdict
    reasons: List[RejectionReason] = field(default_factory=list)
    details: List[str] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)
    effective_n: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict.value,
                "reasons": [r.value for r in self.reasons],
                "details": self.details, "checks_run": self.checks_run,
                "effective_n": self.effective_n}


@dataclass
class FalsificationContext:
    """Tout ce que le red team a besoin de savoir pour attaquer.

    Les grandeurs absentes ne sont pas supposees favorables : un controle qui
    ne peut pas s'exercer le dit, et son incapacite est enregistree.
    """

    sampling_step_ms: int
    typical_spread_bps: Optional[float] = None
    available_depth_usd: Optional[float] = None
    required_notional_usd: Optional[float] = None
    transport_delay_ms: Optional[float] = None
    holdout_relation: Optional[Relation] = None
    early_half_mean_bps: Optional[float] = None
    late_half_mean_bps: Optional[float] = None
    costs_applied: bool = False
    quote_currencies: Sequence[str] = ()
    book_age_ms: Optional[float] = None
    max_book_age_ms: int = 2_000
    #: Identifiants des carnets dont la profondeur a ete SOMMEE dans
    #: `available_depth_usd`. Deux entrees identiques signifient que la meme
    #: liquidite a ete comptee deux fois.
    depth_sources: Sequence[str] = ()
    #: Liquidite disponible AU MEILLEUR NIVEAU seulement.
    touch_depth_usd: Optional[float] = None
    #: L'etude suppose-t-elle un fill au prix du meilleur niveau ?
    fill_assumed_at_touch: bool = False
    #: Le seuil du phenomene a-t-il ete choisi en regardant l'echantillon ?
    threshold_selected_in_sample: bool = False
    #: L'univers d'instruments a-t-il ete filtre APRES avoir vu les resultats ?
    universe_filtered_on_outcome: bool = False
    #: L'hypothese pretend-elle deja a une rentabilite nette ?
    claims_profitability: bool = False


class FalsificationAgent:
    """Red team. Retourne REJECTED des qu'un controle mord."""

    name = "FALSIFICATION_AGENT"

    def __init__(self, accounting: MultipleTestingAccount,
                 min_effective_n: int = MIN_EFFECTIVE_N):
        self.accounting = accounting
        self.min_effective_n = min_effective_n

    # ── controles ─────────────────────────────────────────────────────────
    @staticmethod
    def effective_sample_size(relation: Relation, step_ms: int) -> float:
        """N effectif : nombre de fenetres NON chevauchantes.

        Echantillonner toutes les `step_ms` un effet a horizon `horizon_ms`
        cree des fenetres qui se recouvrent. Le N nominal surestime alors
        l'information disponible d'un facteur horizon/step.
        """
        if step_ms <= 0 or relation.horizon_ms <= 0:
            return float(relation.n)
        overlap = max(1.0, relation.horizon_ms / step_ms)
        return relation.n / overlap

    def attack(self, h: Hypothesis, ctx: FalsificationContext) -> FalsificationResult:
        reasons: List[RejectionReason] = []
        details: List[str] = []
        checks: List[str] = []
        r = h.relation

        # 1. Observations correlees — le controle le plus mordant.
        checks.append("correlated_observations")
        eff_n = self.effective_sample_size(r, ctx.sampling_step_ms)
        if eff_n < self.min_effective_n:
            reasons.append(RejectionReason.CORRELATED_OBSERVATIONS)
            details.append(
                f"N nominal {r.n} mais N EFFECTIF {eff_n:.1f} < {self.min_effective_n} "
                f"(horizon {r.horizon_ms}ms / pas {ctx.sampling_step_ms}ms : les "
                f"fenetres se recouvrent, le N nominal est optimiste)")

        # 2. Echantillon brut insuffisant.
        checks.append("insufficient_sample")
        if r.n < self.min_effective_n:
            reasons.append(RejectionReason.INSUFFICIENT_SAMPLE)
            details.append(f"N={r.n} < {self.min_effective_n}")

        # 3. Tests multiples.
        checks.append("multiple_testing")
        if not self.accounting.survives_correction(r.p_value):
            reasons.append(RejectionReason.MULTIPLE_TESTING)
            bh = self.accounting.benjamini_hochberg_threshold()
            details.append(
                f"p={r.p_value} ne survit pas a Benjamini-Hochberg "
                f"(seuil {bh}, sur {self.accounting.n_tests} tests)")

        # 4. Pas d'exces sur la reference.
        checks.append("excess_over_baseline")
        if abs(r.excess_bps) <= 1e-9:
            reasons.append(RejectionReason.NO_EXCESS_OVER_BASELINE)
            details.append(
                f"mouvement conditionnel {r.mean_forward_bps:.4f} bps identique a "
                f"la reference {r.baseline_mean_bps:.4f} bps : c'est la derive "
                "du marche, pas un effet")

        # 5. Effet sous le spread — illusion la plus frequente.
        checks.append("effect_below_spread")
        if ctx.typical_spread_bps is None:
            details.append("spread inconnu : controle 'effet sous le spread' "
                           "NON EXERCE (absence de donnee, pas un succes)")
        elif abs(r.excess_bps) <= ctx.typical_spread_bps:
            reasons.append(RejectionReason.EFFECT_BELOW_SPREAD)
            details.append(
                f"exces {abs(r.excess_bps):.4f} bps <= spread "
                f"{ctx.typical_spread_bps:.4f} bps : effet peut-etre reel, "
                "mais non capturable")

        # 6. Melange de denominations.
        checks.append("denomination_mismatch")
        quotes = {q.upper() for q in ctx.quote_currencies if q}
        if len(quotes) > 1 and not quotes <= {"USD", "USDT", "USDC"}:
            reasons.append(RejectionReason.DENOMINATION_MISMATCH)
            details.append(f"devises non comparables sans taux de change: {sorted(quotes)}")

        # 7. Instabilite hors echantillon.
        checks.append("out_of_sample_stability")
        if ctx.holdout_relation is not None:
            ho = ctx.holdout_relation
            same_sign = (r.excess_bps * ho.excess_bps) > 0
            if not same_sign:
                reasons.append(RejectionReason.UNSTABLE_OUT_OF_SAMPLE)
                details.append(
                    f"signe inverse sur le holdout: decouverte {r.excess_bps:+.4f} "
                    f"bps vs holdout {ho.excess_bps:+.4f} bps")
            elif abs(ho.excess_bps) < abs(r.excess_bps) * 0.25:
                reasons.append(RejectionReason.UNSTABLE_OUT_OF_SAMPLE)
                details.append(
                    f"effet s'effondre hors echantillon: {abs(ho.excess_bps):.4f} "
                    f"< 25% de {abs(r.excess_bps):.4f} bps")
        else:
            details.append("aucun holdout disponible : controle de stabilite "
                           "NON EXERCE")

        # 8. Dependance de regime.
        checks.append("regime_dependence")
        if ctx.early_half_mean_bps is not None and ctx.late_half_mean_bps is not None:
            total = abs(ctx.early_half_mean_bps) + abs(ctx.late_half_mean_bps)
            if total > 0:
                concentration = max(abs(ctx.early_half_mean_bps),
                                    abs(ctx.late_half_mean_bps)) / total
                if concentration > MAX_REGIME_CONCENTRATION:
                    reasons.append(RejectionReason.REGIME_DEPENDENCE)
                    details.append(
                        f"{concentration:.0%} de l'effet vient d'une seule moitie "
                        "de la fenetre : dependance de regime, pas une relation")

        # 9. Illusion de capacite.
        checks.append("capacity_illusion")
        if ctx.available_depth_usd is not None and ctx.required_notional_usd:
            if ctx.available_depth_usd < ctx.required_notional_usd:
                reasons.append(RejectionReason.CAPACITY_ILLUSION)
                details.append(
                    f"profondeur ${ctx.available_depth_usd:,.0f} < notionnel requis "
                    f"${ctx.required_notional_usd:,.0f}")

        # 10. Illusion de latence : l'effet meurt-il avant l'arrivee de l'ordre ?
        checks.append("latency_illusion")
        if ctx.transport_delay_ms is not None:
            if r.horizon_ms <= ctx.transport_delay_ms:
                reasons.append(RejectionReason.LATENCY_ILLUSION)
                details.append(
                    f"horizon {r.horizon_ms}ms <= delai de transport "
                    f"{ctx.transport_delay_ms:.0f}ms : l'effet est deja termine "
                    "quand l'ordre arriverait")

        # 11. Carnet perime.
        checks.append("stale_quotes")
        if ctx.book_age_ms is not None and ctx.book_age_ms > ctx.max_book_age_ms:
            reasons.append(RejectionReason.STALE_QUOTES)
            details.append(f"carnet age de {ctx.book_age_ms:.0f}ms")

        # 12. Omission des couts de transaction.
        #     Ne pas avoir applique les couts n'est pas une faute au stade
        #     DISCOVERY : c'en est une des que l'hypothese PRETEND etre
        #     rentable. C'est cette pretention qui est rejetee, pas l'absence.
        checks.append("transaction_cost_omission")
        if not ctx.costs_applied:
            if ctx.claims_profitability:
                reasons.append(RejectionReason.TRANSACTION_COST_OMISSION)
                details.append("rentabilite revendiquee alors qu'AUCUN cout n'a "
                               "ete applique : le brut n'est pas un resultat")
            else:
                details.append("couts non encore appliques a ce stade : l'hypothese "
                               "ne peut PAS etre declaree rentable ici")

        # 13. Fuite de donnee : la primitive regarde-t-elle vers l'avant ?
        checks.append("data_leakage")
        for f in h.features_used:
            if "forward" in f or "future" in f or "next" in f:
                reasons.append(RejectionReason.DATA_LEAKAGE)
                details.append(f"primitive '{f}' semble regarder vers l'avant")

        # 14. Look-ahead : la condition precede-t-elle bien l'issue ?
        checks.append("look_ahead")
        if r.horizon_ms <= 0:
            reasons.append(RejectionReason.LOOK_AHEAD)
            details.append(f"horizon {r.horizon_ms}ms <= 0 : l'issue ne suit pas "
                           "la condition")

        # 15. Liquidite comptee deux fois.
        #     Sommer la profondeur de deux carnets qui publient le MEME livre
        #     (ou le meme instrument vu par deux canaux) double une capacite
        #     qui n'existe qu'une fois.
        checks.append("duplicate_liquidity")
        if ctx.depth_sources:
            counts: Dict[str, int] = {}
            for src in ctx.depth_sources:
                counts[src] = counts.get(src, 0) + 1
            dupes = sorted(k for k, n in counts.items() if n > 1)
            if dupes:
                reasons.append(RejectionReason.DUPLICATE_LIQUIDITY)
                details.append(
                    f"profondeur agregee sur des sources repetees {dupes} : la "
                    "meme liquidite est comptee plusieurs fois")
        else:
            details.append("origine de la profondeur non declaree : controle "
                           "'liquidite dupliquee' NON EXERCE")

        # 16. Fill irrealiste : servir plus que ce que le meilleur niveau porte,
        #     au prix du meilleur niveau.
        checks.append("unrealistic_fill")
        if ctx.fill_assumed_at_touch:
            if ctx.touch_depth_usd is None:
                details.append("fill suppose au touch mais profondeur du touch "
                               "inconnue : controle NON EXERCE")
            elif (ctx.required_notional_usd
                  and ctx.required_notional_usd > ctx.touch_depth_usd):
                reasons.append(RejectionReason.UNREALISTIC_FILL)
                details.append(
                    f"fill suppose au meilleur niveau pour "
                    f"${ctx.required_notional_usd:,.0f} alors qu'il ne porte que "
                    f"${ctx.touch_depth_usd:,.0f} : le reste traverse le carnet")

        # 17. Biais de selection : seuil choisi DANS l'echantillon sans holdout.
        checks.append("selection_bias")
        if ctx.threshold_selected_in_sample and ctx.holdout_relation is None:
            reasons.append(RejectionReason.SELECTION_BIAS)
            details.append(
                "le seuil du phenomene a ete choisi en regardant ces memes "
                "donnees et AUCUN holdout ne vient le confirmer : l'effet mesure "
                "inclut le choix du seuil")

        # 18. Biais du survivant : univers filtre apres coup sur le resultat.
        checks.append("survivorship_bias")
        if ctx.universe_filtered_on_outcome:
            reasons.append(RejectionReason.SURVIVORSHIP_BIAS)
            details.append(
                "l'univers d'instruments a ete restreint APRES observation des "
                "resultats : les instruments ecartes faisaient partie du test")

        verdict = (FalsificationVerdict.REJECTED if reasons
                   else FalsificationVerdict.SURVIVED)
        return FalsificationResult(verdict=verdict, reasons=reasons, details=details,
                                   checks_run=checks, effective_n=eff_n)

    def apply(self, h: Hypothesis, ctx: FalsificationContext
              ) -> tuple[Hypothesis, FalsificationResult]:
        """Attaque et produit la NOUVELLE VERSION portant le verdict."""
        res = self.attack(h, ctx)
        status = (HypothesisStatus.SURVIVED_FALSIFICATION if res.verdict.survived
                  else HypothesisStatus.REJECTED)
        return h.with_status(status, tests=res.checks_run,
                             reasons=[r.value for r in res.reasons]), res
