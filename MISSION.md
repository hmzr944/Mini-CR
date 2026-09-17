# PRISM — où se trouve, ou ne se trouve pas, l'argent

Ce document répond à la mission : *comment construire, à partir de ce que le
marché permet réellement, un système générant beaucoup de PnL net rapidement
avec peu de capital ?*

Il ne présente aucun résultat positif. Il présente le **calcul qui décide**,
les mesures qui l'alimentent, et les erreurs trouvées en route — y compris
deux des miennes.

---

## 1. Ce que l'audit a trouvé

### FAUX — le dénominateur de l'objectif n'était jamais calculé

L'objectif est `PnL net / capital immobilisé / temps`. Le numérateur a été
mesuré des dizaines de fois. Le dénominateur était **un nombre écrit à la
main** : `capital_efficiency.Family` reçoit un PnL déjà exprimé « en bps du
capital immobilisé », sans qu'aucune ligne du dépôt ne demande à OKX combien
il bloque réellement.

Deux lectures opposées circulaient, toutes deux fausses :

| lecture | ce qu'elle suppose | pourquoi c'est faux |
|---|---|---|
| « entièrement collatéralisé », levier 1× | capital = notionnel | c'est un choix de **financement**, l'exchange ne l'exige pas |
| « levier maximal affiché », 100× | capital = notionnel / lever | c'est le levier du **premier palier**, et il ignore qu'il faut survivre entre l'entrée et la sortie |

Corrigé par deux modules : `margin.py` (barème public échelonné par la taille,
482 familles mises en cache) et `capital.py` (coussin de survie **mesuré** dans
375 jours de prix, au quantile déclaré).

### FAUX — l'« inconnue décisive » de `CARRY_FINDING.md` n'en était pas une

Le document annonçait un facteur 20 suspendu à une question : *OKX nette-t-il
les marges d'une paire delta-neutre ?* — et affirmait qu'il fallait un compte
authentifié pour le savoir. Le barème est public, et la question ne mordait
pas. Voici la vraie contrainte, calculée :

| détention | levier réel | net/jour de capital |
|---|---|---|
| 0,25 j | 4,6× | −538 bps |
| 1 j | 2,9× | −82 bps |
| 4 j | **1,0×** | −6 bps |
| 30 j | 1,0× | **+0,57 bps** |

Le levier tombe à 1,0× dès quatre jours : le coussin nécessaire pour ne pas
être liquidé dépasse alors le notionnel. **Le netting aurait doublé le levier ;
il en faut 110.** Le chiffre de `CARRY_FINDING.md` (191 bps/an) était juste ;
son raisonnement ne l'était pas.

### CONFIRMÉ — le slippage réalisé est absent, et le code le dit

`slippage_observed()` existe et n'est **jamais appelé**. `bot.py` déclare
`slippage_excluded_for_paper_validation()`. Ce n'est pas une erreur cachée :
la qualité `EXCLU` est portée explicitement. Conséquence à retenir : **tous
les coûts du projet sont des bornes inférieures.** Le slippage réalisé exige
de vrais ordres ; il restera MANQUANT tant qu'on ne trade pas.

### Protections ajoutées

`funding_feed` porte la correction la plus coûteuse du dépôt (la cadence de
funding) et n'avait **aucun test**. Rien n'empêchait la régression : 12 tests
ajoutés. Idem pour le flux signé de l'observatoire (6 tests), la marge (14) et
le capital (21).

---

## 2. Le calcul qui décide

Toutes les mesures du projet se rangent en deux formes, et l'algèbre en
élimine une sans rien mesurer de plus.

**Forme A — capture par traversée.** On traverse le carnet pour entrer et pour
sortir : `PnL = edge − c` par cycle, rendement `(edge − c) × fréquence`.
Quand `edge < c`, le rendement est négatif **pour toute fréquence** — la
fréquence multiplie un nombre négatif. Or huit mesures indépendantes du projet
donnent `edge` entre 1 et 6 bps contre `c` entre 8 et 31 bps. La forme A n'est
pas « pas encore trouvée » : elle est **fermée par construction**.

**Forme B — capture par flux.** On traverse une fois, on détient `T`, on
encaisse `r` par unité de temps :

```
R(T) = L(T) × (r − c/T)        avec  L(T) = 1 / (marge initiale + coussin(T))
```

Le coût s'amortit (`c/T` décroît) mais le coussin grandit, donc le levier
tombe. L'arbitrage est réel et il a un prix.

**Le crible qui en découle.** En inversant, avec le levier que le coussin
autorise réellement (3,4× médian à un jour) et 25 bps d'aller-retour :

> **il faut un flux d'au moins ≈ 43,6 bps/jour de notionnel — soit ≈ 159 %/an —
> pour atteindre l'objectif.**

Ce seuil ne dépend d'aucun talent : seulement du levier que la volatilité
permet, du coût d'aller-retour, et de la durée. Aucune élégance, aucune
fréquence, aucune finesse d'exécution ne rattrape un flux trop petit.

---

## 3. Le recensement : tous les flux accessibles

| flux | mesure | bps/jour de notionnel | vs 43,6 |
|---|---|---|---|
| carry perp inverse/linéaire | 92 j, 15 actifs, 4 135 périodes | moyenne 0,01 – 1,58 ; **max jamais vu 13,7** | 0,03× |
| basis futures datés | 28 contrats, prix exécutables | 0,5 – **2,1** | 0,05× |
| funding OKX médian | 92 j | 0,30 | 0,007× |
| prime de variance (options) | 400 j de réalisé contre IV cotée | **≈ 0, signe alternant** | — |
| funding inter-venues OKX/Hyperliquid | 60 j, 138 actifs, cadence lue période par période | médiane des p90 **6,55** | 0,15× |
| prêt/emprunt | mesuré précédemment | négatif | — |

Les futures datés méritent une note : ils ont une propriété qu'aucun perpétuel
n'a — **une date de convergence garantie par contrat**, donc un flux qui ne
peut pas s'inverser. Le projet ne les avait jamais interrogés (`instType=FUTURES`
n'était jamais appelé). Leur basis annualise à **1,9 – 7,7 %/an**.

Sur les options, la prime de variance mesurée contre **notre propre** volatilité
réalisée vaut +0,9 %, −3,5 %, −3,2 %, −4,8 % aux échéances longues (les plus
stables). Elle est nulle à négative : il n'y a rien à récolter, et les
lectures positives aux échéances courtes changent de signe d'une échéance à
l'autre — c'est du bruit, pas une prime.

Le cas inter-venues méritait un test à part : un actif y garde des écarts
énormes (KAITO, |différentiel| au-dessus du seuil 18,2 % du temps) tout en
ayant une moyenne **signée négative**. C'est la signature d'un écart symétrique
et événementiel, pas d'un flux — on ne détient pas une position dans les deux
sens. Test économique, strictement causal (décider sur le différentiel déjà
payé à `t`, encaisser de `t` à `t+N`) :

| actif | coût A/R | brut réellement encaissé | net |
|---|---|---|---|
| KAITO | 33,5 | 0,19 – 4,83 | −28,6 à −35,1 |
| ZORA | 39,7 | 4,29 – 16,70 | −23,0 à −35,4 |
| MOVE | 50,2 | 0,48 – 9,05 | −41,1 à −49,7 |
| SOPH | 66,0 | 5,61 – 16,18 | −49,9 à −60,4 |

**0 cellule positive sur 26** (actif × seuil × tenue). Et le point décisif :
**le brut encaissé ne grandit pas avec le seuil d'entrée** — KAITO donne
+0,19 bps en entrant au-dessus de 20 bps/jour, et **−1,60 bps** en entrant
au-dessus de 100. Entrer sur des dislocations plus grandes rapporte *moins*.
Un flux persistant paierait davantage quand il est plus grand ; celui-ci a
déjà disparu au moment où l'on pourrait tenir la position.

**Tous ces flux atterrissent entre 2 et 16 %/an.** Ce n'est pas une
coïncidence : c'est la condition de non-arbitrage. Une position couverte
rapporte le coût du capital, parce que c'est ce que l'arbitrage impose.
L'objectif (×10 en un an ≈ 900 %/an) en est distant d'un facteur 55 à 180, et
cette distance est **maintenue par le marché**, pas par mon incapacité à la
trouver.

---

## 4. Et la branche risquée ?

Si aucun flux couvert n'y arrive, reste la position nue. Le calcul, sur nos
propres données :

```
g(L) = L·μ − L²σ²/2       maximum sur L :  g* = μ²/(2σ²)
```

Le terme quadratique est la traînée de variance : elle ne se négocie pas, elle
est une propriété de la volatilité mesurée. Avec les volatilités observées sur
500 jours (BTC 39,8 %, ETH 60,9 %, SOL 66,0 %, DOGE 74,4 %, XRP 64,6 %),
atteindre `g = ln(10) = 230 %/an` exige une **dérive espérée d'au moins 86 %/an**
sur le sous-jacent — et c'est le maximum sur tous les leviers possibles. Aucun
levier ne l'améliore ; au-delà de l'optimum, la traînée le dégrade.

Une dérive de 86 %/an n'est pas une hypothèse que nos données soutiennent, et
ce serait de toute façon une prédiction de prix — précisément ce que huit
mesures ont trouvé impossible à 1–6 bps contre 8–31 bps de coût.

---

## 5. Deux erreurs de ma part, dites en entier

**Signe de la sélection adverse.** Ma première mesure de tenue de marché
affichait une sélection adverse *négative* — le marché s'écartant en notre
faveur après chaque remplissage passif — et **+400 bps/jour**. C'est
l'invraisemblance économique qui l'a arrêtée, pas la statistique.

**Cadence de funding, deuxième round.** Mon crible inter-venues a fait
ressortir KAITO à **+95,5 bps/jour**, seul actif sur 142 à franchir le seuil.
En localisant les grandes valeurs dans le temps, elles se concentrent **toutes
sur cinq jours** (9–13 août 2026) — pendant lesquels OKX avait fait passer
KAITO de 4 h à 1 h de cadence de funding. Mon crible appliquait la cadence
médiane globale : surévaluation d'un facteur 4, exactement la classe d'erreur
déjà payée. **La cadence n'est constante ni entre instruments, ni dans le temps
pour un même instrument.** Hors de cette fenêtre, le différentiel KAITO vaut
±10 bps/jour, et sur les 20 derniers jours il vaut 2 %/an.

Et une fausse alerte, dite aussi : j'ai cru le fichier gzip de l'observatoire
corrompu. `_load_gzip_members` récupérait déjà la totalité des 1 173 717
enregistrements. Aucun défaut.

---

## 6. Réponse à la mission

**Dans l'univers accessible à PRISM, aucune structure mesurable n'atteint
×10 par an, et la raison est structurelle, pas circonstancielle :**

1. la capture **par traversée** est fermée par l'algèbre : `edge < c` sur huit
   mesures indépendantes, et la fréquence multiplie un nombre négatif ;
2. la capture **par flux** est plafonnée par la non-arbitrage : cinq mesures
   indépendantes trouvent 2–16 %/an, contre 159 %/an requis ;
3. la capture **par le risque** est plafonnée par la traînée de variance : il
   faudrait une dérive de 86 %/an, que rien ne soutient.

Ce sont trois plafonds différents, mesurés séparément, et ils ferment les
trois formes que peut prendre un gain de marché.

Ce que je ne ferai pas : maquiller une de ces trois branches en succès, ni
proposer une quatrième famille sans avoir de raison mesurée de croire qu'elle
échappe à l'un de ces trois plafonds.

Ce que le projet possède maintenant et n'avait pas : **un crible chiffré**
(`required_rate_bps_per_day`) qui élimine un candidat en une ligne, à partir de
trois grandeurs observables. Toute piste future se juge contre lui avant
d'écrire une seule ligne de stratégie.

---

*Aucun seuil, aucune hypothèse de coût, aucun remplissage n'a été modifié pour
rendre un chiffre plus présentable. LIVE reste désactivé. Aucun secret, aucune
clé, aucun ordre réel.*
