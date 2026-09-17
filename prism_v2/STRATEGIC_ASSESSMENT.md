# Évaluation stratégique — ce que je recommande, en pleine responsabilité

J'ai pris le projet au mot : je n'ai traité ni son architecture, ni sa
direction, ni mes propres conclusions antérieures comme acquises. Voici ce
que j'ai testé cette session, ce que cela a changé, et ce que je pense qu'il
faut faire.

---

## 1. J'ai trouvé une erreur de méthode dans mon propre livrable précédent

Le livrable `ECONOMIC_ENGINE.md` concluait « objectif incompatible ». La
conclusion tenait, mais **son périmètre était trop étroit pour la justifier**.
Toutes mes mesures portaient sur des stratégies *toujours actives*, évaluées
en moyenne. Or une stratégie opportuniste — plate la plupart du temps,
déployée fort dans des dislocations rares — est invisible pour cette
méthodologie. J'avais mesuré la moyenne et conclu sur la queue.

J'ai donc testé la queue. C'était la bonne chose à faire, et le résultat est
plus intéressant que la conclusion.

---

## 2. Ce que j'ai mesuré cette session

### 2.1 Le différentiel de funding inter-venues

142 actifs cotés sur OKX **et** Hyperliquid, 45 jours, règle gelée et
committée avant toute analyse.

La famille avait, pour la première fois dans ce projet, **une réponse
crédible à « pourquoi n'est-ce pas déjà arbitré ? »** : la contrainte de
capacité. Un différentiel de 200 %/an sur un actif à 500 k$ d'open interest
n'intéresse aucun acteur institutionnel. C'était la seule configuration où
un petit capital est un avantage structurel.

**Le signal est réel.** À 100 %/an de seuil, on capture encore 38,7 %/an
annualisés sur 24 h, avec une décroissance propre. Ce n'est pas du bruit.

**Et la famille est fermée quand même.** Les 16 configurations sont
négatives ; en validation (jamais regardée) : 47 % de positifs, 0,21 bps/jour.
Même à **20× de levier, avec des fills maker sur les deux jambes et un spread
nul** — trois hypothèses indéfendables sur des actifs illiquides — le plafond
est **0,34×** l'objectif.

Un bug trouvé en route mérite d'être noté : **90 des 142 instruments OKX
paient toutes les 4 h, pas 8**. Ma cadence codée en dur divisait le taux
horaire par deux sur 63 % de l'univers. Le symptôme — un différentiel lu à
+3879 %/an — ressemblait à une opportunité spectaculaire. **Ce type d'erreur
fabrique toujours des opportunités, jamais des pertes.** C'est le signe
auquel on le reconnaît, et c'est exactement ce qu'un système autonome aurait
pris pour un signal.

### 2.2 Le plafond de la catégorie — la preuve la plus forte du dépôt

HLP (Hyperliquid) est le véhicule le mieux placé du marché crypto pour
extraire de la valeur sans prévision : market making, **backstop de
liquidation du protocole**, opérateurs professionnels, 187 M$ déployés. Son
PnL est réalisé et vérifiable on-chain — ni backtest, ni simulation, ni
README commercial.

```
perpMonth  30,4 jours réalisés  ->  4,38 bps/jour   =  16 %/an
                                     0,069x l'objectif
```

Je retiens délibérément la fenêtre mensuelle et j'écarte le « allTime » à
43,9 %/an : l'encours a crû d'un ordre de grandeur sur la période, donc
rapporter le PnL cumulé à un encours moyen surestime le rendement. Prendre
le chiffre flatteur aurait été le réflexe exactement inverse de celui
qu'exige ce dépôt.

**Si une stratégie systématique et neutre pouvait rendre beaucoup plus, HLP
le rendrait.**

---

## 3. La loi structurelle que six familles dessinent

```
rendement sur capital  =  edge par cycle  ×  cycles par jour
                          ──────────────────────────────────
                                multiple de capital
```

| Famille | bps/jour | × objectif |
|---|---:|---:|
| carry, netting 20× 🟡 | 10,48 | 0,166× |
| **HLP, extraction professionnelle** 🟢 | **4,38** | **0,069×** |
| carry inverse/linéaire 🟢 | 0,52 | 0,008× |
| funding inter-venues 🟢 | −1,19 | — |
| maker OKX 🟢 | −3,34 | — |
| taker microstructure OKX 🟢 | −10,35 | — |

Six familles indépendantes, trois venues, deux classes d'actifs, des
méthodes différentes. **Toutes atterrissent entre 0,01× et 0,35×.** Ce n'est
pas une coïncidence, et la raison est arithmétique :

> Une stratégie couverte encaisse une **prime de risque**. Les primes de
> risque sont bornées par l'arbitrage : c'est le prix d'un risque dont
> personne ne veut, pas un cadeau. Elles valent 2 à 40 %/an du notionnel. La
> couverture **double le capital** (deux jambes, aucun netting inter-venues).
> Le rendement sur capital est donc de 1 à 20 %/an.

Pour atteindre ×10 par an il faut 15 à 50× de levier — c'est-à-dire
réintroduire exactement le risque que la couverture avait retiré. **Le levier
ne crée aucun edge. Il multiplie l'edge et le risque dans la même
proportion.**

---

## 4. Réponse directe à l'objectif

**L'objectif tel que formulé — multiplier significativement le capital sur un
horizon court, de façon contrôlée — n'est pas atteignable par une stratégie
systématique neutre. Je ne peux pas vous donner raison sur ce point.**

Ce n'est pas un défaut d'exécution, de latence, d'infrastructure ou d'effort.
C'est que **« rendements élevés » et « de façon contrôlée » sont le même
paramètre lu deux fois.** Ce que le marché paie pour un risque couvert est
borné ; l'objectif demande davantage que cette borne.

Ce qui est réellement disponible, et que je peux défendre chiffre en main :

| Ambition | Faisabilité |
|---|---|
| **10–16 %/an**, neutre, couvert | 🟢 réel — c'est le régime HLP, atteint par des professionnels |
| **×1,5–2 par an** | 🟡 exige un levier agressif sur un livre couvert ; risque de liquidation réel |
| **×10 par an** | 🔴 aucune famille mesurée n'en approche ; exigerait 15 à 50× de levier |

Les trois routes qui échappent à l'arithmétique, pour être complet :

1. **multiple de capital < 1** — capital emprunté (flash loans). On-chain
   uniquement, guerre de gas.
2. **cycles par jour ≫ 1** — l'edge doit se refermer en secondes. Mesuré
   mort sur OKX (617 820 événements) et neutralisé par la venue sur
   Polymarket.
3. **revenu qui n'est pas une prime de risque** — extraction (MEV,
   liquidations) ou information réelle. **HLP est précisément cela, et
   plafonne à 16 %/an.**

La route 3 était le meilleur espoir. Le benchmark la ferme.

---

## 5. Ce que je recommande — et ce que j'abandonnerais

### À abandonner

**La chasse aux stratégies dans le crypto neutre.** Quatre familles fermées
par la mesure, deux par la littérature, un plafond de catégorie établi par
un tiers à 187 M$. Une cinquième famille du même type ne changera pas l'ordre
de grandeur. Continuer serait ignorer ma propre preuve.

**L'infrastructure d'exécution, pour l'instant.** La contrainte qui bloque
n'est pas l'exécution : c'est qu'il n'y a rien à exécuter. Construire un
moteur d'ordres maintenant optimiserait le mauvais goulot.

### À garder, et à formaliser

**Le pipeline de falsification.** C'est la seule chose qui a composé. Cette
session a fermé une famille entière en huit appels d'outils — reconnaissance,
gel de règle, collecte, mesure causale, fermeture — là où la construire comme
bot aurait pris des semaines. Et il a attrapé un bug (4 h vs 8 h) qu'un
système autonome aurait pris pour un signal et tradé.

Le système autonome que vous voulez doit donc être **un moteur de criblage
avant d'être un moteur d'exécution**. Sa boucle, telle qu'elle a fonctionné
aujourd'hui :

1. mémoire d'échec — qui a déjà essayé, avec quel résultat ;
2. test de **forme** avant test de rendement : bps/jour de capital, jamais
   ROI par trade ;
3. gel de règle **committé** avant la moindre analyse ;
4. mesure causale, cadences déduites et non supposées ;
5. fermeture documentée, avec le biais découvert.

C'est ce qui existe déjà en pièces détachées dans ce dépôt
(`capital_efficiency.py`, `funding_arb.py`, `cross_venue.py`, Failure
Memory, replay causal). Ce qui manque, c'est l'orchestration.

### La décision qui vous revient

Je ne construirai pas un système qui promet ×10 en sachant que la preuve dit
0,07×. Deux chemins honnêtes :

- **(A)** viser 10–20 %/an, neutre et contrôlé — réel, défendable, et c'est
  ce que font les professionnels avec bien plus de moyens ;
- **(B)** accepter du risque directionnel non couvert, où les rendements ne
  sont pas bornés par l'arbitrage — mais alors « de façon contrôlée » tombe,
  et rien dans ce dépôt ne montre un edge directionnel (617 820 événements,
  8 465 fills, aucun signal).

**(A) est un projet. (B) est un pari.** Le choix vous appartient, mais je ne
peux pas le déguiser en (A).

---

## 6. Le seul test que je ferais encore

Un criblage on-chain des liquidations de protocoles de prêt : fréquence,
bonus, et **concentration des capteurs**. Si 95 % des liquidations vont à une
poignée d'adresses dans le bloc même, la route 1 est fermée aussi, et la
démonstration est complète. Coût : quelques heures, zéro capital.

C'est le dernier endroit où l'arithmétique de la §3 pourrait ne pas
s'appliquer — parce qu'un flash loan met le multiple de capital **sous 1**.

---

*Aucun chiffre de ce document ne provient d'un backtest sans coûts, d'une
simulation sans file d'attente, ou d'un résultat sans holdout. Le benchmark
HLP est du PnL réalisé, vérifiable publiquement. Les hypothèses 🟡 sont
marquées comme telles, y compris dans le code qui les produit.*
