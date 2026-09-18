"""L'ETAT DU MARCHE ENTIER, vu d'un instrument, sans jamais lire son futur.

POURQUOI CE MODULE EXISTE. La politique construite jusqu'ici ne voyait qu'un
instrument a la fois. Elle etait donc structurellement incapable de percevoir
qu'un instrument bouge APRES les autres : toute information d'avance-retard
lui etait invisible, non pas parce qu'elle n'existe pas, mais parce que la
representation ne la contenait pas. Le panneau, lui, echantillonne quinze
perpetuels sur la meme horloge : c'est la seule donnee du depot qui autorise
cette question.

LE PIEGE, ET IL EST SERIEUX. Les lignes du panneau ne sont PAS alignees par
indice. A un meme indice, l'horodatage varie jusqu'a 2,3 SECONDES d'un
instrument a l'autre. Croiser les instruments par indice reviendrait donc a
melanger des instants distants de plusieurs secondes — et, pour l'instrument
en retard d'horloge, a lui faire lire le FUTUR des autres. Sur une question
d'avance-retard a l'echelle de la seconde, ce serait exactement l'artefact
recherche.

D'ou la regle de ce fichier : tout croisement passe par une GRILLE DE TEMPS
commune, et la valeur d'un instrument a l'instant T est celle de sa derniere
ligne d'horodatage <= T. Jamais la suivante. Un instrument qui n'a pas encore
publie a T garde sa valeur precedente ; il n'emprunte rien a personne.

FUITE PAR SOI-MEME. Une moyenne transversale qui inclut l'instrument etudie
contient son propre mouvement et le lui « predit » trivialement. Toutes les
agregations d'ici sont donc a EXCLUSION DE SOI (leave-one-out).
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: Variables transversales, ajoutees a l'etat local.
CROSS_FEATURES = (
    "mkt_ret_court",   # rendement moyen des AUTRES, fenetre courte, bps
    "mkt_ret_long",    # idem, fenetre longue
    "mkt_ofi",         # desequilibre de flux moyen des AUTRES, [-1, +1]
    "mkt_disp",        # dispersion des rendements des AUTRES, bps
    "residu_court",    # rendement propre MOINS celui du marche, bps
    "residu_long",
)


@dataclass(frozen=True)
class Grid:
    """Grille de temps commune et, pour chaque instrument, l'indice causal.

    `idx[inst][k]` est l'indice de la DERNIERE ligne de cet instrument dont
    l'horodatage est <= times[k], ou -1 si l'instrument n'avait rien publie
    a cet instant.
    """
    times: Tuple[int, ...]
    idx: Dict[str, Tuple[int, ...]]

    def __len__(self) -> int:
        return len(self.times)


def align(panel: Dict[str, Sequence[Sequence[float]]], step_ms: int = 300
          ) -> Grid:
    """Reindexe le panneau sur une grille de temps commune, causalement.

    La grille part du premier instant ou TOUS les instruments ont publie au
    moins une fois : avant cela, une agregation transversale porterait sur un
    echantillon changeant, et sa variation refleterait l'arrivee des
    instruments plutot que le marche.
    """
    if not panel:
        raise ValueError("panneau vide")
    debut = max(rows[0][0] for rows in panel.values())
    fin = min(rows[-1][0] for rows in panel.values())
    if fin <= debut:
        raise ValueError("les instruments ne se recouvrent pas dans le temps")
    times = tuple(range(int(debut), int(fin) + 1, int(step_ms)))

    idx: Dict[str, Tuple[int, ...]] = {}
    for inst, rows in panel.items():
        col, p = [], 0
        n = len(rows)
        for T in times:
            # Avance tant que la ligne SUIVANTE est encore <= T. On s'arrete
            # donc sur la derniere ligne connue a T, jamais sur une future.
            while p + 1 < n and rows[p + 1][0] <= T:
                p += 1
            col.append(p if rows[p][0] <= T else -1)
        idx[inst] = tuple(col)
    return Grid(times, idx)


class CrossSection:
    """Agregats transversaux a exclusion de soi, sur une grille alignee."""

    def __init__(self, panel: Dict[str, Sequence[Sequence[float]]],
                 grid: Grid) -> None:
        self.grid = grid
        self.insts = tuple(sorted(panel))
        # Milieu de chaque instrument a chaque point de grille. None quand
        # l'instrument n'avait rien publie ou que son carnet est croise.
        self.mids: Dict[str, List[Optional[float]]] = {}
        self.flux: Dict[str, List[Tuple[float, float]]] = {}
        for inst in self.insts:
            rows, col = panel[inst], grid.idx[inst]
            mids: List[Optional[float]] = []
            flux: List[Tuple[float, float]] = []
            precedent = -1
            for k, i in enumerate(col):
                if i < 0:
                    mids.append(None)
                    flux.append((0.0, 0.0))
                    precedent = i
                    continue
                b, a = rows[i][1], rows[i][2]
                mids.append((a + b) / 2.0 if a > b > 0 else None)
                # Flux agrege sur les lignes REELLEMENT franchies depuis le
                # point de grille precedent : ni double compte, ni oubli.
                bu = su = 0.0
                for j in range(precedent + 1, i + 1):
                    bu += rows[j][5]
                    su += rows[j][6]
                flux.append((bu, su))
                precedent = i
            self.mids[inst] = mids
            self.flux[inst] = flux

    def _ret(self, inst: str, k: int, w: int) -> Optional[float]:
        if k - w < 0:
            return None
        a, b = self.mids[inst][k - w], self.mids[inst][k]
        if a is None or b is None or a <= 0:
            return None
        return (b - a) / a * 10_000.0

    def at(self, inst: str, k: int, short_w: int, long_w: int
           ) -> Optional[Dict[str, float]]:
        """Variables transversales vues par `inst` au point de grille k."""
        autres_c, autres_l = [], []
        bu = su = 0.0
        for j in self.insts:
            if j == inst:
                continue
            rc, rl = self._ret(j, k, short_w), self._ret(j, k, long_w)
            if rc is not None:
                autres_c.append(rc)
            if rl is not None:
                autres_l.append(rl)
            for w in range(max(0, k - short_w + 1), k + 1):
                b_, s_ = self.flux[j][w]
                bu += b_
                su += s_
        if len(autres_c) < 2 or len(autres_l) < 2:
            return None
        propre_c, propre_l = self._ret(inst, k, short_w), self._ret(inst, k, long_w)
        if propre_c is None or propre_l is None:
            return None
        mc, ml = st.fmean(autres_c), st.fmean(autres_l)
        tot = bu + su
        return {
            "mkt_ret_court": mc,
            "mkt_ret_long": ml,
            "mkt_ofi": ((bu - su) / tot) if tot > 0 else 0.0,
            "mkt_disp": st.pstdev(autres_c) if len(autres_c) > 1 else 0.0,
            "residu_court": propre_c - mc,
            "residu_long": propre_l - ml,
        }


def resample(panel: Dict[str, Sequence[Sequence[float]]], grid: Grid
             ) -> Dict[str, List[Tuple[float, ...]]]:
    """Reecrit le panneau sur la grille commune, une ligne par point.

    Apres cet appel, l'indice k designe le MEME instant chez tous les
    instruments, et tout le moteur existant — construction d'etat, execution,
    grille disjointe — s'applique sans modification.

    Le carnet retenu a l'instant T est le dernier PUBLIE avant T : c'est ce
    qu'un participant verrait, pas ce qui sera publie ensuite. Le flux, lui,
    est la SOMME des lignes reellement franchies depuis le point precedent :
    sans cela, un instrument lent perdrait des transactions et un instrument
    rapide en compterait deux fois.

    Les points anterieurs a la premiere publication d'un instrument sont
    ecartes en tete : la serie commence quand l'instrument existe.
    """
    out: Dict[str, List[Tuple[float, ...]]] = {}
    for inst, rows in panel.items():
        col = grid.idx[inst]
        serie: List[Tuple[float, ...]] = []
        precedent = -1
        for k, i in enumerate(col):
            if i < 0:
                serie.append(())
                precedent = i
                continue
            bu = su = 0.0
            for j in range(precedent + 1, i + 1):
                bu += rows[j][5]
                su += rows[j][6]
            r = rows[i]
            serie.append((grid.times[k], r[1], r[2], r[3], r[4], bu, su))
            precedent = i
        out[inst] = serie
    return out
