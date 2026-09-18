# PRISM — où se trouve, ou ne se trouve pas, l'argent

**Cible fixée : 1 000 € → ~20 €/jour net, ambition ×5 en 60 jours.**
Traduite dans la métrique du projet :

| formulation | bps/jour de capital |
|---|---|
| 20 €/jour sur 1 000 € | **200** |
| ×5 en 60 jours | **272** |
| *(ancienne cible ×10/an, pour mémoire)* | 63,28 |

La cible est donc **3 à 4 fois plus exigeante** que celle contre laquelle les
mesures de ce document ont été faites. Le critère d'admission devient
`r ≥ 200/L + c/T`, soit avec le levier réellement mesuré (3,4× à un jour) et
25 bps d'aller-retour : **≈ 84 bps/jour de notionnel, ≈ 306 %/an**.

**Contrainte de capital à 1 000 € : aucune.** Les 482 instruments SWAP d'OKX
sont accessibles ; la marge minimale médiane vaut 0,006 % du capital, le
maximum 0,1 %. Ni la taille minimale ni la profondeur ne mordent à ce niveau.
Le capital ne limite rien — ce qui retire une excuse et laisse tout reposer sur
le rapport flux / coût.

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

## 3 ter. Là où rien n'arbitre : actions, matières, pré-IPO

La conclusion de la section 3 — *les flux couverts sont épinglés près du coût
du capital par l'arbitrage* — fait une prédiction falsifiable : **un
instrument que rien n'arbitre devrait y échapper.**

OKX cote trois classes que PRISM n'avait jamais inventoriées : des
perpétuels sur **actions et ETF tokenisés** (TSLA, NVDA, MU, MSTR, QQQ, SOXL,
SAMSUNG, SKHYNIX), sur **matières premières** (XAU, XAG, CL, BZ), et sur des
noms **pré-IPO** (SPCX, ZHIPU, UNITREE) qui n'ont *aucun* sous-jacent
négociable — ni spot, ni emprunt, ni livraison.

**La prédiction se vérifie.** Funding sur 92 jours, cadence lue période par
période :

| classe | p90 \|funding\| | demi-spread médian |
|---|---|---|
| crypto majeur | **3,0** bps/j (plafonné net) | 0,39 bps |
| actions | 10,6 | 0,26 |
| matières | 14,3 | 0,51 |
| pré-IPO | 19,5 | 1,55 |

SKHYNIX ressort à **16,55 bps/jour de moyenne (604 %/an)** avec un demi-spread
de 0,04 bps et 165 M$/jour de volume. Douze fois le plus gros flux couvert
mesuré ailleurs, pour un coût de transaction quasi nul.

**Et ça ne tient pas.** Trois raisons mesurées, dans cet ordre :

1. **Ce n'est pas une accumulation, ce sont des pointes.** SKHYNIX : 48 % de
   périodes positives, les 5 % plus grandes portent 35 % du cumul, et la
   **médiane par période vaut 0,000 bps**. La période typique ne paie rien.
   BZ et CL sont pires : 58 % et 76 % du cumul dans 5 % des périodes.

2. **Le PnL apparent est de la sélection de période.** Un court SOXL affiche
   +63 bps/jour sur la fenêtre — dont 540 USD de prix contre 38 de funding,
   parce que SOXL a chuté de 44 %. Le même calcul donne −5 bps/jour sur NVDA,
   −38 sur BZ, −37 sur CL. C'est la fenêtre, pas la stratégie.

3. **Ce que rien n'arbitre, rien ne couvre non plus.** Encaisser le funding
   exige d'être court ; couvrir par le sous-jacent est impossible (pas d'accès
   au KRX, pas d'emprunt). Reste la couverture par un perpétuel corrélé :

   | paire | corrélation | différentiel couvert | % périodes > 0 | résidu p90/h |
   |---|---|---|---|---|
   | SKHYNIX/SOXL | 0,698 | **12,84** bps/j | 50 % | 1,23 % |
   | SKHYNIX/MU | 0,761 | 12,61 | 46 % | 1,10 % |
   | SKHYNIX/SAMSUNG | 0,827 | 7,39 | 50 % | 0,96 % |
   | BZ/CL | 0,974 | −3,74 | 17 % | 0,18 % |

   Le meilleur flux couvert vaut **12,84 bps/jour contre 84 requis (0,15×)**,
   il est positif une fois sur deux, et le résidu de couverture non couvert
   vaut ~1 %/heure — deux ordres de grandeur au-dessus du flux.

**Un mécanisme testé et confirmé, qui ne suffit pas.** SKHYNIX et SAMSUNG
suivent des actions du KRX, fermé 17,5 h sur 24 et le week-end. Si le résidu
se concentrait en séance, détenir hors séance effondrerait le risque de prix.
Mesuré : écart-type du résidu **0,961 %/h en séance contre 0,550 %/h hors
séance, rapport 1,75×**. Le mécanisme est réel. Mais hors séance le résidu
vaut encore **51 %/an** — les deux perpétuels divergent à 51 %/an *alors que
leur sous-jacent ne peut pas bouger* — contre un flux de 7,09 bps/jour.

**Ce que cette section ajoute à la conclusion générale**, et c'est plus net
que ce qui précède : *ce qui n'est pas épinglé par l'arbitrage n'est pas non
plus couvrable, parce que couvrir c'est arbitrer.* L'absence d'ancrage qui
laisse le funding s'écarter est exactement ce qui empêche de neutraliser le
prix. Magnitude et couvrabilité ne sont pas seulement anti-corrélées : elles
sont **la même variable, vue des deux côtés**.

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

## 5 bis. La machine à capital, mesurée bout en bout

Le mandat (section 12) exige de chercher l'amélioration **avant** le signal :
meilleure allocation, meilleure réutilisation du capital, meilleur turnover.
Je n'avais jamais évalué qu'une position à la fois. Deux choses manquaient.

**1. Les jambes se compensent — quand elles sont dans la même devise.** Le
carry oppose un perp *inverse* (margé en coin) à un *linéaire* (margé en
USDT) : les jambes ne se compensent pas. Les paires non-crypto sont **deux
perps linéaires margés en USDT** : dans un compte en marge croisée, la perte
de l'une est couverte par le gain de l'autre. Le coussin se dimensionne alors
sur le *résidu*, pas sur chaque jambe.

**2. Le coussin se mutualise.** Mesuré sur 16 paires et 2 391 heures :
corrélation médiane des résidus **0,056** (p90 0,294). Coussin individuel
moyen **14,63 %** du notionnel contre **6,61 %** pour le portefeuille
équipondéré — **gain réel 2,21×** (contre 4,0× si les résidus étaient
parfaitement indépendants). Le levier passe de 5,37× à **9,43×**.

**3. L'allocation causale fonctionne.** Le différentiel de funding est un prix
*affiché*, pas une prédiction : allouer aux plus grands est une décision ex
ante légitime. Mesuré, en décidant sur le taux déjà payé à `t` et en
encaissant de `t` à `t+N` :

| k (paires retenues) | flux brut capté |
|---|---|
| 1 | **6,46** bps/jour |
| 4 | 4,54 |
| 8 | 2,59 |
| 16 (équipondéré) | 1,00 |

Concentrer capte **6,5× plus de flux**. La logique d'allocation est réelle.

**Et la machine complète donne ceci :**

| k | N périodes | net notionnel | levier | **net sur CAPITAL** |
|---|---|---|---|---|
| 1 | 1 | −58,94 | 6,52× | −384,50 |
| 2 | 3 | −14,93 | 8,37× | −125,03 |
| 2 | 6 | −9,46 | 7,54× | −71,38 |
| **2** | **12** | **+1,61** | **6,44×** | **+10,36** |

**3 cellules positives sur 20, 0 survivant à Benjamini-Hochberg**, et les
trois positives ont n = 21 entrées — indistinguables de zéro.

Le meilleur résultat que PRISM sache produire, tout compris, vaut donc
**10,36 bps/jour de capital = 1,04 €/jour sur 1 000 €**, contre 20 € visés.
**Distance : ×26.**

**Ce que cela établit sur le goulot.** L'allocation est bonne (×6,5), le levier
est bon (×2,21), le turnover est optimisé (balayé sur k et N). Aucun des trois
n'est le goulot. Le flux brut vaut 6,46 bps/jour contre 21,8 bps
d'aller-retour : **le levier multiplie un nombre négatif jusqu'à ce que la
détention soit assez longue pour amortir le coût, et à cette durée il a
lui-même chuté.** C'est une tenaille, et elle se referme à 10,36.

---

## 5 ter. Le plafond du mécanisme, mesuré — et son abandon

Le rendement d'une position couverte s'écrit `R(T) = L(T) × (r − c/T)`. Le coût
s'amortit quand on tient longtemps, mais le coussin de survie grandit, donc le
levier tombe. Tout dépend de **la vitesse à laquelle il grandit** — et je
l'avais extrapolée, pas mesurée.

Hypothèse testable : si le résidu d'une paire couverte **revient** au lieu de
dériver, le coussin cesse de croître et la tenaille s'ouvre.

**Critère d'abandon déclaré avant la mesure : α ≥ 0,45 → mécanisme abandonné.**

Mesuré sur 16 paires, 2 392 heures, résidus poolés, coussin lu comme le pire
cumul *atteint* dans la fenêtre :

| durée | coussin q95 | √T attendu |
|---|---|---|
| 1 j | 5,43 % | 5,43 % |
| 3 j | 9,79 % | 9,41 % |
| 7 j | 15,23 % | 14,38 % |
| 14 j | 20,03 % | 20,33 % |
| 20 j | 23,60 % | 24,30 % |

**α = 0,493 ± 0,012, intervalle à 95 % [0,469 ; 0,517], R² = 0,9964.**

0,500 est dans l'intervalle. Le résidu est une **marche aléatoire** à la
précision de la mesure. L'hypothèse est falsifiée nettement, pas de justesse.

Conséquence économique, avec la marge réelle (5,67 %) et la mutualisation
mesurée (2,21×) :

| durée | levier | R bps/jour | €/jour |
|---|---|---|---|
| 3 j | 9,90× | −8,0 | −0,80 |
| 7 j | 7,96× | 26,6 | 2,66 |
| **14 j** | **6,79×** | **33,3** | **3,33** |
| 20 j | 6,12× | 32,8 | 3,28 |

**Plafond mesuré : 33,3 bps/jour = 3,33 €/jour. Il manque un facteur 8,2.**

Le critère était déclaré d'avance et il est appliqué : **mécanisme abandonné.**
Aucune optimisation supplémentaire, y compris le fill maker — dont le plafond,
calculé avec ce coussin mesuré, ne dépasse pas 42,6 bps/jour (4,26 €/jour,
facteur 6,4 manquant).

**Ce que ce chiffre ferme.** Le plafond est le produit de deux bornes mesurées
dont aucune ne bouge : un flux capté de 6,46 bps/jour de notionnel, épinglé par
l'arbitrage, et un levier de 6,8×, fixé par α = 0,493. Pour atteindre la cible
il faudrait soit un flux 8× plus grand *et toujours couvrable* — or ce qui
n'est pas épinglé n'est pas couvrable —, soit un coussin qui cesse de croître —
or il croît en √T à 0,012 près.

---

## 5 quater. La forme « contrainte » : l'urgence ne paie pas

Les cinq formes fermées partageaient une structure : *une valeur est **publiée**
comme un taux, on immobilise du capital, on attend.* La directive finale pointe
une structure différente : **quelqu'un doit agir et paie une concession pour le
droit d'agir maintenant.**

L'observable de cette contrainte n'est pas un motif de prix — c'est la
**profondeur consommée**. Un ordre qui traverse plusieurs niveaux révèle un
agresseur insensible au prix. La concession qu'il paie est reçue par le côté
passif : c'est la question *qui reçoit la valeur aujourd'hui ?*

Ce n'est pas l'étude maker refaite. Celle-ci mesurait les remplissages **au
touch**, moyennés (−3,34 bps). Elle n'a jamais regardé les niveaux 2..N. Le
touch est où l'**information** frappe ; la profondeur est où l'**urgence**
frappe.

**Mesure** : 29 038 rafales agressives reconstruites, carnet incrémentiel
400 niveaux, 19 instruments, 287 118 messages.

| seau (impressions) | n | USD médian | concession | adv 1 s | **passif net** | t |
|---|---|---|---|---|---|---|
| 1 | 21 369 | 115 | **0,00** | 0,88 | −2,50 | −25,00 |
| 2–3 | 3 463 | 507 | **0,17** | 1,85 | −2,83 | −12,17 |
| 4–10 | 2 947 | 816 | **0,15** | 1,54 | −2,85 | −13,27 |
| >10 | 1 239 | 1 515 | **0,26** | 1,54 | −2,41 | −7,56 |

**0/16 positif, 0/16 survivent à Benjamini-Hochberg.**

**Le biais a été levé, et il allait dans l'autre sens que la crainte.** Ma
première lecture prenait le touch dans le carnet reconstruit, et le diagnostic
intégré a tiré : la concession ne croissait pas avec la profondeur, signe que
le carnet lu était post-trade. Recalculée depuis les **impressions seules** —
un acheteur remonte le carnet, donc sa première impression est la moins chère,
et cette lecture ne dépend d'aucun ordonnancement entre canaux — la concession
est **encore plus petite** : 0,26 bps au lieu de 0,65.

**Ce que cela établit.** La concession d'urgence existe, et elle est dérisoire :
l'écart de prix à l'intérieur d'une rafale de plus de dix impressions vaut
**0,53 bps**. Les carnets sont trop denses au touch pour qu'il y ait quoi que
ce soit à collecter. Contre 1,54 bps de sélection adverse, le côté passif rend
six fois ce qu'il reçoit.

**Sur ce marché, à cette taille, l'urgence ne se paie pas.** La valeur que la
théorie des contraintes prédit — un participant contraint qui concède — n'est
pas présente ici. Elle existe là où les carnets sont clairsemés ; sur OKX au
touch, elle vaut 0,26 bps.

---

## 5 quinquies. L'univers, mesuré au lieu d'être hérité

Toutes les mesures de ce document portent sur 21 instruments d'une seule
venue. Ce choix n'a jamais été justifié : il a été **hérité** de ce que PRISM
collectait. La directive finale exige que l'économie justifie l'univers.

**Paramètre structurel mesuré** : le *budget* d'un instrument = mouvement
quotidien (Parkinson sur haut/bas 24 h) rapporté au coût d'un aller-retour
(2 × frais taker publics + demi-spread observé). Il dit combien de fois par
jour le prix parcourt la distance qu'il faut payer pour agir. C'est une
condition **nécessaire, jamais suffisante**.

**3 419 instruments**, quatre venues joignables (OKX, MEXC, Bitget,
Hyperliquid), volume 24 h ≥ 50 000 USD.

| venue | n | budget médian | vs OKX |
|---|---|---|---|
| **OKX** | 1 002 | **21,3** | 1,00× |
| Bitget | 640 | 16,9 | 0,79× |
| MEXC | 1 604 | 14,5 | 0,68× |
| Hyperliquid | 173 | 11,1 | 0,52× |

**Changer de venue ne sert à rien** : OKX est déjà la meilleure. Mais la
dispersion est **à l'intérieur** : médiane 17,3, p90 52,5, p99 123,7, max 474.

**Et voici le biais, qui est le mien :**

| instrument étudié par PRISM | rang | centile | budget |
|---|---|---|---|
| SOXL-USDT-SWAP | 342/3419 | 90,0 | 52,5 |
| SOL-USDT-SWAP | 678 | 80,2 | 35,9 |
| ETH-USDT-SWAP | 1806 | 47,2 | 16,1 |
| **BTC-USDT-SWAP** | **2085** | **39,0** | **13,4** |

PRISM a passé son existence dans le quintile le plus pauvre de l'univers
accessible. Et le décile supérieur n'est pas de la camelote illiquide : son
demi-spread médian (2,10 bps) est **plus serré** que la médiane globale
(6,58), avec une volatilité 2,2× plus grande. ARB-USDT-SWAP : demi-spread
0,24 bps, σ 2 126 bps/jour, 296 M$/jour, **budget 15× celui de BTC**.

**Et cela ne change rien.** Un budget élevé dit qu'il y a de la place, pas que
le mouvement soit exploitable. Mesure de l'exposant α du déplacement avec
l'horizon — aucune règle, aucune position, aucun signal, seulement une
propriété du processus :

| population | budget | **α médian** | part α < 0,45 |
|---|---|---|---|
| haut budget (28 instruments) | 106 – 361 | **0,545** | 4 % |
| témoin (déjà étudiés) | 13 – 73 | **0,544** | 0 % |
| marche aléatoire | — | 0,500 | — |

**Un facteur 15 sur le budget correspond à 0,001 d'écart sur α.** L'échelle du
mouvement change ; sa structure non. Le rapport prévisible/imprévisible est
identique aux deux extrémités de l'univers accessible. Et α > 0,5 signifie
légèrement tendanciel, pas moyenne-réversif.

**Portée exacte de ce résultat** : il démontre qu'aucune capture fondée sur le
choix d'un horizon de détention ne peut moissonner ce budget, à aucune échelle
de l'univers accessible. Il ne démontre pas l'absence de structure
conditionnelle — α est une mesure inconditionnelle.

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
