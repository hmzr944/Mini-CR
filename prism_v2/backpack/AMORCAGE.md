# Venues en amorçage et programmes de points — fermeture par construction

**Statut : `CATEGORIE NON RESOLUE PAR CONSTRUCTION — et ce n'est pas un manque
de donnees`**

Le balayage precedent (`VENUES_EU.md`) a etabli que le rebate de marche vaut
0,25 a 1,00 bps partout, et que la barriere n'est pas un seuil administratif
mais le RAPPORT entre la taille du rebate et celle du capital. Il laissait un
seul cas ouvert : **une venue en amorçage payant AU-DESSUS du prix de marche**,
parce que hors marche par nature. Ce document le traite.

## Comment une venue peut payer au-dessus du prix de marche

Elle ne le peut pas en cash — le cash a un prix et ce prix est le marche.
Elle le peut uniquement en payant dans quelque chose **dont le prix n'existe
pas encore** : equity non cotee, jeton non emis, « points ».

C'est exactement ce qu'on observe :

| programme | forme du paiement | prix etabli ? |
|---|---|---|
| Backpack MM | equity, valorisation fevrier 2024 | non, et rachat discretionnaire |
| Backpack Points | « points » | **non, et la venue le dit** |
| Kraken Market Participation | warrants sur equity | non |
| Bitstamp DMM | cash, 10 000 $/mois | oui — et 5 places, membres MTF |

**Le seul programme a paiement en cash etabli est aussi le seul dont les
conditions d'acces sont franchement hors de portee.** Ce n'est pas une
coincidence : c'est la meme grandeur vue des deux cotes.

## Le cas le plus favorable, examine a fond

Backpack EU exploite un programme de **Points** ouvert au retail, **sans
capital minimal declare** — le seul dispositif rencontre dans tout ce balayage
qui ne soit pas ferme par un seuil. Source : documentation EEA.

Ce que la venue ecrit elle-meme :

- les points sont distribues **hebdomadairement** selon l'activite ;
- les criteres sont **« intentionally opaque »** et peuvent changer ;
- les points **« have no intrinsic monetary value »** et il n'existe **« no
  guarantee they will have any value »** ;
- la documentation ne mentionne **aucun airdrop de jeton** lie aux points ;
- l'eligibilite **n'est pas garantie** selon les regions.

## Pourquoi cela ferme la categorie, et pas seulement ce programme

Le filtre economique du protocole demande un rendement net estimable, avec un
scenario central et un scenario defavorable. Ici il n'y a rien a estimer : la
venue **refuse explicitement d'attribuer une valeur** a ce qu'elle verse.

Ce n'est pas un inconnu qu'une mesure supplementaire leverait. C'est la
propriete qui rend le mecanisme possible :

> Une venue ne peut payer au-dessus du prix de marche qu'en payant dans un
> actif sans prix. Des qu'un prix existe, le paiement revient au prix de
> marche.

**« Payer au-dessus du marche » et « rendement mesurable » s'excluent par
construction.** Le seul angle que `VENUES_EU.md` laissait ouvert se referme
donc sur lui-meme, sans qu'aucune collecte soit necessaire.

## Ce que cela n'etablit pas

- Que les points ne vaudront rien. Ils peuvent valoir beaucoup. C'est un
  **billet de loterie**, pas un rendement — et la distinction est tout le
  sujet de ce depot.
- Qu'aucune venue ne paiera jamais en cash au-dessus du marche. Une telle
  offre serait une subvention a perte, donc breve, donc non planifiable sur
  l'horizon de deux mois vise.

## Statut au registre

    categorie                   venues en amorçage / programmes de points
    verdict                     NON RESOLU PAR CONSTRUCTION
    raison                      la venue ne publie aucune valeur, et ne peut
                                pas en publier sans cesser de payer au-dessus
                                du marche
    collecte declenchee         AUCUNE
    reouverture                 exige un programme a paiement CASH, montant
                                publie, et seuil d'acces sous le capital
