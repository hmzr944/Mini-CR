"""Discovery Memory — dans QUELLES CONDITIONS la capture fonctionne.

Pas de modele predictif, pas de score opaque. Un tableau de contingence :
on enregistre les CONDITIONS observees au moment de chaque candidate, puis
son issue, et on regarde quelles conditions vont avec quelles issues.

Le systeme doit pouvoir repondre a deux questions, et seulement a celles-la :

    "Quelles conditions ont historiquement produit une capture nette ?"
    "Quelles conditions la detruisent systematiquement ?"

Les conditions sont discretisees en tranches (buckets) explicites. Les bornes
de tranches sont des conventions de LECTURE, pas des parametres optimises :
les deplacer change la granularite du tableau, jamais un resultat economique.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .edge_health import describe

DEFAULT_MEMORY_PATH = Path(__file__).parent / "ledger" / "discovery_memory.jsonl"

#: Bornes de tranches. Conventions de lecture, explicites et documentees.
SPREAD_BUCKETS_BPS = (0.5, 2.0, 5.0, 10.0, 25.0)
VOL_BUCKETS_BPS = (1.0, 5.0, 15.0, 40.0)
DEPTH_BUCKETS_USD = (10_000.0, 100_000.0, 1_000_000.0, 10_000_000.0)
SIZE_BUCKETS_USD = (100.0, 1_000.0, 10_000.0, 100_000.0)
EDGE_BUCKETS_BPS = (1.0, 5.0, 20.0, 100.0)


def bucket(value: Optional[float], edges: Tuple[float, ...], unit: str = "") -> str:
    """Tranche lisible. `None` reste `unknown` : jamais rattache a une tranche."""
    if value is None:
        return "unknown"
    v = abs(value)
    if v < edges[0]:
        return f"<{edges[0]:g}{unit}"
    for lo, hi in zip(edges, edges[1:]):
        if v < hi:
            return f"{lo:g}-{hi:g}{unit}"
    return f">={edges[-1]:g}{unit}"


@dataclass
class Observation:
    """Une candidate + ses conditions + son issue."""

    family: str
    inst_id: str
    venue: str
    verdict: str                      # DiscoveryVerdict ou CaptureStatus
    gross_bps: Optional[float]
    net_bps: Optional[float]
    realized_bps: Optional[float]
    spread_bps: Optional[float]
    realized_vol_bps: Optional[float]
    depth_usd: Optional[float]
    size_usd: Optional[float]
    latency_ms: Optional[float]
    execution_mode: Optional[str]
    hour_utc: Optional[int]
    failure_reason: Optional[str] = None
    ts_utc: str = ""

    def conditions(self) -> Dict[str, str]:
        return {
            "family": self.family,
            "inst_id": self.inst_id,
            "venue": self.venue,
            "spread": bucket(self.spread_bps, SPREAD_BUCKETS_BPS, "bps"),
            "volatility": bucket(self.realized_vol_bps, VOL_BUCKETS_BPS, "bps"),
            "depth": bucket(self.depth_usd, DEPTH_BUCKETS_USD, "$"),
            "size": bucket(self.size_usd, SIZE_BUCKETS_USD, "$"),
            "gross_edge": bucket(self.gross_bps, EDGE_BUCKETS_BPS, "bps"),
            "execution_mode": self.execution_mode or "unknown",
            "hour_utc": ("unknown" if self.hour_utc is None
                         else f"{self.hour_utc:02d}h"),
        }

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["conditions"] = self.conditions()
        return d


#: Issues considerees comme "la capture a survecu a l'analyse".
SURVIVING_VERDICTS = frozenset({"SURVIVES_ALL_BOUNDS", "ACCEPTED"})
#: Issues economiquement concluantes (par opposition a un manque de donnees).
CONCLUSIVE_VERDICTS = SURVIVING_VERDICTS | {"DEAD_EVEN_AT_BEST", "NO_RAW_EDGE",
                                            "REJECTED"}


@dataclass
class DiscoveryMemory:
    path: Path = field(default_factory=lambda: DEFAULT_MEMORY_PATH)
    observations: List[Observation] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, obs: Observation) -> None:
        self.observations.append(obs)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obs.to_dict(), ensure_ascii=False, default=str) + "\n")

    def load(self) -> "DiscoveryMemory":
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                d.pop("conditions", None)
                self.observations.append(Observation(**d))
        return self

    # ── analyse par condition ────────────────────────────────────────────
    def contingency(self, dimension: str,
                    min_n: int = 5) -> Dict[str, Dict[str, Any]]:
        """Tableau issue x tranche, pour une dimension de condition.

        Les tranches sous `min_n` sont conservees mais MARQUEES : une tranche
        a N=2 ne permet aucune lecture, et le taire serait trompeur.
        """
        by_bucket: Dict[str, List[Observation]] = defaultdict(list)
        for o in self.observations:
            by_bucket[o.conditions().get(dimension, "unknown")].append(o)

        out: Dict[str, Dict[str, Any]] = {}
        for b, obs in sorted(by_bucket.items()):
            conclusive = [o for o in obs if o.verdict in CONCLUSIVE_VERDICTS]
            surviving = [o for o in obs if o.verdict in SURVIVING_VERDICTS]
            out[b] = {
                "n": len(obs),
                "n_conclusive": len(conclusive),
                "n_surviving": len(surviving),
                "survival_rate": (len(surviving) / len(conclusive)
                                  if conclusive else None),
                "verdicts": dict(Counter(o.verdict for o in obs).most_common()),
                "gross_bps": describe([o.gross_bps for o in obs
                                       if o.gross_bps is not None]),
                "net_bps": describe([o.net_bps for o in obs if o.net_bps is not None]),
                "sufficient_n": len(conclusive) >= min_n,
                "note": (None if len(conclusive) >= min_n else
                         f"N={len(conclusive)} < {min_n} : aucune lecture possible"),
            }
        return out

    def favourable_conditions(self, min_n: int = 5) -> List[Dict[str, Any]]:
        """Tranches ou la capture survit le plus souvent, N suffisant seulement."""
        rows: List[Dict[str, Any]] = []
        for dim in ("family", "inst_id", "venue", "spread", "volatility",
                    "depth", "size", "execution_mode", "hour_utc"):
            for b, stats in self.contingency(dim, min_n).items():
                if stats["sufficient_n"] and stats["survival_rate"] is not None:
                    rows.append({"dimension": dim, "bucket": b,
                                 "survival_rate": stats["survival_rate"],
                                 "n_conclusive": stats["n_conclusive"]})
        rows.sort(key=lambda r: (-r["survival_rate"], -r["n_conclusive"]))
        return rows

    def destructive_conditions(self, min_n: int = 5) -> List[Dict[str, Any]]:
        rows = [r for r in self.favourable_conditions(min_n)
                if r["survival_rate"] == 0.0]
        return rows

    def report(self, min_n: int = 5) -> Dict[str, Any]:
        fav = self.favourable_conditions(min_n)
        return {
            "n_observations": len(self.observations),
            "n_conclusive": sum(1 for o in self.observations
                                if o.verdict in CONCLUSIVE_VERDICTS),
            "by_family": self.contingency("family", min_n),
            "by_spread": self.contingency("spread", min_n),
            "by_volatility": self.contingency("volatility", min_n),
            "favourable_conditions": fav[:10],
            "destructive_conditions": self.destructive_conditions(min_n)[:10],
            "readable": bool(fav),
            "note": ("aucune tranche n'atteint le N minimal : la memoire existe "
                     "mais n'est pas encore lisible" if not fav else
                     "lecture partielle — ne vaut que pour les conditions observees"),
        }
