# Polymarket — ce qui est vérifié, ce qui est revendiqué, ce qui manque

Notes de recherche. Chaque ligne est classée par **source**, pas par plausibilité.

---

## 🟢 Vérifié — documentation officielle

**Formule de frais** (`docs.polymarket.com/trading/fees`) :

```
fee = C × feeRate × p × (1 − p)        C = parts, p = prix
```

- **Les makers ne sont jamais facturés.** Seuls les takers paient.
- Taux par catégorie : Crypto 0,07 · Sports/Economics/Culture/Weather 0,05 · Finance/Politics/Tech 0,04 · **Géopolitique 0 %**
- Rebates makers : **15–25 %** des frais collectés, distribution quotidienne (Crypto 20 %, Finance/Politics 25 %)
- Les frais culminent à p = 0,5 et décroissent symétriquement vers les extrêmes

**Vérifié indépendamment dans l'API** : le champ `feeSchedule` publie ces paramètres **par marché**. Mon code les lit plutôt que de les coder en dur — et c'est nécessaire : sur mes 12 marchés suivis, `rate` vaut 0,03 / 0,04 / 0,05 et `rebateRate` 0,15 ou 0,25. Une table figée aurait été fausse.

**Formule des récompenses de liquidité** (`docs.polymarket.com/market-makers/liquidity-rewards`) :

```
S(v,s) = ((v − s)/v)² · b        v = spread max, s = spread effectif
Q_min  = max(min(Q_one,Q_two), max(Q_one/c, Q_two/c))     avec c = 3,0
```

- Époque = **10 080 échantillons** d'une minute (une semaine)
- Si le mid ∈ [0,10 ; 0,90] la liquidité unilatérale marque ; en dehors, elle doit être bilatérale
- Récompense = part de votre `Q_epoch` sur la somme de tous les makers × pool du marché

---

## 🔵 Vérifié dans du code public

| dépôt | ce qu'il implémente |
|---|---|
| `warproxxx/poly-maker` | microprix pondéré par la profondeur + EWMA du flux signé → fair value ; inventory skew ; EWMA de markout par fill élargissant le spread ; 5 régimes (QUIET / TRENDING / EVENT / REDUCE_ONLY / HALTED) ; sélection des marchés par revenu rebate+reward vs risque |
| `babajaha4/pmlp` | modélisation de file explicite ; markout à 300 s |
| `direkturcrypto/polymarket-terminal` | maker rebate HF, copy trading, sniper de carnet |

**Le plus instructif méthodologiquement est `pmlp`** :

> défaut : **100 % de la taille affichée est devant vous**, latence de cotation **250 ms**. `--queue-ahead 0` est décrit comme *« optimistic upper-bound, not the default »*.

> les récompenses de liquidité **« must not be treated as realized income »** — *« they are not settled rewards »*.

C'est exactement la discipline que j'applique : les récompenses restent un terme séparé et non acquis, jamais fondu dans le PnL net.

Autre point relevé : `poly-maker` indique qu'un **backtester de replay sur journaux capturés n'est pas encore construit**. Le dépôt public le plus sophistiqué n'a donc pas fait la validation hors ligne que cette mesure fait.

---

## 🔴 Non démontré — et je ne l'utiliserai pas

- **Aucune preuve indépendante de rentabilité nette durable** pour un seul de ces systèmes. Leurs auteurs l'écrivent eux-mêmes : *« can lose money … a research harness, not a guaranteed-profitable product »*.
- Les chiffres de rendement circulant sur des sites commerciaux (200–800 $/jour sur 50 k$, soit 146–584 %/an) sont du **marketing**, sans méthode ni holdout. Ils ne sont pas retenus.
- Que l'edge mécanique `YES + NO < 1` soit **capturable** après remplissage partiel et jambe unilatérale.

---

## ⚠️ Un résultat académique qui contraint la lecture

Une étude LBS/Yale sur 1,72 M de comptes : **3,14 %** seulement sont des gagnants « skilled » ; avec les market makers ils captent **plus de 30 %** des profits ; et **≈ 29 % sont des gagnants chanceux dont l'edge disparaît hors échantillon**.

Conséquence directe pour ma mesure : elle donne le **fill moyen**, pondéré par le volume. Un maker sélectif — qui ne cote que dans certaines conditions — n'est pas le maker moyen. C'est pourquoi l'analyse découpe par tranche de prix, de spread et d'intensité : si un edge existe, il est dans une tranche, pas dans la moyenne.

---

## Ce que ma mesure fait que les autres ne font pas

**Elle n'a besoin d'aucune hypothèse de file d'attente.**

Sur OKX je devais simuler les fills et supposer ma position dans la file — hypothèse généreuse, invérifiable. Ici je pars du **flux de trades réellement exécutés** : chaque trade implique un maker réel de l'autre côté, qui était effectivement servi. La position dans la file est déjà **réalisée** dans la donnée.

C'est ce qui rend ce jeu de données strictement supérieur pour cette question — et c'est aussi pourquoi `pmlp` doit, lui, choisir un défaut à 100 % de queue devant : il simule, je mesure.

**Limite honnête en contrepartie** : je mesure le maker *moyen*. Si la population des makers perd en moyenne pendant qu'un décile gagne, ma mesure le dira négative sans invalider l'existence d'un edge sélectif.

---

## La structure économique, telle qu'elle ressort

Les deux termes de revenu **ne se comportent pas pareil** :

| terme | proportionnel à | dilué par la concurrence ? | passe à l'échelle ? |
|---|---|---|---|
| **rebate** | vos **fills** | non | **oui** |
| **récompense de liquidité** | votre **temps coté** dans la bande | **oui** (pool fixe partagé) | **non** |

Conséquence : pour un objectif de revenus **importants**, seule la partie rebate compte. Or le rebate est proportionnel aux fills — donc à l'adverse selection qui les accompagne. **La question se réduit entièrement au fill**, ce qui est précisément ce que l'expérience mesure.

La récompense, elle, est un subside **plafonné en valeur absolue** : elle peut rendre une petite opération rentable, elle ne peut pas produire beaucoup de revenus.

---

## Sources

- [Polymarket — Fees](https://docs.polymarket.com/trading/fees)
- [Polymarket — Liquidity Rewards](https://docs.polymarket.com/market-makers/liquidity-rewards)
- [warproxxx/poly-maker](https://github.com/warproxxx/poly-maker)
- [babajaha4/pmlp](https://github.com/babajaha4/pmlp)
- [direkturcrypto/polymarket-terminal](https://github.com/direkturcrypto/polymarket-terminal)
- [Polymarket/poly-market-maker](https://github.com/Polymarket/poly-market-maker)

---

## 🟢 Vérifié — littérature académique

**Biais favori/longshot sur Polymarket** (588 M de trades, 2,48 M de comptes) :

| zone | rendement documenté |
|---|---|
| achats **< 0,10** | **−19,3 ¢ par dollar** |
| achats **≥ 0,90** | **+0,83 ¢ par dollar** |

Deux conséquences directes pour ce projet.

**1. Une piste est réfutée sans coût.** Le « sniper » à 1–3 ¢ de
`polymarket-terminal` achète exactement dans la zone qui perd 19,3 ¢ par
dollar. Famille fermée par la littérature, pas par une expérience à mener.

**2. Les deux sources de revenu ne se recouvrent pas.**

| prix p | rebate (rate = 0,04) | biais documenté |
|---|---|---|
| 0,05 | 0,048 ¢/part | −19,3 ¢/$ |
| **0,50** | **0,250 ¢/part** | non documenté |
| 0,90 | 0,090 ¢/part | +0,83 ¢/$ |

Le rebate culmine à p = 0,5 ; le biais favori est à p → 1. À p = 0,90 le biais
(≈ 0,75 ¢/part sur une part à 90 ¢) vaut **8 fois le rebate**. Une stratégie
qui maximise l'un renonce à l'autre. Le découpage par tranche de prix sépare
donc explicitement longshot et favori, au lieu de les réunir sous « extrême ».

**Mise en garde méthodologique de l'étude elle-même** : le signe s'inverse
selon le regroupement — les longshots perdent 6,3 ¢/$ à contrat égal mais
*gagnent* 4,1 ¢/$ quand les contrats liés sont d'abord groupés par événement
parent. Un résultat qui dépend à ce point de la convention d'agrégation
n'autorise aucune conclusion forte sur la zone intermédiaire.

Et l'étude note que le décile de comptes achetant le plus de longshots
**obtient des rendements semblables aux autres** : acheter des longshots n'est
pas une compétence distinctive, dans un sens comme dans l'autre.

### Source

- [The Favorite–Longshot Bias in Prediction Markets: Evidence from Polymarket](https://arxiv.org/abs/2609.12878)

---

## 🟢 Vérifié — le market making en marché de prédiction perd, en moyenne

**Étude Kalshi** (Bürgi, Deng, Whelan — plus de 300 000 contrats, données
transactionnelles) :

> **Les takers perdent près de 32 % en moyenne. Les makers perdent environ
> 10 %.**

C'est la preuve académique la plus directe disponible, et elle est négative.
L'avantage du maker sur le taker est réel — **22 points de pourcentage** — mais
il ne suffit pas à rendre l'activité profitable.

L'étude confirme aussi le biais favori/longshot **sur une seconde venue**, avec
un motif « bien plus marqué pour les takers que pour les makers ». Deux venues,
deux jeux de données indépendants, même conclusion.

### La nuance qui compte, et qui interdit de transposer directement

Les « makers » de cette étude sont définis par le **type d'ordre** — ils ont
posté des offres — et leur rendement est mesuré **jusqu'à la résolution**.

Or un market maker véritable ne détient pas jusqu'à la résolution : il se
remet à plat. Le −10 % mesure donc « les gens qui ont posté des ordres limités
et sont restés jusqu'au bout », ce qui est une **prise de position**, pas de la
tenue de marché.

Ma mesure est différente : markout à 60 s / 300 s / 1 800 s, c'est-à-dire le
sort d'un maker qui se remet à plat rapidement. Les deux chiffres ne sont pas
comparables, et je ne prétendrai pas que l'un valide l'autre.

Ce qu'il faut en retenir malgré tout : **la présomption de départ est
négative**, et un résultat positif de ma mesure devra être attaqué d'autant
plus fort.

### Une tension entre deux études à ne pas trancher à la légère

| source | conclusion sur les makers |
|---|---|
| LBS/Yale, Polymarket, 1,72 M comptes | market makers + « skilled » captent **plus de 30 %** des profits |
| Bürgi–Deng–Whelan, Kalshi, 300 k contrats | makers **perdent ~10 %** en moyenne |

Venues différentes, définitions différentes, périodes différentes. La première
parle d'une **minorité** qui capte une part des profits ; la seconde d'une
**moyenne** qui perd. Les deux peuvent être vraies simultanément : c'est même
la lecture la plus naturelle, et elle dit que l'edge, s'il existe, est dans la
queue de distribution et non dans la moyenne.

C'est précisément la limite que ma mesure porte : elle donne le **fill moyen**.

### Sources

- [Makers and Takers: The Economics of the Kalshi Prediction Market (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5502658)
- [CEPR DP20631](https://cepr.org/publications/dp20631)

---

## 🔴 Fermée — arbitrage de latence sur les marchés crypto courts

C'est la seule famille rencontrée jusqu'ici dont la **forme** correspondait
à l'objectif : petit capital, cycle de quelques secondes, donc capital qui
se recycle des centaines de fois par jour. Elle est fermée, et la raison
de sa fermeture est plus instructive que la famille elle-même.

**Hypothèse.** Les marchés crypto à 15 minutes de Polymarket cotent une
probabilité dérivée du prix spot. Si le carnet Polymarket réagit plus
lentement que Binance/Coinbase au spot, la probabilité affichée est
observablement en retard sur une information publique. Prendre le côté
que le spot a déjà tranché transfère de la valeur sans prévision.

**Statut de l'asymétrie : 🟢 elle a réellement existé.** Ce n'est pas une
hypothèse : la latence entre le spot et le carnet Polymarket était
mesurable, et des portefeuilles l'ont exploitée à grande échelle. La
presse rapporte un portefeuille passé de 313 $ à 414 k$ en un mois
(🟡 chiffre rapporté, non vérifié par moi — je ne l'utilise pas comme
preuve, seulement comme indication que la famille était exploitable).

**Pourquoi elle est fermée — et ce n'est pas l'usure naturelle.**
Polymarket a déployé en janvier 2026 un barème de frais *taker* dynamique
sur ces marchés, explicitement pour neutraliser cette stratégie. Le barème
n'est pas une taxe uniforme : c'est une contre-mesure calibrée sur la
stratégie.

```
fee = C × feeRate × p × (1 − p)        (docs.polymarket.com/trading/fees)
feeRate(Crypto) = 0.07

à p = 0,50 :  0.07 × 0,25 = 1,75 ¢ par part
              sur une part payée 50 ¢  →  3,5 % du capital engagé
```

La presse annonce ~3,15 % sur un contrat à 50 ¢ : cohérent avec la
formule. Le point décisif est la **forme** de `p(1−p)` : le frais est
**maximal exactement à 50/50**, c'est-à-dire précisément là où
l'arbitrage de latence opérait, et il s'annule aux extrêmes, là où
l'arbitrage n'avait rien à prendre.

**Conséquence quantitative.** Un aller-retour doit capturer **plus de
3,5 points de probabilité** avant spread, uniquement pour couvrir les
frais. Un retard de cotation de quelques secondes sur un sous-jacent
crypto ne déplace pas la probabilité d'un contrat 15 minutes de 3,5
points dans le cas général ; quand il le fait, c'est sur un mouvement
spot violent, c'est-à-dire exactement quand le carnet se vide et quand
la sélection adverse est maximale. La distribution des opportunités et
la distribution des coûts sont corrélées dans le mauvais sens.

**Biais découvert — le plus transférable de toute cette recherche.**
Une asymétrie observable, exploitable et *publiquement documentée* n'est
pas un actif : c'est une dette de la plateforme. La plateforme la voit
dans ses propres données avant n'importe quel chercheur externe, et elle
a un instrument — le barème — qu'aucun participant ne peut contrer. La
contre-mesure a été calibrée sur la signature de la stratégie, pas sur
le volume global. **Toute famille dont le rendement provient d'un défaut
corrigible par un paramètre de la venue a une durée de vie décidée par
la venue, pas par le marché.** Cela vaut pour les frais, les limites de
taux, les fenêtres de résolution et les règles de récompense.

**Ce que je ne conclus pas.** Je ne conclus pas que l'arbitrage de
latence est mort partout : il est mort *sur cette venue, sur ces
marchés, à ce barème*. Une venue sans frais taker sur des marchés
courts rouvrirait la famille — et fermerait de la même manière dès
qu'elle deviendrait coûteuse pour elle.

**Coût de la fermeture : 0 collecte, 0 expérience.** La recherche de
mémoire d'échec a suffi : la famille était déjà tuée publiquement avant
que j'engage la moindre donnée. C'est le protocole qui a fonctionné.

**Un fil qui reste ouvert, et qui pointe ailleurs.** Ces frais taker ne
disparaissent pas : ils **financent le Maker Rebates Program**. La venue
a donc organisé un transfert permanent taker → maker. Cela n'ouvre pas
la porte au market making naïf — ma mesure OKX (net −3,34 bps) et la
littérature Kalshi (makers −10 %) disent que la sélection adverse mange
plus que le rabais. Mais cela déplace la question : le rabais n'est pas
une remise commerciale, c'est un budget d'incitation alimenté par un
flux identifié. C'est une famille *extraction d'incitation*, pas une
famille *market making*.

### Sources

- [Polymarket — Fees](https://docs.polymarket.com/trading/fees)
- [Finance Magnates — Polymarket Introduces Dynamic Fees to Curb Latency Arbitrage in Short-Term Crypto Markets](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/)
- [Unchained — Polymarket Introduces Taker Fees in 15-Minute Markets](https://unchainedcrypto.com/polymarket-introduces-taker-fees-in-15-minute-markets/)
