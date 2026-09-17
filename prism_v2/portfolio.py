#!/usr/bin/env python3
"""PORTEFEUILLE — position cible continue, pas des trades.

CHANGEMENT DE PARADIGME. Les modules precedents detectaient un motif, entraient,
sortaient, et payaient un aller-retour complet a chaque cycle. C'est ce churn
qui les a tues : 39 bps de cout pour capter 20 bps de signal.

Ici il n'y a ni motif, ni declencheur, ni trade. Il y a une fonction

        etat du marche  ->  exposition cible  pi*(t)

evaluee en continu, et une politique qui deplace la position VERS cette cible
en ne payant que le mouvement marginal. Rester en place ne coute rien. Le
marche determine la position ; aucune strategie ne determine un trade.

LES TROIS PIECES, ET POURQUOI ELLES SONT CELLES-LA.

1. ESPERANCE DE RENDEMENT. La seule source de rendement attendu MESUREE dans
   ce depot est le funding. Toute prevision de prix a ete testee et rejetee
   (617 820 evenements, aucun signal). On ne modelise donc pas le prix : on
   suppose E[r] = 0, ce qui est le resultat mesure, pas une commodite.
   L'esperance d'un long est donc -E[f] : porter un actif dont le funding est
   negatif rapporte, porter un funding positif coute.

2. RISQUE. Volatilite glissante causale. Elle entre au denominateur : a
   esperance egale, on porte moins de ce qui bouge plus.

3. COUT. Il ne se soustrait pas du signal, il AMORTIT le mouvement. D'ou la
   bande de non-negociation : tant que l'ecart a la cible ne paie pas son
   propre franchissement, on ne bouge pas. C'est le resultat classique du
   probleme de portefeuille sous couts proportionnels.

NEUTRALITE PAR CONSTRUCTION. Les esperances sont centrees en coupe
transversale avant tout dimensionnement. Ce qui reste est le carry RELATIF ;
le facteur commun — quand tout le marche paie pour etre long — est retire.
Sans ce centrage, le portefeuille serait un pari directionnel deguise en carry.

ANTI-LOOK-AHEAD STRUCTUREL. La position portee de t a t+1 n'est construite
qu'avec de l'information horodatee <= t. Le rendement et le funding de t+1
sont encaisses, jamais consultes.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

HOURS_PER_YEAR = 24 * 365


# ── 1. Esperance de rendement ────────────────────────────────────────────────

@dataclass(frozen=True)
class SignalConfig:
    """`lookback_h` lisse le funding. Une seule impression est bruitee : la
    mesure de persistance montrait un retournement violent sur la premiere
    heure apres une lecture extreme. Moyenner attenue ce bruit sans rien
    anticiper."""
    lookback_h: int = 24
    #: Centrage transversal. Le desactiver transforme le portefeuille en pari
    #: directionnel sur le marche entier.
    cross_sectional_demean: bool = True
    #: Plafond d'esperance, en rendement horaire. Les lectures extremes de
    #: funding sont majoritairement du bruit (mesure : le realise sature vers
    #: 11-14 %/an quel que soit le signal). Sans plafond, le portefeuille se
    #: concentrerait sur ce bruit.
    max_abs_mu_annual: float = 1.00
    #: SIGNE DU SIGNAL. +1 = carry pur : on porte ce qui paie du funding.
    #: -1 = rendement TOTAL : le carry est utilise comme indicateur de
    #: positionnement, et l'on prend le sens inverse.
    #:
    #: Ce parametre existe parce que la mesure a montre que le carry ne
    #: s'encaisse pas : le signal predit le prix avec un beta de -5,5
    #: (t=-2,24, n=43 680). Un actif offrant +10,7 %/an de carry perd
    #: -59,1 %/an en prix. Le rendement total d'un long vaut donc
    #: carry x (1 + beta) < 0 : porter le carry perd, et seul le SIGNE compte
    #: puisque les poids sont renormalises ensuite.
    #:
    #: ATTENTION. Ce signe a ete choisi APRES avoir vu le resultat sur
    #: DISCOVERY. C'est une selection, et elle n'a de valeur que confirmee
    #: hors echantillon. Aucun resultat en -1 ne doit etre lu sans son
    #: holdout.
    signal_sign: float = 1.0

    def __post_init__(self) -> None:
        if self.signal_sign not in (1.0, -1.0):
            raise ValueError("signal_sign doit valoir +1 ou -1")
        if self.lookback_h < 1:
            raise ValueError("lookback_h < 1")


def expected_returns(funding_window: Dict[str, Sequence[float]],
                     cfg: SignalConfig) -> Dict[str, float]:
    """Esperance horaire d'un LONG, par actif, a partir du funding passe.

    `funding_window[asset]` : taux horaires deja PAYES, le plus recent en fin.
    Un actif sans historique suffisant est absent du resultat — jamais mis a
    zero, ce qui se lirait comme « esperance neutre mesuree ».
    """
    mu: Dict[str, float] = {}
    cap = cfg.max_abs_mu_annual / HOURS_PER_YEAR
    for asset, series in funding_window.items():
        window = list(series)[-cfg.lookback_h:]
        if len(window) < cfg.lookback_h:
            continue
        m = -statistics.fmean(window)          # long : on paie le funding
        m *= cfg.signal_sign
        mu[asset] = max(-cap, min(cap, m))
    if cfg.cross_sectional_demean and len(mu) > 1:
        avg = statistics.fmean(mu.values())
        mu = {k: v - avg for k, v in mu.items()}
    return mu


# ── 2. Risque ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RiskConfig:
    lookback_h: int = 168
    #: Plancher de volatilite. Sans lui, un actif au prix fige (marche mort,
    #: donnee manquante) afficherait une vol nulle et attirerait une position
    #: infinie. C'est la faille qui fait exploser ce type de modele.
    min_vol_hourly: float = 0.002


def volatilities(return_window: Dict[str, Sequence[float]],
                 cfg: RiskConfig) -> Dict[str, float]:
    """Volatilite horaire glissante, causale."""
    out: Dict[str, float] = {}
    for asset, rets in return_window.items():
        window = list(rets)[-cfg.lookback_h:]
        if len(window) < max(10, cfg.lookback_h // 4):
            continue
        sd = statistics.stdev(window) if len(window) > 1 else 0.0
        out[asset] = max(cfg.min_vol_hourly, sd)
    return out


# ── 3. Position cible ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TargetConfig:
    """`gamma` est une aversion au risque, pas un parametre a optimiser :
    elle fixe l'echelle du livre, et `gross_leverage` la renormalise de toute
    facon. Les bornes, elles, mordent."""
    gamma: float = 1.0
    #: Exposition brute totale, en multiple du capital. C'est LE parametre de
    #: risque du livre.
    gross_leverage: float = 1.0
    #: Poids maximal d'un actif, en fraction du capital. Empeche le livre de
    #: se reduire a un pari unique.
    max_weight: float = 0.10
    #: Neutralite dollar : somme des poids ramenee a zero.
    dollar_neutral: bool = True
    #: Neutralite BETA. Un livre dollar-neutre n'est PAS neutre au marche
    #: des lors que les actifs n'ont pas le meme beta — et en crypto ils ne
    #: l'ont jamais. Mesure sur 420 jours : le livre carry portait un beta
    #: de +0,111 et 23,8 % de la variance de son PnL venait du marche.
    #:
    #: Cette variance-la n'est pas de l'alpha : c'est du bruit directionnel
    #: qui masque le signal et fait changer le resultat de signe selon que
    #: la fenetre est haussiere ou baissiere. La retirer ne peut pas creer
    #: de rendement ; elle rend seulement visible celui qui existe.
    beta_neutral: bool = True


def neutralise_beta(weights: Dict[str, float], beta: Dict[str, float]
                    ) -> Dict[str, float]:
    """Retire l'exposition au marche en projetant les poids orthogonalement
    au vecteur de betas.

        w' = w - (w.b / b.b) * b

    Par construction w'.b = 0 : le livre n'a plus d'exposition au facteur
    commun. Les actifs sans beta connu sont laisses INTACTS plutot
    qu'exclus — les exclure changerait le livre pour une raison technique.

    Si aucun beta n'est connu, ou s'ils sont tous nuls, la fonction rend les
    poids inchanges : on ne neutralise pas contre un facteur qu'on n'a pas
    mesure.
    """
    common = [a for a in weights if a in beta]
    if not common:
        return dict(weights)
    beta_sq_sum = sum(beta[a] ** 2 for a in common)
    if beta_sq_sum <= 0:
        return dict(weights)
    w_dot_beta = sum(weights[a] * beta[a] for a in common)
    k = w_dot_beta / beta_sq_sum
    out = dict(weights)
    for a in common:
        out[a] = weights[a] - k * beta[a]
    return out


def market_betas(return_window: Dict[str, Sequence[float]]
                 ) -> Dict[str, float]:
    """Beta de chaque actif au marche, le marche etant la moyenne equiponderee
    des rendements de l'univers a chaque instant.

    Causal : n'utilise que les rendements deja realises. Un actif dont
    l'historique est trop court est absent — jamais dote d'un beta de 1 par
    defaut, ce qui lui pretendrait une exposition mesuree.
    """
    if len(return_window) < 2:
        return {}
    n = min(len(v) for v in return_window.values())
    if n < 10:
        return {}
    aligned = {a: list(v)[-n:] for a, v in return_window.items()}
    mkt = [statistics.fmean(aligned[a][i] for a in aligned) for i in range(n)]
    mm = statistics.fmean(mkt)
    var = sum((x - mm) ** 2 for x in mkt)
    if var <= 0:
        return {}
    out: Dict[str, float] = {}
    for a, rs in aligned.items():
        ra = statistics.fmean(rs)
        cov = sum((x - mm) * (y - ra) for x, y in zip(mkt, rs))
        out[a] = cov / var
    return out


def target_weights(mu: Dict[str, float], vol: Dict[str, float],
                   cfg: TargetConfig,
                   beta: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """pi* = mu / (gamma * sigma^2), borne, neutralise, puis renormalise.

    L'ordre compte. Neutraliser APRES avoir borne reintroduirait un biais
    directionnel ; borner apres avoir renormalise casserait l'echelle. On
    borne, on neutralise, on renormalise.
    """
    common = [a for a in mu if a in vol]
    if not common:
        return {}
    raw = {a: mu[a] / (cfg.gamma * vol[a] ** 2) for a in common}

    scale = max(abs(v) for v in raw.values())
    if scale <= 0:
        return {a: 0.0 for a in common}
    w = {a: v / scale * cfg.max_weight for a, v in raw.items()}

    # NEUTRALISER PUIS METTRE A L'ECHELLE NE MARCHE PAS : la mise a
    # l'echelle suivie de l'ecretage par actif casse les deux neutralites
    # qu'on vient d'imposer. C'est le defaut qui laissait le livre porter
    # une exposition nette de +0,054 et un beta de +0,111 alors qu'il se
    # croyait neutre.
    #
    # On itere donc : neutraliser, mettre a l'echelle, ecreter, recommencer.
    # L'ecretage est une borne de RISQUE et gagne toujours ; la neutralite
    # est retablie a chaque tour et converge des que l'ecretage ne mord plus.
    for _ in range(12):
        if cfg.beta_neutral and beta:
            w = neutralise_beta(w, beta)
        if cfg.dollar_neutral and len(w) > 1:
            avg = statistics.fmean(w.values())
            w = {a: v - avg for a, v in w.items()}
        gross = sum(abs(v) for v in w.values())
        if gross <= 0:
            break
        k = cfg.gross_leverage / gross
        clipped = {a: max(-cfg.max_weight, min(cfg.max_weight, v * k))
                   for a, v in w.items()}
        if all(abs(clipped[a] - w[a] * k) < 1e-15 for a in w):
            w = clipped
            break                      # l'ecretage ne mord plus : converge
        w = clipped
    return w


def exposures(weights: Dict[str, float],
              beta: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Expositions residuelles du livre. Sert a VERIFIER, pas a decider.

    Un livre qui se croit neutre et ne l'est pas est pire qu'un livre
    directionnel assume : il attribue a son signal une performance qui vient
    du marche.
    """
    out = {
        "brut": sum(abs(v) for v in weights.values()),
        "net_dollar": sum(weights.values()),
    }
    if beta:
        common = [a for a in weights if a in beta]
        out["net_beta"] = sum(weights[a] * beta[a] for a in common)
    return out


def max_reachable_gross(n_assets: int, cfg: TargetConfig) -> float:
    """Brut maximal atteignable compte tenu du plafond par actif.

    Demander davantage n'est pas une erreur : c'est simplement impossible, et
    `target_weights` rend alors le maximum atteignable.
    """
    return n_assets * cfg.max_weight


# ── 4. Deplacement vers la cible, sous cout ──────────────────────────────────

@dataclass(frozen=True)
class TradingConfig:
    """La bande est le coeur economique du module.

    `band_multiple` fixe sa largeur en multiples du cout aller simple. Un
    ecart qui ne franchit pas la bande n'est pas traite : le corriger
    couterait plus que l'erreur qu'il represente. C'est ce qui distingue une
    position continue d'une suite d'allers-retours.
    """
    cost_bps: float = 4.5          # taker Hyperliquid, bareme public
    band_multiple: float = 2.0
    #: Fraction de l'ecart franchi effectivement comblee. 1.0 = on va
    #: exactement au bord de la bande ; en dessous, on sur-amortit.
    partial_adjust: float = 1.0

    def band_width(self) -> float:
        """Largeur de bande, en fraction de capital."""
        return self.band_multiple * self.cost_bps / 10_000.0


def move_toward_target(current: Dict[str, float], target: Dict[str, float],
                       cfg: TradingConfig) -> Dict[str, float]:
    """Nouvelle position. Hors de la bande on comble jusqu'a son bord.

    Aucun actif n'est force a zero s'il disparait de la cible : il conserve
    sa position et sera traite au cycle suivant. Liquider sur donnee
    manquante serait une decision de marche prise pour une raison technique.
    """
    band = cfg.band_width()
    out: Dict[str, float] = dict(current)
    for asset in set(current) | set(target):
        cur = current.get(asset, 0.0)
        tgt = target.get(asset)
        if tgt is None:
            continue
        gap = tgt - cur
        if abs(gap) <= band:
            out[asset] = cur                      # immobile : cout nul
            continue
        step = (abs(gap) - band) * cfg.partial_adjust
        out[asset] = cur + math.copysign(step, gap)
    return out


# ── 5. Livre : accumulation du PnL ───────────────────────────────────────────

@dataclass
class BookState:
    """Le livre, marque en continu. Toutes les grandeurs sont en fraction du
    capital, ce qui rend le resultat independant de la taille."""

    weights: Dict[str, float] = field(default_factory=dict)
    pnl_price: float = 0.0
    pnl_funding: float = 0.0
    cost_paid: float = 0.0
    turnover: float = 0.0
    n_rebalances: int = 0
    equity_curve: List[float] = field(default_factory=lambda: [0.0])

    @property
    def pnl_net(self) -> float:
        return self.pnl_price + self.pnl_funding - self.cost_paid

    def gross_exposure(self) -> float:
        return sum(abs(w) for w in self.weights.values())

    def net_exposure(self) -> float:
        return sum(self.weights.values())


def apply_step(book: BookState, new_weights: Dict[str, float],
               returns: Dict[str, float], funding: Dict[str, float],
               cfg: TradingConfig) -> None:
    """Un pas horaire : on paie le mouvement, puis on encaisse le pas suivant.

    ORDRE IMPERATIF. Le cout est paye sur le passage de l'ancienne a la
    nouvelle position. Le rendement et le funding sont ensuite appliques a la
    NOUVELLE position, pour la periode a venir. Appliquer le rendement a
    l'ancienne position reviendrait a trader a un prix qu'on n'a pas paye.
    """
    traded = sum(abs(new_weights.get(a, 0.0) - book.weights.get(a, 0.0))
                 for a in set(new_weights) | set(book.weights))
    cost = traded * cfg.cost_bps / 10_000.0
    book.cost_paid += cost
    book.turnover += traded
    if traded > 0:
        book.n_rebalances += 1
    book.weights = dict(new_weights)

    for asset, w in book.weights.items():
        r = returns.get(asset)
        if r is not None:
            book.pnl_price += w * r
        f = funding.get(asset)
        if f is not None:
            book.pnl_funding -= w * f      # long paie le funding positif
    book.equity_curve.append(book.pnl_net)


# ── 6. Mesures ───────────────────────────────────────────────────────────────

def summarise_book(book: BookState, hours: int, capital_usd: float = 1.0) -> dict:
    """Le critere du projet : bps par jour de capital immobilise."""
    if hours <= 0:
        raise ValueError("hours <= 0")
    days = hours / 24.0
    curve = book.equity_curve
    steps = [b - a for a, b in zip(curve, curve[1:])]
    sd = statistics.stdev(steps) if len(steps) > 1 else 0.0
    mean = statistics.fmean(steps) if steps else 0.0
    sharpe = (mean / sd * math.sqrt(HOURS_PER_YEAR)) if sd > 0 else float("nan")
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    return {
        "heures": hours,
        "jours": round(days, 1),
        "pnl_net_bps": round(book.pnl_net * 10_000.0, 2),
        "pnl_prix_bps": round(book.pnl_price * 10_000.0, 2),
        "pnl_funding_bps": round(book.pnl_funding * 10_000.0, 2),
        "couts_bps": round(book.cost_paid * 10_000.0, 2),
        "turnover_total": round(book.turnover, 2),
        "bps_par_jour": round(book.pnl_net * 10_000.0 / days, 3),
        "rendement_annualise_pct": round(book.pnl_net / days * 365 * 100, 2),
        "sharpe_annualise": round(sharpe, 2) if sharpe == sharpe else None,
        "drawdown_max_bps": round(mdd * 10_000.0, 2),
        "n_rebalances": book.n_rebalances,
        "capital_usd": capital_usd,
        "pnl_net_usd": round(book.pnl_net * capital_usd, 2),
    }
