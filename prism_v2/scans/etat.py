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
            "MAGNITUDE BRUTE, et elle seule. La machine a capital a ete "
            "mesuree bout en bout : l'allocation causale capte 6,5x plus de "
            "flux que l'equiponderation (6,46 contre 1,00 bps/jour), la "
            "mutualisation du coussin multiplie le levier par 2,21, et les "
            "deux ensemble donnent 10,36 bps/jour de capital. Ni l'allocation, "
            "ni le levier, ni le turnover, ni la representation ne sont le "
            "goulot : le flux brut vaut 6,46 bps/jour contre 21,8 bps "
            "d'aller-retour."),
        next_action=(
            "Aucune action de recherche supplementaire sur cette famille. Le "
            "cout d'aller-retour est la seule variable qui puisse encore "
            "bouger — mesurer si un fill MAKER est atteignable sur ces "
            "instruments, ce qui diviserait le cout par ~2,5."),
        next_action_why=(
            "A 21,8 bps d'aller-retour, il faut tenir 12 periodes (4 jours) "
            "pour que le net devienne positif, et le levier a cette duree est "
            "tombe a 6,4x. En maker (2 bps par jambe au lieu de 5), le cout "
            "passerait a ~9,4 bps et le net deviendrait positif des 4 periodes, "
            "ou le levier vaut encore 9x. C'est la seule variable mesurable "
            "restante dont l'effet est d'un facteur, pas d'un pourcent. "
            "PREALABLE : la probabilite de fill maker est INCONNUE — aucun "
            "modele de file d'attente n'existe. C'est ce qu'il faut mesurer, "
            "pas supposer."),
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
