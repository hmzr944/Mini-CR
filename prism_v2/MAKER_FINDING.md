# Markout des fills passifs — la famille maker est fermée sur OKX

**Statut : `EDGE_NOT_EXECUTABLE` — fermé par la structure tarifaire, pas par la stratégie**

---

## Pourquoi cette mesure

Les expériences taker avaient établi que le **péage par aller-retour** est la contrainte : ni la latence, ni la taille, ni les frais. Il reste exactement deux façons de le désarmer :

1. **l'amortir** → testé : carry inverse/linéaire, 191 bps/an, dominé (`CARRY_FINDING.md`) ;
2. **le recevoir au lieu de le payer** → être maker. C'est cette mesure.

Un maker n'achète pas au mid, il achète au bid : il encaisse un demi-spread. Mais il n'est exécuté que **parce que quelqu'un a choisi son prix**, et ce quelqu'un peut en savoir plus.

```
PnL du fill passif = demi-spread encaissé
                   + dérive du mid après exécution   (le MARKOUT)
                   − frais maker
```

Le markout était la seule inconnue. Il est maintenant mesuré.

## Hypothèse volontairement favorable au maker

La position dans la file est **inconnue** : je suppose la **première place**, donc exécution dès qu'un trade atteint le prix. C'est l'hypothèse la plus généreuse — elle donne au maker tous les fills, y compris les bénins.

**Un résultat négatif sous cette hypothèse est décisif. Un résultat positif n'aurait rien prouvé.**

## Le résultat

6,50 h · 15 perpétuels inverses · 1 173 717 instantanés · **8 465 fills passifs simulés**

| horizon | N | demi-spread | markout | frais | **NET** | t | part > 0 |
|---|---|---|---|---|---|---|---|
| 1 s | 8 465 | +1,4020 | **−2,7432** | −2,0 | **−3,3412** | −94,68 | 7,5 % |
| 5 s | 8 465 | +1,4020 | −2,8682 | −2,0 | −3,4662 | −69,65 | 13,2 % |
| 30 s | 8 464 | +1,4017 | −3,1805 | −2,0 | −3,7788 | −39,06 | 27,6 % |

Et **aucune** condition observable à la décision ne redresse le signe :

| caractéristique | bas | moyen | haut |
|---|---|---|---|
| spread | −3,094 | −3,684 | −3,621 |
| déséquilibre de profondeur | −3,214 | −3,616 | −3,569 |
| intensité des trades | −3,046 | — | −4,266 |
| part agresseur | −3,466 | — | — |

C'est la réponse directe à `P(fill rentable | caractéristiques)` : **négative dans les douze tranches**.

## Le contrôle qui valide la mesure

Un markout négatif pourrait n'être qu'une dérive de ma mesure. Le contrôle poste **le même ordre au même prix, à intervalles réguliers, sans exiger qu'un trade vienne le frapper** :

| horizon | N contrôle | markout contrôle | t | markout conditionnel | **écart** |
|---|---|---|---|---|---|
| 1 s | 23 092 | **0,0000** | 0,00 | −2,7432 | **−2,7432** |
| 5 s | 23 092 | 0,0000 | 0,00 | −2,8682 | −2,8682 |
| 30 s | 23 092 | 0,0000 | 0,00 | −3,1805 | −3,1805 |

Le contrôle est **exactement nul**. L'écart est donc de l'**adverse selection** — il vient d'**avoir été choisi**, pas d'une dérive du marché ni d'un défaut de mesure.

## Le chiffre qui tranche

```
demi-spread encaissé   +1,4020 bps
adverse selection      −2,7432 bps
                       ───────────
AVANT TOUT FRAIS       −1,3412 bps
```

**L'adverse selection dépasse à elle seule le demi-spread encaissé d'un facteur 2.**

Coter passivement perd **avant même de payer le moindre frais**, sous l'hypothèse la plus favorable de file d'attente.

## La conclusion porte sur la tarification, pas sur la stratégie

Il faudrait un **rebate de 1,3412 bps par fill** pour atteindre l'équilibre — c'est-à-dire **être payé pour coter**, là où OKX facture 2,0 bps au palier public.

Ce n'est pas « le market making ne marche pas ». C'est : **sur une venue où le maker est facturé, l'équation a ce signe. Sur une venue où il est rémunéré, elle change de signe.**

## Ce que cela dit des recherches Polymarket fournies

Le point 11 de ces recherches — `P(fill profitable | features)` mesuré par markout — est exactement la mesure faite ici, et elle conclut négativement **sur OKX**.

Mais les points 7 à 9 identifient la différence structurelle décisive : sur Polymarket les makers **ne paient pas** les frais taker dans les catégories concernées et **peuvent recevoir des rebates** financés par ces frais, plus des *Liquidity Rewards*. C'est un terme économique qui **n'existe pas** dans l'équation OKX.

Ce qui reste **non démontré**, et que ces recherches disent elles-mêmes :

- que le market making y soit rentable (les deux dépôts publics se présentent comme des *research harness*, pas comme des produits rentables) ;
- que l'edge mécanique `YES + NO < 1` soit **capturable** après remplissage partiel et positions unilatérales ;
- que l'adverse selection y soit plus faible — elle pourrait être **pire** : ces mêmes recherches indiquent que le sens d'une transaction déduit du carnet n'est correct qu'à ~59–61,5 %.

Sur ce dernier point, OKX a un avantage que Polymarket n'a pas : le canal `trades` fournit le côté **taker explicitement**. Le problème de classification de flux ne se pose pas ici — il se poserait là-bas.

## Limite honnête de cette mesure

L'observatoire agrège les trades par tranche de 250 ms. Je sais qu'une vente a eu lieu et dans quelle fourchette de prix, **pas quel trade exact**. Je ne peux donc pas reconstituer une file d'attente — seulement borner. La **direction** du résultat est solide (le contrôle l'établit) ; la **magnitude** de −2,74 bps porte l'incertitude de cette agrégation.

## Prochaine mesure

Deux candidates, et le choix dépend d'une chose que je ne peux pas trancher seul :

| piste | ce qu'elle exige | ce qu'elle déciderait |
|---|---|---|
| **netting de marge OKX** | un compte **lecture seule** | facteur 20 sur le carry (191 → 3 826 bps/an) |
| **Polymarket** | rien — l'API est joignable (HTTP 200 vérifié) | si le terme `rebate` change le signe de l'équation maker |

La seconde est réalisable immédiatement et sans compte. C'est celle que je poursuis par défaut.
