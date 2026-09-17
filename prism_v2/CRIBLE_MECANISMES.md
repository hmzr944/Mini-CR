# Crible de mécanismes — résultat

Chercher dans l'espace des **causes**, pas des stratégies. C'est la méthode
que l'audit impose : le coût de la Phase C n'était pas sa stratégie, c'était
d'avoir choisi un événement sans vérifier son sens causal.

## Données

6 instruments liquides (BTC, ETH, SOL, XRP, DOGE, BNB), carnets et tape de
trades échantillonnés toutes les **6 secondes**, 45 minutes. Tailles converties
par `ctVal` — le défaut qui surestimait la profondeur BTC d'un facteur 100 est
ici un paramètre explicite.

## Cinq événements candidats

Chacun annonce un sens. On mesure si le prix bougeait déjà dans ce sens
**avant**, ou s'il bouge **après**.

| événement | avant (bps) | après (bps) | ratio | verdict |
|---|---:|---:|---:|---|
| gros trade agressif | 2,78 | 0,59 | 4,7 | **symptôme** |
| rafale de trades | 4,13 | 0,15 | 26,9 | **symptôme** |
| déséquilibre de carnet | 4,64 | 2,05 | 2,3 | **symptôme** |
| écartement de spread | 2,57 | −1,32 | −2,0 | **symptôme** |
| effondrement de profondeur | 0,89 | 1,14 | 0,8 | marginal |

Sur quatre horizons (30/30, 10/30, 30/60, 10/120 s), **tous deviennent des
symptômes**. Aucun ne reste causal de façon stable.

## Le verdict économique

```
meilleur mouvement postérieur, tous événements et horizons : 2,03 bps
coût d'aller-retour mesuré                                 : 11,00 bps
écart                                                      : -8,97 bps
```

**Le plus grand effet observable dans tout l'espace des mécanismes de
microstructure vaut 2,03 bps. Mon plancher de coût en vaut 11.**

## Ce que cela établit

Ce n'est plus une famille qui échoue, c'est **l'espace entier fermé par
arithmétique, à mon palier de frais**. Je n'ai plus besoin de tester famille
par famille : aucun événement observable sur ces instruments ne produit un
mouvement postérieur du même ordre que mon coût.

Et cela referme proprement la boucle de l'audit d'architecture : je paie
2 à 5× ce que paient mes concurrents, et les effets de microstructure valent
1 à 2 bps. **Un professionnel à 2,4 bps de coût aurait, sur le déséquilibre de
carnet à 2,05 bps, une marge nulle. Moi, une perte de 9 bps.** Le même
événement est marginal pour lui et impossible pour moi.

## Deux défauts trouvés en UTILISANT le crible

1. **La vérification de causalité acceptait un événement sans pouvoir
   prédictif.** « rafale de trades » ressortait CANDIDAT avec un mouvement
   postérieur de −0,60 bps : le ratio était favorable parce qu'il était
   *négatif*. Un ratio ne suffit pas, il faut quelque chose à capturer.
2. **L'agrégation exigeait dix événements par instrument** au lieu de dix au
   total, ce qui éliminait silencieusement les trois événements de carnet —
   rares par instrument, nombreux au total. Le crible ne voyait que les
   événements de trades.

Le second est de la même classe que les défauts de l'audit : une règle qui
rend « rien trouvé » pour une raison technique.

## Limites, explicites

6 instruments très liquides, 45 minutes, 5 définitions d'événements,
résolution 6 s. C'est un **crible**, pas une recherche exhaustive. Un effet
sub-seconde serait invisible — mais il serait aussi inexploitable à ma
latence de 300 ms – 1 s. Et les actifs illiquides, où les effets pourraient
être plus grands, ont une capacité qui ne permet pas de les monétiser.
