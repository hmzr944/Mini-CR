# Le revenu contractuel : ce que la venue paie, et ce qu'elle fait payer

Directive : cesser d'inventer des familles de signaux de prix, et examiner les
mécanismes économiques qui **ne dépendent pas de prédire le prochain
mouvement**. Programmes d'incitation, récompenses de liquidité, rémunérations
contractuelles, paliers de frais réellement applicables.

Ce document répond par des mesures. Chaque nombre est reproduit par un script.
Aucun tarif théorique n'y est traité comme acquis.

---

## 1. La carte, mesurée

| mécanisme | source | rendement | vs 271,9 bps/jour |
|---|---|---|---|
| Récompenses Polymarket, **marché médian, hors choix** | 496 marchés, pool lu | **+3 à +5 bps/jour** | 54 à 90× court |
| Récompenses Polymarket, **en choisissant les mieux payés** | idem, test hors échantillon | **−234 à −3 039 bps/jour** | ruineux |
| Vault HLP Hyperliquid (tenue de marché du protocole) | `vaultDetails`, APR lu | 3,93 %/an = **1,08 bps/jour** | 252× court |
| Prêt USDT / USDC sur OKX | endpoint public | 3,50 %/an = **0,96 bps/jour** | 283× court |
| Prêt SOL | endpoint public | 4,00 %/an = 1,10 bps/jour | 247× court |
| **Rebate maker** Hyperliquid (−0,10 bps) | `feeSchedule.tiers.mm` | exige **0,5 % du volume maker de la venue** | **≈ 31 M$/jour** |
| Amélioration de palier de frais | les deux venues | **aucune accessible** | — |

**Le plafond de toute la catégorie est ~5 bps/jour**, soit ~20 %/an.

## 2. Les frais réellement applicables, lus et non supposés

`userFees` donne le barème Hyperliquid complet :

| palier | déclencheur | taker | maker |
|---|---|---|---|
| base | — | 4,50 | **1,50** |
| VIP 1 | 5 M$ / 14 j | 4,00 | 1,20 |
| VIP 4 | 500 M$ / 14 j | 2,80 | **0,00** |
| MM 1 | **0,5 % du volume maker de la venue** | — | **−0,10** |
| MM 3 | 3,0 % du volume | — | −0,30 |

Le volume médian de la venue vaut **6,18 G$/jour**. Le premier palier où le
frais maker **change de signe** exige donc ≈ **31 M$ de volume par jour**. Avec
1 000 €, même à cent rotations quotidiennes du capital, on atteint 0,1 M$ :
il manque un facteur **≈ 300**.

OKX ne publie pas d'endpoint de palier non authentifié ; son barème public
(5,0 / 2,0 bps) reste de qualité `PUBLIC_SCHEDULE` au sens de `fees.py`, et
ne peut pas autoriser un passage en exécution.

**Conclusion de l'item 2 : le rebate existe, il est chiffré, et il est
inaccessible. Le frais maker le moins cher atteignable est 1,50 bps.**

## 3. Polymarket : le seul endroit où la venue paie pour coter

C'est la seule source identifiée dans ce projet qui ne demande aucune
prédiction. Les paramètres sont **publiés par marché** et lus, jamais supposés :
`rewardsDailyRate`, `rewardsMinSize`, `rewardsMaxSpread`, et
`feeSchedule.takerOnly = true` — **le maker n'y paie aucun frais**.

Recensement complet : **496 marchés récompensés, pool 49 963 USD/jour.**

L'audit précédent avait mesuré 2 500 $/jour sur douze marchés et conclu au
plafond — « un opérateur plus gros ne l'augmente pas : il se dilue lui-même ».
C'est le plafond d'un **gros** opérateur. Le pool étant partagé **au prorata**,
1 000 € est dans la situation inverse : sa part est grande là où personne ne
cote. Ce document teste cette situation-là.

### 3.1 La formule impose l'arbitrage, et c'est toute l'économie

Un ordre de taille `v` à distance `s` du milieu marque

    S(v, s) = v · ((maxSpread − s) / maxSpread)²      si s ≤ maxSpread

et le score d'un participant est **min(S_bid, S_ask)** : la cotation doit être
bilatérale. Le poids décroît **en carré** avec la distance au milieu. Coter
loin est sûr et ne marque presque rien ; coter au milieu marque le maximum et
se fait ramasser par tout flux informé. **Revenu et risque sont gouvernés par
le même paramètre, en sens opposés.**

### 3.2 Ce que le balayage donne, capital 1 000 USD

Règle de remplissage : un ordre passif n'est touché que lorsque le prix vient
le chercher, donc lorsqu'il bouge **contre** lui. Aucune probabilité de
remplissage favorable n'est supposée.

| s (cents) | récompense | PnL d'inventaire | NET | bps/jour | remplissages |
|---|---|---|---|---|---|
| 0,0 | 4,57 | **−46,50** | −37,34 | **−373** | 1 430 |
| 0,5 | 4,04 | −5,38 | −0,87 | −9 | 19 |
| 1,0 | 3,35 | −1,75 | +0,10 | +1 | 5 |
| 2,0 | 2,18 | 0,00 | +0,25 | +3 | 1 |
| 4,0 | 0,24 | 0,00 | +0,05 | +1 | 0 |

**L'adverse selection mange la récompense presque exactement.** Le meilleur net
médian vaut 0,25 USD/jour sur 1 000 — **3 bps/jour**.

### 3.3 Une erreur de ma part, dite en entier

La première version de ce module **cotait 1 000 parts et n'en remplissait que
20**. La taille qui marque et la taille qui se fait remplir sont pourtant la
**même grandeur** ; les séparer fabrique un revenu sans le coût qui va avec.
L'inventaire en ressortait cinquante fois trop petit, et le net paraissait
positif partout — jusqu'à **+6 325 bps/jour** sur le « meilleur » marché.

Corrigé, le même marché est ruineux. Sept gardes verrouillent désormais
l'égalité des deux tailles et la contrainte de capital
(`v · (1 + plafond) = capital`).

### 3.4 Le test qui tranche : choisir ici, évaluer ailleurs

Le maximum sur 439 marchés × 15 configurations est un maximum, pas un
résultat. Choix sur la première moitié de la journée, évaluation sur la
seconde :

| filtre (jours avant résolution) | n | top 10, **choix** | top 10, **évaluation** | bps/jour | médiane évaluation |
|---|---|---|---|---|---|
| ≥ 0 | 413 | +156,21 | **−303,87** | −3 039 | +0,47 |
| ≥ 1 | 362 | +106,81 | −23,41 | −234 | +0,48 |
| ≥ 7 | 284 | +74,34 | −123,79 | −1 238 | +0,42 |
| ≥ 30 | 222 | +46,08 | −192,67 | −1 927 | +0,30 |
| ≥ 90 | 167 | +33,99 | −129,13 | −1 291 | +0,36 |

**Aucun filtre ne renverse le signe.** Les marchés qui paient le mieux sont
ceux que personne ne cote, et personne ne les cote parce qu'une cotation
bilatérale y est ramassée du mauvais côté à mesure que l'issue se révèle —
à un jour comme à cent jours d'échéance.

Ce qui reste, stable dans les 15 cellules et à tous les filtres, est la
**médiane : +0,30 à +0,62 USD/jour, soit 3 à 6 bps/jour.**

## 3.5 Pourquoi la dispersion ne sauve rien — et l'erreur qu'elle a failli me faire publier

La part vaut `qm/(qm+qc)` : elle est **concave** en la taille. Disperser
1 000 € sur dix marchés à 100 € capte donc bien plus de récompense que les
concentrer — mesuré : **223 → 1 111 USD/jour**, un facteur 5. Pour un pool au
prorata, la dispersion est la bonne stratégie de capital, et c'est exactement
l'inverse de ce qui vaut pour un gros opérateur.

Le résultat brut donnait alors **+7 814 bps/jour**. Il est faux, et la raison
est structurelle.

**Le critère sélectionne les marchés où `Q ≈ 0`, et `Q ≈ 0` a une cause.** Les
dix marchés retenus ont un écart NATUREL de 11 à 41 cents pour une bande de
récompense de 4,5 cents :

| marché | écart naturel | bande | niveaux dans la bande |
|---|---|---|---|
| Shenzhen, température | **41 c** | 4,5 | 0 bid / 0 ask |
| Hormuz, navigation | 27 c | 4,5 | 0 / 0 |
| Shanghai, température | 24 c | 4,5 | 0 / 0 |
| OpenAI, matériel grand public | 20 c | 4,5 | 0 / 0 |

Pour toucher la récompense il faut coter **dans** la bande. Sur le marché
Shenzhen, cela signifie acheter à 0,593 ce que le marché offre à 0,41 et
vendre à 0,638 ce que le marché paie 0,82 : **on offre 18 cents par part à qui
veut les prendre.** Personne ne cote ces marchés parce que `Q = 0` est leur
évaluation correcte, pas un oubli.

Les deux populations, mesurées :

| groupe | n | demi-écart naturel | bande | récompense | remplissages pour effacer la journée |
|---|---|---|---|---|---|
| `Q ≈ 0` — personne ne cote | 17 | **5,5 c** | 4,5 c | 40,00 $/j | **20** |
| `Q > 0` — le marché est coté | 85 | **0,5 c** | 4,5 c | 1,27 $/j | — |

**Un défaut de mon propre simulateur, dit en entier.** Il cotait au milieu et
ne remplissait que sur les mouvements du milieu. Dans un marché à 41 cents
d'écart, un ordre au milieu est pris *instantanément* — le simulateur ne le
voyait pas et **sous-estimait donc le coût précisément sur les marchés que le
critère retenait**. La perte, elle, se calcule sans aucune trajectoire :
`demi-écart naturel − demi-bande`.

**La configuration saine, testée aussi.** Sur le second groupe la bande est
neuf fois plus large que le marché : il existe une zone
`demi-écart < s ≤ maxSpread` où l'on marque en restant **derrière** le
meilleur prix. Testée à trois marges et cinq tailles de portefeuille, elle
donne +2 233, −331, +626, +1 067 bps/jour à marge constante — **le signe
change avec le nombre de marchés et avec la marge**, sans motif. Le net y est
dominé par quelques issues d'inventaire, pas par un avantage systématique.

**Et la part elle-même n'est pas stable.** `Q` relevé en série sur 25 marchés
varie de **0 à 106,6** ; 9 sur 25 dépassent 10 au moins une fois. Un
instantané de carnet ne peut pas porter le calcul de part, puisque la
récompense est distribuée par échantillon d'une minute sur la journée entière.

## 4. Le fait structurel

**Dans chaque marché d'incitation mesuré, la subvention est tarifée pour
compenser exactement le risque qu'elle paie.** La formule au prorata le rend
mécanique : là où la concurrence est absente (Q = 0), la récompense est grande
*parce que* le risque est grand ; là où le risque est faible, la concurrence
dilue la part. La médiane **est** l'équilibre, et elle vaut ~4 bps/jour.

Ce n'est pas un défaut de recherche qu'un meilleur filtre corrigerait : le test
hors échantillon montre que **sélectionner sur la taille de la récompense est
activement ruineux**, à tous les horizons de résolution.

## 5. Ce que je ne peux pas dire

Que la catégorie est vide : elle ne l'est pas. Le prêt, le vault HLP et la
cotation du marché médian rapportent tous un montant **positif**, mesuré, entre
1 et 5 bps/jour. Ils ne sont pas nuls — ils sont **petits**, et petits pour une
raison structurelle, pas circonstancielle.

Que des frais nuls changeraient la conclusion. Le rebate maker est chiffré et
inaccessible d'un facteur 300 en volume ; et là où le frais est déjà nul —
Polymarket, `takerOnly` — le net reste à 3 bps/jour, parce que ce qui mord
n'est pas le frais mais l'inventaire.

---

## Reproduction

```
python3 pm_collect.py                      # 496 marchés, carnets + trajectoires
PRISM_PM_DATA=<f> python3 -m prism_v2.scans.pm_rewards   # balayage de s
PRISM_PM_DATA=<f> python3 pm_oos.py        # choix / évaluation séparés
PRISM_PM_DATA=<f> python3 pm_screen.py     # filtre sur la distance à la résolution
```

*Aucun seuil, aucune hypothèse de remplissage, aucun tarif n'a été modifié pour
rendre un chiffre plus présentable. LIVE reste désactivé. Aucun secret, aucune
clé, aucun ordre réel. 947 tests passent.*
