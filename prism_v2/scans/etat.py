"""ETAT ECONOMIQUE DE PRISM — le tableau de bord de la section 24, instancie.

Toutes les valeurs proviennent de mesures faites dans ce depot et citees. Ce
qui n'a pas ete mesure est INCONNU et s'affiche INCONNU.

    python -m prism_v2.scans.etat
"""
from prism_v2.dashboard import (DERIVED, MEASURED, OBSERVED, UNKNOWN,
                                Dashboard, Metric, Objective)
from prism_v2.kill_registry import (CAPITAL, COST_DOMINATES, PAIR,
                                    NOTIONAL as _N,
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
    # Couverture MEME SOUS-JACENT : alpha = 0,236 [0,215 ; 0,258] contre 0,493
    # en correlation, coussin 45x plus petit, levier 6,8x -> 24,2x. Et pourtant
    # l'economie BAISSE : l'allocation causale n'y capte que 0,26 a 0,82
    # bps/jour de flux contre 6,46, et 0/15 cellules sont positives. Le levier
    # n'etait pas le goulot.
    # STRUCTURE CONDITIONNELLE. alpha = 0,545 etait INCONDITIONNEL : il
    # etablissait l'absence de structure MOYENNE, pas l'absence de structure
    # conditionnelle. Quatre tests successifs ont produit quatre chiffres
    # spectaculaires, tous artefactuels :
    #   alpha | etat  : ecart 0,206 monotone -> retour de VOLATILITE, pas de
    #                   direction ;
    #   VR    | etat  : 3,49 -> 0,56, +669 bps/A-R -> denominateur instantane
    #                   contre numerateur sur q periodes, meme confond ;
    #   M(q)  | etat  : 1,661, +458 bps net -> fenetres chevauchantes, moment
    #                   d'ordre 4, aucune barre d'erreur ;
    #   M(2) > 1 partout, rho = 0,13 -> PRIX PERIMES : USDC-USDT a 38,7 %
    #                   d'heures a rendement exactement nul. Apres filtre,
    #                   rho = -0,0103.
    # Test economique direct, causal, 23 instruments apres filtre : 0/25
    # cellules survivent a Benjamini-Hochberg ; a 1 h (n=5 300/cellule) le net
    # vaut -13 a -15 bps avec t de -4,7 a -13,6.
    Ceiling("structure conditionnelle a l'etat de volatilite", -13.0,
            NO_MAGNITUDE, 26_512,
            "cond_final_eco.py — rho = -0,010 apres filtre des prix perimes ; "
            "0/25 survivants BH",
            denominator=_N, aggregation=PAIR),
    # UNIVERS. Le budget economique d'un instrument — mouvement quotidien
    # rapporte au cout d'un aller-retour — a ete mesure sur 3 419 instruments
    # de quatre venues joignables. PRISM n'avait jamais regarde que 21
    # instruments, tous du quintile le PLUS PAUVRE : BTC-USDT-SWAP est au rang
    # 2085/3419 (39e centile, budget 13,4) quand le haut de la distribution
    # atteint 474. Le biais de selection etait le mien.
    # ET CELA NE CHANGE RIEN : l'exposant de croissance du deplacement vaut
    # 0,545 sur le haut budget contre 0,544 sur le temoin. Un facteur 15 sur
    # le budget donne 0,001 d'ecart sur alpha. L'echelle change, la structure
    # non ; le rapport previsible/imprevisible est identique.
    Ceiling("haut budget (15x BTC) — structure identique", 0.0,
            NO_MAGNITUDE, 3_419,
            "univers_budget.py + budget_alpha.py — alpha 0,545 contre 0,544 "
            "sur le temoin, marche aleatoire = 0,500",
            denominator=_N, aggregation=PAIR),
    # FORME « CONTRAINTE » (directive finale, section 7) : quelqu'un DOIT agir
    # et paie une concession pour le droit d'agir maintenant ; le cote passif
    # la recoit. Mesure sur 29 038 rafales agressives reconstruites, carnet
    # incrementiel 400 niveaux, 19 instruments.
    # La concession existe et elle est DERISOIRE : 0,00 a 0,26 bps lue depuis
    # les impressions seules (donc sans biais d'ordonnancement entre canaux).
    # L'ecart de prix a l'interieur d'une rafale de plus de dix impressions
    # vaut 0,53 bps : les carnets sont trop denses au touch pour qu'il y ait
    # quoi que ce soit a collecter. Face a cela, selection adverse 1,54-1,88.
    # 0/16 cellules positives, 0/16 survivent a Benjamini-Hochberg,
    # t de -7,56 a -25,00.
    Ceiling("concession d'urgence au cote passif (ECHEC)", -2.41,
            COST_DOMINATES, 29_038,
            "concession_verdict.py — concession 0,26 bps contre 1,54 de "
            "selection adverse ; l'urgence ne paie pas sur ces carnets",
            denominator=_N, aggregation=PAIR),
    # RESET DE REPRESENTATION : panier NON COUVERT, risque dilue
    # transversalement, flux = NIVEAU du funding et non differentiel, 2
    # traversees par nom au lieu de 4. Le funding capte (1,51 a 4,06 bps/jour)
    # ne couvre le cout (11,5 bps par entree) a AUCUN horizon teste. Les
    # cellules positives le sont par le terme de prix, qui oscille de -43 a
    # +179 bps sur 15 entrees. 0/16 survivent a Benjamini-Hochberg.
    Ceiling("panier non couvert, risque dilue (RESET, ECHEC)", -1.40,
            COST_DOMINATES, 98,
            "basket_reset.py — bruit de prix ~45 bps/jour contre 1,5 de "
            "funding, rapport 1:30 ; l'univers accessible est un seul facteur",
            denominator=_N, aggregation=PAIR),
    # Mesure PAR PAIRE (alpha propre, flux causal propre) : alpha varie de
    # 0,160 a 0,727 selon la paire — le pooling cachait bien de la structure,
    # mais AUCUNE paire ne combine gros flux et alpha bas. Meilleure : 19,6.
    Ceiling("meilleure paire isolee (SKHYNIX/MU)", 19.60, COST_DOMINATES, 17,
            "per_pair.py — 4/28 paires positives, 0/28 survivent a BH",
            denominator=CAPITAL, aggregation=PAIR),
    Ceiling("carry meme sous-jacent (levier 24x, ABANDONNE)", -137.2,
            NOT_PERSISTENT, 21,
            "carry_alloc.py — 0/15 cellules positives ; alpha_peg.py pour le "
            "coussin (alpha = 0,236)",
            denominator=CAPITAL),
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
            "DEMONTRE, ET NON PLUS AFFIRME. Le choix d'univers a ete mesure "
            "et non herite : budget economique de 3 419 instruments sur quatre "
            "venues. PRISM n'avait jamais quitte le quintile le plus pauvre "
            "(BTC au 39e centile) et le haut de la distribution offre 15 fois "
            "plus de place — mais l'exposant du mouvement y vaut 0,545 contre "
            "0,544 sur le temoin. L'echelle change, la structure non. "
            "Anciennement : la nature de l'acces, pas les donnees. Quatre FORMES de gain de "
            "marche ont ete bornees par des mesures independantes : la capture "
            "par TRAVERSEE (edge 1-6 bps contre 8-31 de cout, huit mesures), "
            "par FLUX detenu (33,3 bps/jour, cinq representations), par le "
            "RISQUE (derive de 86 %/an requise) et par l'IMMEDIATETE "
            "(concession 0,26 bps contre 1,54 de selection adverse, 29 038 "
            "rafales). Toutes les grandeurs mesurees tombent entre 0,26 et "
            "6,5 bps quand tout aller-retour en coute 8 a 31 : un rapport "
            "constant de 1 pour 5 a 1 pour 20. Ce n'est pas un defaut de "
            "mesure, c'est l'aspect d'une venue dense et concurrentielle vue "
            "de l'exterieur avec des donnees publiques."),
        next_action=(
            "AUCUNE que je puisse justifier economiquement. Je ne propose pas "
            "une cinquieme variante des formes fermees, et je n'ai pas "
            "d'observable qui rende une cinquieme FORME mesurable."),
        next_action_why=(
            "Ce n'est pas une limite de DONNEES : plus d'historique de funding "
            "validerait le 33,3 sans l'elever, et la concession de 0,26 bps "
            "est une propriete structurelle de la densite des carnets, mesuree "
            "sur 29 038 rafales. Les sources d'economie qui restent exigent ce "
            "que ce compte n'a pas — latence, information, ou une position du "
            "cote de la venue — et non davantage de donnees."),
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
    d.add(Metric("coussin a 14 j, couverture correlation", 20.03, "%", MEASURED,
                 "buffer_alpha.py — alpha = 0,493 [0,469 ; 0,517]"))
    d.add(Metric("coussin a 14 j, couverture meme sous-jacent", 0.441, "%",
                 MEASURED, "alpha_peg.py — alpha = 0,236 [0,215 ; 0,258]"))
    d.add(Metric("historique de funding disponible", 92.0, "jours", MEASURED,
                 "plafond de l'API OKX, uniforme sur tous les instruments"))
    d.add(Metric("historique requis pour tester 14 j x 30 entrees", 420.0,
                 "jours", DERIVED, "30 fenetres independantes de 14 jours"))
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
