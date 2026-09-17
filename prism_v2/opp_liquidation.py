#!/usr/bin/env python3
"""OPPORTUNITE — fade du flux de liquidation force, en direct.

Regle GELEE dans prism_v2/LIQUIDATION_PROTOCOL.md avant que les donnees de
test existent.

POURQUOI CE DETECTEUR EXISTE MAINTENANT PLUTOT QU'APRES LA COLLECTE.
Le barreau ou j'ai ecrit m'attendre a mourir est la LIQUIDITE : le prix se
deplace de 81 bps pendant une minute de vente forcee, mais le carnet est vide
precisement a cet instant. Or cette question est INVERIFIABLE sur donnees
historiques — je n'ai pas le carnet a l'instant du declenchement, et aucune
API ne le reconstitue.

Seul un observateur en direct peut la trancher. Ce detecteur existe donc pour
mesurer la profondeur et le spread AU MOMENT OU LE FLUX FRAPPE, ce qu'aucune
rediffusion ne permettra jamais. C'est l'instrument qui peut fermer la famille
en quelques heures plutot qu'en quatorze jours.

L'ESPERANCE UTILISEE EST LA PRUDENTE, PAS LA FLATTEUSE.
La mesure exploratoire a produit deux chiffres : 61,9 bps sur le sous-ensemble
a flux ample (t=3,04) et 12,47 bps sur l'ensemble conditionnel complet
(t=1,31). Le premier est issu d'une selection apres coup parmi douze
combinaisons ; le retenir reviendrait a faire entrer dans le moteur le biais
que le protocole existe pour exclure. On retient donc 12,47 bps, non
significatif, marque comme tel — et l'economie tranchera.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.core_types import Direction, Provenance, utc_now_iso
from prism_v2.liquidation_flow import (
    AMPLITUDE_THRESHOLD, HOLD_MINUTES, IMBALANCE_THRESHOLD, Trigger,
)
from prism_v2.opportunity import (
    Candidate, DetectionResult, MarketContext, Opportunity,
)

FAMILY = "FORCED_FLOW_FADE"

#: Esperance de capture, en bps du notionnel, sur l'horizon gele de 30 min.
#: MESUREE sur l'ensemble conditionnel COMPLET (1 528 minutes, 21 instruments),
#: t = 1,31 — NON SIGNIFICATIVE. C'est volontairement le chiffre prudent.
EXPECTED_FADE_BPS = 12.47
EXPECTED_SOURCE = ("prism_v2/LIQUIDATION_PROTOCOL.md — ensemble conditionnel "
                   "complet, 24 h, t=1,31, NON significatif")


@dataclass(frozen=True)
class ForcedFlowState:
    """Etat du flux force d'un instrument, a la minute courante.

    `amplitude` est deja normalisee par la mediane glissante CAUSALE de
    l'instrument : le detecteur ne recalcule rien et ne peut donc pas
    introduire de look-ahead par megarde.
    """
    instrument: str
    minute_ms: int
    imbalance: float
    amplitude: float
    forced_total_usd: float

    def triggers(self) -> bool:
        return (abs(self.imbalance) >= IMBALANCE_THRESHOLD
                and self.amplitude >= AMPLITUDE_THRESHOLD)

    def direction(self) -> Direction:
        """Fade : on achete quand on force a vendre."""
        return Direction.LONG if self.imbalance > 0 else Direction.SHORT


class ForcedFlowFadeOpportunity(Opportunity):
    """Detecte un desequilibre de liquidation forcee ample, et le fade.

    Ne fait aucun appel reseau : l'etat lui est fourni. La collecte reste
    separee de l'interpretation.
    """

    name = "forced_flow_fade"
    requires = ("extras",)

    def detect(self, ctx: MarketContext) -> DetectionResult:
        state = (ctx.extras or {}).get("forced_flow")
        if not isinstance(state, ForcedFlowState):
            return DetectionResult.insufficient(
                "etat de flux force absent du contexte",
                missing=["extras.forced_flow"])

        if not state.triggers():
            return DetectionResult.ok([])

        cand = Candidate(
            ts_utc=ctx.ts_utc,
            instrument=ctx.instrument,
            opportunity_type=self.name,
            direction=state.direction(),
            gross_capture_bps=EXPECTED_FADE_BPS,
            capacity_usd=(ctx.extras or {}).get("capacity_usd"),
            provenance=Provenance(
                exchange="OKX", endpoint="liquidation-orders (public)",
                fetched_at=utc_now_iso(), inst_id=ctx.instrument.inst_id,
                extra={"expected_source": EXPECTED_SOURCE}),
            family=FAMILY,
            candidate_id=f"flow-{ctx.instrument.base}-{state.minute_ms}",
            causal_reference_ts_ms=state.minute_ms,
            expected_horizon_ms=HOLD_MINUTES * 60_000,
            required_execution="TAKER",
            invalidation_conditions={
                "carnet_vide": "la profondeur au touch ne permet pas d'entrer",
                "flux_continue": "le flux force ne s'arrete pas et le prix "
                                 "poursuit au lieu de revenir",
            },
            observed_state={
                "imbalance": state.imbalance,
                "amplitude": state.amplitude,
                "forced_total_usd": state.forced_total_usd,
                "minute_ms": state.minute_ms,
                "hold_minutes": HOLD_MINUTES,
            },
            metadata={
                "esperance_source": EXPECTED_SOURCE,
                "esperance_significative": False,
                "famille_statut": "NON VALIDEE — collecte en cours, "
                                  "verdict a 14 jours minimum",
            },
        )
        return DetectionResult.ok([cand])
