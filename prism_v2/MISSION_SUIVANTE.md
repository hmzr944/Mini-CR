# Mission PRISM suivante — état au 23 septembre 2026

Établie depuis le dépôt, pas depuis un souvenir. Une seule mission autorisée.

---

## 1. Le fait nouveau qui décide de l'ordre des chantiers

Le client WebSocket `prism_v2/backpack/ws.py` a été écrit hier pour le test de
propagation. Il change l'instrument disponible pour **toutes** les mesures de
carnet du dépôt :

| instrument | écart médian entre deux carnets | n |
|---|---|---|
| sondage `/depth` à 5 s (`collector.py`) | **4 989 ms** | 570 |
| WebSocket `bookTicker` (`ws.py`) | **4 ms** | 895 915 |

**Rapport : 1 250×.** Mesuré sur mes propres fichiers, pas estimé.

Or la garde `tape_mid_is_fit_for_level` se déclenchait quand le demi-spread
mesuré s'écartait du demi-spread lu — c'est-à-dire quand **le mid enregistré
était périmé par rapport au fill**. Sa cause était la cadence de sondage.

**La garde a écarté 4 lignes sur 6 lors de la dernière mesure, dont ARB à
+2,88 bps — le seul chiffre au-dessus du frais maker.** Elle avait raison avec
l'instrument d'alors. Elle n'a plus de raison de se déclencher pour cette
cause-là.

C'est un changement d'instrument **vérifié et mesuré**, pas un espoir. Il
satisfait la règle de réouverture du dépôt.

---

## 2. Hypothèses encore ouvertes, classées honnêtement

| # | hypothèse | statut | plafond économique |
|---|---|---|---|
| **A** | Le rejet de 4 marchés sur 6 était une limite de mesure, non une propriété de marché | **ouverte, instrument neuf** | inconnu — c'est l'objet du test |
| **B** | Cotation à distance du mid, corrigée de la dérive | ouverte, jamais testable en 8 min | a priori faible |
| **C** | Le carry EEA survit à plusieurs régimes et à l'exécution | ouverte | **plafonnée : facteur 70** |
| **D** | Un mécanisme économiquement différent | **aucun candidat** | — |

### Sur C — à dire avant de le travailler
Le carry EEA mesure 7,4–10,1 %/an net, soit **12 à 17 EUR sur 60 jours pour
1 000 EUR**. Le valider est justifié pour la **correction** du dépôt — c'est
le seul net positif sur instruments accessibles — mais aucun résultat de ce
chantier ne peut changer la trajectoire du capital. Le traiter comme un espoir
de rendement serait se mentir.

### Sur D — je n'ai pas de candidat
Proposer une quatrième famille pour ne pas rendre une case vide serait
exactement le mode de défaillance que ce dépôt existe pour attraper. La case
reste vide jusqu'à ce qu'un mécanisme causal, observable **au moment où
l'ordre peut être passé**, se présente.

---

## 3. La mission retenue : hypothèse A

> **Le verdict « la cotation passive au toucher ne couvre pas son frais »
> tient-il quand le carnet est mesuré à 4 ms au lieu de 5 000 ms ?**

Ce n'est pas la même expérience. C'est la même question avec un instrument
1 250 fois mieux résolu, sur la mesure dont la garde avait écarté les deux
tiers des lignes.

### Modules concernés

| fichier | rôle | modification |
|---|---|---|
| `backpack/ws.py` | flux temps réel | **aucune** — il existe |
| `backpack/collector.py` | sondage 5 s | **remplacé** par le flux WS pour cette mesure |
| `backpack/replay.py` | rejeu contre carnet | `MAX_SNAPSHOT_AGE_POLLS` à resserrer |
| `backpack/passive.py` | économie du fill, gardes | **aucune** — les gardes restent |
| `scans/backpack_mm.py` | le scan | branché sur le nouveau flux |

Un seul ajout nécessaire : **s'abonner au flux de trades** en plus du
`bookTicker`, pour que les fills et le carnet viennent de la même horloge.
Aujourd'hui la bande est récupérée par REST, le carnet par WS.

---

## 4. Défauts à éviter — les six commis dans ce dépôt

Chacun a produit, au moins une fois, un résultat faux et crédible.

1. **Demi-spread compté deux fois** — markout mesuré depuis le prix de fill au
   lieu du mid. → identité `demi-spread + markout = PnL` figée par un test.
2. **Niveau tiré de la bande** — artefact de fraîcheur, 22,93 → 3,42 bps selon
   la fenêtre. → un niveau se **lit** au carnet.
3. **Bande capturée après coup** — BTC/ETH/SOL à « 0 échange », lu comme un
   marché mort. → capture à la fin de la collecte, couverture vérifiée.
4. **Analyse à un seul côté** — la hausse du marché lue comme un edge. →
   mesure à deux côtés, ou correction placebo explicite.
5. **Conclusion écrite avant les nombres** — le script de vérification a
   imprimé « q95 négatif » alors qu'il était positif. → aucune phrase de
   verdict pré-écrite dans un script de mesure.
6. **Collecte d'arrière-plan** — fauchée deux fois par le conteneur éphémère.
   → **premier plan obligatoire**, par tranches de 9 minutes.

**Et le défaut propre à CETTE mission** : re-mesurer une famille dont on sait
qu'une ligne exclue était positive. Le protocole l'interdit explicitement —
voir §5.

---

## 5. Test minimal, préenregistré

### Ce qui est gelé avant collecte

| paramètre | valeur |
|---|---|
| univers | les **20 familles déjà gelées** dans `propagation.PAIRES` |
| durée | ~1 h, premier plan, tranches de 9 min |
| fraîcheur max du carnet | **200 ms** (contre 10 000 ms auparavant) |
| horizons de markout | 60 s / 300 s / 1 800 s, inchangés |
| seuil de médiane | **N ≥ 30 fills**, inchangé |
| garde de cohérence | **inchangée**, rapport dans [0,5 ; 2,0] |
| frais maker | 2 bps, barème Tier 1 |

### La règle anti-biais, et elle est la clé

**Tous les marchés sont mesurés et rapportés, dans le même passage, avec les
mêmes seuils.** Il est interdit de :

- ne re-mesurer que les lignes précédemment exclues ;
- assouplir la garde de cohérence parce qu'elle écartait un positif ;
- lire ARB avant les dix-neuf autres.

La garde reste en place. Si elle écarte encore, c'est qu'elle mesure autre
chose que la cadence de sondage — et **cela aussi est un résultat**.

### Critères chiffrés

| verdict | condition |
|---|---|
| **INCONNU** | moins de 5 marchés atteignent N ≥ 30 et passent la garde |
| **FAMILLE FERMÉE** | équilibre médian **< 2 bps** sur tous les marchés cohérents |
| **CANDIDAT** | ≥ 3 marchés cohérents à équilibre **> 2 bps**, avec IC 95 % par bloc excluant 2 bps |
| **RÉSULTAT NUL DE L'HYPOTHÈSE A** | la garde écarte encore ≥ 4 lignes sur 6 → la cause n'était pas la cadence |

Le seuil de 2 bps est le **frais maker réel**, pas un nombre choisi : en
dessous, coter perd de l'argent, quelle que soit l'élégance du reste.

### Ce que ce test ne pourra pas établir

- Une rentabilité : au mieux un `CANDIDAT` à valider hors échantillon.
- La robustesse : une heure reste un régime.
- Le coût de sortie de l'inventaire, toujours absent du code.
- Que la famille soit rentable **ailleurs** qu'à ce barème et sur cette venue.

---

## 6. Ordre d'exécution

1. **Hypothèse A** — coût : ~1 h de collecte + branchement du flux de trades.
   C'est le seul chantier où l'instrument a changé de façon mesurée.
2. **Carry EEA (C)** — validation d'exécution, pour la correction du dépôt.
   Plafond connu et modeste ; à ne pas présenter autrement.
3. **Hypothèse B** — seulement si A ne ferme pas la famille. Exige des jours de
   collecte, et mon a priori reste bas.
4. **D** — rien. La case reste vide jusqu'à ce qu'un mécanisme se présente.

Aucune collecte au-delà de A n'est autorisée avant lecture de son résultat.
