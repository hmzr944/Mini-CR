# Coter passivement sur Backpack : premier chiffre, et il est negatif

**Statut : `ADVERSE SELECTION MESUREE — LE DEMI-SPREAD NE LA COUVRE PAS`**

Fenetre unique de 75 min, 20 marches, 16 360 instantanes de carnet a 5 s.
Un creneau, un regime. Ce n'est pas une verite tous regimes.

## Ce qui etait mesure, et comment

Le mid n'est pas estime : il est LU dans un instantane pris a moins de deux
cadences de sondage du fill. Le demi-spread encaisse et le markout sont donc
deux mesures. L'ordre simule est pose DERRIERE la file reellement enregistree
au toucher — le remplissage n'est plus suppose.

## Le controle qui trie les mesures des artefacts

Un demi-spread mesure doit valoir environ la MOITIE du spread lu. Un rapport
eloigne de 1 signale que le prix a bouge entre deux instantanes : le mid
enregistre ne decrit alors plus le carnet au moment du fill, et le markout
calcule contre lui est contamine.

| marche | spread | demi-spr | rapport | markout 60 s | equilibre | N | |
|---|---|---|---|---|---|---|---|
| NEAR | 2,29 | 2,24 | 2,0 | −2,12 | **+0,12** | 479 | coherent |
| HYPE | 0,11 | 0,05 | 0,9 | −0,21 | **−0,16** | 420 | coherent |
| ARB | 2,21 | 0,44 | 0,4 | +2,44 | +2,88 | 69 | contamine |
| MNT | 1,59 | 2,38 | 3,0 | −3,97 | −1,59 | 44 | contamine |
| BNB | 0,12 | 1,57 | 26,2 | −7,29 | −5,72 | 371 | contamine |
| ZEC | 0,07 | 1,38 | 39,4 | −14,78 | −13,40 | 225 | contamine |

Les quatre lignes contaminees sont ecartees, y compris ARB — la seule dont
l'equilibre depassait le frais standard. **Le sondage a 5 s ne suit pas un
marche rapide**, et les chiffres les plus spectaculaires, dans les deux sens,
sont precisement les moins fiables.

## Le resultat, sur les deux mesures qui tiennent

     marche   equilibre   frais maker standard   net par fill
     NEAR       +0,12 bps          2 bps           −1,88 bps
     HYPE       −0,16 bps          2 bps           −2,16 bps

**Coter passivement au bareme standard perd sur les deux marches ou la mesure
est interne-coherente.** Le demi-spread encaisse est integralement repris par
l'adverse selection avant meme que le frais s'applique : NEAR encaisse 2,24 et
rend 2,12 ; HYPE encaisse 0,05 et rend 0,21.

Et ce net ne compte PAS le cout de SORTIE. Le markout mesure la valeur d'un
inventaire non solde ; solder coute une traversee de plus. Ce terme manque au
code, et il ne peut que BAISSER le resultat. Il n'a pas ete ajoute parce que
le resultat est deja sous le frais : l'ajouter ne changerait pas le signe.

## Ce que cela etablit

La condition pour que la cotation passive paie sur ces marches n'est pas un
frais bas — c'est un frais NEGATIF. Il faut que la venue PAIE, d'au moins
0,12 a 0,16 bps pour atteindre le neutre, et davantage pour degager un profit
et absorber la sortie.

La question se deplace donc entierement du marche vers le programme de market
making, et la reponse n'est pas dans une donnee publique.

## Ce que cela n'etablit pas

- Un creneau de 75 min. Rien sur les heures creuses, les regimes de
  volatilite, la stabilite dans le temps.
- Deux marches. NEAR et HYPE ne sont pas l'univers.
- Le PnL net reste INCONNU : il exige le bareme de rebate par palier, non
  publie au niveau requis.
- Les marches a spread LARGE — les seuls candidats interessants — n'ont pas
  fourni 30 fills en 75 min. MNT seul a atteint 45, et il est contamine. Le
  piege structurel tient : la mesure est rapide la ou elle est inutile.

## Deux defauts de methode, declares

1. **BANDE FETCHEE TROP TARD.** BTC, ETH et SOL rendent 0 echange dans la
   fenetre. Ce n'est pas un fait de marche : `/trades` plafonne a 1 000
   echanges, soit 41 min d'historique sur BTC, et le rejeu a tourne 105 min
   apres la fin de la collecte. La bande doit etre capturee A LA FIN DE LA
   COLLECTE, pas apres. Trois marches perdus par sequencement.

2. **UN SEUIL ECRIT APRES LE DEBUT DE LA COLLECTE.** `MAX_SNAPSHOT_AGE_POLLS`
   a ete fige a 17:45, trois minutes apres le premier instantane (17:42). Les
   trois autres seuils sont anterieurs (07:47 et 17:32). Au moment ou il a
   ete ecrit, le seul resultat observe etait INCONNU sur toute la ligne, donc
   sans chiffre vers lequel l'ajuster — contamination faible, non nulle,
   ecrite ici plutot que tue.

## Prochaine mesure

Cadence de sondage a 1 s sur les marches a rapport coherent, bande capturee a
la fin de la collecte, et duree en JOURS et non en heures — c'est le seul
moyen d'atteindre 30 fills sur un marche a spread large. Avant cela, la
reponse de `vip@backpack.exchange` sur l'eligibilite et le bareme decide s'il
vaut la peine de collecter.
