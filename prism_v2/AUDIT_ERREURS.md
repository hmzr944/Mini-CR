# Audit — la cause racine, et elle est unique

Quatorze défauts trouvés dans ce projet. **866 tests passaient.** Aucun test
n'a attrapé aucun de ces défauts : je les ai tous trouvés en remarquant un
chiffre absurde, et les tests ont été écrits **après**.

Ce n'est pas de la malchance. C'est une propriété de l'architecture.

## La cause racine

> **Les tests vérifiaient ce que le code FAISAIT. Aucun ne vérifiait que la
> quantité calculée CORRESPONDAIT à la réalité qu'elle prétendait décrire.**

Dans chaque cas, le code faisait exactement ce que ses tests demandaient, et
le nombre ne voulait pas dire ce que son nom disait :

- `notional` était en contrats, pas en dollars
- `rate_per_hour` était un taux par 4 h divisé par 8
- un livre « dollar-neutre » portait 23,8 % de variance marché
- une liquidation « cause » du mouvement en était le symptôme

## Les six classes, et ce qu'elles font

| classe | défauts | effet |
|---|---|---|
| **Constante de venue supposée uniforme** | `ctVal`, cadence funding 4 h/8 h, tailles en contrats | **fabriquent** des opportunités |
| **Normalisation par une statistique du futur** | médiane de spread sur tout le segment, médiane d'ampleur sur toute la période | look-ahead |
| **Règle dégénérée** | référence médiane nulle, fenêtre jamais prête, mélange S5 ≡ 0 | **faux négatifs silencieux** |
| **Horodatage de la découverte** | liquidations datées du sondage (délai médian 2 434 s) | causalité contaminée |
| **Invariant déclaré, jamais mesuré** | dollar-neutre ≠ market-neutre, mise à l'échelle détruisant la neutralité | le livre ne fait pas ce qu'il dit |
| **Méthodologie** | inversion de signe après échec, famille renommée | conclusions invalides |

Deux classes sont plus dangereuses que les autres. Les **constantes de venue**
ne produisent jamais de pertes, seulement des opportunités — on ne les
découvre donc qu'en se méfiant d'un bon chiffre. Les **règles dégénérées**
rendent « aucune opportunité », strictement indiscernable d'un vrai résultat
négatif : la référence médiane nulle aurait tourné quatorze jours avant que
j'en conclue une fermeture.

## Le défaut le plus coûteux

La Phase C entière reposait sur *« un ordre de liquidation pousse le prix »*.
Je ne l'avais jamais testée — je l'avais supposée. La mesure : le prix bougeait
déjà de **14,55 bps dans la direction de la liquidation pendant les 60 s la
précédant, sur 100 % des événements**.

Conditionner sur une liquidation revenait donc à conditionner sur un mouvement
de prix passé — **exactement la première famille du projet, fermée sur
617 820 événements**. La Phase C ne testait pas une famille nouvelle : elle
retestait la première sous un autre nom, et ma porte d'hypothèse ne pouvait
pas le voir parce qu'elle valide une *histoire*, pas un *mécanisme*.

## Ce que l'audit produit

`prism_v2/sanity.py` — la couche qui manquait. Six vérifications, sur données
réelles, avant toute conclusion économique :

1. **grandeur** — la quantité tient-elle dans le domaine physique de son unité
2. **déclenchement** — la règle peut-elle seulement se déclencher
3. **causalité** — l'événement précède-t-il le mouvement ou le suit-il
4. **invariant** — ce que le livre prétend être, l'est-il mesurablement
5. **nouveauté** — le déclencheur est-il prédit par une famille déjà fermée
6. **horodatage** — le délai de publication est-il négligeable devant la fenêtre

**Rejeu sur les défauts réels : 10 sur 10 attrapés.** Y compris la prémisse
causale de la Phase C et son caractère de famille renommée.

## Calibration externe

Un papier de validation walk-forward rigoureuse sur signaux de microstructure
(arXiv 2512.12924), 34 périodes de test indépendantes, coûts réalistes,
rapporte **0,55 %/an, Sharpe 0,33, p = 0,34**, explicitement non significatif.

C'est à cela que ressemble la recherche honnête dans ce domaine. Mes résultats
négatifs sont **conformes à la littérature qui applique la même discipline**,
et non le signe d'une incompétence particulière. La littérature qui rapporte
mieux est celle qui ne l'applique pas : plus de 90 % des stratégies
académiques échouent en capital réel.

Et un principe qui résume tout ce que j'ai mesuré :
**« forecasting ability and ease of exploitation are anti-correlated ».**

## Ce que l'audit change dans la méthode

Arrêter de chercher dans l'espace des **stratégies**. Chercher dans l'espace
des **mécanismes**, et n'accorder du travail qu'à ceux qui passent le test
d'antériorité causale — lequel coûte vingt minutes et aurait tué la Phase C
le premier jour.
