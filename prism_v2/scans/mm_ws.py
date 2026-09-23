#!/usr/bin/env python3
"""HYPOTHESE A — la cotation passive, mesuree a 4 ms au lieu de 5 000.

    python3 -m prism_v2.scans.mm_ws --in FICHIER

CE QUI EST TESTE, ET CE QUI NE L'EST PAS. La mesure precedente rendait un
equilibre de -3,57 puis +0,01 bps sur BTC — le seul marche declare coherent —
et la garde `tape_mid_is_fit_for_level` ecartait QUATRE lignes sur six, dont
ARB a +2,88 bps, le seul chiffre au-dessus du frais maker. La garde se
declenchait quand le mid ENREGISTRE etait perime par rapport au fill : sa
cause etait la cadence de sondage, mesuree a 4 989 ms d'ecart median entre
deux carnets. Le flux WebSocket la ramene a 4 ms.

La question n'est donc pas neuve. L'instrument l'est, d'un facteur 1 250.

LA REGLE ANTI-BIAIS, ECRITE AVANT LA COLLECTE. Je sais qu'une ligne ecartee
etait positive, et c'est exactement la ou un biais entre. Donc :

  - TOUS les marches sont mesures et rapportes dans le MEME passage ;
  - la garde de coherence reste INCHANGEE, seuils compris ;
  - chaque exclusion est enregistree AVEC SA RAISON et publiee ;
  - aucune lecture ciblee avant le calcul uniforme.

Si la garde ecarte encore autant, c'est qu'elle mesure autre chose que la
cadence — et c'est un resultat, pas un echec.

CE QUE LE bookTicker NE DEMONTRE PAS. Il donne le meilleur bid/ask et sa
taille. Il ne demontre ni la position dans la file, ni le taux de
remplissage, ni l'adverse selection subie. L'equilibre calcule ici reste donc
un MAJORANT de ce qu'un maker capture, et le verdict le dit.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.backpack.passive import (MAX_IMPLIED_TO_LIVE_SPREAD_RATIO,
                                       half_spread_earned_bps, markout_bps,
                                       tape_mid_is_fit_for_level)
from prism_v2.backpack.replay import MIN_FILLS_FOR_MEDIAN, BookHistory, Snap
from prism_v2.subsidy.tape import Trade
from prism_v2.tail import quantile

#: Fraicheur maximale d'un carnet, en secondes. RESSERREE de 10,0 s (deux
#: sondages de 5 s) a 0,2 s — le flux publie un carnet toutes les 4 ms en
#: mediane, donc 200 ms laisse passer cinquante messages manques sans laisser
#: passer un carnet d'une autre seconde.
MAX_AGE_S = 0.2

#: Horizon de markout rapporte. Repris des horizons geles.
HORIZON_S = 60.0

#: Frais maker Backpack EU Tier 1, en bps. Le seuil de decision EST ce frais :
#: en dessous, coter perd de l'argent quelle que soit l'elegance du reste.
FRAIS_MAKER_BPS = 2.0

#: Tirages du bootstrap par bloc.
N_BOOTSTRAP = 4000


def charge(path: Path) -> Tuple[Dict[str, BookHistory], Dict[str, List[Trade]], dict]:
    """Charge le fichier unifie. Une ligne tronquee est sautee, jamais reparee."""
    books: Dict[str, List[Snap]] = defaultdict(list)
    tapes: Dict[str, List[Trade]] = defaultdict(list)
    meta = {"trous": 0, "lignes_illisibles": 0}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                meta["lignes_illisibles"] += 1
                continue
            k = d.get("k")
            if k == "book":
                books[d["sym"]].append(Snap(d["ts_ex"], d["bid"], d["ask"],
                                            d["bid_sz"], d["ask_sz"]))
            elif k == "trade":
                tapes[d["sym"]].append(Trade(d["ts_ex"], d["price"], d["size"],
                                             d["taker_is_buy"]))
            elif k == "trou":
                meta["trous"] += 1
    out: Dict[str, BookHistory] = {}
    for s, snaps in books.items():
        h = BookHistory(snaps)
        h.max_age_s = MAX_AGE_S          # resserrement declare en tete
        out[s] = h
    return out, dict(tapes), meta


class Resultat:
    """Ce qu'un marche rend, et pourquoi il a ete retenu ou ecarte."""

    def __init__(self, sym: str):
        self.sym = sym
        self.demi: List[float] = []
        self.mark: List[float] = []
        self.equi: List[float] = []
        self.spreads: List[float] = []
        self.exclusion: Optional[str] = None

    @property
    def n(self) -> int:
        return len(self.equi)

    def spread_median(self) -> Optional[float]:
        return st.median(self.spreads) if self.spreads else None

    def equilibre_median(self) -> Optional[float]:
        return st.median(self.equi) if self.n >= MIN_FILLS_FOR_MEDIAN else None

    def ratio_coherence(self) -> Optional[float]:
        """Demi-spread mesure / demi-spread LU. Doit valoir ~1."""
        sp = self.spread_median()
        if sp is None or sp <= 0 or self.n < MIN_FILLS_FOR_MEDIAN:
            return None
        return st.median(self.demi) / (sp / 2.0)

    def ic95(self, rng: random.Random) -> Optional[Tuple[float, float]]:
        """IC 95 % par bootstrap sur les fills de CE marche."""
        if self.n < MIN_FILLS_FOR_MEDIAN:
            return None
        b = [st.median([rng.choice(self.equi) for _ in self.equi])
             for _ in range(N_BOOTSTRAP)]
        return quantile(b, 0.025), quantile(b, 0.975)


def mesure(sym: str, book: BookHistory, tape: Sequence[Trade]) -> Resultat:
    """Chaque echange devient un fill passif de l'autre cote.

    Le mid AVANT est lu strictement avant l'echange : l'inclure contaminerait
    le demi-spread par le prix de notre propre execution.
    """
    r = Resultat(sym)
    for t in sorted(tape, key=lambda x: x.ts):
        avant = book.before(t.ts)
        apres = book.at_or_after(t.ts + HORIZON_S)
        if avant is None or apres is None:
            continue
        passif_achete = not t.taker_is_buy
        hs = half_spread_earned_bps(t.price, passif_achete, avant.mid)
        mo = markout_bps(t.price, passif_achete, avant.mid, apres.mid)
        r.demi.append(hs)
        r.mark.append(mo)
        r.equi.append(hs + mo)
        r.spreads.append(avant.spread_bps)
    return r


def verdict(retenus: Sequence[Resultat], ecartes: int,
            rng: random.Random) -> Tuple[str, List[str]]:
    """Verdict, avec PRECEDENCE declaree avant la collecte.

    L'ordre resout l'ambiguite signalee en revue : une mediane globale sous le
    frais et trois marches au-dessus avec leurs IC ne doivent pas produire
    deux verdicts contradictoires.

        1. CANDIDAT   >= 3 marches dont la BORNE BASSE de l'IC 95 % depasse
                      le frais maker. Un positif robuste sur des marches
                      identifies est un resultat, meme si la mediane globale
                      est basse : la mediane melange des marches differents.
        2. MITIGE     1 ou 2 marches passent ce critere. Rapporte comme tel,
                      jamais promu, et exige une confirmation independante.
        3. FERMEE     aucun marche ne passe.
        4. INCONNU    moins de 5 marches exploitables — prime sur tout le
                      reste, car sans echantillon aucun verdict ne tient.
        5. HYPOTHESE A NULLE  la garde ecarte encore >= 4 lignes sur 6, soit
                      les deux tiers : la cadence n'etait pas la cause.
    """
    notes: List[str] = []
    total = len(retenus) + ecartes
    if total and ecartes / total >= 2.0 / 3.0:
        notes.append(f"la garde ecarte encore {ecartes}/{total} lignes : "
                     f"la cadence de sondage n'etait pas la cause")
    if len(retenus) < 5:
        return "INCONNU", notes + [f"{len(retenus)} marches exploitables, minimum 5"]
    passants = []
    for r in retenus:
        ic = r.ic95(rng)
        if ic and ic[0] > FRAIS_MAKER_BPS:
            passants.append(f"{r.sym} IC95 [{ic[0]:.2f} ; {ic[1]:.2f}]")
    if len(passants) >= 3:
        return "CANDIDAT", notes + passants
    if passants:
        return "MITIGE", notes + passants
    return "FERMEE", notes + [
        f"aucun des {len(retenus)} marches n'a sa borne basse au-dessus de "
        f"{FRAIS_MAKER_BPS:.0f} bps"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="inp", type=Path, required=True)
    a = p.parse_args(argv)

    books, tapes, meta = charge(a.inp)
    rng = random.Random(20260923)

    print(f"fraicheur max du carnet : {MAX_AGE_S * 1000:.0f} ms"
          f"   horizon : {HORIZON_S:.0f} s"
          f"   frais maker : {FRAIS_MAKER_BPS:.0f} bps")
    print(f"trous de flux : {meta['trous']}   lignes illisibles : "
          f"{meta['lignes_illisibles']}")
    print()

    res = [mesure(s, books[s], tapes.get(s, [])) for s in sorted(books)]
    retenus, ecartes = [], []
    for r in res:
        if r.n < MIN_FILLS_FOR_MEDIAN:
            r.exclusion = f"N={r.n} sous le seuil de {MIN_FILLS_FOR_MEDIAN}"
            ecartes.append(r); continue
        ratio = r.ratio_coherence()
        sp = r.spread_median()
        if ratio is None or sp is None:
            r.exclusion = "spread ou ratio non calculable"
            ecartes.append(r); continue
        if not tape_mid_is_fit_for_level(2.0 * abs(st.median(r.demi)), sp):
            r.exclusion = f"garde de coherence : ratio {ratio:.1f}"
            ecartes.append(r); continue
        retenus.append(r)

    head = (f"{'marche':<20}{'N':>7}{'spread':>9}{'ratio':>8}{'demi-spr':>10}"
            f"{'markout':>10}{'equilibre':>11}{'IC 95 %':>20}")
    print("MARCHES RETENUS"); print(head); print("-" * len(head))
    for r in sorted(retenus, key=lambda x: -x.n):
        ic = r.ic95(rng)
        ics = f"[{ic[0]:>6.2f} ; {ic[1]:>6.2f}]" if ic else "INCONNU"
        print(f"{r.sym:<20}{r.n:>7}{r.spread_median():>9.2f}"
              f"{r.ratio_coherence():>8.1f}{st.median(r.demi):>10.2f}"
              f"{st.median(r.mark):>10.2f}{r.equilibre_median():>11.2f}{ics:>20}")

    print()
    print("MARCHES ECARTES — chaque exclusion avec sa raison")
    for r in sorted(ecartes, key=lambda x: x.sym):
        eq = r.equilibre_median()
        eqs = f"{eq:>8.2f}" if eq is not None else "       -"
        print(f"  {r.sym:<20} N={r.n:>6}  equilibre={eqs}  ->  {r.exclusion}")

    v, notes = verdict(retenus, len(ecartes), rng)
    print()
    print(f"VERDICT : {v}")
    for n in notes:
        print(f"  - {n}")
    print()
    print("L'equilibre est un MAJORANT : le bookTicker ne demontre ni la")
    print("position dans la file, ni le taux de remplissage, ni l'adverse")
    print("selection subie. Un equilibre positif serait un CANDIDAT a valider,")
    print("jamais une capture demontree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
