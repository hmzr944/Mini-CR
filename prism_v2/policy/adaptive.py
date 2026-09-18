"""LA POLITIQUE QUI SE CORRIGE : marche en avant et perte de confiance.

La politique construite jusqu'ici est ajustee une fois puis gelee. Elle ne
peut donc ni s'ameliorer ni s'arreter : si l'economie se degrade, elle
continue de trader exactement comme avant. C'est le chainon manquant de la
boucle — le retour d'experience doit MODIFIER LE COMPORTEMENT, pas alimenter
un rapport.

DEUX MECANISMES, ET RIEN DE PLUS.

  MARCHE EN AVANT. Le temps est decoupe en segments. Chaque segment est joue
  avec une politique ajustee UNIQUEMENT sur ce qui le precede. Aucun segment
  n'est jamais rejoue, aucune donnee future n'entre dans son ajustement. La
  suite des segments forme un unique parcours hors echantillon, pas une
  moyenne de decoupes rejouees.

  CORRECTION DU BIAIS. A la fin de chaque segment on compare le realise a
  l'attendu. L'ecart moyen accumule est le BIAIS de la politique : de
  combien elle surestime ses propres actions. Ce biais devient la BARRE que
  toute action doit franchir au segment suivant. Une politique qui a
  surestime de 7 bps doit desormais en promettre 7 de plus avant d'agir.

  UNE PREMIERE VERSION N'AVAIT QUE LE RETRECISSEMENT, ET ELLE NE POUVAIT PAS
  MARCHER. Retrecir multiplie la valeur par n/(n+k), un facteur toujours
  POSITIF : une cellule positive reste positive, simplement plus petite, et
  franchit encore un seuil de zero. Sur le panneau reel, la confiance est
  tombee d'un facteur cent — 1,000 puis 0,010 — pendant que le nombre de
  trades ne bougeait pas : 21, 25, 26, 32, 17, 10, 18, 29. Le seul levier
  construit ne pouvait structurellement pas arreter le systeme. Pour qu'un
  retour d'experience puisse l'arreter, il faut deplacer la BARRE, pas
  seulement rapetisser la valeur.

  Le retrecissement est conserve — il protege des cellules rares — mais ce
  n'est plus lui qui porte l'adaptation.

CE QUE LE BIAIS N'EST PAS. Ce n'est pas un reglage. C'est la moyenne
mesuree de (realise - attendu) sur les segments deja joues, en points de
base, avec sa definition arithmetique exacte et aucune interpretation
au-dela.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from prism_v2.policy.action import EXECUTABLE, Action
from prism_v2.policy.attribution import Attribution, attribuer_tout
from prism_v2.policy.engine import Sample, run_policy
from prism_v2.policy.ledger import Decision, Ledger
from prism_v2.policy.value import PRIOR_N, ActionValue, select_features

#: Facteur applique a la confiance quand un segment deçoit, et quand il
#: tient. Declares d'avance ; jamais ajustes sur un resultat.
PENALITE = 0.5
RECOMPENSE = 1.25
CONFIANCE_MIN = 0.02        # sous ce seuil la politique ne trade plus
CONFIANCE_MAX = 1.0


@dataclass
class Segment:
    """Ce qu'un segment de marche en avant a produit."""
    indice: int
    n_apprentissage: int
    n_decisions: int
    features: Tuple[str, ...]
    confiance_entree: float
    seuil_entree_bps: float       # barre imposee par le biais accumule
    attendu_bps: float
    realise_bps: float
    trades: int
    net_usd: float


@dataclass
class MarcheEnAvant:
    """Resultat complet d'un parcours en marche en avant."""
    segments: List[Segment] = field(default_factory=list)
    registre: Optional[Ledger] = None
    attribution: Optional[Attribution] = None

    def biais_final_bps(self) -> float:
        """Biais mesure a la fin du parcours, en bps. Negatif = surestime."""
        tot = sum(s.trades for s in self.segments)
        if tot == 0:
            return 0.0
        return sum((s.realise_bps - s.attendu_bps) * s.trades
                   for s in self.segments) / tot

    def confiance_finale(self) -> float:
        if not self.segments:
            return CONFIANCE_MAX
        c = self.segments[-1].confiance_entree
        s = self.segments[-1]
        return _maj_confiance(c, s.attendu_bps, s.realise_bps, s.trades)


def _biais(segments: Sequence[Segment]) -> float:
    """Moyenne ponderee de (realise - attendu) sur les segments deja joues.

    Ponderee par le nombre de trades : un segment qui n'a rien fait
    n'apporte aucune information sur la justesse des estimations.
    """
    tot = sum(s.trades for s in segments)
    if tot == 0:
        return 0.0
    return sum((s.realise_bps - s.attendu_bps) * s.trades
               for s in segments) / tot


def _maj_confiance(confiance: float, attendu: float, realise: float,
                   trades: int) -> float:
    """Nouvelle confiance apres un segment.

    Un segment sans trade ne prouve rien, ni dans un sens ni dans l'autre :
    la confiance est laissee intacte. La punir reviendrait a punir la
    prudence, la recompenser reviendrait a recompenser l'inaction.
    """
    if trades == 0:
        return confiance
    if realise < attendu:
        return max(CONFIANCE_MIN * 0.5, confiance * PENALITE)
    return min(CONFIANCE_MAX, confiance * RECOMPENSE)


def parcourir(samples: Sequence[Sample], features: Sequence[str],
              actions: Sequence[Action], n_segments: int,
              part_apprentissage: float, capital_usd: float,
              cout_attendu_bps: float, statut: str = EXECUTABLE,
              prior_n: float = PRIOR_N) -> MarcheEnAvant:
    """Joue les echantillons en marche en avant, avec perte de confiance.

    `part_apprentissage` fixe la taille du premier bloc d'apprentissage. Les
    segments suivants s'ajustent sur TOUT ce qui les precede (fenetre
    croissante) : ce que le systeme a appris n'est jamais oublie, seule la
    confiance qu'il s'accorde peut baisser.
    """
    if not 0.0 < part_apprentissage < 1.0:
        raise ValueError("part_apprentissage doit etre dans ]0, 1[")
    if n_segments < 1:
        raise ValueError("il faut au moins un segment")
    ech = sorted(samples, key=lambda s: s.ts_ms)
    debut = int(len(ech) * part_apprentissage)
    if debut < 50 or debut >= len(ech):
        raise ValueError("echantillon trop petit pour une marche en avant")

    taille = max(1, (len(ech) - debut) // n_segments)
    res = MarcheEnAvant()
    registre = Ledger(reserved_capital_usd=capital_usd)
    confiance = CONFIANCE_MAX

    for s_i in range(n_segments):
        lo = debut + s_i * taille
        hi = min(len(ech), lo + taille) if s_i < n_segments - 1 else len(ech)
        if lo >= hi:
            break
        appr, test = ech[:lo], ech[lo:hi]

        # Le retrecissement est le SEUL canal par lequel le retour
        # d'experience agit. Une confiance basse gonfle le prior, donc tire
        # toutes les cellules vers zero, donc vers NO_TRADE.
        prior_eff = prior_n / max(confiance, CONFIANCE_MIN)
        # LA BARRE. Le biais accumule est ce dont la politique s'est montree
        # trop optimiste ; elle doit desormais le promettre en plus. Un biais
        # positif (elle a sous-estime) n'abaisse PAS la barre sous zero :
        # se croire modeste n'autorise pas a trader une valeur negative.
        seuil = max(0.0, -_biais(res.segments))
        feats, av, _ = select_features([(x.state, x.nets) for x in appr],
                                       features, actions, prior_n=prior_eff)
        if av is None:
            res.segments.append(Segment(s_i, len(appr), len(test), (),
                                        confiance, seuil, 0.0, 0.0, 0, 0.0))
            continue

        led = run_policy(test, av, actions, capital_usd, seuil_bps=seuil)
        traites = [d for d in led.decisions if d.action.is_trade]
        for d in led.decisions:
            registre.record(d)
        attendu = (sum(d.predicted_bps for d in traites) / len(traites)
                   if traites else 0.0)
        realise = (sum(d.net_bps for d in traites) / len(traites)
                   if traites else 0.0)
        res.segments.append(Segment(
            indice=s_i, n_apprentissage=len(appr), n_decisions=len(test),
            features=feats, confiance_entree=confiance, seuil_entree_bps=seuil,
            attendu_bps=attendu, realise_bps=realise, trades=len(traites),
            net_usd=sum(d.net_usd for d in traites)))
        confiance = _maj_confiance(confiance, attendu, realise, len(traites))

    res.registre = registre
    res.attribution = attribuer_tout(
        [d for d in registre.decisions if d.action.is_trade and
         d.status == statut], cout_attendu_bps)
    return res
