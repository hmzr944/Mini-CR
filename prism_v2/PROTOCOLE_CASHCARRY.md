# Protocole gelé — cash-and-carry spot/perp sur le funding extrême (OKX)

Écrit le 21/09/2026 AVANT toute mesure de rendement. Aucun seuil ci-dessous
n'a été choisi en regardant un résultat.

## Hypothèse
Sur OKX, une position delta-neutre **long spot + short perpétuel** sur les
instruments dont le funding est le plus élevé capture un flux net supérieur au
coût d'aller-retour.

## Source économique
Le funding est le mécanisme qui arrime le perpétuel au spot. Quand la demande
de levier long est extrême, les longs **paient** les shorts. Le payeur est le
long à effet de levier ; le receveur fournit l'offre qui équilibre. C'est un
transfert contractuel, pas une prédiction de prix.

## Déclencheur observable, sans look-ahead
Le taux est **publié avant** le règlement. Ce dépôt l'a déjà vérifié :
`fundingRate == realizedRate` sur 8 330 relevés (CARRY_FINDING.md). On décide
sur le taux déjà payé à `t`, on encaisse ce qui est payé de `t` à `t+N`.

## Coûts, comptés en entier
- perp taker 5 bps × 2 (entrée + sortie) = 10 bps
- spot taker 8 bps × 2 = 16 bps
- **aller-retour = 26 bps de notionnel**, plus les demi-spreads réels
- funding payé si le signe s'inverse : compté, pas ignoré

## Capital
En compte unifié le spot sert de collatéral, mais la position reste
**essentiellement 1× de levier** : il faut détenir le spot. Capital ≈ notionnel.
Aucun levier n'est supposé. C'est la différence majeure avec le carry
inverse/linéaire, qui portait 14×.

## Univers
Les perpétuels OKX ayant **un spot correspondant** et **> 1 M$ de volume 24 h**.
156 instruments au 21/09/2026. Figé avant mesure.

## Critères de réussite, déclarés maintenant
- **RÉFUTÉE** si le flux net capté ex ante, en blocs disjoints, est ≤ 0.
- **NON CONCLUANTE** si le flux net est positif mais < 20 bps/jour de capital.
- **PROMETTEUSE** si le flux net ≥ 20 bps/jour de capital (0,2 %/jour) sur des
  blocs disjoints, avec une capacité ≥ 1 000 € et tous les blocs positifs.
- L'objectif du mandat est 200 bps/jour. 20 bps/jour est le seuil en dessous
  duquel la famille ne mérite pas la suite — pas une cible déguisée.

## Comptage des essais
Une seule règle est testée : « trier par funding publié à t, retenir les k plus
élevés, tenir N règlements ». Le balayage porte sur k ∈ {1,3,5,10,20} et
N ∈ {1,3,6,12,24}, soit **25 cellules**. Benjamini-Hochberg sera appliqué sur
les 25, pas sur les survivantes.

## Falsification
Si le taux publié à `t` ne prédit pas le taux payé en `t+1..t+N` — c'est-à-dire
si la persistance est nulle — la famille est fermée, quelle que soit l'ampleur
du funding instantané.
