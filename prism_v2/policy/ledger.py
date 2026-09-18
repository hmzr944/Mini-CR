"""LE REGISTRE DES DECISIONS : une ligne par decision, auditable.

Le mandat impose que chaque decision simulee porte son etat, son action, ses
prix, sa duree, sa decomposition de couts, son PnL net et le capital qu'elle
immobilise. Une decision qui ne peut pas remplir ces champs n'est pas
enregistree : elle est refusee.

Ce registre ne calcule aucun PnL lui-meme et n'en invente aucun. Il recoit
ce que l'execution a produit, et il REFUSE toute incoherence : un net qui ne
se recompose pas a partir de ses termes est une erreur de programmation, pas
une valeur a stocker.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from prism_v2.policy.action import EXECUTABLE, UPPER_BOUND, Action

#: Tolerance de recomposition du net, en points de base. Purement numerique.
_EPS = 1e-6


@dataclass(frozen=True)
class Decision:
    """Une decision, complete. Tous les champs de la section 27 du mandat."""
    ts_ms: int
    instrument: str
    state: Dict[str, float]
    action: Action
    target_position_usd: float      # signe : + long, - short, 0 si NO_TRADE
    entry_px: Optional[float]
    exit_px: Optional[float]
    size_usd: float
    holding_s: float
    gross_bps: float
    fee_bps: float
    spread_bps: float
    slippage_bps: float
    adverse_selection_bps: Optional[float]   # None quand la notion ne
                                             # s'applique pas (taker)
    net_bps: float
    capital_usd: float              # marge reellement immobilisee
    status: str
    predicted_bps: float            # ce que le modele attendait, pour l'audit

    def __post_init__(self) -> None:
        if self.action.is_trade:
            if self.entry_px is None or self.exit_px is None:
                raise ValueError("une action de marche exige deux prix")
            if self.size_usd <= 0 or self.capital_usd <= 0:
                raise ValueError("une action de marche exige taille et marge")
            recompose = self.gross_bps - self.fee_bps - self.slippage_bps
            if abs(recompose - self.net_bps) > _EPS:
                raise ValueError(
                    f"net incoherent : {self.net_bps} != {recompose}")
        else:
            if self.net_bps != 0.0 or self.size_usd != 0.0 \
                    or self.capital_usd != 0.0:
                raise ValueError("NO_TRADE ne coute ni ne rapporte rien")

    @property
    def net_usd(self) -> float:
        return self.net_bps / 10_000.0 * self.size_usd


@dataclass
class Ledger:
    """Le journal, et les agregats economiques qu'il autorise.

    La seule chose que ce journal refuse absolument : melanger dans un meme
    total une decision EXECUTABLE et une decision qui depend d'un
    remplissage passif inconnu. Les deux totaux sont tenus separement.
    """
    reserved_capital_usd: float
    decisions: List[Decision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.reserved_capital_usd <= 0:
            raise ValueError("le capital reserve doit etre > 0")

    def record(self, d: Decision) -> None:
        self.decisions.append(d)

    # ------------------------------------------------------------------ vues
    def of_status(self, status: str) -> List[Decision]:
        return [d for d in self.decisions if d.action.is_trade
                and d.status == status]

    @property
    def trades(self) -> List[Decision]:
        return [d for d in self.decisions if d.action.is_trade]

    # ------------------------------------------------------------- economie
    def net_usd(self, status: str = EXECUTABLE) -> float:
        return sum(d.net_usd for d in self.of_status(status))

    def span_days(self) -> float:
        if len(self.decisions) < 2:
            return 0.0
        ts = [d.ts_ms for d in self.decisions]
        return (max(ts) - min(ts)) / 86_400_000.0

    def capital_time_weighted_usd(self, status: str = EXECUTABLE) -> float:
        """Capital moyen REELLEMENT immobilise, pondere par la duree.

        Diagnostic de rotation. Ce n'est PAS le denominateur economique :
        le capital qui dort ne rapporte rien, et l'objectif 1 000 -> 5 000
        porte sur la somme entiere. Le denominateur qui mord est
        reserved_capital_usd.
        """
        sec = self.span_days() * 86_400.0
        if sec <= 0:
            return 0.0
        return sum(d.capital_usd * d.holding_s
                   for d in self.of_status(status)) / sec

    def bps_per_day(self, status: str = EXECUTABLE) -> Optional[float]:
        """PnL net / capital RESERVE / jour, en points de base."""
        j = self.span_days()
        if j <= 0:
            return None
        return self.net_usd(status) / self.reserved_capital_usd * 10_000.0 / j

    def utilisation(self, status: str = EXECUTABLE) -> float:
        """Capital moyen immobilise rapporte au capital reserve.

        Une premiere version comptait la FRACTION DU TEMPS passee en
        position et la plafonnait a 1. Sur un panneau de quinze instruments
        tenus en parallele, cette somme depasse 1 des que plusieurs
        positions coexistent, et le plafonnement le cachait au lieu de le
        montrer. La definition retenue rapporte le capital, pas le temps :
        elle vaut 1 quand la totalite du capital reserve travaille en
        permanence, et peut valoir plus si les positions en exigent plus —
        auquel cas le chiffre le DIT au lieu de l'absorber.
        """
        if self.reserved_capital_usd <= 0:
            return 0.0
        return self.capital_time_weighted_usd(status) / self.reserved_capital_usd

    def turnover_per_day(self, status: str = EXECUTABLE) -> Optional[float]:
        j = self.span_days()
        if j <= 0:
            return None
        return sum(d.size_usd for d in self.of_status(status)) \
            / self.reserved_capital_usd / j

    def drawdown_usd(self, status: str = EXECUTABLE) -> float:
        """Pire recul de la courbe de PnL cumule, dans l'ordre du temps."""
        cum, pic, dd = 0.0, 0.0, 0.0
        for d in sorted(self.of_status(status), key=lambda d: d.ts_ms):
            cum += d.net_usd
            pic = max(pic, cum)
            dd = max(dd, pic - cum)
        return dd

    def peak_capital_usd(self, status: str = EXECUTABLE) -> float:
        """Marge SIMULTANEE maximale exigee par la politique.

        Le capital moyen pondere par la duree peut tenir dans le capital
        reserve alors que le pic ne tient pas : sur un panneau de quinze
        instruments, plusieurs positions coexistent. Si ce pic depasse le
        capital reserve, la politique n'est tout simplement PAS jouable sur
        ce compte, et le PnL rapporte a ce capital serait un PnL rapporte a
        de l'argent que le compte n'a jamais eu.

        Balayage des ouvertures et des fermetures dans l'ordre du temps.
        """
        ev = []
        for d in self.of_status(status):
            ev.append((d.ts_ms, +d.capital_usd))
            ev.append((d.ts_ms + int(d.holding_s * 1000), -d.capital_usd))
        ev.sort(key=lambda x: (x[0], x[1]))   # fermetures avant ouvertures
        cour, pic = 0.0, 0.0
        for _, delta in ev:
            cour += delta
            pic = max(pic, cour)
        return pic

    def fits_in_reserve(self, status: str = EXECUTABLE) -> bool:
        """La politique tient-elle dans le capital reserve a tout instant ?"""
        return self.peak_capital_usd(status) <= self.reserved_capital_usd

    def summary(self, status: str = EXECUTABLE) -> Dict[str, object]:
        t = self.of_status(status)
        nets = [d.net_bps for d in t]
        return {
            "statut": status,
            "decisions": len(self.decisions),
            "trades": len(t),
            "part_no_trade": (1.0 - len(self.trades) / len(self.decisions))
            if self.decisions else 0.0,
            "net_usd": self.net_usd(status),
            "net_bps_moyen": (sum(nets) / len(nets)) if nets else 0.0,
            "net_bps_median": _median(nets),
            "bps_par_jour": self.bps_per_day(status),
            "capital_reserve_usd": self.reserved_capital_usd,
            "capital_pondere_usd": self.capital_time_weighted_usd(status),
            "capital_pic_usd": self.peak_capital_usd(status),
            "tient_dans_la_reserve": self.fits_in_reserve(status),
            "utilisation": self.utilisation(status),
            "turnover_par_jour": self.turnover_per_day(status),
            "drawdown_usd": self.drawdown_usd(status),
            "duree_moyenne_s": (sum(d.holding_s for d in t) / len(t))
            if t else 0.0,
            "cout_execution_bps": (sum(d.fee_bps + d.slippage_bps for d in t)
                                   / len(t)) if t else 0.0,
            "jours": self.span_days(),
        }


def _median(xs: List[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
