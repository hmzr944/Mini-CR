#!/usr/bin/env python3
"""COUCHE DE CORRESPONDANCE — ce qui manquait a l'architecture.

AUDIT. Quatorze defauts ont ete trouves dans ce projet. Tous les modules
etaient testes ; 866 tests passaient. Aucun test n'a attrape aucun de ces
defauts — je les ai tous trouves en remarquant un chiffre absurde, et les
tests ont ete ecrits APRES.

La cause est unique et elle est structurelle :

    LES TESTS VERIFIENT CE QUE LE CODE FAIT.
    AUCUN NE VERIFIE QUE LA QUANTITE CALCULEE CORRESPOND A LA REALITE
    QU'ELLE PRETEND DECRIRE.

`notional` etait en contrats et pas en dollars. `rate_per_hour` etait un taux
par 4 h divise par 8. Un livre « dollar-neutre » portait 23,8 % de variance
marche. Une liquidation « cause » du mouvement en etait le symptome. Dans
chaque cas le code faisait exactement ce que ses tests demandaient, et le
nombre ne voulait pas dire ce que son nom disait.

Ce module teste la CORRESPONDANCE, sur donnees reelles, avant toute
conclusion economique. Ce n'est pas une couche de plus : c'est la couche qui
manquait, et son absence explique quatorze defauts sur quatorze.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

OK = "OK"
ALERTE = "ALERTE"
IMPOSSIBLE = "IMPOSSIBLE"      # verification non realisable faute de donnees


@dataclass(frozen=True)
class Verdict:
    check: str
    status: str
    detail: str
    value: Optional[float] = None

    @property
    def passed(self) -> bool:
        return self.status == OK


# ── 1. Grandeurs : une unite a un domaine physique ───────────────────────────
#
# Classe d'erreur couverte : constante de venue supposee uniforme.
#   - ctVal code en dur pour les inverses
#   - cadence de funding OKX supposee 8 h alors que 90/142 sont a 4 h
#   - tailles de carnet lues en unites de base au lieu de contrats
# Les trois ont FABRIQUE des opportunites, jamais des pertes. Elles se
# reconnaissent a un chiffre hors du domaine physique de son unite.

PLAUSIBLE: Dict[str, Tuple[float, float]] = {
    # funding annualise : au-dela de +/-500 %/an, c'est une erreur d'unite
    # avant d'etre une opportunite. Le bug de cadence produisait +3879 %/an.
    "funding_apr": (-5.0, 5.0),
    # demi-spread : un carnet sain ne cote pas a plus de 100 bps de demi-spread
    "half_spread_bps": (0.0, 100.0),
    # capture attendue sur un horizon intrajournalier
    "capture_bps": (-500.0, 500.0),
    # notionnel d'un niveau de carnet, en dollars
    "level_notional_usd": (0.0, 1e9),
    # exposition residuelle d'un livre declare neutre
    "net_exposure": (-0.05, 0.05),
    # rendement par barre
    "bar_return": (-0.5, 0.5),
}


def check_magnitude(name: str, values: Sequence[float],
                    unit: str) -> Verdict:
    """La grandeur tient-elle dans le domaine physique de son unite ?"""
    if unit not in PLAUSIBLE:
        return Verdict(f"grandeur:{name}", IMPOSSIBLE,
                       f"unite inconnue: {unit}")
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return Verdict(f"grandeur:{name}", IMPOSSIBLE, "aucune valeur")
    lo, hi = PLAUSIBLE[unit]
    out = [v for v in vals if v < lo or v > hi]
    if out:
        worst = max(out, key=abs)
        return Verdict(
            f"grandeur:{name}", ALERTE,
            f"{len(out)}/{len(vals)} hors du domaine [{lo}, {hi}] de "
            f"'{unit}' — pire: {worst:.4g}. Une unite fausse fabrique des "
            f"opportunites avant de creer des pertes.", worst)
    return Verdict(f"grandeur:{name}", OK,
                   f"{len(vals)} valeurs dans [{lo}, {hi}]")


# ── 2. Une regle doit pouvoir se declencher ──────────────────────────────────
#
# Classe d'erreur couverte : regle degeneree.
#   - mediane de reference sur TOUTES les minutes -> zero partout
#   - fenetre « prete » a 1440 alors qu'on ne pousse que les minutes actives
#   - melange S5 identiquement nul par construction
# Ces erreurs sont SILENCIEUSES : elles produisent « aucune opportunite »,
# indiscernable d'un vrai resultat negatif. C'est le pire mode d'echec.

def check_firing_rate(name: str, n_triggers: int, n_opportunities: int,
                      min_rate: float = 1e-4,
                      max_rate: float = 0.20) -> Verdict:
    """Le taux de declenchement est-il plausible sur donnees reelles ?

    Trop bas : la regle est probablement degeneree et rendra « rien trouve »
    quelle que soit la realite. Trop haut : le seuil ne filtre rien et la
    « regle » n'est qu'un echantillonnage du marche.
    """
    if n_opportunities <= 0:
        return Verdict(f"declenchement:{name}", IMPOSSIBLE, "aucune occasion")
    rate = n_triggers / n_opportunities
    if n_triggers == 0:
        return Verdict(
            f"declenchement:{name}", ALERTE,
            f"0 declenchement sur {n_opportunities} occasions. Une regle qui "
            f"ne peut pas se declencher n'est pas une hypothese — et son "
            f"resultat negatif serait indiscernable d'un vrai.", 0.0)
    if rate < min_rate:
        return Verdict(f"declenchement:{name}", ALERTE,
                       f"taux {rate:.2e} sous le plancher {min_rate:.0e} : "
                       f"regle probablement degeneree", rate)
    if rate > max_rate:
        return Verdict(f"declenchement:{name}", ALERTE,
                       f"taux {rate:.1%} au-dessus du plafond {max_rate:.0%} : "
                       f"le seuil ne filtre rien", rate)
    return Verdict(f"declenchement:{name}", OK,
                   f"{n_triggers} declenchements, taux {rate:.2%}", rate)


# ── 3. Le sens de la causalite ───────────────────────────────────────────────
#
# Classe d'erreur couverte : prendre un symptome pour une cause.
# La Phase C entiere reposait sur « la liquidation POUSSE le prix ». Mesure :
# le prix bougeait deja de 14,55 bps dans la direction de la liquidation
# pendant les 60 s qui la precedaient, sur 100 % des evenements. C'etait un
# symptome retarde, deja price. Un mois de travail sur une premisse fausse et
# testable en vingt minutes.

@dataclass(frozen=True)
class CausalReport:
    n: int
    pre_move_bps: float
    post_move_bps: float
    ratio: Optional[float]
    verdict: Verdict


def check_causal_direction(name: str, pre_moves: Sequence[float],
                           post_moves: Sequence[float],
                           symptom_ratio: float = 2.0,
                           min_post_bps: float = 0.0) -> CausalReport:
    """L'evenement PRECEDE-t-il le mouvement, ou le suit-il ?

    `pre_moves` : mouvement du prix AVANT l'evenement, oriente dans le sens
    que l'evenement impose. `post_moves` : idem apres.

    Si le mouvement anterieur domine le posterieur, l'evenement est un
    SYMPTOME : conditionner dessus revient a conditionner sur un mouvement
    de prix deja advenu et deja integre.
    """
    if len(pre_moves) < 10 or len(post_moves) < 10:
        v = Verdict(f"causalite:{name}", IMPOSSIBLE,
                    "moins de 10 evenements : rien n'est conclu")
        return CausalReport(len(pre_moves), 0.0, 0.0, None, v)
    pre = statistics.fmean(pre_moves)
    post = statistics.fmean(post_moves)
    ratio = pre / post if abs(post) > 1e-12 else float("inf")

    # Premiere condition, decouverte en UTILISANT ce controle : un evenement
    # dont le mouvement posterieur est nul ou NEGATIF n'annonce rien. La
    # version initiale ne testait que la domination de l'anteriorite, si bien
    # qu'un evenement sans aucun pouvoir predictif — voire contraire a celui
    # qu'il pretend — ressortait « OK » parce que son ratio etait negatif.
    if post <= min_post_bps:
        v = Verdict(
            f"causalite:{name}", ALERTE,
            f"le mouvement posterieur vaut {post:.2f} bps, au plus "
            f"{min_post_bps:.2f} : l'evenement n'annonce rien dans le sens "
            f"qu'il pretend. Un ratio favorable ne suffit pas, il faut "
            f"quelque chose a capturer.", ratio)
    elif pre > 0 and ratio >= symptom_ratio:
        v = Verdict(
            f"causalite:{name}", ALERTE,
            f"le prix bouge {ratio:.1f}x plus AVANT l'evenement qu'apres "
            f"({pre:.2f} contre {post:.2f} bps). L'evenement est un symptome "
            f"deja price, pas une cause exploitable. Conditionner dessus "
            f"revient a conditionner sur un mouvement passe.", ratio)
    else:
        v = Verdict(f"causalite:{name}", OK,
                    f"avant {pre:.2f} bps, apres {post:.2f} bps — "
                    f"l'evenement precede un mouvement reel", ratio)
    return CausalReport(len(pre_moves), pre, post, ratio, v)


# ── 4. Les invariants declares doivent tenir ─────────────────────────────────
#
# Classe d'erreur couverte : invariant suppose, jamais verifie.
#   - « dollar-neutre » portait un beta de +0,111 et 23,8 % de variance marche
#   - la boucle de mise a l'echelle detruisait la neutralite juste imposee

def check_invariant(name: str, measured: float, tolerance: float,
                    claim: str) -> Verdict:
    """Ce que le livre PRETEND etre, l'est-il mesurablement ?"""
    if abs(measured) <= tolerance:
        return Verdict(f"invariant:{name}", OK,
                       f"{claim} verifie : {measured:+.5f} dans "
                       f"+/-{tolerance}", measured)
    return Verdict(
        f"invariant:{name}", ALERTE,
        f"{claim} FAUX : mesure {measured:+.5f}, tolerance +/-{tolerance}. "
        f"Un livre qui se croit neutre attribue a son signal une performance "
        f"qui vient d'ailleurs.", measured)


# ── 5. Une famille « nouvelle » l'est-elle vraiment ? ────────────────────────
#
# Classe d'erreur couverte : retester une famille fermee sous un autre nom.
# La Phase C se croyait nouvelle — « flux force », pas « prevision de prix ».
# Elle conditionnait en fait sur un mouvement de prix passe, c'est-a-dire
# exactement la premiere famille du projet, fermee sur 617 820 evenements.
#
# Le test est EMPIRIQUE, pas declaratif : si le declencheur nouveau est
# predit par le declencheur ancien, c'est le meme conditionnement.

def check_novelty(name: str, new_trigger: Sequence[bool],
                  closed_trigger: Sequence[bool],
                  max_agreement: float = 0.60) -> Verdict:
    """Le nouveau declencheur est-il predit par une famille deja fermee ?

    On mesure la fraction des nouveaux declenchements qui coincident avec
    ceux d'une famille fermee. Une coincidence elevee signifie qu'on
    conditionne sur la meme chose sous un autre nom.
    """
    if len(new_trigger) != len(closed_trigger):
        return Verdict(f"nouveaute:{name}", IMPOSSIBLE,
                       "series de longueurs differentes")
    fired = [i for i, x in enumerate(new_trigger) if x]
    if len(fired) < 10:
        return Verdict(f"nouveaute:{name}", IMPOSSIBLE,
                       f"{len(fired)} declenchements : trop peu pour conclure")
    agree = sum(1 for i in fired if closed_trigger[i]) / len(fired)
    if agree >= max_agreement:
        return Verdict(
            f"nouveaute:{name}", ALERTE,
            f"{agree:.0%} des declenchements coincident avec une famille "
            f"DEJA FERMEE. Ce n'est pas une famille nouvelle, c'est la meme "
            f"sous un autre nom.", agree)
    return Verdict(f"nouveaute:{name}", OK,
                   f"recouvrement {agree:.0%} avec la famille fermee", agree)


# ── 6. Horodatage : l'evenement est-il date de sa survenue ? ─────────────────
#
# Classe d'erreur couverte : dater un evenement de sa DECOUVERTE.
# Les liquidations OKX ont un delai de publication median de 2 434 s ; 77 %
# sont vues plus de 30 s apres les faits. Une analyse de causalite fondee sur
# l'instant du sondage etait donc integralement contaminee.

def check_publication_lag(name: str, lags_s: Sequence[float],
                          window_s: float) -> Verdict:
    """Le delai de publication est-il negligeable devant la fenetre d'analyse ?"""
    vals = [x for x in lags_s if x is not None]
    if len(vals) < 10:
        return Verdict(f"horodatage:{name}", IMPOSSIBLE, "moins de 10 delais")
    med = statistics.median(vals)
    over = sum(1 for x in vals if x > window_s) / len(vals)
    if over > 0.10:
        return Verdict(
            f"horodatage:{name}", ALERTE,
            f"delai median {med:.0f} s, et {over:.0%} depassent la fenetre "
            f"de {window_s:.0f} s. Dater l'evenement de sa DECOUVERTE au lieu "
            f"de sa survenue contamine toute analyse temporelle.", med)
    return Verdict(f"horodatage:{name}", OK,
                   f"delai median {med:.1f} s, {over:.0%} hors fenetre", med)


# ── Agregation ───────────────────────────────────────────────────────────────

def gate(verdicts: Sequence[Verdict]) -> Tuple[bool, List[Verdict]]:
    """Aucune conclusion economique tant qu'une alerte subsiste.

    Les verdicts IMPOSSIBLE ne bloquent pas : ils signalent qu'une
    verification n'a pas pu etre faite, ce qui est une information et non un
    echec. Mais ils sont rapportes, pour qu'une absence de verification ne
    passe jamais pour une verification reussie.
    """
    alerts = [v for v in verdicts if v.status == ALERTE]
    return (not alerts), list(verdicts)


def render(verdicts: Sequence[Verdict]) -> str:
    sym = {OK: "OK  ", ALERTE: "ALER", IMPOSSIBLE: "----"}
    lines = []
    for v in verdicts:
        lines.append(f"[{sym[v.status]}] {v.check:<28} {v.detail}")
    passed, _ = gate(verdicts)
    lines.append("")
    lines.append("VERDICT : " + ("aucune alerte — la mesure peut etre lue"
                                 if passed else
                                 "ALERTE — aucune conclusion economique"))
    return "\n".join(lines)
