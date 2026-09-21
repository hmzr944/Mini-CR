# Protocole gelé — BOOK_IMBALANCE contient-il de l'information directionnelle ?

Écrit le 21/09/2026, **pendant la collecte et avant d'en avoir lu une ligne**.
Aucun seuil, aucune définition, aucun estimateur ci-dessous n'a été choisi en
regardant un résultat.

## La question, et une seule
Le brut positif mesuré (+0,69 à +4,17 bps) vient-il de l'**information portée
par le signe du signal**, ou de la **dérive commune du marché** sur l'horizon
de détention ?

Rien d'autre n'est testé ici. Ni les frais, ni les fills, ni la capacité.

## Le détecteur est GELÉ
`BookImbalanceDetector` tel qu'il est au commit `cd58455` :
`|déviation du microprix| > 25 % du demi-spread`, direction = signe de la
déviation. **Aucun réglage après observation.** Toute modification invaliderait
ce test et devrait le faire recommencer.

## Pourquoi un placebo à direction aléatoire ne suffit pas
Tirer un signe au hasard par événement laisse les observations partager les
**mêmes mouvements de marché**. Le placebo précédent l'a montré : il rendait
−2,31 puis +1,02 bps là où il devrait tendre vers 0. La dispersion commune
domine.

Et l'espérance du brut signé se décompose :

```
E[signe × r] = Cov(signe, r)  +  E[signe] · E[r]
                  ^                    ^
            l'information         la dérive, multipliée par
            que l'on cherche      le biais directionnel du signal
```

Un signal 60 % LONG dans un marché qui monte produit un brut positif **sans
porter la moindre information**. C'est exactement le second terme.

## Estimateur principal — différence stratifiée
Pour chaque strate `s = (instrument × tranche de 5 minutes)` :

```
Δ_s  =  moyenne(r | signe = +1, dans s)  −  moyenne(r | signe = −1, dans s)
```

où `r` est le rendement mid-à-mid **non signé** sur l'horizon.

La dérive commune à la strate affecte **les deux bras de façon identique** et
disparaît de la différence. L'estimateur global est la moyenne des `Δ_s`
pondérée par `min(n₊, n₋)`.

Les strates ne contenant qu'un seul signe sont **écartées**, et leur nombre est
rapporté — les omettre en silence serait une sélection.

## Distribution nulle — permutation des signes DANS les strates
`B = 5 000` permutations. À chaque tirage, les signes sont permutés **à
l'intérieur de chaque strate**, ce qui préserve la structure temporelle et
instrumentale et ne casse **que** l'association signe ↔ rendement.
`p = fraction des permutations dont la statistique ≥ celle observée`.

## Incertitude — bootstrap par blocs
`B = 5 000` rééchantillonnages **de strates entières, avec remise** — jamais de
captures individuelles. Les captures se chevauchent ; les traiter comme
indépendantes gonflerait la précision. Intervalle à 90 % par percentiles.

## Séparation temporelle
La fenêtre est coupée en deux moitiés **par le temps** :
- **IS** — première moitié : diagnostics, inspection, mise au point du code.
- **OOS** — seconde moitié : **consultée une seule fois**, à la fin, pour le
  test confirmatoire. Rien n'y est réglé.

## Horizons
**30, 120, 300 s.** Trois tests sur l'OOS ; la correction de multiplicité en
tient compte.

## La latence n'est PAS testée, et je le dis plutôt que de l'afficher
La collecte REST échantillonne à ~2,5 s. Un balayage de latence à 200 / 1 000 /
3 000 ms tomberait sur le même carnet et rendrait des cellules **identiques au
centième** — ce qui s'est produit au test précédent. La latence est donc
**retirée de la grille** : cette infrastructure ne peut pas la mesurer.

## Critères, déclarés maintenant
- **RÉFUTÉE** : Δ ≤ 0 sur l'OOS, ou `p` de permutation > 0,10.
- **NON CONCLUANTE** : Δ > 0 mais l'intervalle bootstrap à 90 % contient 0.
- **PROMETTEUSE** : Δ > 0 sur l'OOS, `p` ≤ 0,05, intervalle excluant 0, **et**
  signe cohérent avec l'IS.

**Aucun de ces statuts n'autorise à allouer du capital.** « PROMETTEUSE »
établirait une information directionnelle — pas une capture nette. Les frais,
le remplissage passif, la sélection adverse, la capacité et le turnover
resteraient entiers, et sont explicitement **différés** jusque-là.

## Ce que ce test ne pourra pas établir
1. Une fenêtre d'une heure reste **un seul régime de marché**.
2. Six instruments, tous des perpétuels USDT crypto majeurs.
3. Aucun ordre réel. Tout reste de la simulation.
4. Même un succès complet ne dirait rien sur l'objectif de 1 000 → 5 000 € en
   60 jours : il faudrait encore la capture nette, la capacité, le turnover et
   le risque de perte.
