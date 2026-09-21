"""Remplissage passif et selection adverse, mesures sur le touch collecte.

LA QUESTION. Le carry est negatif en taker et positif en maker. Le signe du
livre depend donc entierement de deux nombres que le depot n'a jamais mesures :

  1. la PROBABILITE qu'un ordre pose au meilleur prix soit rempli ;
  2. ce que fait le prix APRES ce remplissage (la selection adverse).

RAPPORT_FINAL.md le reconnait : « la probabilite de remplissage passif est
INCONNUE -- PRISM n'a pas de modele de file d'attente. Toute economie maker
produite ici est donc une BORNE SUPERIEURE, file supposee gagnee a chaque
transaction. » Ce module remplace cette borne par une mesure.

LE MODELE DE FILE, et ce qu'il suppose.

On pose un ordre au meilleur bid B a l'instant t. Devant nous il y a `bidSz`
contrats deja en file. On est rempli quand le flux AGRESSEUR VENDEUR arrive a
un prix <= B pour un volume cumule superieur a cette file.

Trois hypotheses, toutes CONSERVATRICES, declarees ici :

  - on arrive DERRIERE toute la file affichee (priorite temporelle pire cas) ;
  - les annulations devant nous ne nous font PAS avancer (elles le feraient en
    realite, donc la vraie probabilite est >= celle-ci) ;
  - si le meilleur bid monte au-dessus de B, notre ordre cesse d'etre au touch
    et on considere qu'il n'est PAS rempli sur cette fenetre.

Le troisieme point est celui qui mord : c'est exactement la selection adverse.
On est rempli quand le marche vient vers nous, c'est-a-dire quand il s'apprete
a continuer dans ce sens.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from typing import Iterable, List, Sequence


@dataclass(frozen=True)
class Snapshot:
    ts: int                      #: ms
    inst: str
    bid: float
    bid_sz: float
    ask: float
    ask_sz: float
    agg: Sequence[Sequence]      #: [ts, px, sz, side] arrives depuis le precedent

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class FillOutcome:
    filled: bool
    seconds_to_fill: float | None
    markout_bps: float | None    #: signe ECONOMIQUE : > 0 = en notre faveur


def _aggressive_volume_at_or_below(snap: Snapshot, price: float, side: str) -> float:
    """Volume agresseur qui aurait consomme un ordre passif a `price`.

    Pour un bid passif, ce sont les VENTES agressives a un prix <= price.
    Pour un ask passif, les ACHATS agressifs a un prix >= price.
    """
    total = 0.0
    for row in snap.agg:
        _, px, sz, s = row[0], float(row[1]), float(row[2]), row[3]
        if side == "bid" and s == "sell" and px <= price + 1e-12:
            total += sz
        elif side == "ask" and s == "buy" and px >= price - 1e-12:
            total += sz
    return total


def simulate_passive_order(window: Sequence[Snapshot], start: int, side: str,
                           horizon_s: float, markout_s: float) -> FillOutcome:
    """Pose un ordre au touch au relevé `start` et regarde ce qui lui arrive."""
    s0 = window[start]
    price = s0.bid if side == "bid" else s0.ask
    queue = s0.bid_sz if side == "bid" else s0.ask_sz
    consumed = 0.0

    for j in range(start + 1, len(window)):
        s = window[j]
        if s.inst != s0.inst:
            continue
        elapsed = (s.ts - s0.ts) / 1000.0
        if elapsed > horizon_s:
            break
        # le touch s'est-il eloigne de nous ? alors on n'est plus au meilleur prix
        if side == "bid" and s.bid > price + 1e-12:
            return FillOutcome(False, None, None)
        if side == "ask" and s.ask < price - 1e-12:
            return FillOutcome(False, None, None)

        consumed += _aggressive_volume_at_or_below(s, price, side)
        if consumed > queue:
            mk = _markout(window, j, s0.inst, price, side, markout_s)
            return FillOutcome(True, elapsed, mk)
    return FillOutcome(False, None, None)


def _markout(window: Sequence[Snapshot], fill_idx: int, inst: str,
             price: float, side: str, markout_s: float) -> float | None:
    """Mouvement du mid apres le remplissage, en bps, signe ECONOMIQUEMENT.

    On a achete au bid : on gagne si le mid MONTE. On a vendu a l'ask : on gagne
    s'il BAISSE. Un markout negatif est donc de la selection adverse, quel que
    soit le cote -- c'est le signe qui avait ete pris a l'envers une fois dans
    ce depot (cf. SCAN_DONNEES.md section 4).
    """
    t0 = window[fill_idx].ts
    for k in range(fill_idx + 1, len(window)):
        s = window[k]
        if s.inst != inst:
            continue
        if (s.ts - t0) / 1000.0 >= markout_s:
            move = (s.mid - price) / price * 1e4
            return move if side == "bid" else -move
    return None


@dataclass
class FillStats:
    inst: str
    n_attempts: int
    n_filled: int
    median_seconds_to_fill: float | None
    mean_markout_bps: float | None
    n_markout: int
    n_windows: int = 0
    """Nombre de fenetres de collecte DISTINCTES ayant contribue.

    C'est la vraie taille d'echantillon. Deux essais espaces de 3 secondes dans
    la meme fenetre de 8 minutes voient la meme trajectoire de prix : ce sont
    des copies d'une observation, pas deux observations. Le depot s'est deja
    fait prendre exactement ainsi (RAPPORT_FINAL.md : « ce t = 8,8 portait sur
    1 348 remplissages ; mais la bande dure 30 heures, donc a l'horizon 300 s
    il n'existe que 360 fenetres DISJOINTES »).
    """

    @property
    def fill_rate(self) -> float:
        return self.n_filled / self.n_attempts if self.n_attempts else 0.0


#: Ecart au-dela duquel deux releves appartiennent a deux fenetres de collecte.
WINDOW_GAP_S = 300.0


def measure(snapshots: Iterable[Snapshot], inst: str, side: str = "bid",
            horizon_s: float = 240.0, markout_s: float = 30.0) -> FillStats:
    w = [s for s in snapshots if s.inst == inst]
    w.sort(key=lambda s: s.ts)
    outcomes: List[FillOutcome] = []
    windows, contributing = 0, set()
    for i, s in enumerate(w):
        if i and (s.ts - w[i - 1].ts) / 1000.0 > WINDOW_GAP_S:
            windows += 1
        # une tentative n'est evaluable que si assez d'avenir la suit DANS SA
        # fenetre -- pas dans la suivante, collectee des heures plus tard
        end = i
        while end + 1 < len(w) and (w[end + 1].ts - w[end].ts) / 1000.0 <= WINDOW_GAP_S:
            end += 1
        if (w[end].ts - s.ts) / 1000.0 < horizon_s:
            continue
        contributing.add(windows)
        outcomes.append(simulate_passive_order(w, i, side, horizon_s, markout_s))
    filled = [o for o in outcomes if o.filled]
    marks = [o.markout_bps for o in filled if o.markout_bps is not None]
    return FillStats(
        inst=inst, n_attempts=len(outcomes), n_filled=len(filled),
        median_seconds_to_fill=st.median([o.seconds_to_fill for o in filled]) if filled else None,
        mean_markout_bps=st.fmean(marks) if marks else None,
        n_markout=len(marks), n_windows=len(contributing))


#: Seuils de decision de l'etape 2 de OBJECTIF.md, declares avant la mesure.
MIN_FILL_RATE = 0.60          #: en dessous, le cout effectif rejoint le taker
MAX_ADVERSE_OVER_SPREAD = 1.0  #: selection adverse <= demi-spread encaisse
MIN_WINDOWS = 30              #: fenetres DISTINCTES, pas releves


def verdict(stats: FillStats, half_spread_bps: float) -> str:
    if stats.n_windows < MIN_WINDOWS:
        return (f"INSUFFISANT ({stats.n_windows} fenêtres distinctes, "
                f"il en faut {MIN_WINDOWS})")
    if stats.n_attempts < 200:
        return f"INSUFFISANT ({stats.n_attempts} essais, il en faut 200)"
    if stats.fill_rate < MIN_FILL_RATE:
        return f"ECHEC remplissage ({stats.fill_rate:.0%} < {MIN_FILL_RATE:.0%})"
    if stats.mean_markout_bps is None:
        return "INSUFFISANT (aucun markout calculable)"
    adverse = -stats.mean_markout_bps
    if adverse > MAX_ADVERSE_OVER_SPREAD * half_spread_bps:
        return (f"ECHEC selection adverse ({adverse:.2f} bps > "
                f"demi-spread {half_spread_bps:.2f})")
    return f"OK ({stats.fill_rate:.0%} remplis, markout {stats.mean_markout_bps:+.2f} bps)"
