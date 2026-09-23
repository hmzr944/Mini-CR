# Le programme de market making Backpack EU — lu a la source, et ferme

**Statut : `PORTE FERMEE POUR CE COMPTE — par trois conditions independantes`**

Source primaire : `eu.support.backpack.exchange/exchange/programs/market-maker-program.md`
(texte integral, pas un resume tiers). Les chiffres ci-dessous sont cites, non
estimes. Le commit precedent les estimait ; ceux-la les remplacent.

## Ce qui est confirme, et qui etait la bonne nouvelle

Le programme **existe bel et bien sur l'entite EEA** — contact
`vip@eu.backpack.exchange`, distinct du contact global. La derniere question
ouverte sur la couverture juridique est donc tranchee : elle est positive.

## Les trois conditions, et pourquoi chacune suffit a fermer

### 1. Taille moyenne d'ordre >= 2 000 $, et il faut coter DES DEUX COTES

    pouvoir d'achat  1 000 EUR x 2x (plafond ESMA retail) =  2 160 $
    requis           deux ordres de 2 000 $               =  4 000 $
    manquant                                                1 840 $

Tenir un marche n'est pas poser un ordre, c'est en tenir deux. Le compte ne
peut pas financer la taille minimale des deux cotes — il lui manque 1,9x
lui-meme. **Ce n'est pas une marge insuffisante, c'est une exclusion
structurelle.**

### 2. Recevoir au moins 1 % de l'allocation TOTALE

Citation exacte : « MMs must receive at least 1% of the total allocation in
order to be eligible for equity rewards in a given month. »

    pool mensuel futures            195 000 $
    seuil d'eligibilite (1 %)         1 950 $/mois

C'est la lecture PESSIMISTE des deux que le commit precedent laissait
ouvertes, et c'est celle qui est ecrite. Il faut gagner 1 950 $/mois pour
toucher le premier dollar — en concurrence avec des market makers
professionnels, dont le volume constitue le denominateur (« volume of all MMs
in the program », pas le volume du marche).

### 3. Le bonus KPI exige 10 000 $ de profondeur DE CHAQUE COTE

    requis 20 000 $   contre 2 160 $ disponibles   ->   9,3x

## Et la nature de la recompense, qui change le sujet

« Backpack has allocated 5% of the company's equity as of December 2023 [...]
which nets to ~195 000 $ in monthly rewards [...] using our latest valuation
of 120m $ as of February 2024. »

**Ce n'est pas un revenu en cash. C'est de l'EQUITY d'une societe privee**,
valorisee a un prix de fevrier 2024. Le rachat en cash est optionnel, « for a
limited time », a cette meme valorisation. Meme un participant eligible ne
recevrait pas 500 EUR/mois d'argent disponible mais une part illiquide d'un
exchange crypto, a un prix qui n'est plus teste depuis deux ans.

## Le seul acquis exploitable

Les participants au programme passent au palier **VIP2, frais maker NULS**.
C'est exactement le scenario B du scan de levier. Confronte a la mesure du
jour :

    marche   demi-spread   markout   net a frais NULS
    NEAR         2,24       -2,12         +0,12 bps
    HYPE         0,05       -0,21         -0,16 bps

**Meme avec des frais maker a zero, coter passivement rend environ zero.**
L'adverse selection reprend le demi-spread a elle seule. La gratuite des
frais, qui etait l'espoir de la veille, ne suffit pas — elle n'etait que la
moitie du probleme.

## Ce que cela ferme, et ce que cela n'etablit pas

FERME : la subvention Backpack pour un compte de 1 000 EUR. Trois conditions
independantes, chacune suffisante, dont deux purement arithmetiques. Aucune
mesure supplementaire ne les deplacera.

N'ETABLIT PAS : que tout programme d'incitation soit ferme. Backpack a des
seuils explicites ; d'autres venues europeennes en ont peut-etre de plus bas.
C'est le seul angle qui reste au balayage de subventions, et il est desormais
a tester contre des seuils REELS et non contre une esperance.

## Ce que ce document remplace

Les fourchettes du tour precedent — « 50 a 200 EUR/mois, meilleur cas 400 » —
reposaient sur deux hypotheses : repartition egale du pool entre marches, et
lecture per-marche de la regle des 1 %. La source dit le contraire sur la
seconde et ne confirme pas la premiere. **Ces fourchettes sont retirees.**
