# BOOK_IMBALANCE : information directionnelle réelle, trop petite pour être capturée

21/09/2026. Test conduit selon `prism_v2/PROTOCOLE_BOOK_IMBALANCE.md`, gelé
avant lecture des données. Détecteur gelé au commit `cd58455`. Aucun seuil
ajusté.

**61,7 minutes, 6 instruments, 8 886 états, 2,48 s de résolution.**
Coupure IS/OOS par le temps. L'OOS a été lu **une fois**.

---

## Le résultat

| horizon | IS Δ | IS p | **OOS Δ** | **OOS p** | IC 90 % OOS | n OOS | verdict |
|---|---|---|---|---|---|---|---|
| **30 s** | +1,897 | 0,0010 | **+0,747** | **0,0050** | **[+0,339 ; +1,170]** | 3 280 | **PROMETTEUSE** |
| 120 s | −0,824 | 0,821 | −0,436 | 0,822 | [−1,270 ; +0,418] | 3 107 | RÉFUTÉE |
| 300 s | −0,560 | 0,718 | −0,379 | 0,759 | [−1,279 ; +0,546] | 2 777 | RÉFUTÉE |

Trois tests sur l'OOS. Seuil de Bonferroni 0,05/3 = 0,0167 : **h = 30 s survit
même corrigé** (p = 0,0050). Les deux autres ont un Δ **négatif** et p > 0,75.

**42 strates utilisées, 0 écartée** — aucune sélection silencieuse.

> **À 30 secondes, le signe de BOOK_IMBALANCE porte une information
> directionnelle réelle : +0,747 bps, sur 3 280 déclenchements, hors
> échantillon, avec un intervalle qui exclut zéro.**

C'est le premier résultat directionnel de ce dépôt qui survive à un contrôle
correctement construit. Il y a deux heures, ce détecteur ne pouvait émettre
aucun candidat.

---

## Le point décisif — et il annule une de mes conclusions

Au test précédent, le brut **naïf** croissait avec l'horizon : +0,687 bps à
30 s, **+4,171 bps à 300 s**. J'en avais tiré un contrefactuel maker donnant
jusqu'à +3,30 bps par capture.

Stratifié, le signal fait exactement l'**inverse** :

| horizon | Δ stratifié |
|---|---|
| 30 s | **+0,747** |
| 120 s | **−0,436** |
| 300 s | **−0,379** |

> **La croissance avec l'horizon était de la dérive de marché, pas du signal.**
> Le contrefactuel maker que j'avais présenté reposait entièrement dessus.
> **Il tombe.**

C'est précisément ce que vous aviez demandé de départager, et la réponse est
nette : le mouvement favorable à long horizon n'appartenait pas au détecteur.

---

## L'économie de la seule cellule qui survit

L'information vaut **0,747 bps**. Contre le plancher de coût :

| hypothèse d'exécution | coût A/R | net | facteur manquant |
|---|---|---|---|
| taker (10 bps frais + 1,33 impact **mesuré**) | 11,33 | **−10,58** | **15,2×** |
| maker parfait, demi-spread encaissé nul | 4,00 | −3,25 | 5,4× |
| maker + demi-spread encaissé **en entier** (borne irréaliste) | 2,67 | −1,92 | **3,6×** |

**Même sous l'hypothèse d'exécution la plus généreuse qui puisse s'écrire,
l'edge reste 3,6 fois plus petit que son coût.**

Il n'existe donc aucun modèle d'exécution capable de rendre cette famille
rentable. Ce n'est pas une question de fills à mesurer : c'est une question
d'ordre de grandeur, et l'écart n'est pas franchissable.

---

## Stabilité — la réserve honnête

L'effet **décroît d'un facteur 2,5 entre IS (+1,897) et OOS (+0,747)**. Même
signe, tous deux significatifs, mais la magnitude est instable sur une seule
heure. Sur le jeu de validation de 12 min, IS et OOS divergeaient déjà
fortement (+0,27 contre +1,93).

Cela ne change pas la conclusion économique — à +1,897 bps l'edge resterait
sous le plancher de coût le plus favorable — mais cela interdit de citer
+0,747 comme une constante.

---

## Ce que ce test établit, et ce qu'il n'établit pas

**Établi :**
1. Le détecteur corrigé produit une hypothèse mesurable, et elle a été mesurée.
2. À 30 s, son signe porte une information directionnelle réelle, hors
   échantillon, corrigée pour la multiplicité, sur 3 280 déclenchements.
3. À 120 s et 300 s, il n'en porte pas — et le signe s'inverse.
4. Le « brut croissant avec l'horizon » du test précédent était de la dérive.

**Non établi :**
1. Aucune capture nette. Aucun PnL. Aucun ordre.
2. Une heure = **un seul régime de marché**. Six instruments, tous des
   perpétuels USDT crypto majeurs.
3. La latence n'est pas testée (collecte à 2,48 s).
4. La stabilité de la magnitude n'est pas établie.

---

## Décision

**Arrêter cette piste sur le plan économique. Conserver l'instrument.**

Le critère d'abandon était déclaré : si l'information directionnelle survivait,
on passerait à l'exécution passive. Elle survit — mais elle est **3,6 à 15 fois
plus petite que le coût le plus favorable imaginable**. Passer à la mesure du
remplissage passif reviendrait à mesurer finement un poste qui ne peut pas
combler un facteur 3,6.

**Je ne le recommande donc pas**, et je le dis alors même que le test est un
succès méthodologique.

Ce qui reste acquis, et qui vaut au-delà de cette famille :

- `prism_v2/directional_test.py` — estimateur stratifié qui **supprime la
  dérive**, permutation intra-strate, bootstrap par blocs. 18 tests, dont celui
  qui vérifie qu'un détecteur 80 % LONG dans un marché haussier rend un Δ
  **exactement nul**.
- `prism_v2/family_audit.py` — l'entonnoir qui distingue « donnée manquante »,
  « seuil non franchi » et « rien trouvé ».
- La garde `sanity.check_firing_rate`, enfin branchée.

**Toute famille future devra passer ce test avant qu'on parle d'exécution.**
Appliqué à l'ancien résultat, il aurait tué le contrefactuel maker en une heure
au lieu de le laisser vivre.
