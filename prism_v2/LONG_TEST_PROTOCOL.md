# Protocole gelé — test long, 833 jours

**Écrit et committé pendant la collecte, avant d'avoir vu la moindre donnée.**
La date du commit en fait foi.

## Pourquoi ce test existe

Le test précédent (`PORTFOLIO_PROTOCOL.md`) a été rejeté par son holdout, et
la cause était identifiée : **sous-dimensionnement**. 45 jours, dont 7 d'
échauffement par fenêtre, laissaient 13 / 5 / 7 jours effectifs. Une
validation de 5 jours n'a aucune puissance ; le Sharpe de 5,32 en discovery
était du bruit, et il s'est comporté comme tel.

Ici : **833 jours en barres 4 h** (juin 2024 → septembre 2026), soit 18× plus
de données et plusieurs régimes de marché — bull, bear, consolidation.

## Univers

Tous les perpétuels Hyperliquid non délistés disposant d'au moins 600 barres
4 h. Panneau irrégulier : un actif absent à une date n'est pas remplacé par
zéro, il est absent.

## Découpage, chronologique et strict

| fenêtre | part | jours ≈ |
|---|---:|---:|
| DISCOVERY | 50 % | 416 |
| VALIDATION | 25 % | 208 |
| HOLDOUT | 25 % | 208 |

Le HOLDOUT n'est ouvert **qu'une fois**, après sélection finale sur la
VALIDATION. Aucun paramètre ne bouge après son ouverture.

## Signaux pré-enregistrés — exactement cinq, aucun autre

L'architecture est fixe (`prism_v2/portfolio.py`). Seul `μ` change. Chaque
signal a **une seule** paramétrisation, figée ici. Aucune grille, aucune
recherche sur discovery.

| id | μ (espérance d'un long) | fenêtre | motivation |
|---|---|---:|---|
| **S1 CARRY+** | −moyenne(funding) | 42 barres (7 j) | le carry comme prime. Mesuré perdant sur 45 j : sert de témoin. |
| **S2 CARRY−** | +moyenne(funding) | 42 barres | le funding comme indicateur de positionnement. C'est l'hypothèse rejetée sur 45 j, re-testée avec puissance. |
| **S3 TSMOM** | rendement passé | 42 barres | momentum de série temporelle, l'anomalie la mieux documentée sur futures. |
| **S4 REV** | −rendement passé | 6 barres (1 j) | retournement de court terme. |
| **S5 BLEND** | moyenne des z-scores de S1..S4 | — | pré-enregistré **maintenant** pour m'ôter la tentation de le construire après coup. |

Paramètres communs, figés : `γ = 1`, `gross_leverage = 1.0`,
`max_weight = 0.10`, neutralité dollar, `band_multiple = 2.0`,
`cost_bps = 6.54` (**mesuré** : frais 4,5 + demi-spread p75 réel 2,04),
`min_vol` au plancher, volatilité sur 42 barres.

## Correction pour tests multiples

5 hypothèses. Benjamini-Hochberg à q = 0,10 sur les p-values de VALIDATION.
**Seuls les signaux survivant à BH passent au HOLDOUT.** Si aucun ne survit,
le holdout n'est pas ouvert du tout et la conclusion est négative.

## Critères d'invalidation

Un signal est rejeté si l'un de ces faits est établi :

1. net ≤ 0 en VALIDATION ;
2. net > 0 en VALIDATION mais ≤ 0 en HOLDOUT ;
3. il ne survit pas à la correction BH ;
4. le résultat est porté par moins de 10 actifs, ou par moins de 5 % des
   barres ;
5. il disparaît quand le coût passe de 6,54 à 13 bps (le double du mesuré).

## Ce que je rapporterai, quoi qu'il arrive

Les cinq signaux, sur les trois fenêtres, sans en cacher aucun. Un signal qui
gagne en discovery et meurt en validation sera rapporté comme tel.

## Lien avec l'objectif

Le critère reste **bps/jour de capital immobilisé**, seuil 63,28 (×10 en un
an, composé). Le livre tourne à `gross_leverage = 1.0`. Je rapporterai donc
aussi, explicitement, **le levier brut qu'il faudrait pour atteindre
l'objectif** — et le fait qu'un levier ne crée aucun edge : il multiplie le
rendement et le risque dans la même proportion.

Si aucun signal ne survit, je le dirai sans détour et ne proposerai pas un
sixième.

---

## Amendement — S5 BLEND retiré, **avant réception des données**

En implémentant les signaux j'ai constaté que **S5 est identiquement nul, par
construction et non par les données** :

```
S2 = -S1   et   S4 = -S3      (exactement, par definition)
z(-x) = -z(x)                 (le z-score est impair)
=> z(S1)+z(S2)+z(S3)+z(S4) = 0   pour tout jeu de donnees
```

Vérifié numériquement sur un tirage aléatoire : `max |z(-x)+z(x)| = 0`.

Mélanger un signal et sa négation exacte ne produit aucune information. Ce
n'est pas un résultat empirique, c'est une identité algébrique — constatable
sans regarder la moindre donnée, et je la constate avant que la collecte soit
terminée.

**S5 est retiré.** Il reste **quatre** hypothèses, et la correction
Benjamini-Hochberg porte donc sur 4 tests, non 5.

Aucun signal de remplacement n'est introduit : en ajouter un maintenant
serait exactement le geste que ce protocole existe pour interdire.

---

# Résultat — les quatre signaux échouent

161 actifs, 5 041 barres 4 h, **840 jours** (juin 2024 → septembre 2026).
Protocole exécuté sans modification.

## Les trois fenêtres, en bps/jour (levier brut 1,0)

| signal | DISCOVERY 420 j | VALIDATION 210 j | HOLDOUT 210 j |
|---|---:|---:|---:|
| S1 CARRY+ | **−7,17** | **+11,32** | — rejeté par BH |
| S2 CARRY− | **+4,44** | **−13,79** | — rejeté par BH |
| S3 TSMOM | **−0,56** | **+16,91** | **−5,89** |
| S4 REV | −25,98 | −30,58 | — rejeté par BH |

**Chaque signal change de signe d'une fenêtre à l'autre.** C'est la signature
d'une absence d'edge, pas d'un edge instable.

## Benjamini-Hochberg, q = 0,10, sur les p-values de VALIDATION

```
S3_TSMOM     p=0.0026  ->  SURVIT
S1_CARRY+    p=0.0872  ->  rejete
S2_CARRY-    p=0.9510  ->  rejete
S4_REV       p=1.0000  ->  rejete
```

Un seul survivant. Holdout ouvert une fois, pour lui seul.

## HOLDOUT de S3_TSMOM

```
net        -1196,3 bps      =  -5,89 bps/jour      Sharpe -4,07
prix          -89,1 bps        drawdown max 1764 bps
funding       -35,1 bps        turnover total 163,9
couts       -1072,0 bps        rendement annuel -21,5 %
```

**Critère d'invalidation n° 2 déclenché** : net > 0 en validation, ≤ 0 en
holdout. Le dernier signal tombe.

## Ce que le résultat enseigne, au-delà du verdict

**1. Le carry n'est pas un piège stable — il est instable.** Sur 45 jours
j'avais mesuré β = −5,5 et conclu « le carry est un piège ». Sur 840 jours,
S1 perd en discovery (−7,17) et gagne en validation (+11,32). La relation
funding/prix **dépend du régime**. Ma conclusion précédente était vraie sur sa
période et fausse comme loi. C'est exactement ce que 45 jours ne pouvaient pas
révéler — et c'est le vrai gain de cette collecte.

**2. Le coût n'est pas un détail, c'est le terme dominant.** Sur le holdout de
S3, le brut vaut −124 bps et les coûts **1 072 bps**. Sur S4, coûts 3 494 bps
contre un brut de −2 715. La position continue a bien supprimé le churn des
allers-retours, mais ces signaux exigent un turnover que 6,54 bps par unité
ne pardonnent pas.

**3. La puissance statistique a fait son travail.** Le test de 45 jours avait
produit un Sharpe de 5,32 en discovery. Ici, avec 18× plus de données, aucun
signal ne tient. Le premier résultat était du bruit, et seul un échantillon
correctement dimensionné pouvait le dire.

## Ce qui n'a pas été fait

Aucun cinquième signal. Aucun réglage de fenêtre, de γ, de bande, de coût,
d'univers. Aucune exclusion d'actif. Les trois fenêtres sont **brûlées** :
toute recherche supplémentaire sur ces 840 jours produirait un résultat sans
valeur, et je ne le présenterais pas comme tel.
