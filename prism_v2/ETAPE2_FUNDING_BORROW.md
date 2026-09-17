# Étape 2 — où sont les grandes amplitudes, et pourquoi on ne peut pas les prendre

Recherche menée sans partir d'aucune famille. Les données ont désigné la
direction ; elles l'ont ensuite fermée.

## La contrainte de départ

L'audit a établi le fait qui gouverne tout : **mon plancher de coût vaut
10–11 bps, et le plus grand effet de microstructure observable en vaut 2,03.**
La seule question utile était donc : *où existe-t-il, dans mes données, des
écarts dont l'amplitude écrase ce plancher ?*

## Mesure large — 3 appels d'API, toute la coupe transversale

Écart **exécutable** (bid d'un côté contre ask de l'autre), pas mid-à-mid :

| comparaison | n | médiane | p90 | p99 | > 11 bps |
|---|---:|---:|---:|---:|---:|
| perp linéaire vs spot, même devise | 654 | 2,53 | 11,25 | 33,80 | 10,6 % |
| perp linéaire vs perp inverse | 45 | 9,18 | 12,28 | 13,19 | 20,0 % |
| perp OKX vs perp Hyperliquid | 426 | −1,51 | 8,84 | 15,76 | 7,3 % |

**Les écarts spatiaux sont trop petits.** Médiane 2,5 bps contre 11 de coût.

## Deux chiffres spectaculaires, deux artefacts

Ma propre règle — un bon chiffre est un bug jusqu'à preuve du contraire — a
servi deux fois.

**PURR à 19 643 bps** : Hyperliquid cote 0,111, OKX cote 12,66. **Deux actifs
différents.** Collision de noms.

**ONE à 875 bps** : écart réel perp/spot de 8,4 %, même venue, même devise.
Mais son funding vaut **−0,5264 % par heure, soit −4 611 %/an**. Le perp cote
sous le spot parce que le funding paie les longs pour combler l'écart. **Ce
n'est pas un arbitrage, c'est le prix du portage.**

## La seule piste à amplitude percent-scale

Le funding extrême. Structure du trade neutre : long perp (encaisse le funding
négatif) + short spot (paie l'emprunt).

**Résolution d'unité d'abord**, parce que tout en dépend. Le taux d'emprunt
OKX vaut 0,0000276 pour ETH : par heure cela ferait 24,2 %/an, par jour 1,0 %.
Recoupement avec un **endpoint indépendant** (`savings/lending-rate-summary`) :

| ccy | ×365 (jour) | ×24×365 (heure) | savings estRate |
|---|---:|---:|---:|
| BTC | **0,51 %** | 12,2 % | 0,50 % |
| ETH | **1,01 %** | 24,2 % | 1,00 % |
| USDT | **3,50 %** | 84,1 % | 3,50 % |

**Taux journalier, confirmé 8 fois sur 8.**

## Le fait structurel qui ferme la piste

**Les actifs à funding extrême ne sont pas empruntables.** ONE, AKE, SOPH,
CASHCAT, HEMI : aucun marché de prêt sur OKX. KSM et MINA en ont un.

Le funding reste extrême **précisément parce que la couverture n'existe pas**.
Sa persistance n'est pas un oubli du marché, c'est la preuve de son
inaccessibilité.

## Et sur les actifs empruntables, l'instantané ment

L'instantané montrait 9 actifs sur 167 à écart positif, dont ZEC à
+38,6 %/an. L'historique de 92 jours dit autre chose :

| actif | funding moyen | médiane | % du temps négatif | instantané |
|---|---:|---:|---:|---:|
| ZEC | **+4,7 %/an** | +9,1 | **24 %** | −46,5 |
| PI | −5,8 | **+11,0** | 28 % | −74,3 |
| KSM | −0,7 | **+10,4** | 33 % | −43,0 |
| XTZ | +6,6 | +11,0 | 16 % | −47,8 |

La médiane vaut **+11 %/an** — le taux plancher. Les lectures négatives sont
des excursions transitoires. Tenu en continu, le trade **paierait** le funding
*et* l'emprunt.

## La capture épisodique, chiffrée

N'entrer que pendant les excursions négatives, décision prise sur le taux
**déjà payé**, coût de 33 bps par aller-retour (20 bps de frais + 2× les
demi-spreads perp et spot mesurés) :

| | |
|---|---:|
| capture brute par épisode | 0,1 à 5 bps (36,9 sur ZIL seul) |
| durée moyenne d'un épisode | 5 à 23 h |
| **net total sur 13 actifs** | **−8 150 bps** |
| **actifs nets positifs** | **1 / 13** |
| **médiane** | **−6,53 bps/jour** |

Le seul positif — ZIL, +1,42 bps/jour, soit **0,022× l'objectif** — serait une
sélection post-hoc sur une chance sur treize.

## Ce que cette étape établit

Le phénomène est réel : le funding devient bien fortement négatif. **La
capture ne l'est pas** : les excursions ne sont ni assez profondes ni assez
longues pour payer leur propre entrée.

Et surtout, un fait qui généralise :

> **Dans mon univers accessible, l'amplitude et la couvrabilité sont
> anti-corrélées.** Les écarts à l'échelle du pourcent existent exactement là
> où l'instrument de couverture n'existe pas. Ce n'est pas une coïncidence :
> c'est *pour cette raison* qu'ils sont grands.

C'est la version locale et mesurée du principe trouvé dans la littérature —
*forecasting ability and ease of exploitation are anti-correlated* — sauf
qu'ici ce n'est pas la prévision qui est en cause, c'est l'accès.
