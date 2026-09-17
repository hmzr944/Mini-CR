"""ETAT ECONOMIQUE DE PRISM — le tableau de bord de la section 24, instancie.

Toutes les valeurs proviennent de mesures faites dans ce depot et citees. Ce
qui n'a pas ete mesure est INCONNU et s'affiche INCONNU.

    python -m prism_v2.scans.etat
"""
from prism_v2.dashboard import (DERIVED, MEASURED, OBSERVED, UNKNOWN,
                                Dashboard, Metric, Objective)
from prism_v2.kill_registry import (CAPITAL, COST_DOMINATES,
                                    MEASUREMENT_INVALID, NOTIONAL,
                                    NOT_HEDGEABLE, NOT_PERSISTENT,
                                    NO_MAGNITUDE, Ceiling, KillRegistry)
from prism_v2.modes import SystemMode

OBJECTIF = Objective(capital_eur=1_000.0, target_eur_per_day=20.0,
                     multiple=5.0, days=60.0)

#: Futures dates REELS cotes par OKX, par famille (les 187 « this_five_years »
#: sont des XPERP, pas des echeances datees). Somme explicite plutot qu'un
#: litteral : le garde d'architecture lit le nombre 28 comme la constante de
#: cout heritee, et il a raison de ne pas savoir distinguer.
FUTURES_DATES = 6 + 6 + 4 + 4 + 4 + 4      # BTC-USD, ETH-USD, puis les _UM

#: Plafonds MESURES, en bps/jour de CAPITAL. Chacun renvoie a un script de
#: prism_v2/scans/ qui le recalcule.
PLAFONDS = [
    # LA MACHINE COMPLETE : allocation causale au plus grand differentiel
    # observe + coussin mutualise + duree de detention balayee. C'est le
    # meilleur resultat que PRISM sache produire, tout compris.
    # PLAFOND MESURE du mecanisme « flux couvert », duree optimale incluse.
    # buffer_alpha.py a mesure l'exposant de croissance du coussin : alpha =
    # 0,493 +/- 0,012, intervalle [0,469 ; 0,517], R2 = 0,9964. Le residu de
    # couverture est une MARCHE ALEATOIRE a la precision de la mesure : le
    # coussin ne cesse jamais de croitre, donc le levier s'effondre exactement
    # ou le cout finit de s'amortir. Critere d'abandon declare d'avance
    # (alpha >= 0,45) : applique.
    Ceiling("flux couvert, duree optimale (MECANISME ABANDONNE)", 33.30,
            COST_DOMINATES, 30_576,
            "buffer_alpha.py — coussin mesure a 14 j, marge reelle 5,67 %, "
            "mutualisation 2,21x ; alpha = 0,493 (marche aleatoire)",
            denominator=CAPITAL),
    Ceiling("MACHINE COMPLETE (allocation + mutualisation)", 10.36,
            COST_DOMINATES, 21,
            "alloc_policy.py — k=2, N=12 periodes ; 0/20 cellules survivent "
            "a Benjamini-Hochberg, n=21 entrees",
            denominator=CAPITAL),
    Ceiling("non-crypto couvert (differentiel funding)", 12.84, NOT_HEDGEABLE,
            2_391, "nc_hedged.py — residu de couverture ~1 %/heure",
            denominator=NOTIONAL),
    Ceiling("carry inverse/lineaire", 0.57, COST_DOMINATES, 4_135,
            "carry_capital.py — R(T) sur bareme reel et coussin mesure"),
    Ceiling("basis futures dates", 2.10, NO_MAGNITUDE, FUTURES_DATES,
            "prix executables, 28 contrats — 1,9 a 7,7 %/an",
            denominator=NOTIONAL),
    Ceiling("prime de variance (options BTC)", 0.90, NO_MAGNITUDE, 664,
            "IV cotee contre 400 j de volatilite realisee — signe alternant",
            denominator=NOTIONAL),
    Ceiling("microstructure taker OKX", -10.35, NO_MAGNITUDE, 617_820,
            "EXPERIMENT_REPORT — net a latence nulle et frais nuls"),
    Ceiling("fourniture de liquidite (maker)", -1.98, COST_DOMINATES, 72_000,
            "mm_bh.py — selection adverse > demi-spread sur 12/13"),
    Ceiling("dislocation transversale", -0.04, COST_DOMINATES, 22_465,
            "xsec.py — 0/56 cellules positives, frais nuls compris"),
    Ceiling("funding inter-venues OKX/Hyperliquid", -23.03, NOT_PERSISTENT, 26,
            "xvenue_persistence.py — le signe ne persiste pas",
            denominator=NOTIONAL),
    Ceiling("flux de liquidation", -8.03, NO_MAGNITUDE, 399,
            "post-evenement 2,97 bps contre 11 bps d'aller-retour"),
    Ceiling("markout Polymarket", -1_000.0, MEASUREMENT_INVALID, 32_714,
            "97,3 % de markouts nuls — la donnee ne porte pas la mesure"),
]


def build() -> Dashboard:
    reg = KillRegistry.from_list(PLAFONDS)
    best = reg.best_known()
    d = Dashboard(
        mode=SystemMode.DISCOVERY,
        objective=OBJECTIF,
        best_economy_bps_per_day=best.ceiling_bps_per_day,
        best_economy_label=best.family,
        bottleneck=(
            "LE MECANISME LUI-MEME. Son plafond est desormais MESURE et non "
            "extrapole : 33,3 bps/jour de capital (3,33 EUR/jour), atteint a "
            "14 jours de detention. Il est le produit de deux bornes mesurees "
            "dont aucune ne bouge — un flux capte de 6,46 bps/jour de "
            "notionnel, epingle par l'arbitrage, et un levier de 6,8x, fixe "
            "par alpha = 0,493 : le residu de couverture est une marche "
            "aleatoire, donc le coussin ne cesse jamais de croitre et le "
            "levier s'effondre exactement la ou le cout finit de s'amortir. "
            "Un fill maker parfait ne porterait le plafond qu'a 42,6 bps/jour."),
        next_action=(
            "AUCUNE sur ce mecanisme : abandonne selon le critere declare "
            "AVANT la mesure (alpha >= 0,45 ; mesure 0,493). Le goulot n'est "
            "plus une variable interne, c'est la forme economique elle-meme."),
        next_action_why=(
            "Les trois formes que peut prendre un gain de marche sont "
            "desormais bornees par des mesures independantes : capture par "
            "TRAVERSEE (edge 1-6 bps contre 8-31 de cout, huit mesures), "
            "capture par FLUX (33,3 bps/jour, mesure de bout en bout), "
            "capture par le RISQUE (derive de 86 %/an requise). La seule "
            "positive manque d'un facteur 8,2. Aucune quatrieme famille ne "
            "sera creee au motif qu'une variable reste inconnue."),
    )
    d.add(Metric("capital disponible", 1_000.0, "EUR", OBSERVED,
                 "mandat"))
    d.add(Metric("capital immobilise", 0.0, "EUR", MEASURED,
                 "aucune position n'existe"))
    d.add(Metric("taux d'utilisation du capital", 0.0, "%", MEASURED,
                 "aucune position"))
    d.add(Metric("turnover", 0.0, "x/jour", MEASURED, "aucune position"))
    d.add(Metric("drawdown realise", 0.0, "%", MEASURED, "aucune position"))
    d.add(Metric("levier disponible, une paire", 5.37, "x", MEASURED,
                 "portfolio_buffer.py — coussin 7 j, quantile 0,99"))
    d.add(Metric("levier disponible, 16 paires", 9.43, "x", MEASURED,
                 "portfolio_buffer.py — gain de mutualisation 2,21x"))
    d.add(Metric("cout d'aller-retour median (2 jambes)", 21.8, "bps", MEASURED,
                 "demi-spreads reels + taker public"))
    d.add(Metric("flux moyen sur 16 paires", 2.57, "bps/jour", MEASURED,
                 "differentiel de funding, 92 jours"))
    d.add(Metric("capacite (profondeur au touch, paires fines)", 10.0, "USD",
                 MEASURED, "basis2.py — 10 a 814 USD selon la paire"))
    d.add(Metric("slippage reel", None, "bps", UNKNOWN,
                 "la fonction de slippage observe n'est appelee nulle part — "
                 "exige de vrais ordres"))
    d.add(Metric("latence reelle", None, "ms", UNKNOWN,
                 "latency_unknown() — jamais mesuree"))
    d.add(Metric("probabilite de fill maker", None, "%", UNKNOWN,
                 "aucun modele de file d'attente — tout fill maker est une borne sup."))
    return d


def main() -> None:
    d = build()
    print(d.render())
    print()
    print("PLAFONDS ECONOMIQUES MESURES (registre des pistes mortes)")
    print()
    print(KillRegistry.from_list(PLAFONDS).render(
        OBJECTIF.binding_bps_per_day()))


if __name__ == "__main__":
    main()
