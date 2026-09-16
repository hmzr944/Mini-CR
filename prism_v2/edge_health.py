"""Edge Health — suivi dans le temps, sans score magique.

Chaque metrique a une definition explicite et calculable. Il n'existe
volontairement AUCUN indicateur agrege de type "confiance = 93 %" : un tel
nombre serait exactement le genre de recit que ce projet doit eviter.

Toute statistique porte son N. Une distribution est rapportee par ses
quantiles, jamais par sa seule moyenne.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence


def _quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def describe(values: Iterable[float]) -> Dict[str, Any]:
    """Distribution complete. N d'abord : sans lui, rien n'est interpretable."""
    v = sorted(float(x) for x in values if x is not None)
    if not v:
        return {"n": 0, "note": "aucune observation"}
    return {
        "n": len(v), "min": v[0], "max": v[-1],
        "p05": _quantile(v, 0.05), "p25": _quantile(v, 0.25),
        "median": _quantile(v, 0.50), "p75": _quantile(v, 0.75),
        "p95": _quantile(v, 0.95),
        "mean": sum(v) / len(v),
        "positive_share": sum(1 for x in v if x > 0) / len(v),
    }


#: En dessous de ce N, aucune conclusion economique n'est formulee. Ce n'est
#: pas un seuil de rentabilite mais un seuil d'HONNETETE STATISTIQUE : il ne
#: doit jamais etre abaisse pour rendre un resultat presentable.
MIN_N_FOR_ANY_CLAIM = 30


@dataclass
class EdgeHealth:
    records: List[Dict[str, Any]]

    @classmethod
    def from_records(cls, records: Iterable[Dict[str, Any]]) -> "EdgeHealth":
        return cls(list(records))

    def for_opportunity(self, opportunity_type: str) -> "EdgeHealth":
        return EdgeHealth([r for r in self.records
                           if r.get("opportunity_type") == opportunity_type])

    def counts(self) -> Dict[str, int]:
        out = {"total": len(self.records), "ACCEPTED": 0, "REJECTED": 0, "UNRESOLVED": 0}
        for r in self.records:
            s = r.get("status")
            if s in out:
                out[s] += 1
        out["executed_paper"] = sum(1 for r in self.records if r.get("executed"))
        return out

    def _values(self, key: str) -> List[float]:
        return [r[key] for r in self.records if r.get(key) is not None]

    def report(self) -> Dict[str, Any]:
        counts = self.counts()
        gross = self._values("gross_capture_bps")
        net = self._values("expected_net_capture_bps")
        realized = self._values("realized_bps")
        cost_parts = {k: describe(self._values(f"{k}_bps"))
                      for k in ("fees", "spread", "impact", "slippage", "latency", "funding")}

        expected_vs_realized = None
        pairs = [(r["expected_net_capture_bps"], r["realized_bps"]) for r in self.records
                 if r.get("expected_net_capture_bps") is not None
                 and r.get("realized_bps") is not None]
        if pairs:
            diffs = [a - e for e, a in pairs]
            expected_vs_realized = {"n": len(pairs), "diff_bps": describe(diffs)}

        n_concl = counts["ACCEPTED"] + counts["REJECTED"]
        return {
            "counts": counts,
            "gross_capture_bps": describe(gross),
            "expected_net_capture_bps": describe(net),
            "realized_bps": describe(realized),
            "cost_components_bps": cost_parts,
            "expected_vs_realized": expected_vs_realized,
            "capacity_usd": describe(self._values("capacity_usd")),
            "evidence": self.evidence_statement(n_concl),
        }

    def evidence_statement(self, n_conclusive: Optional[int] = None) -> Dict[str, Any]:
        """Verdict de suffisance statistique. Prefere toujours l'aveu au recit."""
        if n_conclusive is None:
            c = self.counts()
            n_conclusive = c["ACCEPTED"] + c["REJECTED"]
        sufficient = n_conclusive >= MIN_N_FOR_ANY_CLAIM
        return {
            "n_conclusive": n_conclusive,
            "min_n_required": MIN_N_FOR_ANY_CLAIM,
            "sufficient_for_claim": sufficient,
            "statement": ("echantillon suffisant pour une premiere lecture "
                          "(ne vaut que pour la fenetre et les instruments observes)"
                          if sufficient else
                          f"PREUVE INSUFFISANTE : N={n_conclusive} < "
                          f"{MIN_N_FOR_ANY_CLAIM}. Aucune conclusion economique."),
        }
