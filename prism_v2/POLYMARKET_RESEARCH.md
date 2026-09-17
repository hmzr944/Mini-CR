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
