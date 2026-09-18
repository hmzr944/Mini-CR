"""L'ETAT DU MARCHE, calcule uniquement avec ce qui precede l'instant t.

Le mandat interdit de supposer d'avance quelle variable compte. Ce module ne
decide donc rien : il expose un vecteur de variables observables, toutes
CAUSALES, et laisse le modele de valeur trancher lesquelles servent.

ANTI-LOOK-AHEAD, la regle qui gouverne tout ce fichier : une variable a
l'indice i n'utilise que les lignes d'indice <= i. Aucune fenetre ne regarde
devant. Les tests verrouillent cette propriete en comparant le vecteur
calcule sur la serie complete a celui calcule sur la serie TRONQUEE en i :
ils doivent etre identiques bit a bit.

Les lignes de panneau ont la forme

    (ts_ms, bid_px, ask_px, bid_sz, ask_sz, buy_usd, sell_usd)

ou les tailles sont en CONTRATS et les flux en USD agreges sur l'intervalle
depuis la ligne precedente.
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: Noms des variables d'etat, dans l'ordre du vecteur. Le modele de valeur
#: n'en connait aucune a l'avance : il les selectionne sur l'echantillon
#: d'apprentissage.
FEATURES = (
    "spread_bps",      # cout d'une traversee, en points de base
    "qimb",            # desequilibre du carnet au touch, dans [-1, +1]
    "ofi",             # desequilibre du flux agressif recent, dans [-1, +1]
    "intensity",       # flux agressif recent rapporte a sa mediane propre
    "ret_court",       # rendement du milieu sur la fenetre courte, en bps
    "ret_long",        # rendement du milieu sur la fenetre longue, en bps
    "vol",             # volatilite realisee, ramenee a la fenetre courte
    "accel",           # acceleration : ret_court - sa part dans ret_long
    "depth_ct",        # profondeur au touch, cote le plus mince, en contrats
)


@dataclass(frozen=True)
class StateConfig:
    """Les deux seules echelles de temps de la representation.

    Elles sont DECLAREES, pas optimisees : le mandat interdit de choisir une
    echelle apres avoir vu le resultat qu'elle produit. Elles sont exprimees
    en nombre de lignes de panneau, donc en multiples du pas
    d'echantillonnage.
    """
    short_rows: int = 20          # ~6 s a 300 ms
    long_rows: int = 200          # ~60 s a 300 ms

    def __post_init__(self) -> None:
        if not 0 < self.short_rows < self.long_rows:
            raise ValueError("il faut 0 < short_rows < long_rows")

    @property
    def warmup(self) -> int:
        """Nombre de lignes requises avant que l'etat soit defini."""
        return self.long_rows + 1


def _mid(row: Sequence[float]) -> float:
    return (row[1] + row[2]) / 2.0


class StateBuilder:
    """Construit le vecteur d'etat d'une serie de panneau.

    La normalisation de l'intensite du flux utilise une mediane de reference
    passee explicitement. Elle doit provenir de l'echantillon
    d'APPRENTISSAGE : calculer une mediane sur la serie entiere ferait
    entrer de l'information du futur dans l'etat, meme faiblement. Le
    constructeur refuse une reference absente plutot que d'en inventer une.
    """

    def __init__(self, cfg: StateConfig, flow_median_usd: float) -> None:
        if flow_median_usd <= 0:
            raise ValueError("flow_median_usd doit etre > 0 et venir du TRAIN")
        self.cfg = cfg
        self.flow_median_usd = float(flow_median_usd)

    def at(self, rows: Sequence[Sequence[float]], i: int
           ) -> Optional[Dict[str, float]]:
        """Etat a l'indice i, ou None si l'historique est insuffisant.

        N'accede jamais a rows[j] pour j > i.
        """
        cfg = self.cfg
        if i < cfg.warmup - 1 or i >= len(rows):
            return None
        r = rows[i]
        bid, ask, bsz, asz = r[1], r[2], r[3], r[4]
        if not (ask > bid > 0) or bsz <= 0 or asz <= 0:
            return None
        mid = (bid + ask) / 2.0

        # Desequilibre du carnet : en CONTRATS, ce qui est homogene sur un
        # inverse puisque ctVal y est libelle en USD et donc identique des
        # deux cotes du meme instrument.
        qimb = (bsz - asz) / (bsz + asz)

        j0 = i - cfg.short_rows + 1
        k0 = i - cfg.long_rows + 1
        buy = sum(rows[j][5] for j in range(j0, i + 1))
        sell = sum(rows[j][6] for j in range(j0, i + 1))
        tot = buy + sell
        ofi = (buy - sell) / tot if tot > 0 else 0.0

        mid_short = _mid(rows[j0 - 1]) if j0 - 1 >= 0 else mid
        mid_long = _mid(rows[k0 - 1]) if k0 - 1 >= 0 else mid
        ret_court = (mid - mid_short) / mid_short * 10_000.0
        ret_long = (mid - mid_long) / mid_long * 10_000.0

        pas = []
        for j in range(k0, i + 1):
            a, b = _mid(rows[j - 1]), _mid(rows[j])
            if a > 0:
                pas.append((b - a) / a * 10_000.0)
        vol = (st.pstdev(pas) * math.sqrt(cfg.short_rows)) if len(pas) > 1 else 0.0

        # Acceleration : ce que la fenetre courte fait EN PLUS de sa part
        # proportionnelle dans la fenetre longue. Zero = mouvement regulier.
        accel = ret_court - ret_long * (cfg.short_rows / cfg.long_rows)

        return {
            "spread_bps": (ask - bid) / mid * 10_000.0,
            "qimb": qimb,
            "ofi": ofi,
            "intensity": tot / self.flow_median_usd,
            "ret_court": ret_court,
            "ret_long": ret_long,
            "vol": vol,
            "accel": accel,
            "depth_ct": min(bsz, asz),
        }


def flow_median(rows: Sequence[Sequence[float]], cfg: StateConfig,
                lo: int, hi: int) -> float:
    """Mediane du flux agressif par fenetre courte, sur [lo, hi).

    Sert de reference de normalisation. A calculer sur l'APPRENTISSAGE seul.
    Retourne 0.0 si aucun flux n'a ete observe : l'appelant doit alors
    ecarter l'instrument plutot que de substituer une valeur.
    """
    vals = []
    for i in range(max(lo, cfg.warmup - 1), hi):
        j0 = i - cfg.short_rows + 1
        vals.append(sum(rows[j][5] + rows[j][6] for j in range(j0, i + 1)))
    pos = [v for v in vals if v > 0]
    return st.median(pos) if pos else 0.0
