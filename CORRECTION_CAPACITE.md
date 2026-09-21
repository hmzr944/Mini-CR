# Deux erreurs dans mon propre plafond, et le livre corrigé

21/09/2026. Ce document corrige `LIVRE_CARRY.md`, qui annonçait un plafond de
144 €/an. **Ce plafond était faux, et il l'était à cause de deux choix que
j'avais faits sans les vérifier.** Le chiffre corrigé est plus élevé. Je le
consigne avec la même exigence que s'il avait baissé.

---

## Erreur 1 — j'ai mesuré la capacité sur le carnet instantané

J'avais plafonné le notionnel à **25 % de la profondeur à 5 niveaux**. C'est le
bon critère pour un ordre qui doit passer maintenant. Ce n'en est pas un pour une
position qu'on tient **trente jours** et qu'on peut donc entrer sur plusieurs
heures.

Le volume réellement échangé, comparé à la profondeur instantanée :

| jambe inverse | volume 24 h | profondeur 5 niv. | rapport |
|---|---|---|---|
| ETH-USD-SWAP | 202 603 990 $ | 20 515 $ | **9 875×** |
| BTC-USD-SWAP | 284 819 810 $ | 135 515 $ | 2 102× |
| SOL-USD-SWAP | 31 375 160 $ | 22 665 $ | 1 384× |
| ADA-USD-SWAP | 5 639 030 $ | 58 040 $ | 97× |
| BCH-USD-SWAP | 553 850 $ | 12 410 $ | 45× |

En retenant **1 % du volume 24 h** de la jambe contraignante — heuristique
conservatrice pour une entrée patiente — la capacité du livre n'est plus
12 408 $ mais de l'ordre du **million**.

## Erreur 2 — j'ai filtré au mauvais horizon

J'avais rejeté ADA, DOT, SUI et UNI parce que la **médiane par règlement** du
différentiel valait 0 : elles paient rarement et beaucoup. Mais la position se
tient 30 jours, soit **90 règlements**. À cet horizon, la loi des grands nombres
efface exactement cette irrégularité.

Mesuré sur des blocs **disjoints** de 30 jours :

| paire | méd/moy par règlement | blocs 30 j positifs | moyenne | pire bloc |
|---|---|---|---|---|
| ADA | **0,00** *(rejetée par mon critère)* | **3/3** | 1,162 | +0,559 |
| SUI | **0,00** *(rejetée)* | **3/3** | 0,925 | +0,400 |
| UNI | **0,00** *(rejetée)* | **3/3** | 0,443 | +0,317 |
| ETH | 0,54 *(retenue)* | **2/3** | 0,301 | **−0,220** |
| BTC | 0,32 | **2/3** | 0,118 | **−0,274** |
| SOL | 0,65 *(retenue)* | **2/3** | 0,346 | **−0,255** |

> **Mon critère était anti-corrélé avec ce qui compte.** Il a rejeté des paires
> dont les trois blocs de détention sont positifs, et retenu des paires qui
> perdent sur un bloc sur trois.

C'est la même classe d'erreur que celle reprochée à V33 — un seuil appliqué à la
mauvaise grandeur — à ceci près qu'elle est ici corrigée et datée.

---

## Le livre corrigé

### A — détention 14 jours : **six** blocs disjoints, donc un vrai test

| paire | blocs > 0 | p unilatéral | r bps/j | pire bloc | levier | %/an |
|---|---|---|---|---|---|---|
| **BCH** | **6/6** | **0,016** | 1,019 | +0,081 | 14,3× | 26,3 % |
| **DOT** | **6/6** | **0,016** | 1,010 | +0,265 | 14,3× | 25,7 % |
| **ETC** | **6/6** | **0,016** | 1,566 | +0,499 | 14,3× | 67,9 % |
| ADA | 5/6 | 0,109 | 1,137 | −0,005 | 13,0× | 30,9 % |
| ETH | 4/6 | 0,344 | 0,314 | −0,552 | 46,4× | −35,3 % |
| BTC | 3/6 | 0,656 | 0,134 | −0,313 | 38,4× | −45,8 % |

**Livre : BCH, DOT, ETC.** 11 285 $ de notionnel, 790 € de capital,
**233 €/an, 29,5 %/an, 26 cycles par an.**

### B — détention 30 jours : plus de capacité, preuve plus faible

**ETC, BCH, ADA, SUI, LTC, LINK, UNI** — 3/3 blocs positifs chacun, mais
`p = 0,125` par paire : **ce n'est significatif pour aucune d'elles.**

- sur 1 000 € : **403 €/an, soit 40,3 %/an**
- capital maximal absorbable : **12 554 € → 3 934 €/an, 31,3 %/an**

---

## Ce que vaut cette preuve, dit sans adoucissement

Le livre A repose sur **six** observations indépendantes par paire. Le livre B
sur **trois**.

- 7 paires sur 11 montrent 3/3 blocs positifs à 30 jours. Sous une nulle de
  pile ou face indépendante, `p = 1,0 × 10⁻⁴`.
- **Mais les 11 paires ne sont pas indépendantes** : même venue, même régime de
  funding, mêmes 95 jours. Un seul régime favorable les fait toutes basculer
  ensemble. Ce p-value est une **borne supérieure de la preuve**, pas la preuve.
- Le nombre d'observations vraiment indépendantes du livre entier est **trois**
  à 30 jours, **six** à 14 jours.

À 30 jours de détention, il faut **5 blocs pour atteindre p < 0,05**, soit
150 jours de données. Nous en avons 95.

> **Il manque 55 jours de collecte avant le premier test concluant à 30 jours.**
> À 14 jours, les trois paires du livre A sont déjà à p = 0,016 chacune.

Et deux conditions restent entières, inchangées depuis hier :

1. **L'exécution maker n'est pas mesurée.** Tout ce qui précède suppose 8 bps
   d'aller-retour. En taker (~24 bps) le livre A est négatif. La collecte du
   touch tranche cela sous une semaine.
2. **Un seul régime.** Les 95 jours ne contiennent qu'un état du marché. Le
   différentiel inverse-linéaire dépend de la demande de levier ; rien ne dit
   qu'il garde son signe dans un régime opposé.

---

## L'ordre de grandeur, corrigé

| | avant | corrigé |
|---|---|---|
| sur 1 000 € | 144 €/an | **220 à 403 €/an** |
| capital absorbable | 869 € | **12 554 €** |
| à capacité pleine | 144 €/an | **3 934 €/an** |
| rotation | ~12 cycles/an | **26 cycles/an** (livre A) |

Le taux est bon — 22 à 40 %/an. **Le montant absolu ne l'est pas** : sur
1 000 €, cela reste 220 à 400 € par an. Pour que ce soit autre chose qu'un
rendement, il faut du capital, et la capacité s'arrête vers 12 500 €.

Ce n'est toujours pas « du profit en quantité ». C'est un rendement défendable
sur un capital modeste, et il n'est pas encore validé.
