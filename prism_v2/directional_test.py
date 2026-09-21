"""Le signe d'un signal porte-t-il de l'information, ou suit-il la derive ?

LE PIEGE QUE CE MODULE EXISTE POUR EVITER. L'esperance du rendement signe se
decompose :

    E[signe x r]  =  Cov(signe, r)  +  E[signe] . E[r]

Le premier terme est l'information cherchee. Le second est la DERIVE du marche
multipliee par le biais directionnel du signal : un detecteur 60 % LONG dans un
marche qui monte produit un brut positif sans rien savoir. Une moyenne de brut
signe confond les deux.

CE QUE FAIT CE MODULE. Il stratifie par (instrument x tranche de temps) et
compare, DANS chaque strate, le rendement moyen des evenements LONG a celui des
evenements SHORT. La derive commune a la strate frappe les deux bras a
l'identique et disparait de la difference.

    Delta_s = moyenne(r | signe = +1, dans s) - moyenne(r | signe = -1, dans s)

L'incertitude vient d'un bootstrap de STRATES ENTIERES, jamais de captures
individuelles : les captures se chevauchent, et les traiter comme independantes
gonflerait la precision. La distribution nulle vient d'une permutation des
signes A L'INTERIEUR des strates, qui preserve la structure temporelle et
instrumentale et ne casse que l'association signe <-> rendement.

Protocole gele dans prism_v2/PROTOCOLE_BOOK_IMBALANCE.md.
"""
from __future__ import annotations

import random
import statistics as st
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Event:
    """Un declenchement, avec son rendement NON signe."""
    instrument: str
    ts_ms: int
    sign: int            #: +1 (LONG) ou -1 (SHORT), tel que le detecteur l'a emis
    ret_bps: float       #: rendement mid-a-mid sur l'horizon, NON signe


@dataclass
class StratifiedResult:
    delta_bps: Optional[float]      #: difference stratifiee, ponderee
    n_strata_used: int
    n_strata_dropped: int           #: strates a un seul signe — comptees, jamais tues
    n_events_used: int
    naive_signed_bps: Optional[float]  #: la moyenne naive, pour comparaison
    per_stratum: List[Tuple[str, float, int, int]]

    @property
    def drift_component_bps(self) -> Optional[float]:
        """Ce que la moyenne naive doit a la derive plutot qu'au signe."""
        if self.delta_bps is None or self.naive_signed_bps is None:
            return None
        return self.naive_signed_bps - self.delta_bps / 2.0


def _stratum_key(ev: Event, minutes: float) -> str:
    return f"{ev.instrument}|{int(ev.ts_ms // (minutes * 60_000))}"


def stratify(events: Sequence[Event], minutes: float = 5.0) -> Dict[str, List[Event]]:
    out: Dict[str, List[Event]] = defaultdict(list)
    for ev in events:
        out[_stratum_key(ev, minutes)].append(ev)
    return dict(out)


def stratified_difference(events: Sequence[Event],
                          minutes: float = 5.0) -> StratifiedResult:
    """Delta = moyenne(r | LONG) - moyenne(r | SHORT), par strate puis pondere.

    Poids = min(n+, n-) : une strate tres desequilibree informe peu sur la
    difference, et lui donner le poids de son effectif total la surestimerait.
    """
    strata = stratify(events, minutes)
    deltas: List[Tuple[str, float, int, int]] = []
    dropped = 0
    used_events = 0
    for key, evs in sorted(strata.items()):
        pos = [e.ret_bps for e in evs if e.sign > 0]
        neg = [e.ret_bps for e in evs if e.sign < 0]
        if not pos or not neg:
            dropped += 1
            continue
        deltas.append((key, st.fmean(pos) - st.fmean(neg), len(pos), len(neg)))
        used_events += len(pos) + len(neg)
    naive = (st.fmean([e.sign * e.ret_bps for e in events]) if events else None)
    if not deltas:
        return StratifiedResult(None, 0, dropped, 0, naive, [])
    wsum = sum(min(p, n) for _, _, p, n in deltas)
    delta = (sum(d * min(p, n) for _, d, p, n in deltas) / wsum) if wsum else None
    return StratifiedResult(delta, len(deltas), dropped, used_events, naive, deltas)


def permutation_pvalue(events: Sequence[Event], minutes: float = 5.0,
                       n_perm: int = 5_000, seed: int = 20260921) -> Optional[float]:
    """Permute les signes DANS chaque strate. Unilateral, Delta > 0."""
    obs = stratified_difference(events, minutes).delta_bps
    if obs is None:
        return None
    strata = stratify(events, minutes)
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_perm):
        shuffled: List[Event] = []
        for evs in strata.values():
            signs = [e.sign for e in evs]
            rng.shuffle(signs)
            shuffled.extend(Event(e.instrument, e.ts_ms, s, e.ret_bps)
                            for e, s in zip(evs, signs))
        d = stratified_difference(shuffled, minutes).delta_bps
        if d is not None and d >= obs:
            ge += 1
    return (ge + 1) / (n_perm + 1)


def block_bootstrap_ci(events: Sequence[Event], minutes: float = 5.0,
                       n_boot: int = 5_000, level: float = 0.90,
                       seed: int = 20260921) -> Optional[Tuple[float, float]]:
    """Reechantillonne des STRATES entieres, avec remise. Jamais des captures.

    On reechantillonne les couples (Delta_s, poids_s) DEJA calcules, et non les
    evenements : concatener des strates tirees puis re-stratifier les
    refusionnerait par leur cle, et le bootstrap ne ferait rien. C'etait un
    defaut de ma premiere implementation, attrape par le test
    `test_ci_excludes_zero_for_a_strong_signal`.
    """
    per = stratified_difference(events, minutes).per_stratum
    if len(per) < 3:
        return None
    pairs = [(d, min(p, n)) for _, d, p, n in per]
    rng = random.Random(seed)
    draws: List[float] = []
    for _ in range(n_boot):
        pick = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        wsum = sum(w for _, w in pick)
        if wsum:
            draws.append(sum(d * w for d, w in pick) / wsum)
    if len(draws) < n_boot // 2:
        return None
    draws.sort()
    a = (1.0 - level) / 2.0
    return draws[int(a * len(draws))], draws[int((1 - a) * len(draws)) - 1]


def verdict(delta: Optional[float], p: Optional[float],
            ci: Optional[Tuple[float, float]]) -> str:
    """Les criteres de PROTOCOLE_BOOK_IMBALANCE.md, appliques tels quels."""
    if delta is None or p is None:
        return "NON CONCLUANTE (echantillon insuffisant)"
    if delta <= 0 or p > 0.10:
        return f"REFUTEE (Delta = {delta:+.3f} bps, p = {p:.4f})"
    if ci is None or (ci[0] <= 0.0 <= ci[1]):
        lo_hi = f"[{ci[0]:+.3f} ; {ci[1]:+.3f}]" if ci else "indisponible"
        return f"NON CONCLUANTE (IC 90 % {lo_hi} contient 0)"
    if p <= 0.05:
        return (f"PROMETTEUSE (Delta = {delta:+.3f} bps, p = {p:.4f}, "
                f"IC [{ci[0]:+.3f} ; {ci[1]:+.3f}]) — information directionnelle "
                f"seulement, AUCUNE capture nette etablie")
    return f"NON CONCLUANTE (p = {p:.4f} entre 0,05 et 0,10)"
