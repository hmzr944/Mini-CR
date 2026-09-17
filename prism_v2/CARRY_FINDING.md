# Carry inverse/linéaire — première chose qui survit hors échantillon

**Statut : `CANDIDATE_EDGE — BLOQUÉ PAR UNE INCONNUE DE CAPACITÉ`**

---

## Pourquoi cette expérience, et pas une autre

Les expériences taker sur 6,5 h ont établi trois choses :

1. le mouvement récupérable après un déplacement vaut zéro ;
2. le spread traversé deux fois (~3 bps, ~12 bps avec frais) tue tout ;
3. **ni la latence, ni la taille, ni les frais** ne sont la contrainte — le
   **péage par aller-retour** l'est.

Une seule façon de désarmer ce péage sans être maker : **l'amortir**. Un péage
payé une fois et étalé sur des semaines ne pèse plus 12 bps par trade mais
12 bps divisés par le nombre de périodes détenues. Cela déplace la question des
horizons de secondes vers ceux de jours — un régime jamais testé.

**Time-to-proof décisif** : l'historique de funding est disponible par REST.
94 jours × 30 instruments obtenus en **minutes**, contre 6 h d'horloge pour 6 h
de microstructure.

## La position

**Short perpétuel INVERSE + long perpétuel LINÉAIRE**, même sous-jacent,
notionnel USD égal à l'entrée.

La neutralité n'est pas supposée, elle est **vérifiée** par la mécanique de
contrats du projet :

| prix de sortie | variation | PnL inverse | PnL linéaire | **net** |
|---|---|---|---|---|
| 25 000 | −50 % | +5 000,00 $ | −5 000,00 $ | **0,0000** |
| 50 000 | 0 % | 0,00 $ | 0,00 $ | **0,0000** |
| 100 000 | +100 % | −10 000,00 $ | +10 000,00 $ | **0,0000** |

Le PnL USD de l'inverse est linéaire en **P_sortie/P_entrée**, exactement comme
celui du linéaire : ils s'annulent. **Aucun rééquilibrage n'est nécessaire.**
Seul le paiement de funding différentiel subsiste.

## Le fait mesuré

94 jours, 15 paires, 8 330 relevés de taux.

**`fundingRate == realizedRate` sur les 8 330 relevés.** Le taux était
contractuellement connu **avant** le règlement : ce n'est pas une prédiction,
c'est un flux de trésorerie publié. C'est la différence de nature avec tout ce
que le projet avait testé jusqu'ici.

Le funding inverse dépasse le funding linéaire sur **14 paires sur 15** en
découverte, **12 sur 15** en holdout.

## Le chiffre honnête

La règle est **figée sur la découverte** (« différentiel > 0 »), puis le
holdout est lu. Choisir la meilleure paire après avoir vu les quinze holdouts
serait de la sélection sur holdout : le maximum d'un échantillon est biaisé
vers le haut par construction.

| | |
|---|---|
| paires retenues par la règle de découverte | 14/15 |
| différentiel holdout moyen | **+0,1986 bps/période** |
| dont positifs en holdout | 11/14 |
| brut annualisé | 217 bps |
| coût aller-retour moyen | 26,3 bps (payé une fois) |
| **NET ANNUEL** | **191 bps (1,91 %)** |

Pour comparaison, **non retenu comme résultat** : la meilleure paire (ETC)
rend 478 bps/an. L'écart 478 → 191 **est** le biais de sélection.

## L'inconnue qui décide de tout

Les deux jambes **ne se compensent pas au niveau de la marge** : l'inverse est
margée en coin, la linéaire en USDT. Une hausse de prix fait gagner l'une et
perdre l'autre, mais dans des devises différentes — le gain de l'une ne peut
pas sauver l'autre d'une liquidation, **sauf si l'exchange les nette
explicitement**.

Ce netting (*portfolio margin*) est **INCONNU sans compte authentifié**. Deux
bornes, jamais une estimation :

| hypothèse | net annuel |
|---|---|
| plancher — entièrement collatéralisé, aucun netting | **191 bps** |
| plafond — netting parfait au levier le plus contraignant (20×) | **3 826 bps** |

**Un facteur 20 sépare une stratégie dominée d'une opportunité sérieuse, et il
tient à une seule question de capacité.**

## Ce qui n'est pas couvert par la neutralité

Le PnL du **trade** est couvert à la précision machine. Ne le sont pas :

- la marge postée en coin sur la jambe inverse ;
- le funding reçu en coin, exposé jusqu'à sa conversion ;
- le risque de depeg USDT.

## Mécanisme proposé — et ce qu'il implique

La position revient à être **long USDT contre USD**. Le différentiel serait le
prix de marché du **risque de depeg de l'USDT**.

Si c'est le cas, ces ~2 %/an ne sont pas un edge mais une **prime de risque de
queue** : encaissée en régime normal, rendue d'un coup lors d'un depeg.

Cette hypothèse est **plausible, non démontrée**. La démontrer exigerait un
épisode de depeg dans l'échantillon, et 94 jours calmes n'en contiennent pas.

## Capacité imposée par l'exchange (donnée publique)

| instrument | marge init. | levier max | taille max (palier 1) |
|---|---|---|---|
| ADA-USD-SWAP | 5,0 % | 20× | 4 500 |
| BCH-USD-SWAP | 5,0 % | 20× | 2 200 |
| BTC-USD-SWAP | 1,0 % | 100× | 2 000 |
| DOT-USD-SWAP | 5,0 % | 20× | 500 |

Au-delà, le palier suivant s'applique : marge plus chère, levier plus faible.
Plafond **imposé**, pas estimé.

## La comparaison qui tranche

Entièrement collatéralisée, la stratégie rend 191 bps/an en portant un risque
de depeg USDT **et** un risque d'exécution sur quatre traversées de carnet.
Prêter de l'USDT ou détenir des bons du Trésor rend davantage en portant moins
de risques.

**→ Entièrement collatéralisée, la stratégie est DOMINÉE.**
Elle ne devient intéressante que si le netting de marge est réel et
substantiel.

## Verdict et prochaine mesure

`CANDIDATE_EDGE — BLOQUÉ PAR UNE INCONNUE DE CAPACITÉ`

C'est la première chose du projet avec un **net positif hors échantillon**.
Ce n'est pas encore un edge validé : trois conditions du standard de preuve
restent ouvertes (capacité réelle, mécanisme non démontré, un seul régime de
94 jours).

**La prochaine mesure n'est pas une mesure de marché.** C'est une mesure de
**capacité** : *OKX nette-t-il, en portfolio margin, une paire inverse/linéaire
delta-neutre sur le même sous-jacent ?*

Elle décide d'un facteur 20 sur le rendement du capital, elle ne demande aucune
recherche supplémentaire, et elle exige un compte en lecture seule — pas de
capital, pas d'ordre. C'est, de loin, le meilleur rapport information/coût
restant dans le projet.
