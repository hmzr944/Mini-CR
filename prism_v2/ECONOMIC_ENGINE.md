# Le moteur économique — où est l'asymétrie, et à quel prix

Livrable de la mission *Find the Economic Engine*. Question posée : non pas
« quelle stratégie semble rentable ? » mais **« quelle asymétrie observable
permet de transférer de la valeur du marché vers le système après tous les
coûts ? »**

Critère unique : **PnL net attendu / capital immobilisé / jour**.
Reproductible : `python3 prism_v2/capital_efficiency.py`.

Classification employée partout, sans mélange :
🟢 observé dans les données · 🔵 observé dans le code · 🟡 hypothèse
plausible · 🔴 non démontré.

---

## 0. Le seuil, avant toute chose

« Beaucoup de gains, peu de capital, horizon court » n'est pas testable.
Traduit en ×10 sur un an, en **intérêt composé** — la formulation la plus
indulgente possible :

```
seuil = 63,28 bps/jour de capital immobilisé
```

Deux remarques qui changent la lecture de tout ce qui suit.

**Le capital n'entre pas dans le seuil.** C'est un taux : 100 € et 1 M€
exigent exactement le même rendement quotidien. « Peu de capital » ne rend
donc pas l'objectif plus facile — cela le rend plus *difficile*, parce que
les coûts fixes et les minimums de taille pèsent proportionnellement plus.
Un test le vérifie explicitement.

**Le seuil réel est plus haut que 63,28.** Composer suppose que la capacité
suit le capital. C'est faux pour toute famille de microstructure : le carnet
n'absorbe pas dix fois plus parce que le compte a grossi. Une famille qui
échoue contre 63,28 ne peut pas réussir contre le vrai seuil.

---

## 1. Economic Edge Map

Cinq familles, pas quatre. La cinquième — **contre-mesure de venue** — n'est
pas une source de gain : c'est la force qui détruit les autres, et elle s'est
révélée être le facteur dominant de toute cette recherche.

| Famille | Source de l'asymétrie | Qui paie | Statut |
|---|---|---|---|
| **Information / flux d'ordres** | Savoir avant le prix | Le flux non informé | 🔴 fermée — aucun signal trouvé |
| **Dislocation inter-venues** | Deux prix pour un même état du monde | Le retardataire des deux venues | 🟡 **ouverte, non démontrée** |
| **Biais de prix structurel** | Préférence systématique des participants | Le porteur du biais | 🔴 fermée par la littérature |
| **Extraction d'incitation** | Budget payé par la venue | La venue, puis les takers | 🟡 ouverte, présumée négative |
| **Contre-mesure de venue** | — | *le stratège* | 🟢 démontrée, et c'est elle qui décide |

---

## 2. Toutes les familles testées

Généré par `capital_efficiency.py` ; chaque ligne renvoie à sa source.

| Famille | bps/jour | × objectif | Preuve |
|---|---:|---:|---|
| carry, netting de marge parfait 20× | 10,482 | 0,166× | 🟡 hypothèse |
| carry inverse/linéaire 1× | 0,523 | 0,008× | 🟢 observé |
| maker OKX (fills passifs) | −3,340 | −0,053× | 🟢 observé |
| taker microstructure OKX | −10,350 | −0,164× | 🟢 observé |
| maker marché de prédiction (résolution) | −1 000 | −15,8× | 🟢 littérature |
| sniping longshot <10 ¢ | −1 930 | −30,5× | 🟢 littérature |
| arbitrage de latence Polymarket (post-barème) | −70 000 | −1 106× | 🟢 barème officiel |

**Aucune n'atteint le seuil.** La meilleure en est à 0,166×, et c'est la
seule dont le chiffre soit une hypothèse.

Le cas cross-venue est traité séparément (§5) : son rendement dépend d'un
paramètre — l'écart brut exécutable — que ce dépôt n'a pas mesuré.

---

## 3. Familles fermées, et la preuve de chaque fermeture

### 3.1 Taker microstructure OKX — 🟢 fermée par la donnée
6,5 h continues, 617 820 événements, 3 307 configurations.
Fraction de réversion **−0,0511 ± 0,0194** (t = −2,63), compatible avec zéro
après correction pour essais multiples. Entonnoir 100 % → 97,96 % → 36,71 %
→ 0,52 % → **0**.
Ce qui rend la fermeture décisive : **latence et frais éliminés comme cause**.
À 0 ms de latence et 0 bps de frais, le net reste négatif (−10,35 et −3,12).
La contrainte n'est ni la vitesse ni le coût : il n'y a pas de signal.
`prism_v2/EXPERIMENT_REPORT.md`

### 3.2 Maker OKX — 🟢 fermée par la donnée
8 465 fills. demi-spread **+1,40**, sélection adverse **−2,74**, frais
**−2,00** → net **−3,34 bps** (t = −94,68).
Ce qui valide la mesure : le contrôle non conditionnel vaut **exactement
0,0000** sur 23 092 échantillons. Un biais de mesure l'aurait déplacé.
Il faudrait un rabais de **1,34 bps** pour seulement rentrer dans ses frais.
`prism_v2/MAKER_FINDING.md`

### 3.3 Sniping longshot — 🟢 fermée par la littérature, sans collecte
Perte de 19,3 ¢ par dollar sous 10 ¢. Le biais favori/longshot existe, mais
il est du mauvais côté pour l'acheteur de longshot.

### 3.4 Maker en marché de prédiction — 🟢 fermée par la littérature
Kalshi : takers −32 %, **makers −10 %**. Le rabais n'y change rien : il est
d'un ordre de grandeur inférieur à la perte.

### 3.5 Markout Polymarket — 🔴 **mesure invalide, et je la rejette**
Ma propre mesure a produit **+0,0078 $/part** (t = 2,23, 96,9 % positifs).
Je ne la retiens pas : **97,3 % des markouts à 60 s valent exactement zéro**.
Le carnet ne bouge pas à cette échelle ; le t-stat mesure du bruit divisé par
un écart-type artificiellement petit.
Le fond est structurel : le vrai risque d'un contrat binaire est la
**résolution terminale**, à laquelle un markout court est aveugle par
construction. Le markout est le bon outil sur un perpétuel, le mauvais ici.
Un second signe confirmait le défaut : un demi-spread de **−0,051** dans la
tranche p > 0,85, impossible sur un carnet sain, révélant une référence
périmée.
`prism_v2/POLYMARKET_FINDING.md`

### 3.6 Arbitrage de latence Polymarket — 🟢 la famille a existé, 🔴 elle est fermée
**C'est la seule famille rencontrée dont la forme correspondait à
l'objectif** : petit capital, cycle de quelques secondes, donc capital
recyclé des centaines de fois par jour. Elle était réelle.
Polymarket a déployé en janvier 2026 un barème taker en `p(1−p)`,
**maximal exactement à 50/50** — là où la stratégie opérait — et nul aux
extrêmes, là où elle n'avait rien à prendre.

```
feeRate(Crypto) = 0,07 ;  à p = 0,50 : 1,75 ¢/part sur 50 ¢
                                     = 3,5 % du capital par aller-retour
```

Il faut désormais capturer **plus de 3,5 points de probabilité** avant
spread, uniquement pour couvrir les frais. Fermée **sans engager une seule
collecte** : la mémoire d'échec a suffi.
`prism_v2/POLYMARKET_RESEARCH.md`

---

## 4. Familles restées ouvertes

**Dislocation inter-venues (Kalshi ↔ Polymarket)** — 🟡 la seule qui survit
à l'arithmétique. Traitée en §5.

**Extraction d'incitation (Maker Rebates, Liquidity Rewards)** — 🟡 ouverte,
présumée négative. Polymarket a distribué 12 M$ en 2025 et ~52 000 portefeuilles
seulement ont jamais fourni de liquidité. Mais le rabais rémunère la sélection
adverse, il ne l'annule pas : mes 8 465 fills OKX et la littérature Kalshi
disent tous deux que la sélection adverse dépasse le rabais. **La récompense
est visible, la sélection adverse ne l'est pas** — c'est précisément pourquoi
un revenu de rewards peut paraître sain pendant que le livre perd. Je ne
l'ouvre pas sans mesurer d'abord la seconde.

**Information / flux d'ordres** — 🔴 fermée sur OKX, 🟡 non testée sur les
marchés de prédiction. La piste « intelligence de portefeuilles » est
séduisante et je m'en méfie : identifier après coup les portefeuilles
gagnants est du look-ahead déguisé. Une version honnête exigerait de figer
la liste des portefeuilles suivis *avant* la fenêtre d'évaluation.

---

## 5. Meilleur facteur candidat — et ce qui le tient encore debout

**L'écart brut exécutable entre deux venues cotant le même événement.**

Pourquoi c'est le meilleur candidat : c'est le seul gain du dépôt qui **ne
dépend d'aucun modèle**. Acheter YES à 0,48 ici et NO à 0,47 là garantit
1,00 $ à la résolution. Il n'y a rien à prédire.

Ce que les barèmes imposent — calcul, pas hypothèse
(`python3 prism_v2/cross_venue.py`) :

| Scénario | Seuil de rentabilité | Requis pour l'objectif | Net à 4,5 ¢ |
|---|---:|---:|---:|
| sport, résolution 1 jour | 2,99 ¢ | **3,60 ¢** | 158,6 bps/j |
| sport, 1 jour, maker sur Kalshi | 1,69 ¢ | 2,31 ¢ | 294,9 bps/j |
| politique, 30 jours | 2,75 ¢ | 18,08 ¢ | 6,2 bps/j |
| politique, 180 jours | 2,75 ¢ | **impossible** | 1,0 bps/j |
| géopolitique (0 % Polymarket), 30 j | 1,75 ¢ | 17,25 ¢ | 9,7 bps/j |

Trois faits structurels, tous vérifiés par test :

1. **Les deux venues taxent au même endroit.** `p(1−p)` est symétrique, et
   les deux jambes sont à `p` et `1−p` : le facteur est *identique*. Les
   barèmes s'additionnent au lieu de se compenser, et culminent à 50/50, là
   où les marchés jumeaux sont les plus liquides.
2. **Le capital est la somme des deux jambes.** Le rapporter à la jambe la
   moins chère doublerait le rendement affiché.
3. **La fenêtre d'entrée n'est pas la durée de détention.** Les 2 à 7
   secondes rapportées sont le temps pour *saisir* l'écart ; les deux jambes
   sont financées jusqu'à la **résolution**. Confondre les deux gonfle le
   rendement quotidien d'un facteur **10 000**. C'est, je crois, l'erreur qui
   fait vendre des bots d'arbitrage.

**Ce qui tient tout l'édifice — et que je n'ai pas vérifié.** La fourchette
« 1,5 à 4,5 ¢ » vient de littérature commerciale. Sa moitié basse est
**sous** le seuil de rentabilité de 3,0 ¢ : une partie des écarts annoncés
sont structurellement perdants après frais. L'objectif n'est atteint qu'au
voisinage immédiat de la borne haute. **Toute la marge tient dans un chiffre
que ce dépôt n'a pas mesuré.**

---

## 6. Données nécessaires

Une seule chose manque, et elle est précise : **la distribution des écarts
bruts exécutables, sur quotes synchronisées**.

| Besoin | Pourquoi |
|---|---|
| Carnets Kalshi + Polymarket sur les **mêmes** événements | un écart entre deux événements *similaires* n'est pas un arbitrage |
| Horodatage local à la réception, sur les deux flux | un écart mesuré sur des carnets décalés de 2 s est une fiction |
| **Profondeur au touch**, pas seulement le meilleur prix | un écart de 4 ¢ sur 5 parts ne finance rien |
| Appariement d'événements par règle figée, écrite avant collecte | sinon le choix des paires devient le résultat |
| Horizon de résolution de chaque marché | c'est le dénominateur du critère |

Contrainte dure : **jamais d'écart calculé entre un carnet frais et un
carnet périmé**. C'est exactement le défaut qui a produit un demi-spread de
−0,051 dans ma mesure Polymarket.

---

## 7. Test minimal falsifiant

> Sur *N* ≥ 5 000 instants synchronisés couvrant ≥ 72 h et ≥ 30 paires
> d'événements appariées par règle figée : **quelle fraction des instants
> présente un écart brut exécutable ≥ 3,0 ¢ sur une profondeur ≥ 200 $ des
> deux côtés simultanément, les deux carnets datant de moins de 1 s ?**

Prédiction falsifiable, écrite avant la collecte :

- fraction **< 0,5 %** → famille fermée, le rendement ne couvre pas les frais ;
- fraction **0,5 – 5 %** → famille réelle mais non exploitable sans latence
  compétitive : à documenter et fermer pour *ce* système ;
- fraction **> 5 %** → famille ouverte, passage au paper trading.

Ce test peut tuer la famille en 72 heures pour un coût de collecte nul. Il
doit être exécuté **avant** toute ligne de code d'exécution.

---

## 8. Estimation du PnL net

🟡 **Conditionnelle, non démontrée.** Si — et seulement si — la borne haute
de 4,5 ¢ est exécutable :

```
sport, résolution 1 jour, taker/taker
  brut     4,50 c/part
  frais   -2,99 c/part     (Polymarket 0,05 + Kalshi 0,07, tous deux en p(1-p))
  net      1,51 c/part     sur 95,5 c de capital  =  158 bps/jour
```

Sur 1 000 € pleinement déployés et recyclés quotidiennement : **≈ 15,80 €/jour**.
Ce chiffre suppose un déploiement à 100 %, ce qui n'arrivera pas : les
opportunités sont intermittentes. Avec un taux d'occupation de 20 %,
**≈ 3,17 €/jour**, soit 31,7 bps/jour — **0,50× l'objectif**.

L'intervalle honnête est large et il **contient zéro** :

| Écart brut exécutable | Net | bps/jour (100 %) | × objectif (20 %) |
|---|---:|---:|---:|
| 2,0 ¢ | **négatif** | < 0 | — |
| 3,0 ¢ (seuil) | ~0 | ~0 | 0,00× |
| 4,5 ¢ (borne haute annoncée) | 1,51 ¢ | 158,6 | 0,50× |

Tout dépend d'un paramètre non mesuré, et la médiane de la fourchette
annoncée (3 ¢) tombe **exactement sur le seuil de rentabilité**.

---

## 9. Capital nécessaire

| Poste | Montant |
|---|---|
| Capital minimum techniquement viable | ~2 000 € (1 000 € par venue) |
| Capital minimum économiquement sensé | ~10 000 € |
| Raison | les deux jambes sont financées séparément ; aucun netting inter-venues n'existe |

Le point dur : **Kalshi et Polymarket ne se nettent pas**. Chaque jambe
immobilise son propre collatéral, sur sa propre venue, dans sa propre
devise, jusqu'à la résolution. On ne peut pas non plus faire tourner 1 000 €
sur les deux côtés — il en faut 1 000 de chaque.

---

## 10. Capacité

🟡 estimée, non mesurée. Bornée par la profondeur au touch des marchés
jumeaux, typiquement quelques milliers de dollars sur les événements
liquides. La capacité **ne croît pas avec le capital** : c'est pourquoi le
seuil composé de §0 est déjà trop indulgent.

Plafond structurel probable : **quelques dizaines de milliers d'euros**, ce
qui est cohérent avec l'objectif « petit capital » — c'est la seule
dimension où la contrainte joue en faveur.

---

## 11. Risques

| Risque | Nature | Gravité |
|---|---|---|
| **Jambe unique** | une jambe passe, l'autre non → position directionnelle nue | **critique** |
| **Contre-mesure de venue** | 🟢 démontrée : Polymarket l'a déjà fait, sur cette famille précise | **critique** |
| Écarts non exécutables | le 4,5 ¢ affiché est un artefact de carnet mince ou périmé | élevé |
| Divergence de résolution | les deux venues résolvent le « même » événement différemment → perte des deux côtés | élevé |
| Course à la latence | fenêtres de 2 à 7 s ; concurrence colocalisée | élevé |
| Immobilisation jusqu'à résolution | un arb « gagnant » bloque le capital des semaines | moyen |
| Réglementaire | Kalshi est un DCM régulé, Polymarket non ; accès non garanti | moyen |

Le risque de jambe unique mérite d'être nommé pour ce qu'il est : il
transforme une stratégie *sans prévision* en pari directionnel, c'est-à-dire
en la seule chose que six mois de mesures ont montrée perdante.

---

## 12. Architecture minimale

Rien de neuf n'est requis. Ce qui existe suffit, et ce qui manque est court.

**Réutilisé tel quel :** Instrument Registry · InstrumentSpec · reconstruction
de carnet · validation de séquence · replay causal (`book_at(t)`) · coûts
typés OBSERVED/DERIVED/ASSUMED/**UNKNOWN jamais converti en zéro** · limites
de capacité et de risque · kill switches · machine à états d'exécution ·
exécution PAPER · réconciliation · Capture Ledger append-only · Failure
Memory · Edge Health · VenueAdapter.

**À ajouter — trois pièces :**

1. `KalshiAdapter` implémentant `VenueAdapter`, avec son propre `FeeSchedule`
   en `p(1−p)` (jamais de taux global codé en dur) ;
2. un **collecteur bi-venues** à horodatage de réception unique, refusant
   d'apparier deux carnets dont l'écart d'âge dépasse 1 s ;
3. une **table d'appariement d'événements** figée et versionnée, écrite avant
   la collecte.

Aucune classe `RealExecutor`. Aucune clé privée. Aucun ordre réel. LIVE reste
désactivé.

---

## 13. Protocole de paper trading

1. **Discovery** (72 h) : collecte bi-venues, aucune règle écrite.
2. **Gel de règle** : seuil d'écart, profondeur minimale, âge maximal de
   carnet, règle d'appariement — écrits, committés, horodatés. Après ce
   commit, plus aucun paramètre ne bouge.
3. **Validation** (72 h de données neuves) : la règle figée tourne en PAPER
   avec queue explicite et fills jamais supposés. Une jambe non remplie est
   une perte, pas un trade annulé.
4. **Holdout** (72 h, ouvert une seule fois) : un seul chiffre en sort.
   Toute sélection faite après l'avoir regardé invalide le résultat.
5. **Réconciliation** : chaque fill papier confronté au carnet qui l'aurait
   produit. Écart non expliqué → le résultat est invalide, pas ajusté.

Interdits, sans exception : modifier un seuil après avoir vu le holdout ;
supposer un fill ; transformer un coût UNKNOWN en zéro ; rapporter un PnL
qui n'a pas de fill correspondant dans le ledger.

---

## 14. Critères GO / NO-GO

**GO** exige les cinq, simultanément :

- fraction d'instants à écart ≥ 3,0 ¢ et profondeur ≥ 200 $ : **> 5 %** ;
- net après frais **> 0** sur le holdout, jamais ouvert auparavant ;
- **> 63,28 bps/jour** de capital immobilisé, résolution comprise ;
- taux de jambe unique en PAPER **< 5 %** ;
- règle figée avant la validation, vérifiable dans l'historique git.

**NO-GO** si l'un seul manque. Et **NO-GO immédiat** si l'une des venues
publie un changement de barème touchant `p(1−p)` — la famille précédente est
morte exactement ainsi.

---

## 15. Conclusion — compatibilité avec « beaucoup de gains, peu de capital »

**Réponse : non démontrée, et probablement non.** Je ne peux pas donner
raison à cet objectif avec ce que j'ai mesuré.

Sept familles ont été portées jusqu'à un chiffre. **Aucune n'atteint le
seuil.** Six échouent d'un à trois ordres de grandeur. La septième — le
cross-venue — ne survit que dans un scénario où un paramètre non mesuré se
trouve à la borne haute d'une fourchette annoncée par des vendeurs de bots.

Le résultat le plus solide de cette mission n'est aucune de ces sept lignes.
C'est ceci :

> **La seule famille rencontrée dont la forme correspondait à l'objectif — la
> seule capable de transformer un petit capital en gains importants sur un
> horizon court — a réellement existé, a été exploitée, et a été tuée par la
> venue au moyen d'un barème calibré sur sa signature même.**

Cela dit quelque chose de général, et je le tiens pour le vrai livrable :
**une asymétrie assez rentable pour satisfaire cet objectif est assez visible
pour que la venue la voie, et la venue dispose d'un instrument qu'aucun
participant ne peut contrer.** Elle voit ses propres données avant tout
chercheur externe. Le barème `p(1−p)` n'est pas une taxe générale : c'est une
arme de précision, maximale exactement là où la stratégie vivait.

Ce qui reste possible, honnêtement énoncé :

- un rendement de **1,9 % par an** sur du carry, réel, mesuré, sans effet de
  levier magique (0,008× l'objectif) ;
- **peut-être** du cross-venue sport court, à condition qu'un test de 72 h
  démontre des écarts exécutables au-delà de 3 ¢ — et je donnerais cela à
  moins d'une chance sur trois ;
- rien qui ressemble à ×10 en un an avec un petit capital.

La bonne décision n'est pas de construire le bot. C'est d'exécuter le test
du §7, qui peut fermer la dernière famille en 72 heures pour un coût nul. Le
bot n'est justifié que par une découverte économique — jamais l'inverse.

---

### Réponses aux douze questions

**A — Quelle asymétrie est démontrée ?**
Aucune qui soit exploitable. Deux sont démontrées et *perdantes* : la
sélection adverse sur les fills passifs (−2,74 bps, t = −94,68) et le
différentiel de funding inverse/linéaire (+191 bps/an, réel mais dominé).

**B — D'où vient-elle ?**
Le funding vient d'un déséquilibre structurel entre longs et shorts sur des
contrats de mécanique différente. Ce n'est pas une inefficience : c'est un
paiement pour porter un risque de base réel.

**C — Pourquoi n'est-elle pas déjà arbitrée ?**
Elle l'est. 191 bps/an, c'est ce qui reste après que tout le monde soit
passé. Le résidu paie exactement le risque et le capital immobilisé.

**D — Rendement net attendu ?**
Carry : **0,523 bps/jour** (0,008× l'objectif). Cross-venue : 🟡 0 à 158
bps/jour selon un paramètre non mesuré — l'intervalle contient zéro.

**E — Capital requis ?**
Carry : ~5 000 € pour que les frais fixes ne dominent pas. Cross-venue :
~2 000 € minimum technique, ~10 000 € pour avoir un sens, **sans netting
possible entre les deux venues**.

**F — Fréquence des opportunités ?**
Carry : continue, par paiements de 8 h. Cross-venue : **inconnue** — c'est
exactement la quantité que le test du §7 mesure.

**G — Capacité maximale ?**
Carry : élevée, limitée par les paliers de marge. Cross-venue : 🟡 quelques
dizaines de milliers d'euros, bornée par la profondeur au touch.

**H — Risque de perte ?**
Carry : basse, hedge exact à la précision machine, risque résiduel = paliers
de marge et liquidation. Cross-venue : **critique** sur la jambe unique — une
jambe non remplie transforme un arbitrage en pari directionnel nu.

**I — Latence nécessaire ?**
Carry : aucune. Cross-venue : fenêtres de 2 à 7 s, face à des concurrents
colocalisés. **Mon infrastructure n'est pas compétitive à cette échelle**, et
je ne vois pas de chemin crédible pour qu'elle le devienne.

**J — Capital de départ réaliste minimum ?**
**10 000 €.** En dessous, les frais fixes, les minimums de taille et
l'impossibilité de netting entre venues consomment l'essentiel du rendement.

**K — Rendement temporel atteignable ?**
🟢 **1,9 % par an** sur le carry, mesuré et défendable (191 bps).
🟡 Cross-venue : l'intervalle va de **négatif à ×3 par an**, selon un seul
paramètre non mesuré. À la borne haute annoncée (4,5 ¢) et 20 % d'occupation,
l'arithmétique donne 31,7 bps/jour, soit ×3,2 sur un an — mais à la médiane
de cette même fourchette, elle donne **zéro**. Je ne donne pas plus d'une
chance sur trois à la borne haute.
🔴 rien de démontré qui approche ×10 par an.

**L — Compatible avec « beaucoup de gains, petit capital » ?**
**Non.** Non pas parce que je n'ai pas assez cherché, mais parce que les deux
termes se contredisent dans ce domaine. Un petit capital n'atténue aucune
contrainte : le seuil est un taux, identique à toutes les tailles, et les
coûts fixes pèsent plus lourd en bas. Les seules familles à l'échelle du
pourcent-par-trade sont soit démontrées perdantes, soit neutralisées par la
venue. Les familles réelles sont à l'échelle du point de base par jour.

**L'écart entre 0,523 et 63,28 n'est pas un écart d'exécution. C'est un
écart de nature.**

---

*Rien dans ce document ne repose sur un backtest sans coûts, une simulation
sans file d'attente, un README commercial, une capture de PnL, ou un résultat
sans holdout. Les chiffres 🟡 sont marqués comme tels partout, y compris dans
le code qui les produit.*
