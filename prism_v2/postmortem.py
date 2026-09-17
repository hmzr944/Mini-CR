#!/usr/bin/env python3
"""POST-MORTEM — a quel barreau chaque famille meurt-elle ?

« Ca ne marche pas » n'est pas une conclusion exploitable. Ce module dit, pour
chaque famille testee, QUELLE contrainte l'a tuee — et surtout laquelle elle
avait franchie.

L'ECHELLE DES CONTRAINTES, dans l'ordre ou elles s'appliquent :

    signal apparent
       -> stabilite temporelle      (le signe tient-il hors echantillon ?)
       -> alpha reel                (le brut est-il positif ?)
       -> cout de turnover          (alpha/turnover > cout unitaire ?)
       -> liquidite
       -> capacite exploitable
       -> capital immobilise
       -> PnL / EUR / jour

Une hypothese meurt au PREMIER barreau qu'elle ne franchit pas. Savoir lequel
change tout : une famille qui meurt au cout appelle une execution moins chere
ou un signal plus lent ; une famille qui meurt a la stabilite n'appelle RIEN
de tout cela — aucune amelioration d'execution ne rend un signe stable.

CE QUE CE MODULE A CORRIGE DANS MA PROPRE DIRECTION DE RECHERCHE. J'ai passe
un temps considerable a reduire le turnover. La matrice montre que le carry
franchissait deja le barreau du cout avec une marge de 5 a 11x, et mourait un
cran plus tot. J'attaquais le mauvais barreau.

LES BARREAUX NON TESTES RESTENT UNKNOWN. Liquidite, capacite et capital
immobilise n'ont jamais ete mesures systematiquement sur ces familles : elles
mouraient avant. Les marquer « OK » parce qu'on n'a pas vu de probleme serait
transformer une absence de mesure en resultat.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Barreaux, dans l'ordre d'application.
RUNGS: Tuple[str, ...] = (
    "SIGNAL_APPARENT",
    "STABILITE_TEMPORELLE",
    "ALPHA_REEL",
    "COUT_TURNOVER",
    "LIQUIDITE",
    "CAPACITE",
    "CAPITAL_IMMOBILISE",
    "PNL_PAR_EUR_PAR_JOUR",
)

PASS = "PASSE"
FAIL = "ECHOUE"
UNKNOWN = "UNKNOWN"      # jamais mesure — n'est PAS un echec, et pas un succes

#: Cout unitaire mesure : frais 4,5 bps + demi-spread p75 reel 2,04 bps.
COST_PER_TURNOVER_BPS = 6.54


@dataclass(frozen=True)
class WindowResult:
    """Ce qu'une famille a fait sur une fenetre."""
    window: str
    bps_per_day: float
    turnover_per_bar: float
    gross_alpha_bps: float
    alpha_per_turnover_bps: Optional[float]


@dataclass
class Family:
    name: str
    venue: str
    days: int
    windows: List[WindowResult] = field(default_factory=list)
    #: Barreaux dont la mesure n'a jamais ete faite, avec la raison.
    unmeasured: Dict[str, str] = field(default_factory=dict)

    # ---- evaluation barreau par barreau ---------------------------------
    def signal_apparent(self) -> str:
        """Y a-t-il eu, au moins une fois, quelque chose a regarder ?"""
        return PASS if any(w.gross_alpha_bps != 0.0 for w in self.windows) else FAIL

    def temporal_stability(self) -> str:
        """Le SIGNE de l'alpha brut tient-il d'une fenetre a l'autre ?

        C'est le barreau decisif, et le plus souvent fatal. On teste le signe
        du brut, pas du net : une famille dont le brut change de signe est
        morte quels que soient ses couts.
        """
        signs = {1 if w.gross_alpha_bps > 0 else -1 for w in self.windows
                 if w.gross_alpha_bps != 0.0}
        if len(self.windows) < 2:
            return UNKNOWN
        return PASS if len(signs) == 1 else FAIL

    def real_alpha(self) -> str:
        """L'alpha brut est-il positif sur l'ensemble des fenetres ?"""
        if not self.windows:
            return UNKNOWN
        total = sum(w.gross_alpha_bps for w in self.windows)
        return PASS if total > 0 else FAIL

    def turnover_cost(self) -> str:
        """alpha/turnover depasse-t-il le cout unitaire, en VALEUR ABSOLUE ?

        On prend la valeur absolue a dessein : la question posee ici est
        « l'information est-elle assez ample pour payer son repositionnement »,
        pas « va-t-elle dans le bon sens ». Le sens est l'affaire du barreau
        precedent. Les confondre masquerait qu'une famille peut tres bien
        franchir le cout et mourir quand meme.
        """
        ratios = [abs(w.alpha_per_turnover_bps) for w in self.windows
                  if w.alpha_per_turnover_bps is not None]
        if not ratios:
            return UNKNOWN
        return PASS if min(ratios) > COST_PER_TURNOVER_BPS else FAIL

    def verdicts(self) -> Dict[str, str]:
        out = {
            "SIGNAL_APPARENT": self.signal_apparent(),
            "STABILITE_TEMPORELLE": self.temporal_stability(),
            "ALPHA_REEL": self.real_alpha(),
            "COUT_TURNOVER": self.turnover_cost(),
        }
        for rung in RUNGS:
            if rung not in out:
                out[rung] = UNKNOWN
        return out

    def killed_at(self) -> Optional[str]:
        """Premier barreau ECHOUE. None si la famille n'a echoue nulle part."""
        v = self.verdicts()
        for rung in RUNGS:
            if v[rung] == FAIL:
                return rung
        return None

    def cleared_before_death(self) -> List[str]:
        """Barreaux franchis AVANT celui qui l'a tuee.

        C'est l'information qu'un simple « ca ne marche pas » detruit.
        """
        killed = self.killed_at()
        out = []
        v = self.verdicts()
        for rung in RUNGS:
            if rung == killed:
                break
            if v[rung] == PASS:
                out.append(rung)
        return out


def render_matrix(families: Sequence[Family]) -> str:
    cols = ("STABILITE_TEMPORELLE", "ALPHA_REEL", "COUT_TURNOVER",
            "LIQUIDITE", "CAPACITE")
    sym = {PASS: "OK", FAIL: "NON", UNKNOWN: "?"}
    head = f"{'famille':<22}{'venue':<7}{'jours':>6}" + "".join(
        f"{c.split('_')[0][:9]:>11}" for c in cols) + "   tuee a"
    lines = [head, "-" * len(head)]
    for f in families:
        v = f.verdicts()
        row = f"{f.name:<22}{f.venue:<7}{f.days:>6}" + "".join(
            f"{sym[v[c]]:>11}" for c in cols)
        lines.append(row + f"   {f.killed_at() or '-'}")
    return "\n".join(lines)


def measured_families() -> List[Family]:
    """Les familles reellement testees, avec leurs chiffres MESURES.

    Sources : FUNDING_ARB_PROTOCOL.md, LONG_TEST_PROTOCOL.md,
    MOMENTUM_PROTOCOL.md. Aucun chiffre n'est estime ici.
    """
    unmeasured_tail = {
        "LIQUIDITE": "jamais mesuree systematiquement : la famille mourait avant",
        "CAPACITE": "idem. Seule mesure ponctuelle : profondeur au touch "
                    "Hyperliquid, mediane 173 $, p25 20 $ sur 140 actifs",
        "CAPITAL_IMMOBILISE": "non atteint",
        "PNL_PAR_EUR_PAR_JOUR": "non atteint",
    }
    return [
        Family("carry (funding)", "HL", 840, [
            WindowResult("discovery", -7.75, 0.0346, -2639.9, -30.812),
            WindowResult("validation", 12.56, 0.0312, 2797.8, 73.684),
        ], dict(unmeasured_tail)),
        Family("momentum 7 j", "HL", 840, [
            WindowResult("discovery", -1.50, 0.1463, 1749.7, 4.826),
            WindowResult("validation", 15.41, 0.1236, 4112.8, 27.308),
        ], dict(unmeasured_tail)),
        Family("retournement 1 j", "HL", 840, [
            WindowResult("discovery", -25.78, 0.4632, -3142.0, -2.737),
            WindowResult("validation", -27.00, 0.4390, -1984.5, -3.711),
        ], dict(unmeasured_tail)),
        Family("momentum 30 j", "OKX", 900, [
            WindowResult("discovery", 4.21, 0.0498, 2218.8, 20.64),
            WindowResult("validation", -7.19, 0.0430, -743.4, -21.354),
        ], dict(unmeasured_tail)),
        Family("momentum 90 j", "OKX", 900, [
            WindowResult("discovery", -1.85, 0.0219, -358.5, -7.591),
            WindowResult("validation", 0.46, 0.0199, 167.1, 10.346),
        ], dict(unmeasured_tail)),
    ]


def summary(families: Sequence[Family]) -> dict:
    by_rung: Dict[str, int] = {}
    for f in families:
        k = f.killed_at() or "AUCUN"
        by_rung[k] = by_rung.get(k, 0) + 1
    cleared_cost = [f.name for f in families
                    if f.verdicts()["COUT_TURNOVER"] == PASS]
    return {
        "n_familles": len(families),
        "tuees_par_barreau": by_rung,
        "franchissent_le_cout": cleared_cost,
        "jamais_atteint": [r for r in RUNGS
                           if all(f.verdicts()[r] == UNKNOWN for f in families)],
    }


def main() -> int:
    fams = measured_families()
    print(render_matrix(fams))
    s = summary(fams)
    print(f"\nTUEES PAR BARREAU : {s['tuees_par_barreau']}")
    print(f"\nFranchissent le barreau du COUT : {', '.join(s['franchissent_le_cout']) or 'aucune'}")
    print("  -> ces familles-la n'ont PAS un probleme de cout d'execution.")
    print("     Les rendre moins cheres ou plus lentes ne les sauverait pas.")
    print(f"\nBarreaux JAMAIS atteints : {', '.join(s['jamais_atteint'])}")
    print("  -> non mesures, donc UNKNOWN. Les compter comme franchis serait")
    print("     transformer une absence de mesure en resultat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
