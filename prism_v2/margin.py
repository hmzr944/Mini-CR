"""Marge reellement bloquee par l'exchange, lue dans son bareme public.

POURQUOI CE MODULE EXISTE. L'objectif du projet est un ratio :

    PnL net / capital immobilise / temps

Le numerateur a ete mesure des dizaines de fois. Le denominateur, lui,
n'etait calcule nulle part : `capital_efficiency.Family` recoit un PnL deja
exprime « en bps du capital immobilise », et ce capital est un nombre ecrit a
la main par celui qui declare la famille. Aucune ligne du depot ne demandait a
OKX combien il bloque reellement pour une position donnee.

Or le bareme n'est ni plat ni devinable. Il est **echelonne par la TAILLE de
la position, en contrats**. Sur BTC-USD-SWAP, une petite position exige 1 %
de marge initiale (levier 100) ; une grande en exige davantage, par paliers,
jusqu'a rendre la position impossible. Supposer un levier unique surestime
donc la rotation du capital exactement la ou la strategie grossit — c'est-a-
dire la ou le chiffre compte.

FAIL CLOSED. Si aucun palier ne couvre la taille demandee, la fonction rend
None. Elle n'extrapole jamais le dernier palier : au-dela du dernier palier,
l'exchange refuse la position, il ne la tarifie pas plus cher.

Source : GET /api/v5/public/position-tiers, endpoint public, sans
authentification. Aucune cle n'est requise et aucune n'est utilisee.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .contracts import usd_notional
from .instruments import InstrumentSpec

DEFAULT_TIERS_PATH = Path(__file__).parent / "data" / "margin_tiers.json"


@dataclass(frozen=True)
class MarginTier:
    """Un palier du bareme. Les tailles sont en CONTRATS, pas en USD."""

    tier: int
    min_sz: float
    max_sz: float
    imr: float          # marge initiale, fraction du notionnel
    mmr: float          # marge de maintien, fraction du notionnel
    max_lever: float

    def covers(self, contracts: float) -> bool:
        return self.min_sz <= contracts <= self.max_sz


@dataclass(frozen=True)
class MarginSchedule:
    """Bareme complet d'une famille d'instruments."""

    inst_family: str
    tiers: Sequence[MarginTier]

    def tier_for(self, contracts: float) -> Optional[MarginTier]:
        """Palier applicable, ou None si la taille sort du bareme.

        Une taille nulle ou negative n'a pas de palier : ce n'est pas une
        position.
        """
        if contracts <= 0:
            return None
        for t in self.tiers:
            if t.covers(contracts):
                return t
        return None

    def max_contracts(self) -> float:
        return max((t.max_sz for t in self.tiers), default=0.0)

    def initial_margin_usd(self, spec: InstrumentSpec, contracts: float,
                           price: float) -> Optional[float]:
        """Capital REELLEMENT bloque a l'ouverture, en USD.

        None si la taille sort du bareme : l'exchange refuserait la position.
        """
        t = self.tier_for(contracts)
        if t is None:
            return None
        return usd_notional(spec, contracts, price) * t.imr

    def maintenance_margin_usd(self, spec: InstrumentSpec, contracts: float,
                               price: float) -> Optional[float]:
        t = self.tier_for(contracts)
        if t is None:
            return None
        return usd_notional(spec, contracts, price) * t.mmr

    def effective_leverage(self, contracts: float) -> Optional[float]:
        """Levier REELLEMENT atteignable a cette taille.

        C'est 1 / imr du palier, et non le `lever` affiche sur l'instrument :
        celui-ci ne vaut que pour le premier palier.
        """
        t = self.tier_for(contracts)
        if t is None or t.imr <= 0:
            return None
        return 1.0 / t.imr

    def max_notional_usd(self, spec: InstrumentSpec, price: float
                         ) -> Optional[float]:
        """Notionnel maximal que le bareme autorise sur cet instrument."""
        m = self.max_contracts()
        if m <= 0:
            return None
        return usd_notional(spec, m, price)


def parse_tiers(rows: Sequence[Dict[str, object]]) -> List[MarginTier]:
    """Convertit la reponse OKX. Une ligne illisible est ECARTEE, pas devinee."""
    out: List[MarginTier] = []
    for r in rows:
        try:
            t = MarginTier(tier=int(str(r["tier"])), min_sz=float(str(r["minSz"])),
                           max_sz=float(str(r["maxSz"])), imr=float(str(r["imr"])),
                           mmr=float(str(r["mmr"])),
                           max_lever=float(str(r["maxLever"])))
        except (KeyError, TypeError, ValueError):
            continue
        if t.max_sz < t.min_sz or t.imr <= 0 or t.mmr <= 0:
            continue
        out.append(t)
    out.sort(key=lambda x: x.min_sz)
    return out


def schedules_from_payload(payloads: Dict[str, Sequence[Dict[str, object]]]
                           ) -> Dict[str, MarginSchedule]:
    out: Dict[str, MarginSchedule] = {}
    for fam, rows in payloads.items():
        tiers = parse_tiers(rows)
        if tiers:
            out[fam] = MarginSchedule(fam, tiers)
    return out


def load_schedules(path: Path = DEFAULT_TIERS_PATH
                   ) -> Dict[str, MarginSchedule]:
    """Lit le bareme mis en cache. Dictionnaire vide si absent : jamais devine."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return schedules_from_payload(raw.get("families") or {})


def capital_required_usd(legs: Sequence[tuple], schedules: Dict[str, MarginSchedule]
                         ) -> Optional[float]:
    """Capital bloque par une position a plusieurs jambes.

    `legs` est une suite de (spec, contracts, price).

    Les marges des jambes s'ADDITIONNENT. Un compte en mode portefeuille peut
    les compenser partiellement, mais cette compensation depend du mode de
    marge du compte, qui n'est pas observable ici. Additionner est la lecture
    prudente : elle ne fait jamais paraitre le capital plus petit qu'il n'est.

    None des qu'une jambe sort du bareme : une position dont une jambe est
    refusee n'a pas de cout en capital, elle n'existe pas.
    """
    total = 0.0
    for spec, contracts, price in legs:
        sch = schedules.get(spec.family) or schedules.get(
            spec.inst_id.rsplit("-", 1)[0])
        if sch is None:
            return None
        im = sch.initial_margin_usd(spec, contracts, price)
        if im is None:
            return None
        total += im
    return total
