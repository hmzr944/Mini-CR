"""Primitives partagees par tous les modules V2.

Ce module ne depend de rien (stdlib seule) et ne connait ni le marche,
ni les opportunites. Il definit le vocabulaire commun : qualite d'une
mesure, provenance, direction, mode d'execution.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    """Horodatage UTC ISO-8601, seconde entiere, suffixe Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ms_to_iso(ms: int | str) -> str:
    """Convertit un timestamp OKX (ms epoch) en ISO-8601 UTC."""
    return (
        datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class Quality(str, enum.Enum):
    """Qualite epistemique d'une grandeur.

    OBSERVED : mesure directe sur une donnee de marche reelle et horodatee.
    DERIVED  : calculee deterministiquement a partir d'OBSERVED.
    ASSUMED  : posee par hypothese documentee, NON verifiee sur ce compte.
    UNKNOWN  : non mesuree. Interdit de la remplacer par zero.
    """

    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    ASSUMED = "ASSUMED"
    UNKNOWN = "UNKNOWN"


#: Qualites qui n'autorisent PAS une conclusion economique ferme.
WEAK_QUALITIES = frozenset({Quality.ASSUMED, Quality.UNKNOWN})


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def taker_side(self) -> str:
        """Cote du carnet consomme a l'entree : un LONG lifte les asks."""
        return "ask" if self is Direction.LONG else "bid"


class ExecutionMode(str, enum.Enum):
    """Aucun mode REAL n'existe dans V2. Cette enumeration est volontairement
    limitee a PAPER : il n'y a aucun chemin de code vers un ordre reel."""

    PAPER = "PAPER"


@dataclass(frozen=True)
class Provenance:
    """D'ou vient une donnee. Obligatoire sur toute observation et candidate.

    Sans provenance, un nombre n'est pas auditable — c'est precisement la
    dette scientifique que V2 doit eliminer.
    """

    exchange: str
    endpoint: str
    fetched_at: str
    inst_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
