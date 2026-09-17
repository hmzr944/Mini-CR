"""Recensement des CAPTURES PAR FLUX, evaluees en rendement du capital reel.

POURQUOI PAR FLUX, ET PAS PAR SIGNAL. Toutes les mesures du projet se rangent
en deux formes, et l'algebre suffit a en eliminer une :

  Forme A — capture par TRAVERSEE. On traverse le carnet pour entrer et pour
    sortir. Par cycle : PnL = edge - c. Rendement = (edge - c) x frequence.
    Quand edge < c, le rendement est negatif POUR TOUTE frequence : la
    frequence multiplie un nombre negatif. Or toutes les mesures du depot
    donnent edge entre 1 et 6 bps contre c entre 8 et 31 bps. La forme A n'est
    pas « pas encore trouvee » : elle est fermee par construction.

  Forme B — capture par FLUX. On traverse une fois, on detient T, on encaisse
    un taux r par unite de temps, on traverse une fois. Rendement du notionnel
    = r - c/T. Le terme de cout s'amortit et tend vers zero.

Ce module ne traite donc que la forme B, et en tire le rendement du CAPITAL :

    R(T) = L(T) x (r - c/T)        avec  L(T) = 1 / (marge initiale + coussin(T))

Les deux termes, flux et cout, sont exprimes sur le NOTIONNEL : le levier
multiplie les deux. Ne lever que le flux ferait apparaitre un profit qui
n'existe pas.

L'ARBITRAGE CENTRAL. Detenir plus longtemps amortit le cout (c/T baisse) mais
exige un coussin plus gros, donc baisse le levier (L baisse). L'optimum n'est
ni le plus court ni le plus long : il se calcule, et il depend de la
volatilite de l'instrument autant que du flux lui-meme.

CE QUE CE MODULE NE FAIT PAS. Il n'invente aucun flux. `rate_bps_per_day` doit
venir d'une mesure sur les donnees du depot, et le niveau de preuve est porte
avec le chiffre. Une hypothese reste une hypothese jusqu'au bout du calcul.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .capital import (LegCapital, PositionCapital, buffer_for_leg, leg_capital,
                      position_capital)
from .core_types import Direction
from .instruments import InstrumentSpec
from .margin import MarginSchedule

SECONDS_PER_DAY = 86_400.0

OBSERVED = "OBSERVE"
DERIVED = "DERIVE"
HYPOTHESIS = "HYPOTHESE"


@dataclass(frozen=True)
class Leg:
    spec: InstrumentSpec
    direction: Direction
    contracts: float


@dataclass(frozen=True)
class Flow:
    """Un flux candidat : ce qu'on encaisse par jour en detenant la position."""

    name: str
    legs: Sequence[Leg]
    rate_bps_per_day: float       # sur le NOTIONNEL, signe
    round_trip_bps: float         # cout total d'entree ET de sortie, notionnel
    evidence: str
    source: str
    note: str = ""


@dataclass(frozen=True)
class Evaluation:
    horizon_days: float
    capital: Optional[PositionCapital]
    leverage: float
    gross_bps_per_day_notional: float
    cost_bps_per_day_notional: float
    net_bps_per_day_notional: float
    net_bps_per_day_capital: float
    reliable: bool

    def to_dict(self) -> Dict[str, object]:
        return {"horizon_days": self.horizon_days, "leverage": self.leverage,
                "net_bps_per_day_notional": self.net_bps_per_day_notional,
                "net_bps_per_day_capital": self.net_bps_per_day_capital,
                "reliable": self.reliable}


def evaluate_at(flow: Flow, horizon_days: float,
                prices: Dict[str, Sequence[Tuple[int, float]]],
                schedules: Dict[str, MarginSchedule],
                entry_prices: Dict[str, float],
                quantile: float = 0.99) -> Optional[Evaluation]:
    """Rendement du capital pour une duree de detention donnee.

    None si une jambe manque de bareme, de prix, ou sort des paliers : une
    position dont une jambe est refusee n'a pas de rendement, elle n'existe
    pas. On ne la remplace jamais par une hypothese.
    """
    if horizon_days <= 0:
        return None
    horizon_s = horizon_days * SECONDS_PER_DAY
    legs: List[LegCapital] = []
    for leg in flow.legs:
        px = prices.get(leg.spec.inst_id)
        sch = schedules.get(leg.spec.family) or schedules.get(
            leg.spec.inst_id.rsplit("-", 1)[0])
        entry = entry_prices.get(leg.spec.inst_id)
        if not px or sch is None or not entry:
            return None
        buf = buffer_for_leg(leg.spec, leg.direction, px, horizon_s, quantile)
        lc = leg_capital(leg.spec, leg.contracts, entry, sch, buf)
        if lc is None:
            return None
        legs.append(lc)
    cap = position_capital(legs, netting=False)
    if cap is None or cap.capital_usd <= 0:
        return None
    cost_per_day = flow.round_trip_bps / horizon_days
    net_notional = flow.rate_bps_per_day - cost_per_day
    return Evaluation(
        horizon_days=horizon_days, capital=cap,
        leverage=cap.effective_leverage,
        gross_bps_per_day_notional=flow.rate_bps_per_day,
        cost_bps_per_day_notional=cost_per_day,
        net_bps_per_day_notional=net_notional,
        net_bps_per_day_capital=net_notional * cap.effective_leverage,
        reliable=cap.reliable)


def horizon_curve(flow: Flow, prices: Dict[str, Sequence[Tuple[int, float]]],
                  schedules: Dict[str, MarginSchedule],
                  entry_prices: Dict[str, float],
                  horizons_days: Sequence[float] = (0.25, 0.5, 1, 2, 4, 7, 14, 30),
                  quantile: float = 0.99) -> List[Evaluation]:
    """La courbe complete R(T). Elle ne choisit rien.

    CE QUE CETTE FONCTION NE FAIT DELIBEREMENT PAS : rendre le meilleur
    horizon. Prendre le maximum sur huit horizons puis presenter ce maximum
    comme le rendement de la strategie est une selection apres coup — la
    meme faute que retenir le meilleur de quatre signaux sans correction de
    multiplicite. Le garde d'architecture du projet interdit d'ailleurs tout
    identifiant d'optimisation, et il a raison.

    L'horizon de detention est une decision ECONOMIQUE, prise et ecrite avant
    de regarder le resultat. `declared_horizon` la lit. `grid_upper_bound` rend
    le maximum de la grille en le nommant pour ce qu'il est : une borne
    superieure non atteignable, utile seulement pour eliminer.
    """
    out: List[Evaluation] = []
    for h in horizons_days:
        e = evaluate_at(flow, h, prices, schedules, entry_prices, quantile)
        if e is not None:
            out.append(e)
    return out


def declared_horizon(curve: Sequence[Evaluation], horizon_days: float
                     ) -> Optional[Evaluation]:
    """Le point de la courbe a l'horizon DECLARE d'avance. Pas le meilleur."""
    for e in curve:
        if abs(e.horizon_days - horizon_days) < 1e-9:
            return e
    return None


def grid_upper_bound(curve: Sequence[Evaluation]) -> Optional[Evaluation]:
    """Maximum de la grille — une BORNE, jamais un resultat.

    Elle sert a une seule chose : si meme ce maximum echoue contre l'objectif,
    aucun horizon de la grille ne peut reussir, et le flux est elimine sans
    qu'aucune selection n'ait eu lieu. L'utiliser dans l'autre sens, pour
    annoncer un rendement, serait exactement la faute que ce module evite.
    """
    if not curve:
        return None
    return max(curve, key=lambda e: e.net_bps_per_day_capital)


def render(flow: Flow, curve: Sequence[Evaluation],
           threshold_bps_per_day: float) -> str:
    """Rendu texte : la courbe, l'optimum, et la distance a l'objectif."""
    lines = [f"{flow.name}   [{flow.evidence}]",
             f"  flux mesure   : {flow.rate_bps_per_day:+.3f} bps/jour de notionnel",
             f"  aller-retour  : {flow.round_trip_bps:.2f} bps de notionnel",
             f"  source        : {flow.source}"]
    if flow.note:
        lines.append(f"  note          : {flow.note}")
    lines.append("")
    lines.append(f"  {'detention':>10}{'levier':>9}{'cout/jour':>11}"
                 f"{'net/jour notionnel':>20}{'net/jour CAPITAL':>18}{'':>3}")
    lines.append("  " + "-" * 69)
    for e in curve:
        flag = "" if e.reliable else "  (coussin = borne inf.)"
        lines.append(f"  {e.horizon_days:>9.2f}j{e.leverage:>9.1f}"
                     f"{e.cost_bps_per_day_notional:>11.2f}"
                     f"{e.net_bps_per_day_notional:>20.3f}"
                     f"{e.net_bps_per_day_capital:>18.2f}{flag}")
    if curve:
        best = grid_upper_bound(curve)
        lines.append("")
        lines.append(f"  BORNE SUPERIEURE sur la grille (pas un rendement "
                     f"atteignable) : {best.net_bps_per_day_capital:+.2f} "
                     f"bps/jour de capital")
        lines.append(f"    atteinte a {best.horizon_days:.2f} jour(s), "
                     f"levier {best.leverage:.1f}x — cet horizon est LU sur le "
                     f"resultat, il n'a pas ete declare d'avance")
        ratio = best.net_bps_per_day_capital / threshold_bps_per_day
        lines.append(f"  objectif : {threshold_bps_per_day:.2f} bps/jour  "
                     f"->  {ratio:.3f}x de la borne")
        if ratio < 1.0:
            lines.append(f"  -> meme la borne echoue : aucun horizon de la "
                         f"grille ne peut atteindre l'objectif.")
    return "\n".join(lines)


def required_rate_bps_per_day(threshold_bps_per_day: float, leverage: float,
                              round_trip_bps: float, horizon_days: float
                              ) -> Optional[float]:
    """Flux MINIMAL, en bps/jour de notionnel, pour atteindre l'objectif.

    C'est l'inversion de R = L x (r - c/T) :

        r = objectif / L + c / T

    Sa valeur pratique : elle transforme « cherchons une opportunite » en un
    CRIBLE. Tout flux dont le taux mesure est inferieur a ce seuil est elimine
    sans etude, quel que soit son interet par ailleurs. Aucune elegance, aucune
    frequence, aucune sophistication d'execution ne rattrape un flux trop petit,
    parce que le seuil ne depend que de trois grandeurs observables : le levier
    que le coussin de survie autorise, le cout d'aller-retour, et la duree.

    None si le levier est nul ou negatif : sans levier il n'y a pas de position.
    """
    if leverage <= 0 or horizon_days <= 0:
        return None
    return threshold_bps_per_day / leverage + round_trip_bps / horizon_days
