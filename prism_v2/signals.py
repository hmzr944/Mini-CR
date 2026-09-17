#!/usr/bin/env python3
"""SIGNAUX — les cinq esperances pre-enregistrees, et rien d'autre.

Figes dans prism_v2/LONG_TEST_PROTOCOL.md avant toute donnee. Chacun a UNE
parametrisation. Aucune grille, aucune variante, aucun reglage.

L'ARCHITECTURE NE CHANGE PAS. Le moteur de position (portfolio.py) est
identique pour les cinq : seule l'esperance mu differe. C'est la propriete
qui rend la comparaison honnete — si un signal gagne, ce n'est pas parce
qu'on lui a donne un meilleur moteur.

POURQUOI DES Z-SCORES POUR LE MELANGE. Les quatre signaux n'ont pas la meme
unite : un funding moyen et un rendement passe ne sont pas comparables. Les
moyenner bruts laisserait le plus volatil dominer par accident d'echelle.
On les standardise en coupe transversale avant de les melanger.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

HOURS_PER_YEAR = 24 * 365

#: Fenetres figees par le protocole, en BARRES (4 h chacune).
CARRY_BARS = 42      # 7 jours
MOM_BARS = 42        # 7 jours
REV_BARS = 6         # 1 jour


@dataclass(frozen=True)
class AssetWindow:
    """Ce qu'un signal a le droit de voir a l'instant de decision.

    Toutes les series sont PASSEES, la plus recente en fin. Aucun champ ne
    contient d'information posterieure a l'instant de decision : la
    contrainte est structurelle, pas une convention d'appel.
    """
    funding: Sequence[float]      # taux par barre, deja payes
    returns: Sequence[float]      # rendements par barre, deja realises


def _mean_or_none(xs: Sequence[float], need: int) -> Optional[float]:
    if len(xs) < need:
        return None
    return statistics.fmean(list(xs)[-need:])


def s1_carry_plus(w: AssetWindow) -> Optional[float]:
    """Le carry comme prime : porter ce qui paie du funding.

    Temoin. Mesure perdant sur 45 jours (beta = -5,5) ; conserve precisement
    pour que le test long puisse le confirmer ou l'infirmer.
    """
    m = _mean_or_none(w.funding, CARRY_BARS)
    return None if m is None else -m


def s2_carry_minus(w: AssetWindow) -> Optional[float]:
    """Le funding comme indicateur de positionnement : sens inverse."""
    m = _mean_or_none(w.funding, CARRY_BARS)
    return None if m is None else m


def s3_tsmom(w: AssetWindow) -> Optional[float]:
    """Momentum : ce qui est monte continue.

    Moyenne des rendements passes plutot que le rendement cumule : moins
    sensible a une seule barre aberrante, meme information.
    """
    return _mean_or_none(w.returns, MOM_BARS)


def s4_reversal(w: AssetWindow) -> Optional[float]:
    """Retournement de court terme : ce qui vient de monger redescend."""
    m = _mean_or_none(w.returns, REV_BARS)
    return None if m is None else -m


SIGNALS: Dict[str, Callable[[AssetWindow], Optional[float]]] = {
    "S1_CARRY+": s1_carry_plus,
    "S2_CARRY-": s2_carry_minus,
    "S3_TSMOM": s3_tsmom,
    "S4_REV": s4_reversal,
}
BLEND_NAME = "S5_BLEND"


def zscore(values: Dict[str, float]) -> Dict[str, float]:
    """Standardisation en coupe transversale.

    Un ecart-type nul (tous les actifs identiques) rend des zeros : il n'y a
    alors aucune information transversale, et pretendre le contraire en
    divisant par epsilon fabriquerait des positions a partir de bruit.
    """
    if len(values) < 2:
        return {k: 0.0 for k in values}
    mu = statistics.fmean(values.values())
    sd = statistics.stdev(values.values())
    if sd <= 0:
        return {k: 0.0 for k in values}
    return {k: (v - mu) / sd for k, v in values.items()}


def compute_all(windows: Dict[str, AssetWindow]) -> Dict[str, Dict[str, float]]:
    """Les cinq signaux, par actif. Un actif sans historique est absent.

    Le melange n'est calcule que sur les actifs presents dans les QUATRE
    signaux : melanger des sous-univers differents comparerait des choses
    qui ne se ressemblent pas.
    """
    out: Dict[str, Dict[str, float]] = {}
    for name, fn in SIGNALS.items():
        vals = {}
        for asset, w in windows.items():
            v = fn(w)
            if v is not None:
                vals[asset] = v
        out[name] = vals

    common = set(windows)
    for vals in out.values():
        common &= set(vals)

    blend: Dict[str, float] = {}
    if common:
        zs = [zscore({a: out[n][a] for a in common}) for n in SIGNALS]
        for a in common:
            blend[a] = statistics.fmean(z[a] for z in zs)
    out[BLEND_NAME] = blend
    return out
