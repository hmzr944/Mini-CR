"""Protocole de donnees — decoupage IMMUABLE et suivi de contamination.

REGLE. Le FINAL HOLDOUT n'est ouvert qu'une fois, a la fin. Il ne sert jamais
a choisir une hypothese, un seuil, un horizon, une taille ni un mode
d'execution. Toute modification du systeme posterieure a son ouverture le
rend CONTAMINE, et une nouvelle validation devient necessaire.

Le decoupage est TEMPOREL, jamais aleatoire : melanger les instants detruirait
la causalite que tout le reste du systeme protege.

Le protocole ne se contente pas de decouper : il ENREGISTRE chaque ouverture
du holdout et chaque modification declaree, de sorte qu'une contamination ne
puisse pas etre oubliee.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core_types import utc_now_iso


class Split(str, enum.Enum):
    DISCOVERY = "DISCOVERY"        # chercher des phenomenes
    DEVELOPMENT = "DEVELOPMENT"    # choisir seuils et horizons
    VALIDATION = "VALIDATION"      # confirmer avant le holdout
    FINAL_HOLDOUT = "FINAL_HOLDOUT"  # ouvert UNE FOIS

    @property
    def may_inform_choices(self) -> bool:
        """Ce segment peut-il servir a CHOISIR quelque chose ?"""
        return self in (Split.DISCOVERY, Split.DEVELOPMENT)


#: Parts par defaut, dans l'ordre temporel. Le holdout est le segment le plus
#: RECENT : valider sur le passe apres avoir cherche sur le futur serait une
#: fuite temporelle.
DEFAULT_FRACTIONS: Dict[Split, float] = {
    Split.DISCOVERY: 0.40,
    Split.DEVELOPMENT: 0.20,
    Split.VALIDATION: 0.20,
    Split.FINAL_HOLDOUT: 0.20,
}


class HoldoutViolation(RuntimeError):
    """Le holdout a ete sollicite hors du protocole."""


@dataclass
class DataProtocol:
    """Decoupage temporel immuable d'une fenetre d'observation."""

    start_ms: int
    end_ms: int
    fractions: Dict[Split, float] = field(
        default_factory=lambda: dict(DEFAULT_FRACTIONS))
    _bounds: Dict[Split, Tuple[int, int]] = field(default_factory=dict, init=False)
    holdout_opened_at: Optional[str] = field(default=None, init=False)
    holdout_open_count: int = field(default=0, init=False)
    modifications_after_opening: List[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.end_ms <= self.start_ms:
            raise ValueError("fenetre vide ou inversee")
        total = sum(self.fractions.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"les parts doivent sommer a 1 (recu {total})")
        span = self.end_ms - self.start_ms
        cursor = self.start_ms
        for split in (Split.DISCOVERY, Split.DEVELOPMENT,
                      Split.VALIDATION, Split.FINAL_HOLDOUT):
            width = int(span * self.fractions[split])
            self._bounds[split] = (cursor, cursor + width)
            cursor += width
        # Le dernier segment absorbe l'arrondi pour ne perdre aucun instant.
        lo, _ = self._bounds[Split.FINAL_HOLDOUT]
        self._bounds[Split.FINAL_HOLDOUT] = (lo, self.end_ms)

    def bounds(self, split: Split) -> Tuple[int, int]:
        return self._bounds[split]

    def split_of(self, ts_ms: int) -> Optional[Split]:
        for split, (lo, hi) in self._bounds.items():
            if lo <= ts_ms < hi:
                return split
        if ts_ms == self.end_ms:
            return Split.FINAL_HOLDOUT
        return None

    def contains(self, split: Split, ts_ms: int) -> bool:
        lo, hi = self._bounds[split]
        return lo <= ts_ms < hi

    def select(self, split: Split, records: Sequence[Dict[str, Any]],
               ts_key: str = "ts") -> List[Dict[str, Any]]:
        """Sous-ensemble d'un segment. Le holdout passe par `open_holdout`."""
        if split is Split.FINAL_HOLDOUT and self.holdout_open_count == 0:
            raise HoldoutViolation(
                "le FINAL_HOLDOUT ne se lit pas par select() : utiliser "
                "open_holdout(raison), qui enregistre l'ouverture")
        lo, hi = self._bounds[split]
        return [r for r in records if lo <= (r.get(ts_key) or -1) < hi]

    def open_holdout(self, reason: str,
                     records: Optional[Sequence[Dict[str, Any]]] = None,
                     ts_key: str = "ts") -> List[Dict[str, Any]]:
        """Ouvre le holdout. Chaque ouverture est enregistree.

        Une seconde ouverture n'est pas interdite techniquement — elle est
        RENDUE VISIBLE. Un holdout ouvert deux fois pour choisir entre deux
        variantes n'est plus un holdout, et le rapport doit le dire.
        """
        self.holdout_open_count += 1
        if self.holdout_opened_at is None:
            self.holdout_opened_at = utc_now_iso()
        if records is None:
            return []
        lo, hi = self._bounds[Split.FINAL_HOLDOUT]
        return [r for r in records if lo <= (r.get(ts_key) or -1) < hi]

    def declare_modification(self, what: str) -> None:
        """Declare une modification du systeme. Apres ouverture = contamination."""
        if self.holdout_open_count > 0:
            self.modifications_after_opening.append(f"{utc_now_iso()}: {what}")

    @property
    def holdout_is_contaminated(self) -> bool:
        return bool(self.modifications_after_opening) or self.holdout_open_count > 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_ms": [self.start_ms, self.end_ms],
            "duration_s": (self.end_ms - self.start_ms) / 1000.0,
            "splits": {s.value: {"from_ms": lo, "to_ms": hi,
                                 "duration_s": (hi - lo) / 1000.0}
                       for s, (lo, hi) in self._bounds.items()},
            "holdout_opened_at": self.holdout_opened_at,
            "holdout_open_count": self.holdout_open_count,
            "modifications_after_opening": self.modifications_after_opening,
            "holdout_is_contaminated": self.holdout_is_contaminated,
        }

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1,
                                         ensure_ascii=False), encoding="utf-8")
