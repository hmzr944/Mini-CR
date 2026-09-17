"""Tableau de bord economique. Repond, a tout instant, a une seule question :

    le travail en cours rapproche-t-il PRISM de l'objectif, ou l'en eloigne ?

REGLE DE CONSTRUCTION. Chaque ligne porte sa qualite de preuve. Une grandeur
non mesuree vaut INCONNU et s'affiche INCONNU — jamais zero, jamais une
moyenne, jamais un meilleur cas. C'est la seule facon qu'un tableau de bord
ne devienne pas un instrument d'auto-persuasion.

CE QUE CE MODULE NE FAIT PAS. Il ne calcule aucun PnL. Il agrege des mesures
faites ailleurs et les confronte a l'objectif. Un PnL papier n'y entre jamais
dans la colonne « realise » : la separation RECHERCHE / PAPIER / DEMO / LIVE
est portee par le champ `mode` et verifiee par test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .modes import SystemMode

#: Qualites de preuve, identiques au vocabulaire du reste du projet.
OBSERVED = "OBSERVE"
MEASURED = "MESURE"
DERIVED = "DERIVE"
ESTIMATED = "ESTIME"
UNKNOWN = "INCONNU"

_QUALITIES = (OBSERVED, MEASURED, DERIVED, ESTIMATED, UNKNOWN)


@dataclass(frozen=True)
class Metric:
    """Une grandeur, sa valeur, sa qualite de preuve et sa source.

    `value is None` signifie INCONNU et impose `quality == UNKNOWN`. On ne
    peut pas declarer connue une valeur absente, ni absente une valeur
    declaree connue : les deux sens sont verrouilles.
    """

    name: str
    value: Optional[float]
    unit: str
    quality: str
    source: str = ""

    def __post_init__(self) -> None:
        if self.quality not in _QUALITIES:
            raise ValueError(f"qualite inconnue: {self.quality}")
        if self.value is None and self.quality != UNKNOWN:
            raise ValueError(f"{self.name}: valeur absente mais qualite "
                             f"{self.quality} — un INCONNU ne se deguise pas")
        if self.value is not None and self.quality == UNKNOWN:
            raise ValueError(f"{self.name}: valeur presente mais declaree "
                             f"INCONNU — declare sa vraie qualite")

    def render(self) -> str:
        v = "INCONNU" if self.value is None else f"{self.value:,.4g} {self.unit}"
        src = f"  [{self.source}]" if self.source else ""
        return f"{self.name:<38}{v:>22}   {self.quality}{src}"


@dataclass
class Objective:
    """L'objectif, traduit en seuil mesurable sur le capital."""

    capital_eur: float
    target_eur_per_day: float
    multiple: float
    days: float

    def bps_per_day_from_daily_target(self) -> float:
        """20 EUR/jour sur 1 000 EUR = 200 bps/jour. Arithmetique simple."""
        if self.capital_eur <= 0:
            raise ValueError("capital_eur doit etre > 0")
        return self.target_eur_per_day / self.capital_eur * 10_000.0

    def bps_per_day_from_multiple(self) -> float:
        """x5 en 60 jours, en interet COMPOSE — la lecture la plus indulgente."""
        if self.days <= 0 or self.multiple <= 0:
            raise ValueError("days et multiple doivent etre > 0")
        return (self.multiple ** (1.0 / self.days) - 1.0) * 10_000.0

    def binding_bps_per_day(self) -> float:
        """Le seuil qui mord : le PLUS EXIGEANT des deux formulations.

        Prendre le plus doux laisserait passer une economie qui rate l'une des
        deux promesses. Les deux ont ete demandees, les deux comptent.
        """
        return max(self.bps_per_day_from_daily_target(),
                   self.bps_per_day_from_multiple())


@dataclass
class Dashboard:
    """Etat economique complet. Tout ce qui n'est pas mesure est INCONNU."""

    mode: SystemMode
    objective: Objective
    metrics: List[Metric] = field(default_factory=list)
    best_economy_bps_per_day: Optional[float] = None
    best_economy_label: str = ""
    bottleneck: str = ""
    next_action: str = ""
    next_action_why: str = ""

    def add(self, metric: Metric) -> "Dashboard":
        self.metrics.append(metric)
        return self

    def realised_pnl_eur(self) -> Metric:
        """PnL NET REALISE. En dehors du LIVE il vaut zero, pas « le papier ».

        C'est la ligne que l'on serait le plus tente d'embellir ; elle est donc
        derivee du mode et non fournie par l'appelant.
        """
        if self.mode is SystemMode.LIVE:
            return Metric("PnL net realise", None, "EUR", UNKNOWN,
                          "LIVE non implemente")
        return Metric("PnL net realise", 0.0, "EUR", MEASURED,
                      f"mode {self.mode.value} — aucun ordre reel n'existe")

    def distance_to_objective(self) -> Optional[float]:
        """Rapport meilleure economie demontree / seuil. None si inconnu."""
        if self.best_economy_bps_per_day is None:
            return None
        return self.best_economy_bps_per_day / self.objective.binding_bps_per_day()

    def eur_per_day_at_best(self) -> Optional[float]:
        if self.best_economy_bps_per_day is None:
            return None
        return (self.best_economy_bps_per_day / 10_000.0
                * self.objective.capital_eur)

    def render(self) -> str:
        o = self.objective
        seuil = o.binding_bps_per_day()
        lines = [
            "=" * 78,
            f"TABLEAU DE BORD ECONOMIQUE — mode {self.mode.value}",
            "=" * 78,
            "",
            f"{'capital de depart':<38}{o.capital_eur:>18,.0f} EUR",
            f"{'cible quotidienne':<38}{o.target_eur_per_day:>18,.0f} EUR/jour"
            f"  = {o.bps_per_day_from_daily_target():.0f} bps/jour",
            f"{'cible de multiple':<38}x{o.multiple:>17,.0f} en {o.days:.0f} j"
            f"  = {o.bps_per_day_from_multiple():.0f} bps/jour",
            f"{'SEUIL QUI MORD':<38}{seuil:>18,.0f} bps/jour de capital",
            "",
            "-" * 78,
            self.realised_pnl_eur().render(),
        ]
        for m in self.metrics:
            lines.append(m.render())
        lines.append("-" * 78)
        if self.best_economy_bps_per_day is None:
            lines.append("MEILLEURE ECONOMIE DEMONTREE       INCONNU")
        else:
            d = self.distance_to_objective()
            e = self.eur_per_day_at_best()
            lines.append(f"MEILLEURE ECONOMIE DEMONTREE       "
                         f"{self.best_economy_bps_per_day:,.2f} bps/jour"
                         f"   ({self.best_economy_label})")
            lines.append(f"  soit, sur {o.capital_eur:,.0f} EUR            "
                         f"{e:,.2f} EUR/jour  contre {o.target_eur_per_day:,.0f} vises")
            lines.append(f"  DISTANCE A L'OBJECTIF             {d:.3f}x"
                         f"   (il manque un facteur {1.0/d:,.1f})"
                         if d and d > 0 else
                         "  DISTANCE A L'OBJECTIF             economie negative")
        lines.append("")
        lines.append(f"GOULOT D'ETRANGLEMENT : {self.bottleneck or 'INCONNU'}")
        lines.append(f"PROCHAINE ACTION      : {self.next_action or 'INCONNU'}")
        if self.next_action_why:
            lines.append(f"  pourquoi            : {self.next_action_why}")
        lines.append("=" * 78)
        return "\n".join(lines)
