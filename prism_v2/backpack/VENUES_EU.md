# Balayage documentaire des venues europeennes — grille, sources, verdicts

**Statut : `AUCUNE VENUE EXAMINEE N'EST ACCESSIBLE A 1 000 EUR — et la raison
est STRUCTURELLE, pas venue par venue`**

Regle du balayage, fixee d'avance : **aucune collecte de donnees de marche
avant qu'une venue n'ait franchi la grille d'accessibilite.** Le tour
precedent a mesure 75 min de carnet sur une venue dont le programme s'est
revele ferme par trois conditions arithmetiques. L'ordre est desormais
inverse : documents d'abord, donnees ensuite.

## Grille appliquee a chaque venue

1. eligibilite d'un resident francais, et entite juridique
2. capital minimal et taille minimale de cotation
3. conditions exactes des recompenses : seuil, periode, allocation, forme
4. frais maker/taker, rebates, conditions pour y acceder
5. exigences de volume, de presence au carnet, de nombre de marches
6. paper trading disponible

## Verdicts, sur sources primaires

### Backpack EU — `FERME`
Source : `eu.support.backpack.exchange/.../market-maker-program.md` (integral).

| critere | valeur |
|---|---|
| entite / France | Trek Labs Europe Ltd, CySEC 273/15 — **accessible** |
| taille min. d'ordre | **2 000 $ de moyenne**, et coter exige DEUX cotes |
| seuil de recompense | **1 % de l'allocation TOTALE** = 1 950 $/mois |
| forme du paiement | **equity de societe privee**, valorisation fevrier 2024 |
| frais en programme | VIP2, **maker nul** |
| KPI profondeur | **10 000 $ de chaque cote** |

### Bitstamp — spot — `FERME`
Source : `Bitstamp_Spot_DMM_Program_BESA_October.pdf`.

| palier | rebate maker | condition |
|---|---|---|
| MM0 | 0,0000 % | aucun critere atteint |
| MM1 | −0,0020 % | **> 1,5 % du volume maker TOTAL de l'exchange** |
| MM2 | −0,0035 / −0,0050 % | > 3,5 % du total ET > 3,5 % par paire sur 15 % des paires T2 |
| MM3 | −0,0050 / −0,0075 % | > 6,0 % du total ET > 6,0 % par paire sur 30 % des paires T2 |
| bonus max | **−0,0100 %** | profondeur requise sur paires eligibles |

Et deux obligations qui ferment seules : « trade material volume in **at least
12 pairs** » et « present in the orderbook **at least 80 % of the time** in
each relevant pair ».

### Bitstamp — derives — `FERME`
Source : `Bitstamp_Derivatives_Program.pdf`. Programme pilote, BTC et ETH perp.

    profondeur exigee   50 000 $ a 1 bp (BTC) / 37 500 $ (ETH)
                       100 000 $ a 2 bp (BTC) /  75 000 $ (ETH)
    volume             > 2 % du volume maker total de l'exchange
    presence           90 % du temps, double cotation obligatoire
    places             5 participants MAXIMUM, membres MTF seulement
    compensation       10 000 $/mois

La profondeur exigee seule vaut **46x** le capital disponible.

### Kraken — `FERME`
Source : blog officiel du programme Liquidity Provider (4 septembre 2026).

Le programme se presente explicitement comme retirant « the gatekeeping
dynamics that lock out mid-size operators » : qualification par **part** du
volume maker et non par volume notionnel fixe, cinq paliers, « a sub-1% share
threshold » suffit pour le premier. Mais une part sub-1 % du volume maker de
Kraken reste hors de portee de plusieurs ordres de grandeur, et le meilleur
rebate du programme vaut **−0,006 % = 0,60 bps**.

## Le fait general que le balayage etablit

Trois venues, trois structures de programme differentes, **un seul et meme
produit vendu** :

| venue | meilleur rebate | porte d'entree |
|---|---|---|
| Kraken | 0,60 bps | part du volume maker total |
| Bitstamp spot | 1,00 bps | > 6 % du volume maker total, 12 paires |
| Bitstamp derives | 1,00 bps | 50 000 $ de profondeur, 5 places |
| Backpack EU | frais nuls | 2 000 $/ordre, 1 % du pool |

**Le rebate de marche vaut 0,25 a 1,00 bps.** Ce n'est pas une variable :
c'est le prix auquel l'industrie achete de la liquidite, et il est le meme
partout. Ce qui change d'une venue a l'autre, c'est seulement la forme de la
barriere — jamais sa hauteur.

Ce que cela exige pour produire 500 EUR/mois :

    rebate 0,60 bps  ->   9 000 000 $/mois de volume maker  ->  139 AR/jour
    rebate 1,00 bps  ->   5 400 000 $/mois                  ->   83 AR/jour
    rebate 0,25 bps  ->  21 600 000 $/mois                  ->  333 AR/jour

pour un pouvoir d'achat de **2 160 $**. Et ces allers-retours devraient etre
des fills PASSIFS des deux cotes, sur des marches ou la mesure du tour
precedent donne un net d'environ zero avant rebate.

**La barriere n'est donc pas un seuil administratif qu'une venue plus jeune
pourrait abaisser. C'est le rapport entre la taille du rebate et la taille du
capital.** Une venue qui supprimerait tout seuil d'eligibilite ne changerait
pas ce rapport.

## Ce que cela n'etablit pas

- Le balayage couvre quatre programmes sur trois venues. D'autres existent.
- Une venue TRES jeune, en phase d'amorcage, peut payer bien au-dessus du
  prix de marche pour attirer ses premiers teneurs — c'est le seul cas de
  figure que l'arithmetique ci-dessus ne couvre pas, parce qu'il est par
  nature hors marche. C'est le seul angle qui reste, et il est petit,
  temporaire, et porte un risque de contrepartie que les quatre venues
  examinees ici n'ont pas.
- Rien ici ne dit que le market making est perdant en general. Il dit que le
  rebate disponible ne remunere pas 2 160 $ de capital.

## Decision

Balayage documentaire **clos sans candidat**. Aucune collecte de donnees de
marche n'est lancee, conformement a la regle posee en tete : aucune venue n'a
franchi la grille.
