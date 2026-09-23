# Hypothèse A — la cotation passive mesurée à 4 ms

**Statut : `MITIGÉ SUR LA MESURE BRUTE — et le seul marché passant repose sur
un inventaire que le compte ne peut pas financer`**

Collecte du 23 septembre 2026, ~54 min, premier plan, observation seule.
Lecture globale unique. Aucun seuil déplacé.

---

## 1. Les contrôles d'horloge, exigés en revue

Carnet et bande viennent de la **même connexion WebSocket**, donc de la même
horloge d'exchange. Les deux horodatages sont conservés par message.

| | |
|---|---|
| messages | **808 204 carnets**, 3 048 échanges |
| trous de flux | **0** |
| lignes illisibles | **0** |
| latence de transport médiane | **29,4 ms** |
| latence p95 | 32,1 ms |
| **dérive sur la fenêtre** | **+1,2 ms** |

Un désalignement carnet/bande ne peut plus provenir de la mesure.

---

## 2. La prémisse de l'hypothèse A est CONFIRMÉE

La garde de cohérence avait écarté **4 lignes sur 6** à la mesure précédente,
avec des ratios de 11,6 · 26,2 · 39,4 · 0,3.

À 4 ms de résolution :

| | |
|---|---|
| marchés écartés **pour incohérence** | **0** |
| ratios des marchés retenus | **1,0 à 1,5** |
| marchés écartés, toutes causes | 10, **tous** pour `N < 30` |

**La garde se déclenchait bien à cause de la cadence de sondage.** C'était une
limite de mesure, pas une propriété de marché. L'hypothèse A avait raison sur
ce point, et le fait est établi.

---

## 3. Résultat économique, tous marchés, même passage

| marché | N | spread | ratio | demi-spr | markout | équilibre | IC 95 % |
|---|---|---|---|---|---|---|---|
| SOL | 206 | 0,86 | 1,0 | 0,43 | −1,93 | −1,29 | [−3,02 ; 0,21] |
| HYPE | 202 | 0,11 | 1,0 | 0,05 | −4,71 | −3,69 | [−5,33 ; −0,26] |
| UNI | 141 | 0,10 | 1,0 | 0,05 | −0,10 | 1,31 | [−6,12 ; 4,83] |
| ETH | 140 | 0,04 | 1,0 | 0,02 | −3,48 | −3,38 | [−3,67 ; −0,59] |
| NEAR | 93 | 1,51 | 1,1 | 0,85 | +2,37 | 3,81 | [−4,81 ; 13,12] |
| BTC | 75 | 0,01 | 1,0 | 0,01 | −1,18 | −1,09 | [−1,40 ; −0,73] |
| XRP | 64 | 0,64 | 1,0 | 0,32 | −1,12 | −0,80 | [−10,23 ; 4,46] |
| **ARB** | 63 | 2,58 | 1,2 | 1,52 | **+3,05** | **12,20** | **[6,33 ; 17,24]** |
| DOGE | 42 | 2,03 | 1,5 | 1,51 | −0,76 | 1,52 | [1,52 ; 2,53] |
| ZEC | 33 | 0,06 | 1,0 | 0,03 | −3,17 | −3,14 | [−9,16 ; 3,20] |

Dix marchés écartés, **tous pour `N < 30`**, aucun pour incohérence.

**Verdict selon la précédence déclarée : `MITIGÉ`** — un seul marché (ARB) a
sa borne basse au-dessus du frais maker ; il en fallait trois pour `CANDIDAT`.

---

## 4. Le contrôle de dérive — il ne tue pas ARB

ARB porte la signature qui m'avait piégé la veille : un markout **positif**.
La dérive inconditionnelle a donc été mesurée à chaque carnet, sans aucun
fill, et appliquée **uniformément aux dix marchés**.

| marché | markout | dérive | excès | équilibre corrigé |
|---|---|---|---|---|
| **ARB** | +3,05 | −1,76 | **+4,81** | **+6,33** |
| **NEAR** | +2,37 | −2,44 | +4,81 | +5,66 |
| tous les autres | — | — | négatif | sous le frais |

ARB **survit** au contrôle, et s'améliore. Ce n'est pas un artefact de
tendance.

---

## 5. Ce qui ferme quand même — l'inventaire

Le contrôle de dérive a révélé autre chose : **ARB et NEAR ont un flux à 70 %
d'un seul côté.**

Or la mesure prend **chaque échange comme un fill**. Elle ne contraint donc
jamais l'inventaire. Voici la position nette que chaque résultat suppose :

| marché | achats | ventes | net | notionnel net | **× le compte** |
|---|---|---|---|---|---|
| **ARB** | 83 | 36 | **+47** | 94 000 $ | **43,5×** |
| NEAR | 103 | 49 | +54 | 108 000 $ | 50,0× |
| SOL | 341 | 207 | +134 | 268 000 $ | 124,1× |
| XRP | 43 | 146 | −103 | 206 000 $ | 95,4× |

Pouvoir d'achat du compte : **2 160 $** (1 000 EUR à 2×, plafond ESMA).

**Le +6,33 bps sur ARB suppose de porter une position nette 43 fois supérieure
à ce que le compte peut financer.** Ce n'est pas un PnL de tenue de marché :
c'est une accumulation directionnelle non contrainte, qui a profité d'un
mouvement de prix pendant la fenêtre.

Un vrai teneur de marché biaise ses cotations pour rééquilibrer son
inventaire — et ce faisant, il renonce précisément aux fills qui créent ce
déséquilibre. La mesure ne modélise pas cette contrainte, et c'est **la plus
grosse hypothèse non déclarée de tout le dispositif**.

---

## 6. Verdict

| | |
|---|---|
| prémisse de l'hypothèse A | **CONFIRMÉE** — la garde était un artefact de cadence |
| résultat économique brut | **MITIGÉ** — 1 marché sur 10 passe |
| après contrainte d'inventaire | **le marché passant n'est pas finançable** |

La cotation passive au toucher **reste fermée pour ce compte**, non plus à
cause d'un manque de signal, mais parce que le seul signal positif mesuré
exige un bilan 43 fois supérieur au capital.

## 7. Ce que ce résultat n'établit pas

- **Une heure, un régime.** ARB a monté pendant la fenêtre.
- **N = 63 sur ARB.** Suffisant pour l'IC affiché, pas pour une conclusion
  stable.
- **Le bookTicker ne démontre ni la file, ni le taux de remplissage, ni
  l'adverse selection subie.** L'équilibre reste un **majorant**.
- **Le coût de sortie d'inventaire est toujours absent du code.** Il ne peut
  qu'abaisser le résultat.
- Il ne dit rien de la cotation passive **avec contrainte d'inventaire**, qui
  est une mesure différente et non faite.

## 8. Conséquence pour la suite

L'hypothèse A a fait ce qu'on lui demandait : elle a levé un artefact de
mesure et rendu les dix marchés lisibles. Elle a aussi révélé que le
dispositif entier mesurait une stratégie **sans contrainte d'inventaire**,
ce qui invalide toute lecture économique de ses résultats positifs — passés
comme futurs.

**C'est un défaut de conception du banc, pas un défaut de ce test.** Il
affecte rétroactivement toutes les mesures de cotation passive du dépôt.
