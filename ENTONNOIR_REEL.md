# L'entonnoir sur données réelles — du candidat au PnL net

21/09/2026. Protocole gelé dans `prism_v2/PROTOCOLE_ENTONNOIR_REEL.md` **avant**
toute mesure. Vos cinq questions, répondues par la mesure.

**Verdict : 0 cellule sur 81 franchit les trois critères déclarés. Toutes les
familles sont réfutées en exécution taker.**

---

## 1. Les données

Collecte L1 (5 niveaux) + tape agresseur, 12 instruments OKX USDT-SWAP,
**12,1 minutes, 1 044 relevés, ~9 s de résolution**. Chaque détection dispose
d'un **avenir réel** dans la même série — condition nécessaire pour mesurer
une capture causale.

## 2. Combien de candidats par famille ?

| famille | états | données KO | seuil KO | **candidats** |
|---|---|---|---|---|
| BOOK_IMBALANCE | 972 | 0 | 213 | **759** |
| AGGRESSIVE_FLOW | 972 | 162 | 178 | **632** |
| SPREAD_DISLOCATION | 972 | 0 | 815 | **157** |
| DEPTH_WITHDRAWAL | 972 | 169 | 803 | **0** |
| SHORT_HORIZON_REVERSION | 972 | **972** | 0 | **0** |

Deux remarques honnêtes :

- **DEPTH_WITHDRAWAL émet 0 candidat** — mais plus parce qu'il est cassé :
  parce qu'aucun élargissement de spread n'est survenu sur ces 12 minutes
  calmes. C'est « pas encore testé », pas « réfuté ».
- **SHORT_HORIZON_REVERSION ne peut pas s'exécuter** : il exige 5 points de mid
  dans une fenêtre de 30 s, et la collecte n'en fournit que ~3 à 9 s de
  résolution. **Limite de ma collecte, pas du détecteur.**

## 3. Décomposition brut → net, causale

Latence 1 000 ms. Entrée à `T0+latence` au **VWAP du carnet réel**, sortie au
carnet de `T0+latence+détention`. Aucune information postérieure.

| famille | notionnel | détention | résolus | **BRUT** | impact | frais | **NET** | t |
|---|---|---|---|---|---|---|---|---|
| BOOK_IMBALANCE | 100 | 30 s | 729 | **+0,687** | −1,330 | −10,0 | **−10,643** | −29,3 |
| BOOK_IMBALANCE | 100 | 300 s | 439 | **+2,814** | −1,285 | −10,0 | −8,470 | −5,9 |
| BOOK_IMBALANCE | 10 000 | 300 s | 282 | **+4,171** | −3,130 | −10,0 | −8,959 | −5,0 |
| AGGRESSIVE_FLOW | 1 000 | 30 s | 600 | +0,797 | −1,464 | −10,0 | −10,667 | −25,4 |
| AGGRESSIVE_FLOW | 10 000 | 300 s | 286 | +2,033 | −2,159 | −10,0 | −10,126 | −5,7 |
| SPREAD_DISLOCATION | 100 | 300 s | 81 | **−5,267** | −1,827 | −10,0 | −17,095 | −13,7 |
| SPREAD_DISLOCATION | 10 000 | 300 s | 45 | **−9,408** | −5,320 | −10,0 | −24,728 | −12,7 |

**Ce que cela établit :**

1. **BOOK_IMBALANCE — la famille qui ne pouvait littéralement jamais émettre —
   a un edge brut causal POSITIF et mesuré : +0,69 à +4,17 bps**, croissant
   avec l'horizon, sur 282 à 729 captures résolues. Ce n'était pas mesurable
   il y a deux heures.
2. **Il est intégralement tué par les 10 bps de frais taker.** Le net est
   −8,5 à −12,1 bps, c'est-à-dire le coût, à peu de chose près.
3. **SPREAD_DISLOCATION a un brut NÉGATIF** : son signal pointe dans le mauvais
   sens, et d'autant plus que l'horizon s'allonge (−9,4 bps à 300 s). Ce n'est
   pas un seuil mal calibré, c'est un signe faux.

## 4. Combien bloqués par un coût INCONNU ?

| motif | occurrences |
|---|---|
| fenêtre collectée ne couvre pas entrée et/ou sortie | **8 913** |
| profondeur insuffisante pour le notionnel | 156 |

Jamais comptés comme nuls. Le premier motif est **ma limite de collecte**
(12 minutes contre des détentions de 300 s), pas une propriété du marché.

## 5. Quel PnL net par jour et par euro immobilisé ?

**Négatif, sur toutes les cellules.** La meilleure vaut −8,47 bps par capture.
Sur 1 000 € avec ~288 rotations/jour : **−244 €/jour**. L'objectif est +20.

## 6. Le contrefactuel maker — et pourquoi ce n'est PAS un résultat

Puisque le brut est positif et que seuls les frais le tuent, la question
devient : et en exécution passive ?

Sur la meilleure cellule mesurée (brut +4,171, impact −3,130) :

| hypothèse sur le demi-spread encaissé | net/capture |
|---|---|
| **nul** (sélection adverse totale) | **+0,17 bps** |
| 50 % | +1,74 bps |
| entier (borne haute irréaliste) | +3,30 bps |

**Aucune de ces trois lignes n'est une mesure.** L'expérience est taker de bout
en bout ; la probabilité de remplissage passif et le markout ne sont pas
mesurés. Et la mesure la plus répétée de ce dépôt est que le demi-spread ≈ la
sélection adverse — ce qui place la ligne réaliste au plus près de **+0,17**.

## 7. Le contrôle placebo — non concluant, et une erreur de ma part

J'avais produit trois colonnes : brut réel, brut placebo (direction aléatoire),
brut inverse. **La colonne « inverse » ne prouve rien** :

```python
gross = sign * (exit_book.mid - entry_book.mid) / entry_book.mid * 1e4
```

`sign` vaut ±1 sur les **mêmes carnets** : `gross(SHORT) = −gross(LONG)`
exactement. L'antisymétrie est une **identité mathématique**, pas un contrôle.
Je l'avais présentée comme une preuve ; je la retire.

Reste le placebo, seul informatif :

| famille | n | brut réel | brut placebo |
|---|---|---|---|
| BOOK_IMBALANCE 30 s | 713 | +0,712 | +0,140 |
| BOOK_IMBALANCE 300 s | 426 | +2,892 | **−2,312** |
| AGGRESSIVE_FLOW 300 s | 376 | +1,416 | **+1,015** |

Un placebo à direction aléatoire devrait tendre vers 0. Il rend −2,31 puis
+1,02 : **la dispersion domine à cette taille d'échantillon.** Le contrôle est
**non concluant**, et il le restera tant que la fenêtre vaut 12 minutes.

> **Conséquence directe : je ne peux pas encore distinguer le « brut positif »
> de BOOK_IMBALANCE d'une simple dérive de marché sur l'horizon de détention.**
> C'est la question n° 1 à trancher, avant toute autre.

## 8. Ce que cette mesure ne peut pas établir

1. **12 minutes = un seul régime.** Rien ici ne vaut hors échantillon.
2. **Les captures se chevauchent** : les `t` rendus sont des bornes supérieures.
3. **La latence n'est pas testée.** Les trois valeurs (200 / 1 000 / 3 000 ms)
   donnent des résultats **identiques au centième**, parce que la collecte
   échantillonne à ~9 s : les trois tombent sur le même carnet. Le balayage de
   latence déclaré au protocole est **dégénéré** et je le signale plutôt que de
   le présenter comme un résultat.
4. **Exécution taker uniquement.** La question maker reste entière.
5. **Aucun ordre réel.** Tout ceci reste de la simulation.

## 9. Décision

**Ne pas allouer de capital. Ne pas déclarer d'edge.** Les nouveaux candidats de
BOOK_IMBALANCE ne sont pas de l'alpha : ils sont, pour la première fois,
*mesurables*.

Ordre des travaux, par valeur d'information décroissante :

1. **Trancher le placebo.** Collecte plus longue et à plus fine résolution, puis
   comparer le brut du détecteur à une direction aléatoire sur le même
   échantillon. Si le placebo égale le réel, la famille meurt là, et aucune
   mesure d'exécution ne vaut la peine d'être faite.
2. **Seulement si le placebo est battu** : mesurer le remplissage passif et le
   markout (`prism_v2/fill_model.py` existe déjà, la collecte `touch_forward`
   tourne 4×/jour). C'est ce qui décide du signe.
3. **Corriger la résolution de collecte** pour que SHORT_HORIZON_REVERSION
   puisse s'exécuter et que la latence soit réellement balayée.
4. **Réexaminer SPREAD_DISLOCATION** : brut négatif et croissant en magnitude,
   ce qui est une information en soi — pas un seuil à ajuster.

**Vous aviez raison sur le fond : la correction a rendu la recherche valide,
pas rentable.** Elle a transformé deux réponses négatives invalides en une
question mesurable, et la première mesure dit que le coût taker écrase l'edge
brut. La séparation détection / économie / exécution / risque n'a été assouplie
nulle part.
