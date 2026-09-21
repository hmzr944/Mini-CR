# Audit indépendant de PRISM V33

Audit conduit le 20–21/09/2026 sur `hmzr944/Mini-CR`, branche
`claude/prism-v33-full-audit-p7dz9z`, HEAD `00a03a1`.

Règle appliquée dans tout ce document : **rapporté ≠ vérifié ≠ reproduit**.
Chaque nombre porte son statut. Les mesures que j'ai faites moi-même sont
marquées REPRODUIT et le script qui les recalcule est nommé.

---

## A. Résumé exécutif

### L'état réel de V33

V33 n'existe pas dans ce dépôt sous la forme qui a produit les performances
citées. **Tout l'historique git de V33 est une reconstruction post-incident.**
Le premier commit V33 (`15b67b7`, 18/07/2026) s'intitule « RECONSTRUCTION
post-incident 16/07 : perte du dossier projet ». Il n'existe aucun commit
antérieur. Le code qui a produit « +45,4 % », « +123,8 % » ou « +290,9 % OOS »
n'a jamais été versionné et n'est plus consultable.

Trois conséquences, toutes vérifiées :

1. **Le bot live V33 est structurellement incapable de passer un ordre.**
   `live_monitor_v33.py:51` fait `from okx_trader import OKXTrader`. Le fichier
   `okx_trader.py` **n'existe dans aucun commit de tout l'historique**. L'import
   échoue, `_OKXTrader = None`, `_init_trader()` retourne immédiatement, et
   toute la branche d'exécution est morte. Idem pour `sentiment_v33.py`.
   « Aucun ordre réel n'a été exécuté » n'est pas une décision de prudence :
   c'est une dépendance manquante.

2. **Le PnL réel est zéro, et c'est prouvé par artefact.** Les quatre fichiers
   d'état live commités dans `15b67b7` puis retirés du suivi portent tous
   `equity = 1000.0`, `total_trades = 0`, `total_pnl = 0.0`, `live_synced = false`.
   `scan_history_v33.json` contient 38 scans horaires du 16 au 18/07, 26 symboles
   chargés à chaque passage, et **0 signal sur les 38**.

3. **Aucun des quatre chiffres de performance du brief n'existe dans le dépôt.**
   Recherche exhaustive sur le contenu de **chaque version de chaque fichier de
   tous les commits** : `45,4 %`, `123,8 %` et `76 % APY` ont **zéro occurrence**.
   La seule correspondance pour « 90,9 % » est une sous-chaîne de `+290.9%`,
   codée en dur comme texte dans `telegram_bot_v33.py:414`. Ces chiffres ne sont
   pas réfutés — ils sont **sans aucun support dans le dépôt**.

### Le résultat central : V33 mesuré hors échantillon pour la première fois

J'ai refetché 13 mois de bougies OKX et fait tourner le moteur, puis coupé
l'échantillon au **22/07/2026 — date du dernier commit V33**. Tout ce qui
précède a pu servir à choisir les 14 paramètres ; rien de ce qui suit ne l'a pu.
La coupure vient de git, pas d'un résultat : c'est un test unique et
pré-spécifié.

| segment | N | WR % | PF | PnL € | annualisé |
|---|---|---|---|---|---|
| **IS** — avant le 22/07/26 (347 j) | 153 | **32,0** | **1,58** | **+5 046** | +5 307 €/an |
| **OOS** — après le 22/07/26 (60 j) | 29 | **6,9** | **0,17** | **−1 774** | **−10 790 €/an** |

Taux de réussite divisé par 4,6. Profit factor divisé par 9. **Deux gagnants
sur vingt-neuf.** La fréquence, elle, ne bouge pas (161 trades/an en IS, 176 en
OOS) : le moteur n'a pas cessé de trader, il a cessé de gagner.

> **P(≤ 2 gagnants sur 29 | p = 0,32) = 0,0015.**

Le rendement total de +327 % sur 13 mois est donc **intégralement porté par la
période d'ajustement**. C'est la mesure qui manquait au dossier, et elle est
sans ambiguïté.

### Ce qui est établi

- Un **look-ahead de 3 heures** existe dans `prism/strategy.py`, dans le filtre
  de tendance 4H qui conditionne les patterns C, D, MOM *et* le régime macro.
  Reproduit, isolé, et son contenu informationnel mesuré : **+1,11 bps de
  rendement horaire gratuit par barre, positif sur 18 symboles sur 18**. Lue
  causalement, cette porte sélectionne des barres dont le rendement moyen est
  négatif ; lue avec la fuite, il devient positif. **Elle ne dégrade pas un
  signal, elle en fabrique un.** **Mais — et c'est contraire à ce que
  j'attendais — le supprimer *améliore* le backtest (+3 272 → +4 779 €).** Le
  look-ahead est un défaut de correction, pas la source des performances
  annoncées. Je le rapporte tel quel.
- Le **funding n'est jamais appliqué** au PnL du backtest, alors que les
  positions sont tenues jusqu'à 96 h avec un levier de 10 à 18×.
- **≥ 14 paramètres ont été choisis en regardant le résultat**, et l'outil de
  balayage nocturne du projet (`tools/overnight_sweep.py:110`) promeut
  explicitement un paramètre **sur son profit factor OOS**. Le « OOS » de V33
  n'en est pas un.
- La **procédure « WFO 5 fenêtres » est citée 7 fois et n'est implémentée nulle
  part** dans V33. Aucune correction de tests multiples n'existe dans V33.
- La logique stratégie de V33 a **zéro test comportemental**. Le seul test qui
  la vise (`tests/test_single_source.py`) est un contrôle AST d'anti-duplication.

### Ce qui est réfuté ou non démontré

- **Le biais TP-avant-SL que j'avais signalé vaut exactement zéro** : sur
  26 symboles et 13 mois, aucune barre horaire n'a jamais couvert à la fois le
  take-profit et le stop. Signalé, quantifié, écarté.

- « ACCEPTED = 0 » n'est pas un moteur Shadow de V33 — **il n'existe aucun moteur
  Shadow dans ce dépôt** (`shadow` n'apparaît que 4 fois, dans du CSS de
  `dashboard.html`). Le phénomène décrit appartient au détecteur de flux de
  liquidation de PRISM V2, et ses trois causes ont déjà été trouvées et corrigées
  dans `5ef49cb`.
- « MOM : 0 victoire sur 10 trades, −100 € » n'est **pas du live**. Le message de
  `572cfe3` dit « rejeu live-realistic », et précise que MOM avait été retenu
  après un « audit 11 variantes, OOS validé sur seulement N=3 ».
- Le « meilleur plafond mesuré » de **33,3 bps/jour** de `RAPPORT_FINAL.md` ne
  porte **pas** sur la crypto. Il est calculé avec `r = 6,46 bps/jour`, issu de
  `alloc_policy.py`, dont l'univers est **16 paires d'actions et matières
  tokenisées** (SKHYNIX/SAMSUNG, NVDA/MRVL, TSLA/NVDA, QQQ/SOXL, XAU/XAG, BZ/CL).
  Sur ces instruments, le dépôt mesure lui-même une profondeur de 10 à 814 USD.

### La conclusion technique et économique principale

Le dépôt ne contient **aucune preuve d'une stratégie capable de gains
significatifs à court terme**, et les mesures que j'ai refaites moi-même ne
produisent pas cette preuve non plus.

Une seule famille survit à l'audit avec une espérance nette positive, un
mécanisme causal nommable, une capacité suffisante et un risque de queue
survivable : le **carry inverse/linéaire même sous-jacent, limité à BTC et ETH**.
Dans mes propres mesures, sur un panier de quatre paires (ADA, LTC, SOL, BCH),
elle vaut **≈ 21 %/an à un levier prudent de 10×** et **≈ 58 %/an à 24×**, pour
des détentions d'environ 30 jours en exécution **maker** — en taker elle est
négative. Et elle est **invalidable avec les données disponibles** : l'API OKX
plafonne l'historique de funding à 95 jours, ce que j'ai vérifié en le
refetchant (286 relevés exactement, sur les 24 instruments).

Ce n'est pas « ×5 en 60 jours ». C'est l'ordre de grandeur honnête de ce que ce
compte, avec ces données publiques, peut viser.

---

## B. Tableau des faits

| # | Affirmation | Statut | Preuve | Limite |
|---|---|---|---|---|
| 1 | Aucun ordre V33 n'a été exécuté en réel sur OKX | **VÉRIFIÉ** | `live_state_v33*.json` (4 fichiers, `15b67b7`) : `total_trades=0`, `equity=1000.0`, `live_synced=false` | Ne couvre que le 16–18/07. Les états postérieurs sont gitignorés et perdus. |
| 2 | Le PnL réel est nul | **VÉRIFIÉ** | idem | idem |
| 3 | La cause est une dépendance manquante, pas une décision | **VÉRIFIÉ** | `okx_trader.py` : 0 occurrence sur `git log --all --name-only` | On ignore si le fichier existait hors git avant le 16/07. |
| 4 | Les perfs historiques n'ont pas de validation OOS indépendante | **VÉRIFIÉ** | `overnight_sweep.py:110` sélectionne sur `pf_oos` ; aucun WFO implémenté ; aucune correction de multiplicité | — |
| 5 | Normalisation utilisant des informations globales de période | **RÉFUTÉ** | Tous les seuils de régime sont glissants : `bbw.rolling(40).quantile(.15)`, médianes 30 j, `b-24`, `b-48`, `peak90` | Le biais existe, mais sa forme est différente (cf. #6). |
| 6 | Look-ahead dans les indicateurs | **REPRODUIT** | `prism/strategy.py:88-96`. `resample("4h").last()` étiquette le seau à son DÉBUT et renvoie la clôture de sa DERNIÈRE heure ⇒ la barre 00:00 lit la clôture de 03:00. Démontré numériquement. | Amplitude mesurée : **+1,11 bps/barre, 18/18 symboles**. Mais le supprimer **améliore** le backtest (ligne 20c) : le biais est réel et n'est pas la source des performances. |
| 7 | Essais multiples de paramètres | **VÉRIFIÉ** | `overnight_sweep.py` balaye **39 valeurs par passage**, en tâche nocturne récurrente, sur une seule fenêtre OOS fixe, sans correction | Nombre de nuits exécutées inconnu ⇒ nombre d'essais réel ≥ 39. |
| 8 | Sélection d'actifs après observation des résultats | **VÉRIFIÉ** | `PATTERN_D_WHITELIST` = 5 symboles sur 26 ; `PATTERN_C_BLACKLIST`, `PATTERN_S_BLACKLIST` ; en-tête de `strategy.py` : « retrait DOGE/SOL/LINK/NEAR jamais validés, DOGE avait WR=0 % IS » | — |
| 9 | Frais, spread, slippage réduisent la rentabilité théorique | **PARTIELLEMENT RÉFUTÉ** | `COMMISSION=0.001` = 10 bps/côté = **20 bps A/R**, soit **2× le taker OKX réel** (5 bps/côté). Le backtest est *conservateur* sur les frais. | Mais le **funding n'est jamais appliqué** : `FUNDING_URL` est déclaré et jamais utilisé. |
| 10 | Le moteur Shadow a rejeté toutes les opportunités (ACCEPTED=0) | **RÉFUTÉ pour V33** | Aucun moteur Shadow dans le dépôt. Le vrai fait V33 : **0 signal sur 38 scans**, ce qui est le comportement attendu (cf. §F). | — |
| 11 | Références nulles sur minutes inactives, fenêtres trop exigeantes | **VÉRIFIÉ — mais c'est PRISM V2, et c'est corrigé** | `5ef49cb` décrit les 3 défauts et les corrige | L'auteur signale lui-même que le correctif 1 a modifié une définition après avoir vu les résultats exploratoires. |
| 12 | MOM désactivé après 0/10 et −100 € | **VÉRIFIÉ, mais ce n'est pas du live** | `572cfe3` : « rejeu live-realistic », « audit 11 variantes, OOS validé sur seulement N=3 » | Avec RR=3, le seuil de rentabilité est WR=25 % ; P(0/10 | p=0,25) = 5,6 %. Preuve faible, et non corrigée pour 11 variantes. |
| 13 | La stratégie historique n'est pas un edge démontré | **VÉRIFIÉ** | Somme de #1, #4, #6, #7, #8 | — |
| 14 | +45,4 %, +123,8 %, +76 % APY | **INTROUVABLE** | 0 occurrence dans le contenu de tous les fichiers de tous les commits | Non réfuté : simplement sans support. |
| 15 | « +90,9 % » | **RÉFUTÉ (erreur de transcription probable)** | Unique correspondance : `+290.9%`, chaîne codée en dur dans `telegram_bot_v33.py:414` | Ce `+290,9 %` est lui-même annoncé sur **31 trades**. |
| 16 | 1 024 tests passent | **REPRODUIT (et sous-estimé)** | `pytest tests/ -q` → **1 079 passed, 85 subtests** en 16 s | **Aucun** ne teste le comportement de V33. |
| 17 | Le carry inverse/linéaire a un différentiel positif mesurable | **REPRODUIT** | Funding refetché par mes soins ; reproduit la table `DIFFERENTIEL_BPS_JOUR` de `carry_capital.py` à 0,04 bps près sur 12 coins sur 12 | Fenêtre de 95 jours (plafond API OKX), identique à celle du projet ⇒ **pas d'OOS indépendant possible**. |
| 18 | Le flux capté concentré vaut 6,46 bps/jour | **NON REPRODUIT sur la crypto** | Sous la règle exacte de `carry_alloc.py` (tri par `abs`, retournement des jambes), mon maximum est **1,60 bps/jour** — 4× moins. Le maximum de la table du projet lui-même est ETC à 1,577. | Résolu : le 6,46 vient de `alloc_policy.py`, univers **non-crypto** (16 paires actions/matières). |
| 19 | Le levier est bridé par le coussin (« la tenaille ») | **RÉFUTÉ pour le même sous-jacent** | Avec α = 0,236 mesuré par le projet, le coussin à 14 j vaut 0,441 % du notionnel contre une marge initiale de 4 % : **le levier est plat à ~24× quelle que soit la durée**. La tenaille est une propriété de la couverture par corrélation (α = 0,493), pas du carry crypto. | — |
| 20 | Capacité suffisante pour le carry | **VÉRIFIÉ, et restrictif** | 18 relevés de carnet espacés, 5 niveaux, jambe **contraignante** (le min des deux) : ADA 52,9 k$, DOGE 20,0 k$, ETH 18,6 k$, SOL 15,5 k$, LTC 13,8 k$, BCH 8,5 k$ — mais **420 à 915 $ pour LINK, ETC, DOT, XRP** | **Correction d'une de mes propres mesures** : un premier relevé unique donnait BTC 118 k$ et ETH 513 k$. C'était un instantané non représentatif — sur 18 relevés la médiane BTC est 70,9 k$ et ETH 18,6 k$. Un seul relevé de carnet ne mesure rien. |
| 20b | **V33 perd de l'argent hors échantillon** | **REPRODUIT** | Coupure au 22/07/26 (dernier commit V33, date fixée par git) : IS PF = 1,58 sur 153 trades, **OOS PF = 0,17 sur 29 trades, 2 gagnants**. P(≤2/29 \| p=0,32) = 0,0015 | OOS = 60 jours, un seul régime de marché. L'amplitude peut varier ; le signe et la significativité, non. |
| 20c | Le look-ahead gonflait les performances | **RÉFUTÉ** | Le supprimer **améliore** le PnL 13 mois : +3 272 → +4 779 € | Le biais reste réel au niveau de la porte (+1,11 bps/barre) et cause une divergence backtest/live. Il n'est simplement pas la source des chiffres annoncés. |
| 20d | Le biais TP-avant-SL gonflait les performances | **RÉFUTÉ** | Variantes B et C identiques au centime : aucune barre horaire sur 252 432 n'a couvert TP **et** SL | — |
| 20e | La whitelist d'actifs porte du biais de sélection | **REPRODUIT** | La retirer : PnL 4 529 → 3 218 €, N 178 → 300, maxDD 47,2 → 54,0 % | Cohérent avec une sélection *a posteriori*, sans la prouver à elle seule. |
| 21 | Le résidu du carry est survivable à fort levier | **REPRODUIT, avec une réserve majeure** | 375 j de bougies : pire dérive 14 j = 60 bps (BTC), 45 bps (ETH) contre 417 bps de seuil de liquidation à 24× | **Mais** le 10/10/2025 à 21:00 UTC, le résidu a explosé **simultanément sur les 11 paires** : XRP −661, FIL −932, DOGE −376 bps. La mutualisation du coussin (×2,21, mesurée à corrélation 0,056) **disparaît exactement dans l'événement qui compte**. |

---

## C. Chronologie complète des commits V33

L'historique du dépôt compte 89 commits. **Le clone initial de cette session
était superficiel (`--depth 50`) et masquait toute l'ère V33** ; il a fallu
`git fetch --unshallow` pour la voir. C'est la première chose qu'un audit doit
vérifier, et elle n'était pas visible.

V33 occupe 15 commits, du 18 au 22 juillet 2026. Aucun commit n'existe avant.

| commit | date | changement réel de comportement | portée | conséquence |
|---|---|---|---|---|
| `15b67b7` | 18/07 | Reconstruction initiale : 18 fichiers, 6 890 lignes. `live_monitor_v33.py` récupéré *verbatim* d'un conteneur Docker survivant ; `prism/strategy.py` idem. | live + état | **Prouvée** : c'est le point zéro de l'histoire. Tout ce qui précède est perdu. |
| `595f2dd` | 18/07 | Docker + `.gitignore` de l'état runtime. Retire les 4 `live_state` du suivi. | infra | **Prouvée** : après ce commit, plus aucune trace d'exécution n'entre dans git. C'est pourquoi le « 0/10 MOM » du 21/07 n'a pas d'artefact. |
| `3b08bc8` | 18/07 | **Reconstruction de `backtest_v33.py` (644 lignes) sans sauvegarde source.** L'en-tête le dit : « reconstruit à partir des fragments et valeurs documentés dans la mémoire projet », « NON garanti bit-exact ». | backtest | **Prouvée** : le moteur qui produit les chiffres n'est pas le moteur qui les a produits. |
| `1fec16d` | 18/07 | Restauration `prism_factory`, `incubator`, watchdogs, `validation_report`, `test_single_source`. | outils | Infra. |
| `aac8ac0`, `f2e756c` | 18/07 | Dé-suivi de `live_logs/` et `__pycache__`. | infra | **Les logs live sont définitivement hors git.** |
| `3f94eeb` | 19/07 | Single source of truth : `live_monitor_v33.py` perd 331 lignes et importe `prism.strategy`. | live + backtest | **Prouvée** : à partir d'ici, le look-ahead 4H de `strategy.py` est partagé par les deux. |
| `f56ce3e` | 19/07 | Restauration `overnight_sweep`, `recalib_check`, `robustness_suite`. | recherche | **C'est le commit qui réintroduit la sélection sur OOS** (`overnight_sweep.py:110`). |
| `0ae2be9` | 19/07 | 3 bugs corrigés dans `backtest_v33.py` (+123/−33). `chaos_regime` totalement absent ; `btc_1h_filter` accepté en paramètre et jamais appliqué ; panique de marché définie et jamais implémentée. | backtest | **Prouvée et décisive** : le message dit que `btc_1h_filter` « laissait passer ~2× trop de trades vs référence (196 vs 93) ». La fidélité de la reconstruction a été établie **en ajustant le moteur jusqu'à retrouver un N mémorisé**. C'est du calage sur une statistique de souvenir, pas une récupération de source. |
| `52e6061` | 20/07 | 7 corrections (+234/−92) : seuil ADX `bear_macro` 22→20, `ranging_regime` remis sur médiane BBW 30 j, `btc_4h_bull` ajouté. | backtest | Même nature : convergence vers un comportement rappelé. |
| `4cb0ee0` | 20/07 | Pattern MOM : restructuration collecte-tri-allocation + gates manquants. | backtest | Précède immédiatement la désactivation de MOM. |
| `a9b11f6` | 20/07 | Encodage console Windows. | infra | Nul. |
| `0ee71de` | 21/07 | Trace les dégradations silencieuses de détection de régime. | live | Observabilité. |
| `572cfe3` | 21/07 | **`MAX_POS_MOM` 2 → 0.** | live | **Rapportée, non reproductible** : les 10 trades cités viennent d'un rejeu dont aucun artefact n'est versionné. |
| `46d32b6` | 22/07 | Digest hebdomadaire Telegram. | infra | Nul. Dernier commit V33. |

Après le 22/07, silence jusqu'au 16/09, où `5ac6d2e` ouvre PRISM V2 — une
réécriture complète qui n'importe rien de V33. **V33 est abandonné depuis deux
mois, pas en cours de développement.**

### Ce qui manque, précisément

| manquant | conséquence sur les conclusions |
|---|---|
| `okx_trader.py` | Aucun audit de plomberie d'exécution n'est possible : le code n'existe pas. |
| `sentiment_v33.py` | Le biais `micro_adj` du live (16 occurrences, 0 dans le backtest) n'est pas évaluable. |
| Historique git antérieur au 18/07 | Les paramètres « validés IS+OOS » ne sont pas traçables à une expérience. |
| `live_logs/`, `backtest_results/`, états postérieurs au 18/07 | Les 10 trades MOM et les 7 pertes C de juillet ne sont pas vérifiables. |
| `prism_v2/data/events` (264 Mo), `data/observatory` (27 Mo), `data/funding`, `data/polymarket` | **Gitignorés et absents du conteneur.** Les mesures 1, 2 et 3 de `SCAN_DONNEES.md`, le markout maker et `fee_floor` ne sont **pas reproductibles**. Seuls `candles_1h.json`, `margin_tiers.json` et `liquidations/fwd_okx.json.gz` survivent. |

---

## D. Audit des biais

### D.1 — Look-ahead de 3 heures sur le filtre de tendance 4H (REPRODUIT)

**Emplacement** : `prism/strategy.py:88-96`, dans `compute_indicators`.

```python
df_4h    = df[["close"]].resample("4h").last().dropna()
ema20_4h = df_4h["close"].ewm(span=20, adjust=False).mean()
df["ema20_4h"] = ema20_4h.reindex(df.index, method="ffill")
```

`resample("4h")` étiquette chaque seau par son **début**, tandis que `.last()`
renvoie la clôture de sa **dernière** heure. Le `ffill` attribue donc à la barre
de 00:00 une valeur qui n'est connue qu'à 03:00.

Démonstration numérique (closes 0..11, horaires) :

| barre | close connue | `ema20_4h` lue par le bot |
|---|---|---|
| 00:00 | 0 | **3** ← clôture de 03:00 |
| 01:00 | 1 | **3** |
| 02:00 | 2 | **3** |
| 03:00 | 3 | 3 |

Le bloc journalier (`resample("1D")`) a la même structure : **jusqu'à 23 h de
look-ahead** sur `ema50d` / `ema200d`.

**Pourquoi c'est grave** : `asset_4h_bull = ema20_4h > ema50_4h` n'est pas un
indicateur décoratif. C'est la **condition directionnelle** des patterns C, D et
MOM, *et* la base de `bear_macro` / `bull_macro` qui décident si le portefeuille
a le droit d'être long ou short.

**Amplitude mesurée** (18 symboles, 13 mois, `tools/audit/lookahead_effect.py`) :

Rendement moyen de la barre **suivante**, conditionnellement à la porte
`ema20_4h > ema50_4h`, selon qu'elle est lue avec la fuite ou causalement.

| symbole | désaccord | rdt h+1 \| fuite | rdt h+1 \| causal | **edge de fuite** |
|---|---|---|---|---|
| DOGE | 1,68 % | +0,196 | −1,623 | **+1,818** |
| ARB | 1,50 % | +2,200 | +0,495 | **+1,704** |
| SUI | 1,44 % | +0,456 | −1,234 | **+1,691** |
| INJ | 1,38 % | +1,742 | +0,168 | **+1,574** |
| ADA | 1,93 % | −1,227 | −2,747 | **+1,520** |
| OP | 1,38 % | −0,035 | −1,548 | **+1,514** |
| NEAR | 1,32 % | +2,989 | +1,597 | **+1,392** |
| UNI | 1,56 % | +2,437 | +1,118 | **+1,319** |
| DOT | 1,62 % | −1,469 | −2,753 | **+1,284** |
| XRP | 1,44 % | −0,648 | −1,792 | **+1,144** |
| ETH | 1,62 % | +0,383 | −0,499 | **+0,883** |
| LTC | 1,81 % | −0,861 | −1,716 | **+0,856** |
| LINK | 1,56 % | +0,297 | −0,478 | **+0,776** |
| AVAX | 1,99 % | −0,618 | −1,308 | **+0,691** |
| SOL | 1,56 % | +0,643 | −0,027 | **+0,669** |
| ATOM | 1,50 % | +0,186 | −0,466 | **+0,652** |
| BTC | 1,44 % | +0,456 | +0,127 | **+0,329** |
| TRX | 1,75 % | +0,364 | +0,203 | **+0,161** |
| **moyenne** | **1,58 %** | **+0,416** | **−0,694** | **+1,110** |

(rendements en bps)

La porte ne diffère que 1,58 % du temps, mais **quand elle diffère, elle a
raison** : **+1,11 bps** de rendement horaire gratuit, **positif sur 18 symboles
sur 18**. Le signe est unanime — ce n'est pas du bruit d'échantillonnage.

Noter le renversement : lue causalement, la porte sélectionne des barres dont
le rendement moyen est **négatif** (−0,694 bps) ; lue avec la fuite, il devient
**positif** (+0,416 bps). **Le look-ahead ne dégrade pas un signal réel : il en
fabrique un là où il n'y en a pas.**
Ce n'est pas assez pour créer un edge à lui seul face à 28 bps d'aller-retour —
mais c'est exactement le type de petit biais cohérent qui fait basculer un
backtest marginal du bon côté, et il se cumule sur la sélection *et* la direction.

**Correctif** : étiqueter chaque seau à l'instant où sa valeur devient
observable (`index + freq − 1 h`) avant le `ffill`. Implémenté dans `causal.py`,
non appliqué au dépôt.

**Conséquence en live** : en direct, le dernier seau 4H est incomplet, donc
`.last()` renvoie la clôture de l'heure courante. Le bot live et le backtest
n'utilisent donc **pas la même valeur d'EMA 4H** — c'est un mécanisme concret de
divergence backtest/live, distinct du look-ahead lui-même.

### D.2 — Sélection des paramètres sur l'OOS (VÉRIFIÉ)

`tools/overnight_sweep.py:110` :

```python
if v != cur and r["pf_oos"] > ref["pf_oos"] * 1.05 \
   and r["pf_is"] >= ref["pf_is"] * 0.95 and r["n_is"] >= ref["n_is"] * 0.7:
    candidates.append((name, v, r))
```

Un paramètre est promu **parce que son profit factor hors échantillon dépasse
la référence de 5 %**. C'est la définition même de la contamination de l'OOS, et
c'est le critère de promotion câblé dans l'outil nocturne du projet.

**Ampleur** : 8 paramètres × 39 valeurs au total par passage —
`ADX_MAX_C` (5), `TIME_STOP_H` (6), `RR_RATIO` (6), `SQUEEZE_BARS_C` (4),
`COOLDOWN_BARS` (5), `NEAR_ATH_THRESH` (4), `MAX_POS` (6), `MAX_MARGIN_RATIO` (3)
— sur **une seule fenêtre OOS fixe**, en **tâche récurrente nocturne**, donc
39 × N nuits essais effectifs sur le même OOS.

**Aucune correction de multiplicité** : `grep -rniE 'benjamini|bonferroni|fdr'`
sur `tools/`, `prism/` et la racine renvoie **zéro**. (PRISM V2 en a une ; V33
n'en a jamais eu.)

**Le garde-fou annoncé n'existe pas** : « validation WFO 5 fenêtres avant
déploiement » est cité **7 fois** dans `overnight_sweep.py` et `recalib_check.py`,
et **aucun code de walk-forward n'existe dans V33**.

### D.3 — Le registre documentaire du sur-ajustement (VÉRIFIÉ)

Les paramètres portent leur propre aveu en commentaire :

| paramètre | fichier | justification inscrite dans le code |
|---|---|---|
| `ADX_MIN_C` 20→18 | `strategy.py:26` | « audit 25/06 : +371 % vs +345 % REF » |
| `_ADX_1BAR` | `strategy.py:40` | « audit 24/06 : +339 % vs +249 % avec 2 barres » |
| `VOL_RATIO_MOM` | `strategy.py:33` | « audit 08/07 : PF 0.84→2.40 » |
| `SCORE_MIN_V` = 999 | `strategy.py:38` | « désactivé v33.9 : N=1 WR=0 % OOS_3M » |
| `_score_size_mult` | `strategy.py:163` | « validé IS+OOS 4/4 » |
| `RR_RATIO` 5→4 | `live_monitor:190` | « +117pp OOS_3M 4/5 » |
| `TIME_STOP_H` 72→96 | `live_monitor:193` | « +360 % vs +345 % » |
| `MAX_POS` 6→8 | `live_monitor:201` | « +40k vs +36k à DD identique » |
| `SCORE_MIN_D` 80→85 | `live_monitor:205` | « grids corrigés 27/06 » |
| `RISK_PCT_D` 10→16 % | `live_monitor:208` | « +61pp OOS_3M » |
| `RISK_PCT_R` | `live_monitor:239` | « plein Kelly validé OOS » |
| `SCORE_MIN_S` 80→82 | `live_monitor:263` | « WFO audit 07/07 : +6,2 % OOS » |
| `S_MARGIN_CAP` 500→600 | `live_monitor:264` | « optimal 5/5 fenêtres » |
| `PATTERN_S_BLACKLIST` | `live_monitor:271` | « audit 03/07 » |

**Quatorze paramètres, chacun fixé sur un résultat observé.** La configuration
V33 n'est pas une hypothèse testée : c'est le point d'un espace de recherche
exploré avec la réponse sous les yeux.

### D.4 — Sélection d'actifs après observation (VÉRIFIÉ)

- `PATTERN_D_WHITELIST` = 5 symboles retenus sur 26.
- `PATTERN_C_BLACKLIST` = 3, `PATTERN_S_BLACKLIST` = 5.
- En-tête de `strategy.py` : « PATTERN_D_BULL_EXTRA aligné sur WHITELIST
  (retrait DOGE/SOL/LINK/NEAR jamais validés, **DOGE avait WR=0 % IS**) ».

Le critère de retrait est explicitement la performance observée en échantillon.

**Biais de survivance en plus** : les 26 `SYMBOLS` sont des actifs qui cotaient
encore en juillet 2026. Aucun actif délisté ou effondré sur la période de 13 mois
n'est présent. Le backtest ne peut donc pas perdre sur un actif disparu.

### D.5 — Coûts : un biais dans chaque sens (VÉRIFIÉ)

- **Conservateur** : `COMMISSION = 0.001` appliqué en `notional * COMMISSION * 2`
  = **20 bps d'aller-retour**, soit **le double du taker OKX réel** (5 bps/côté).
- **Optimiste** : **le funding n'est jamais débité.** `FUNDING_URL` est déclaré
  ligne 45 et n'apparaît nulle part ailleurs. Des positions tenues jusqu'à 96 h
  à 10–18× de levier ne paient aucun financement.
- **Optimiste** : les sorties sont remplies **exactement au prix du stop**
  (`exit_price = sl`), avec 3 bps de slippage. Le risque de gap au-delà du stop
  n'est pas modélisé, et **aucune liquidation n'est simulée**.
- **Optimiste** : quand une barre couvre à la fois le TP et le SL, le code teste
  le TP d'abord (`if hi >= tp ... elif lo <= sl`) et **accorde le TP**. Avec
  `RR_RATIO = 4`, le TP est 4× plus loin que le SL : une barre qui touche les deux
  a bien plus probablement touché le SL en premier. Effet chiffré au §E.

### D.6 — Spécification du risque (VÉRIFIÉ)

`margin = equity * RISK_PCT * combined_scale * mult`, avec `RISK_PCT = 0.28` et
`mult` jusqu'à 2,0 ⇒ marge jusqu'à **56 % du capital sur une seule position**,
à un levier de 10 à 15× ⇒ **notionnel de 5,6 à 8,4× le capital sur un trade**.
Avec un stop à 2,5 %, une seule sortie perdante coûte **14 à 21 % du capital**,
alors que `DAILY_LOSS_CAP = 0.12`. Le plafond journalier est testé **avant
ouverture** et n'entraîne aucune fermeture : **un seul stop peut donc le
traverser de 1,75×.** Le nom `RISK_PCT` désigne une fraction de marge, pas une
fraction de risque — et l'écart entre les deux est d'un facteur 10 à 15.

---

## E. Audit économique

### E.1 — Ce que coûte réellement un aller-retour

| poste | valeur dans le backtest V33 | valeur réelle OKX | écart |
|---|---|---|---|
| Frais taker, par côté | 10 bps (`COMMISSION=0.001`) | 5 bps | backtest **2× trop cher** |
| Frais maker, par côté | non modélisé | 2 bps | — |
| Slippage entrée | 5 bps | inconnu, jamais mesuré | — |
| Slippage sortie | 3 bps | inconnu | — |
| Spread | non modélisé séparément | inclus dans le slippage supposé | — |
| **Funding** | **0 — jamais appliqué** | ~1 bp/8 h de notionnel | **manquant** |
| Gap au-delà du stop | non modélisé (remplissage exact au stop) | réel | **manquant** |
| Liquidation | **non modélisée** | réelle à 10–18× | **manquant** |

Le modèle de coût de V33 est donc **conservateur sur les frais et optimiste sur
tout le reste**. Il ne double-compte rien (le slippage est appliqué au prix,
les frais au notionnel, une seule fois chacun) — sur ce point précis le code est
propre.

### E.2 — Reproduction contrôlée du backtest V33

J'ai refetché 13 mois de bougies horaires OKX pour les 26 symboles
(2025-08-08 → 2026-09-20, 9 799 barres, via `backtest_v33.fetch_symbol` — donc
le chemin de données du dépôt lui-même) et fait tourner le moteur livré, puis
quatre variantes ne différant chacune de la précédente que par **un seul
changement nommé** (`tools/audit/variant_sweep.py`).

| variante | N | WR % | PF | PnL € | equity | maxDD % | rendement |
|---|---|---|---|---|---|---|---|
| **A.** moteur livré, tel quel | 182 | 28,0 | 1,30 | +3 272 | 4 272 | 48,0 | **+327,2 %** |
| **B.** A + look-ahead 4H/1D supprimé | 178 | 29,2 | 1,37 | +4 779 | 5 779 | 47,2 | **+477,9 %** |
| **C.** B + SL testé avant TP sur la même barre | 178 | 29,2 | 1,37 | +4 779 | 5 779 | 47,2 | +477,9 % |
| **D.** C + funding 1 bp/8 h sur le notionnel | 178 | 29,2 | 1,37 | +4 529 | 5 529 | 47,2 | +452,9 % |
| **E.** D + whitelist/blacklists retirées | 300 | 30,7 | 1,20 | +3 218 | 4 218 | 54,0 | +321,8 % |

**Trois résultats contredisent ce que j'attendais, et je les donne tels quels.**

1. **Le look-ahead n'inflate pas le backtest — le supprimer l'améliore** (+3 272
   → +4 779 €). Le biais est réel et mesurable à l'échelle de la porte (+1,11
   bps/barre, §D.1), mais il ne se traduit pas en meilleure sélection de trades
   pour une stratégie de cassure à 4:1 et 28 % de réussite. **Le look-ahead est
   un défaut de correction, pas la source des performances annoncées.**
2. **Le biais TP-avant-SL vaut exactement zéro.** Les variantes B et C sont
   identiques au centime : sur 26 symboles et 13 mois, **aucune barre horaire
   n'a jamais couvert à la fois le TP et le SL**. Avec `RR_RATIO = 4` et un stop
   à 0,6–2,5 %, il faudrait une amplitude horaire de 3 à 12 %. J'avais signalé
   ce biais au §D.5 ; il est **quantifié et écarté**.
3. **Le funding manquant coûte 5,2 % du PnL** (−250 € sur 4 779), sous une
   hypothèse plate de 1 bp/8 h. Réel, mais mineur.
4. **La whitelist fait un vrai travail** : la retirer fait passer N de 178 à 300
   et le PnL de 4 529 à 3 218 €, avec 7 points de drawdown en plus. C'est la
   signature attendue d'une sélection *a posteriori* : les actifs ont été
   retenus **parce qu'**ils performaient.

### E.2bis — Le test qui tranche : IS / OOS daté par git

Aucun des résultats ci-dessus n'est interprétable tant qu'on ne sait pas quelle
part de la fenêtre a servi à choisir les paramètres. **Le dernier commit V33 est
daté du 22/07/2026.** Tout ce qui précède a pu influencer les 14 paramètres ;
**rien de ce qui suit ne l'a pu.** La coupure n'est donc pas choisie sur un
résultat — elle est datée par l'historique git. C'est un test unique et
pré-spécifié, sans problème de multiplicité.

| moteur | segment | N | WR % | PF | PnL € | PnL annualisé |
|---|---|---|---|---|---|---|
| **livré** | IS — avant 22/07/26 (347 j) | 153 | **32,0** | **1,58** | **+5 046** | +5 307 €/an |
| **livré** | **OOS — après 22/07/26 (60 j)** | 29 | **6,9** | **0,17** | **−1 774** | **−10 790 €/an** |
| causal | IS (347 j) | 150 | 33,3 | 1,69 | +7 067 | +7 434 €/an |
| causal | **OOS (60 j)** | 28 | **7,1** | **0,18** | **−2 288** | **−13 921 €/an** |

**Le taux de réussite s'effondre de 32,0 % à 6,9 %. Le profit factor de 1,58 à
0,17. Deux gagnants sur vingt-neuf.**

Ce n'est pas un arrêt de l'activité : la fréquence est **inchangée** (161
trades/an en IS, 176 en OOS). Le moteur a continué à trader exactement au même
rythme, et il a perdu presque à chaque fois.

Ce n'est pas non plus du bruit d'échantillonnage. Sous l'hypothèse nulle que le
taux de réussite OOS égale le taux IS :

> **P(≤ 2 gagnants sur 29 | p = 0,32) = 0,0015**

Et même contre le seuil de rentabilité théorique du pattern C (RR = 4 ⇒ WR
d'équilibre ≈ 25 %) : **P = 0,013**.

En OOS, les trois patterns perdent **tous** : C −384 €, D −1 241 €, S −149 €.

> **Voilà ce qu'aucun document du dépôt ne contenait : une mesure propre de
> V33 sur des données que personne n'a pu regarder en le calibrant. Le
> +327 % total est intégralement porté par la période d'ajustement. Sur les
> 60 jours qui suivent le dernier commit, la stratégie perd de l'argent à un
> rythme qui ruinerait le compte en moins d'un an.**

Réserves, dites en entier : l'OOS ne dure que **60 jours et 29 trades**, et il
tombe dans un régime de marché unique. Un échantillon plus long pourrait
nuancer l'amplitude. Il ne peut pas, en revanche, ramener PF = 0,17 à PF = 1,58
sans que le p = 0,0015 ait été une coïncidence. Et le sens du résultat est
cohérent avec tout le reste de l'audit : 14 paramètres choisis sur le résultat,
39 valeurs balayées par nuit sans correction, une whitelist sélectionnée après
coup, et zéro procédure de walk-forward implémentée.


### E.3 — Divergences backtest / live, mécanisme par mécanisme

| mécanisme | présent en backtest | présent en live | effet |
|---|---|---|---|
| EMA 4H / 1D | seau **complet** (avec 3 h de futur) | dernier seau **incomplet** | **Les deux n'évaluent pas la même valeur.** Divergence structurelle, indépendante du look-ahead. |
| `micro_adj` (sentiment) | 1 occurrence | 16 occurrences | Le live ajuste les scores avec un module (`sentiment_v33.py`) **absent du dépôt**. Le backtest ne peut pas le reproduire. |
| Réconciliation des positions fantômes | absente | présente (`live_monitor:582`) | Sans effet : `_trader` est toujours `None`. |
| Exécution réelle | remplissage parfait au prix du stop | **aucune** (module manquant) | Le live est un simulateur, pas un exécuteur. |
| Cadence | une barre horaire par itération | un scan horaire réel | Le backtest suppose une évaluation à la clôture exacte ; le live scanne à ~3 min après l'heure. |

### E.4 — L'économie de PRISM V2, corrigée

Le chiffre mis en avant par `RAPPORT_FINAL.md` — « meilleur plafond jamais
mesuré : **33,30 bps/jour** » — ne porte pas sur la crypto.

J'ai remonté sa chaîne de calcul. Il provient de `buffer_alpha.py:128`
(`IMR, r_flux, c_tak = 0.0567, 6.46, 21.8`), où `r_flux = 6,46` vient de
`alloc_policy.py`, dont l'univers est déclaré ligne 26 :

```python
PAIRES = [("SKHYNIX","SAMSUNG"),("SKHYNIX","MU"),("SKHYNIX","SOXL"),
          ("SAMSUNG","MU"),("MU","SOXL"),("NVDA","MRVL"),("NVDA","SOXL"),
          ("MRVL","MU"),("INTC","MU"),("TSLA","NVDA"),("MSTR","CRCL"),
          ("AAOI","NBIS"),("CRWV","NBIS"),("QQQ","SOXL"),("XAU","XAG"),
          ("BZ","CL")]
```

Ce sont **16 paires d'actions et de matières tokenisées**, couvertes par
**corrélation** entre **sous-jacents différents** — ce qui explique mécaniquement
que leur résidu soit une marche aléatoire (α = 0,493) : c'est l'écart entre deux
titres distincts. Et `RAPPORT_FINAL.md` §12 mesure lui-même la profondeur de ces
instruments à **10–814 $**.

J'ai reproduit le calcul et confirmé l'arithmétique : avec r = 6,46 et le coussin
α = 0,493, on retrouve **exactement 33,29 bps/jour à T = 14 j**. Le calcul est
juste ; c'est son étiquette qui est trompeuse.

**Sur la crypto, sous la règle exacte de `carry_alloc.py`** (tri par valeur
absolue du différentiel, retournement des jambes du côté qui encaisse, blocs
disjoints), mon maximum sur 12 paires et 286 règlements est **1,60 bps/jour** —
**4× moins que 6,46**. Le maximum de la table du projet lui-même (`ETC` à 1,577
bps/jour) est du même ordre que ma mesure, et **4,1× inférieur au 6,46**
qu'il utilise ailleurs.

| k | N | jours | blocs | brut bps/j | t | net après 21,8 bps |
|---|---|---|---|---|---|---|
| 1 | 3 | 1,0 | 95 | **1,599** | 8,19 | −20,20 |
| 1 | 6 | 2,0 | 47 | 1,378 | 6,08 | −9,52 |
| 1 | 12 | 4,0 | 23 | 0,948 | 3,58 | −4,50 |
| 1 | 24 | 8,0 | 11 | 0,818 | 2,60 | −1,91 |
| 6 | 24 | 8,0 | 11 | 0,569 | 3,22 | −2,16 |
| 12 | 24 | 8,0 | 11 | 0,354 | 2,12 | −2,37 |

**Conclusion de cette section, et elle va dans le sens défavorable** : la famille
carry crypto est **plus pauvre** que ce que le rapport final laisse entendre, pas
plus riche. Le 33,3 bps/jour n'a jamais été un plafond crypto. Le plafond crypto
réel, à ces horizons courts, est **négatif net**. Il ne devient positif qu'en
maker et au-delà de 60 jours de détention (§H.2).

### E.5 — Une structure robuste que le projet n'a pas exploitée

Un fait favorable, mesuré, et absent des documents : au **niveau du portefeuille**,
la concentration du différentiel disparaît.

| | par coin (médiane des 12) | portefeuille équipondéré des 12 |
|---|---|---|
| moyenne | 0,20 bps/règlement | **0,2249** |
| médiane | 0,08 | **0,1903** (≈ moyenne) |
| part des 5 % de règlements les plus forts | 27–938 % | **18,9 %** |
| règlements positifs | 45–60 % | **77,3 %** |
| t | 0,17–9,92 | **12,90** |

Coin par coin, la moyenne est portée par la queue droite (ADA : moyenne 0,387,
**médiane 0,000**). Au niveau du panier, médiane ≈ moyenne, 77 % des règlements
positifs, t = 12,9. **La diversification transforme une loterie en flux.**

Cela ne sauve pas l'économie — le flux reste petit face au coût, et la capacité
impose de se limiter à BTC+ETH qui paient le moins — mais c'est la propriété
statistique la plus saine mesurée dans tout le projet, et elle mérite d'être
notée plutôt que perdue.

---

## F. Attribution de « ACCEPTED = 0 »

### F.1 — Le fait V33, et sa vraie forme

Il n'y a **pas** de moteur Shadow dans ce dépôt. Le fait vérifiable est plus
simple et plus net : sur les 38 scans horaires conservés dans
`scan_history_v33.json` (16/07 12:00 → 18/07 06:00), avec **26 symboles chargés
à chaque passage**, le bot a produit **0 signal** et **0 trade**.

L'opportunité ne meurt donc pas à `COSTS`, ni à `RISK`, ni à `ALLOCATION`.
**Elle meurt à `SIGNAL`.** Aucune opportunité n'a jamais atteint l'étage
économique.

### F.2 — Attribution quantitative, porte par porte

J'ai rejoué les portes de `prism/strategy.py` dans leur ordre exact sur chaque
couple (symbole, barre) de l'historique (`gates.py`). Chaque barre est imputée à
la **première** porte qui la rejette.

**Pattern C — 252 432 barres-symbole, 26 symboles, 13 mois**

| porte | rejets | part |
|---|---|---|
| `C1` BBW < q15 sur 3 barres consécutives | 212 415 | **84,15 %** |
| `C2` expansion au-dessus de q15 sur la barre courante | 7 737 | 13,16 % |
| `C3` ADX en hausse de plus de 1,5 | 971 | 1,65 % |
| `nan` (warmup indicateurs) | 312 | 0,53 % |
| `C5` vol_ratio ≥ 1,90 | 128 | 0,22 % |
| `C4` ADX ≥ 18 | 125 | 0,21 % |
| `C6` cassure de bande + alignement 4H | 33 | 0,06 % |
| **signaux bruts** | **221** | **0,0875 %** — 1 pour 1 142 barres |

**Pattern D — 9,68 % de signaux bruts** (1 pour 10 barres ; `D1` ADX non montant
tue 52,2 %).

**Pattern MOM — 65 signaux, 0,0257 %** — **1 pour 3 884 barres-symbole**
(`M1` EMA non empilées 65,9 %).

### F.3 — Le verdict

Sur la fenêtre live réellement observée (38 scans × 26 symboles = **988
barres-symbole**), le nombre attendu de signaux Pattern C bruts est :

> 988 × 0,000875 ≈ **0,87 signal**

— et cela **avant** le filtre de score (≥ 70), les filtres de régime, la
blacklist et le cooldown. Sous une loi de Poisson, **P(0 signal) ≈ e^−0,87 ≈ 42 %**.

**Observer zéro signal en 38 heures n'est pas une anomalie. C'est l'issue la
plus probable pour une stratégie aussi sélective.** Il n'y a pas de bug à
chercher : la cause est la conjonction `C1 ∩ C2`, qui exige qu'une même série
soit sous son 15ᵉ percentile glissant pendant 3 barres **puis** au-dessus à la
barre suivante. Cette conjonction élimine à elle seule 97,2 % des barres.

MOM est pire d'un facteur 34 : **1 signal pour 3 884 barres-symbole**. C'est
cohérent avec les 10 trades sur 4 mois cités par `572cfe3` — et c'est aussi la
raison pour laquelle ces 10 trades ne peuvent pas trancher : la fréquence du
pattern rend tout verdict statistique inatteignable en moins de plusieurs années.

### F.4 — Ce que le look-ahead ne cause pas

En rejouant les mêmes portes avec la version causale (26 symboles, 252 432
barres), les taux de signal bougent à peine :
**C 0,0875 % → 0,0840 %, D 9,677 % → 9,439 %, MOM 0,0257 % → 0,0257 %** (65
signaux dans les deux cas).

**Le look-ahead n'est donc pas la cause de la rareté des signaux.** Il n'altère
pas *combien* de signaux sortent, il altère *lesquels* — c'est un biais de
qualité et de direction (§D.1), pas de fréquence. Les deux défauts sont
indépendants et doivent être traités séparément.

### F.5 — Ce qui reste non instrumenté

Le `scan_history_v33.json` n'enregistre que `signals: []`. Il ne journalise
**aucun compteur de rejet par porte**. L'attribution ci-dessus n'a été possible
qu'en rejouant le code hors ligne. Si V33 devait reprendre, c'est la première
instrumentation à ajouter — sans elle, « 0 signal » est indiscernable d'un
« moteur muet par bug », et le dépôt a déjà connu exactement ce faux négatif
côté V2 (`5ef49cb` : « zéro déclenchement, pour toujours, sur n'importe quelle
donnée… faux négatif indiscernable d'un vrai résultat »).

---

## G. Audit de la plomberie d'exécution

La question posée était : « peut-on conserver l'infrastructure et remplacer la
logique stratégique ? »

**Réponse : il n'y a pas d'infrastructure d'exécution à conserver.**

`okx_trader.py` n'existe dans **aucun commit de tout l'historique**
(`git log --all --pretty=format: --name-only | grep -c okx_trader` → `0`).
`live_monitor_v33.py` l'importe ligne 51 dans un `try/except ImportError` qui
met silencieusement `_OKXTrader = None`. Tout le chemin d'exécution
(`place_order_hybrid`, `get_equity`, `get_positions`, réconciliation des
positions fantômes) appelle des méthodes d'un objet qui **n'a jamais existé dans
le dépôt**.

| élément | testé unitairement | vérifié en démo | observé en réel | statut |
|---|---|---|---|---|
| Création/modification/annulation d'ordres | non — code absent | non | non | **INEXISTANT** |
| Ordres conditionnels / Hard SL | **aucun code** : `grep -rniE 'order-algo|hard_sl|conditional|trigger'` → 0 dans V33 | non | non | **INEXISTANT** |
| Gestion des erreurs API, timeouts | code absent | non | non | **INCONNU** |
| Idempotence, doublons | code absent | non | non | **INCONNU** |
| Réconciliation local/exchange | `live_monitor:582-594` détecte les positions « ghost » | non | non | **CODE PRÉSENT, JAMAIS EXÉCUTÉ** (`_trader` toujours `None`) |
| Précision quantités/prix, tick, taille min | code absent | non | non | **INCONNU** |
| Allocation de marge, limites de positions | présent côté simulation | non | non | **SIMULÉ SEULEMENT** |
| Circuit breakers | présents (`EQUITY_FLOOR`, `DAILY_LOSS_CAP`, pauses) | non | non | **SIMULÉ SEULEMENT** |
| Perte de connexion / redémarrage | état JSON + `.bak` + `.dbak` quotidiens | non | non | **NON DÉMONTRÉ** |
| Remplissages partiels | code absent | non | non | **INCONNU** |
| Séparation simulation / démo / réel | `OKX_DEMO` lu depuis `.env`, défaut `true` | non | non | **NON DÉMONTRÉ** |

### Sur le Hard SL, précisément

Il n'y a **pas** de fonction Hard SL dont on pourrait discuter l'efficacité :
il n'y en a aucune. Le stop de V33 est **exclusivement logiciel**, évalué dans
la boucle de scan. Or cette boucle tourne **au rythme de la barre horaire**
(`scan_history_v33.json` : un scan par heure). Avec un levier de 10 à 18×, une
position sans ordre stop côté exchange est exposée pendant **jusqu'à une heure**
entre deux évaluations, et n'est pas protégée du tout si le processus meurt.
C'est un défaut de sécurité, pas une imperfection de modélisation.

### Couverture de test réelle

`pytest tests/ -q` → **1 079 passed, 85 subtests passed** en 16 s. **REPRODUIT.**

Mais la répartition est sans appel : ces tests couvrent PRISM V2. Le seul test
visant V33 est `tests/test_single_source.py`, et c'est un **contrôle AST** qui
vérifie qu'aucune fonction stratégie n'est redéfinie hors de `prism/strategy.py`.
Il n'exécute **aucune** logique de pattern, d'indicateur, de sizing ou de sortie.

> **La logique stratégique de V33 a zéro test comportemental.** Un look-ahead de
> 3 heures dans la fonction la plus centrale du système a survécu à la
> reconstruction, à la migration single-source et à 1 079 tests verts.

---

## H. Évaluation des pistes

### H.1 — Le module 15 m « Liquidity Sweep » : déjà testé, et fermé

Cette piste n'est pas neuve pour le dépôt. Elle a été instruite en septembre
sous le nom « flux de liquidation forcé », avec un protocole gelé (`3471e46`),
une collecte en direct (`6614d56`), trois défauts d'implémentation corrigés
(`5ef49cb`) et un verdict (`e05d90b`).

Le verdict porte sur le **mécanisme causal**, pas sur un seuil :

> 73 événements datés par leur horodatage réel, 5 instruments, prix toutes les
> 6 s. **Le prix bougeait déjà dans la direction de la liquidation pendant les
> 60 s qui l'ont précédée : +14,55 bps, t = 18,59, sur 100 % des événements.
> Après, il ne reste que +2,97 bps.**

Autrement dit : la liquidation n'est pas une force qui déplace le marché, c'est
un **symptôme retardé** d'un mouvement déjà advenu et déjà intégré. Conditionner
sur une liquidation revient à conditionner sur un mouvement passé — la famille
« réversion après mouvement », déjà fermée sur 617 820 événements.

Le travail a aussi attrapé l'artefact qui rendait la piste séduisante : le délai
de publication OKX est de **2 434 s en médiane**, et 77 % des liquidations sont
vues plus de 30 s après les faits, si bien que « la fenêtre *avant* contenait de
l'après ». Le « surdépassement de 45 à 81 bps » mesuré sur bougies 1 minute
n'était pas l'impact de la liquidation : c'était le mouvement qui l'avait
provoquée, sur la même minute.

**Ce que cela implique pour un module 15 m.** Un « liquidity sweep » en bougies
15 minutes est, mécaniquement, la même chose vue à une résolution **60 fois plus
grossière** que celle qui a permis de trancher. Il ne peut pas distinguer la
cause de l'effet, puisque le retournement de causalité se joue dans les 60 s qui
précèdent. Sans nouvelle mesure de microstructure, une règle de chandeliers
rebaptisée « sweep » n'a **aucun mécanisme** derrière elle.

Économie finale mesurée : **+2,97 bps de capture maximale contre 10–11 bps de
coût, soit −8 bps net.** Aucun horizon ni seuil ne comble cet écart.

> **Recommandation : ne pas construire ce module.** La question a été posée
> correctement et répondue négativement, avec le contrôle de délai qui aurait
> pu invalider la réponse.

### H.2 — Le carry inverse/linéaire même sous-jacent : la seule famille ouverte

**Mécanisme.** Sur OKX, `X-USD-SWAP` (perpétuel inverse, margé en coin) et
`X-USDT-SWAP` (perpétuel linéaire, margé en USDT) portent le même sous-jacent
mais ont **deux carnets distincts, avec deux clientèles distinctes** : le
linéaire est le défaut du retail à effet de levier, l'inverse exige de détenir
le coin en collatéral et attire des détenteurs et des couvreurs. Deux demandes
de levier différentes ⇒ **deux taux de funding différents**, durablement.

**Pourquoi ça persiste.** L'arbitrage exige de poster de la marge **dans deux
devises à la fois**, sans compensation entre les jambes. C'est un coût
d'immobilisation, pas un risque — et c'est exactement la friction qui empêche
l'écart d'être refermé.

**Ce n'est pas une prédiction.** `CARRY_FINDING.md` établit, et je le confirme
sur mes propres données, que `fundingRate == realizedRate` : le taux est
**publié avant le règlement**. On n'estime rien ; on encaisse un flux annoncé.

**Neutralité vérifiée algébriquement** (`CARRY_FINDING.md`) : le PnL USD de
l'inverse est linéaire en `P_sortie/P_entrée`, exactement comme celui du
linéaire ; les deux s'annulent, sans rééquilibrage. Seul subsiste le funding
différentiel.

#### Mes mesures (REPRODUIT — funding refetché, 12 paires, 286 règlements, 95 j)

D'abord, la vérification : ma table reproduit `DIFFERENTIEL_BPS_JOUR` de
`carry_capital.py` **à 0,04 bps près sur 12 coins sur 12**. Le chiffre du projet
est bon.

Ensuite, ce que le projet n'avait pas mis côte à côte :

| coin | différentiel (bps/j) | profondeur de la jambe **contraignante** (médiane, 5 niveaux) | demi-spread inv / lin |
|---|---|---|---|
| **ADA** | **1,162** | **52 885 $** | 3,24 / 2,16 |
| ETC | 1,535 | 915 $ | 4,34 / 0,58 |
| BCH | 1,136 | 8 545 $ | 2,92 / 1,95 |
| FIL | 0,836 | 4 960 $ | 3,64 / 0,52 |
| DOT | 0,757 | 645 $ | 5,22 / 0,44 |
| XRP | 0,722 | 565 $ | 1,23 / 0,35 |
| LINK | 0,595 | 420 $ | 2,35 / 0,39 |
| **LTC** | **0,547** | **13 770 $** | 2,12 / 0,85 |
| **SOL** | **0,358** | **15 515 $** | 0,45 / 0,44 |
| **ETH** | **0,307** | **18 625 $** | 0,019 / 0,019 |
| **BTC** | **0,125** | **70 870 $** | 0,006 / 0,006 |
| DOGE | 0,018 | 20 040 $ | 0,57 / 0,56 |

> Profondeurs : **médiane de 18 relevés espacés**, pas un instantané. Ma
> première mesure, faite sur un relevé unique, donnait ETH à 513 k$ contre une
> médiane réelle de 18,6 k$ — je l'ai corrigée. Le dépôt a déjà été piégé
> exactement ainsi (`RAPPORT_FINAL.md` : « le demi-spread de 0,01 bps était un
> artefact d'instantané »).

> **Le différentiel est globalement inverse à la liquidité** : ETC, DOT, LINK,
> XRP paient bien et n'ont pas de carnet. Mais la relation n'est pas stricte, et
> c'est ce qui rouvre la famille : **ADA paie 1,162 bps/jour avec la jambe
> contraignante la plus profonde de tout l'univers (52,9 k$)**. Aucun document
> du dépôt ne met ces deux colonnes côte à côte.

**Sous-ensembles déployables.** En exigeant que **les deux jambes** portent au
moins ~10 k$ sur 5 niveaux :

| panier | r bps/j | t | médiane/moyenne | % règlements > 0 | jambe contraignante | pire dérive 14 j |
|---|---|---|---|---|---|---|
| A. BTC + ETH | 0,216 | 3,84 | 0,69 | 54,9 % | 18 625 $ | 60 bps |
| B. ADA seul | **1,162** | 8,51 | **0,00** | 46,5 % | **52 885 $** | 156 bps |
| C. BTC, ETH, SOL, DOGE, ADA, LTC | 0,419 | 7,89 | 0,82 | 63,3 % | 13 770 $ | **387 bps** |
| **D. ADA, LTC, SOL, BCH** | **0,800** | **11,74** | **0,79** | **71,3 %** | 8 545 $ | 156 bps |

Le panier **D** est le meilleur compromis mesuré : il a le t le plus élevé, la
médiane la plus proche de sa moyenne (donc **pas porté par la queue**, contrairement
à ADA seul dont la médiane est **exactement 0**), et 71 % de règlements positifs.
Le panier **C** est écarté malgré sa profondeur : DOGE y apporte une dérive de
387 bps, trop près du seuil de liquidation de 417 bps à 24×.

#### Le levier n'est pas la contrainte ici (RÉFUTATION d'une conclusion du projet)

`MISSION.md` §5 ter conclut que le mécanisme est étranglé par une « tenaille » :
tenir plus longtemps amortit le coût, mais fait grandir le coussin, donc chuter
le levier. Cette tenaille est réelle — **pour la couverture par corrélation**,
où α = 0,493 et le coussin à 14 j vaut 20,03 % du notionnel.

Pour le **même sous-jacent**, le projet a lui-même mesuré (`ee398e7`)
**α = 0,236 ± 0,011**, soit un coussin à 14 j de **0,441 %** — quarante-cinq
fois plus petit. Face à une marge initiale de 4 %, ce coussin est **négligeable** :

| durée | coussin | levier `1/(0,04 + coussin/2,21)` |
|---|---|---|
| 7 j | 0,374 % | 24,0× |
| 14 j | 0,441 % | 23,8× |
| 30 j | 0,528 % | 23,6× |
| 90 j | 0,684 % | 23,2× |

**Le levier est plat.** La tenaille ne se referme pas. Le rendement
`R(T) = L × (r − c/T)` est donc **monotone croissant en T** : il suffit de tenir
plus longtemps. Cette conclusion contredit `RAPPORT_FINAL.md` §5, et elle
découle de deux chiffres que le projet a mesurés lui-même sans les combiner.

#### L'économie des paniers déployables

Hypothèse de coût **conservatrice** : 8 bps d'aller-retour (4 jambes maker à
2 bps), et le demi-spread encaissé supposé **entièrement neutralisé par la
sélection adverse** — ce qui est exactement ce que le projet a mesuré
(`SCAN_DONNEES.md` mesure 2 : demi-spread ≈ sélection adverse, instrument par
instrument). Levier 23,6× (borné par l'IMR, pas par le coussin).

| détention | A. BTC+ETH | **D. ADA/LTC/SOL/BCH** | D en %/an | fenêtres indép. dans 95 j |
|---|---|---|---|---|
| 14 j | −8,39 bps/j | **+5,41** | +21,8 % | 6,8 |
| 30 j | −1,20 | **+12,60** | **+58,3 %** | 3,2 |
| 60 j | +1,95 | **+15,74** | +77,6 % | 1,6 |
| 90 j | +3,00 | **+16,79** | +84,5 % | 1,1 |

**En taker**, le même panier C passe de −30,6 bps/j à 14 jours à seulement
+0,46 à 60 jours : **l'exécution maker n'est pas une optimisation, c'est la
condition d'existence de la famille.**

**Et le levier est le second levier de décision.** Les chiffres ci-dessus sont à
23,6×, ce qui laisse une marge de 417/156 = **2,7×** avant liquidation dans la
pire heure observée. À un levier prudent de 10×, le panier D à 30 jours rend
**5,3 bps/jour de capital, soit ≈ 21 %/an** — avec une marge de 6,4×.

#### Le risque de queue, mesuré (REPRODUIT — 375 jours de bougies déjà au dépôt)

Le résidu de ce livre est le **basis USD/USDT**. C'est la seule chose qui peut
le liquider. Seuil de liquidation à 24× : **417 bps**.

| paire | écart-type | pire dérive adverse sur 14 j | liquide à 24× ? |
|---|---|---|---|
| **BTC** | 5,75 bps | **60 bps** | non (marge ×7) |
| **ETH** | 5,87 bps | **45 bps** | non (marge ×9) |
| ADA | 7,67 | 156 | non |
| DOGE | 7,88 | 387 | **limite** |
| XRP | 9,54 | **670** | **OUI** |
| FIL | 13,55 | **950** | **OUI** |

**Mais la réserve majeure est ailleurs.** Ces extrêmes ne sont pas dispersés :
ils tombent **tous dans la même heure**, le **10/10/2025 à 21:00 UTC** (la
cascade de liquidations d'octobre 2025) :

```
heure        BTC   ETH   SOL   XRP   ADA  DOGE   LTC   BCH  LINK   DOT   FIL
10 20:00       2     2    -5   -10   -13    -2    -2   -13     7     3     5
10 21:00      62    46    84  -661   144  -376   -93   -45   140    -4  -932
10 22:00      29    33   -10   -32    18    53    51    15     2    42   -56
```

Deux enseignements, aucun dans le dépôt :

1. **Les résidus sont corrélés dans la queue.** Le gain de mutualisation ×2,21
   du projet est mesuré sur une corrélation médiane de 0,056 en régime normal.
   Il **disparaît exactement dans l'événement qui décide de la survie**. Toute
   taille de position dérivée de ce ×2,21 est surestimée en situation de stress.
2. **BTC et ETH tiennent** : +62 et +46 bps contre 417 de seuil, dans la pire
   heure de l'échantillon — un facteur de sécurité de 6,7×. Mais c'est **un seul
   événement en 375 jours**. Un livre prudent tournerait à 8–10×, pas 24×, ce
   qui ramène le rendement à **+3 à +7 %/an**.

#### Verdict sur la famille

| critère | verdict |
|---|---|
| Mécanisme causal nommable | **oui** — segmentation des clientèles coin-margé / USDT-margé |
| Flux observable sans look-ahead | **oui** — taux publié avant règlement, `fundingRate == realizedRate` |
| Espérance brute mesurée | **oui** — 0,800 bps/j, t = 11,74 sur le panier ADA/LTC/SOL/BCH, 71 % de règlements positifs |
| Capacité à 1 000 € | **oui, mais serrée à fort levier** — à 10× le notionnel est 10 000 € réparti sur 4 paires, soit 2 500 €/paire contre 8 500–53 000 $ de profondeur |
| Risque de queue survivable | **oui** sur le panier D (marge 2,7× à 24×, 6,4× à 10×) ; **non** sur XRP, FIL, DOGE |
| Espérance **nette** positive | **oui en maker dès ~14 jours** sur le panier D ; **non en taker** avant 60 jours |
| **Validée hors échantillon** | **NON — et impossible avec les données disponibles** |

Le blocage est une **limite de données, pas de marché** : l'API OKX plafonne
l'historique de funding à **286 relevés ≈ 95 jours**, pour tous les instruments.
Je l'ai vérifié en le refetchant moi-même — chaque instrument s'arrête à
exactement 286. Le projet l'avait identifié (`ee398e7`) et a raison. Tester une
détention de 60 jours avec 30 fenêtres indépendantes demanderait **~5 ans**
d'historique. On ne peut que **collecter vers l'avant**.

### H.3 — Les familles déjà fermées, et sur quoi

| famille | ce qui a été mesuré | pourquoi c'est fermé | solidité de la fermeture |
|---|---|---|---|
| Traversée / réversion après mouvement | edge 1–6 bps sur 8 mesures indépendantes | coût 8–31 bps | **solide** — rapport constant 1:4 à 1:20 sur des mécanismes sans rapport |
| Tenue de marché (maker) | demi-spread encaissé ≈ sélection adverse, instrument par instrument | à **frais nuls**, BTC-USDT-SWAP perd 0,86 bps/remplissage, t = −6,70 sur 119 blocs disjoints | **solide** — et le placebo (sens d'agresseur mélangés) rendait 104 % du résultat |
| Dislocations transversales | 0 cellule positive sur 56, **y compris à frais nuls** | l'abaissement du coût fait entrer sur des écarts plus petits qui ne reviennent pas | **solide** |
| Flux de liquidation forcé | +2,97 bps après, contre +14,55 bps **avant** | la causalité est inversée | **solide** (cf. §H.1) |
| Arbitrage de funding inter-venues | signal réel | arithmétique fausse (`954ad9e`) | fermé par le projet |
| Polymarket / Kalshi | preuve académique négative pour les makers | — | fermé sur littérature, pas sur mesure propre |
| Carry non-crypto (actions/matières tokenisées) | plafond 33,3 bps/j | profondeur 10–814 $ | **fermé sur la capacité**, pas sur le signal |

Ces fermetures sont de bonne qualité. **Je ne recommande d'en rouvrir aucune**,
à une nuance près : la famille maker a été fermée sur des données de 30 heures
qui n'existent plus dans le dépôt, donc sa fermeture n'est plus vérifiable —
mais le raisonnement structurel qui la soutient (le spread est fixé par les
teneurs pour couvrir la sélection adverse ; au tarif public on est sous cette
ligne) est solide et n'a pas besoin des données.

---

## I. Plan d'action priorisé

Classé par **information gagnée par heure de travail**, pas par séduction.

### I.1 — Résoudre la question du netting de marge OKX (1–2 h)

| | |
|---|---|
| **Objectif** | Savoir si OKX compense les marges d'une jambe inverse et d'une jambe linéaire dans un même compte. |
| **Justification** | `CARRY_FINDING.md` appelle cela « l'inconnue qui décide de tout » et chiffre l'écart : **191 bps/an sans netting contre 3 826 bps avec**, un facteur 20. `carry_capital.py` affirme ensuite que la question était mal posée. **Les deux documents du dépôt se contredisent et aucun ne tranche.** |
| **Fichiers** | `prism_v2/CARRY_FINDING.md`, `prism_v2/scans/carry_capital.py`, `prism_v2/margin.py` |
| **Données** | Documentation OKX sur les modes *Multi-currency margin* et *Portfolio margin* ; éligibilité du compte. **Aucun ordre requis.** |
| **Résultat attendu** | Un fait binaire, plus une contrainte d'éligibilité. |
| **Réussite** | Le mode de marge du compte est identifié et le netting inverse/linéaire est établi par la documentation ou une lecture du solde en démo. |
| **Abandon** | Sans objet — c'est un fait à établir, et il conditionne tout le reste. |

### I.2 — Lancer une collecte forward du funding (30 min de mise en place, puis passif)

| | |
|---|---|
| **Objectif** | Construire l'historique hors échantillon que l'API ne peut pas fournir. |
| **Justification** | **C'est le seul goulot réel du carry.** 286 relevés = 95 jours, plafond dur vérifié sur 24 instruments. Chaque jour non collecté est un jour de validation définitivement perdu. Un collecteur existe déjà : `tools/funding_collector.py`. |
| **Fichiers** | `tools/funding_collector.py`, `prism_v2/funding_feed.py` |
| **Données** | `GET /api/v5/public/funding-rate-history`, 24 instruments, 3×/jour. Coût ≈ 0. |
| **Effort** | 30 min, puis un cron. |
| **Résultat attendu** | À 6 mois : ~550 relevés, 3 fenêtres indépendantes à 60 jours. À 18 mois : validation réelle possible. |
| **Réussite** | Le différentiel BTC+ETH reste positif avec t > 2 sur les données **collectées après aujourd'hui**. |
| **Abandon** | Le différentiel BTC+ETH devient ≤ 0 en moyenne sur 90 jours de données neuves. |

> À faire **aujourd'hui**, quelle que soit la décision sur le reste. C'est la
> seule action dont le coût d'omission croît avec le temps.

### I.3 — Mesurer le taux de remplissage maker sur les deux jambes (2–3 h + 1 semaine passive)

| | |
|---|---|
| **Objectif** | Remplacer l'hypothèse « file gagnée » par une mesure. |
| **Justification** | Tout le carry bascule sur ce point : en taker, il est négatif jusqu'à ~90 jours ; en maker, il est positif à partir de 60. C'est la **seule** hypothèse non mesurée qui décide du signe. |
| **Méthode** | Journaliser le carnet des 8 jambes du panier D (`ADA`, `LTC`, `SOL`, `BCH` × inverse/linéaire) au touch, poser des ordres **fictifs** au meilleur prix, et mesurer la proportion du flux agressif qui les aurait traversés, file devant comprise. **Mesurer aussi le markout à 1 s / 30 s** : si la sélection adverse dépasse le demi-spread, l'hypothèse de coût à 8 bps est trop généreuse. Aucun ordre réel. |
| **Fichiers** | `prism_v2/l2book.py`, `prism_v2/ws_collector.py`, `prism_v2/markout.py` (tout existe déjà) |
| **Réussite** | Remplissage passif > 60 % à 4 h sur les 8 jambes, **et** sélection adverse ≤ demi-spread. |
| **Abandon** | < 30 % de remplissage, ou sélection adverse > 1,5× le demi-spread : le coût effectif rejoint le taker, et le panier D redevient négatif sous 60 jours. |

### I.4 — Corriger le look-ahead, puis geler V33 (1 h)

| | |
|---|---|
| **Objectif** | Empêcher que le défaut ressorte, sans relancer le développement de V33. |
| **Justification** | Le correctif est trivial (4 lignes) mais il **invalide rétroactivement toute la calibration** : 14 paramètres ont été choisis sur des backtests qui contenaient la fuite. Corriger sans recalibrer ne rend pas V33 valide — cela rend seulement le code honnête. |
| **Fichiers** | `prism/strategy.py:88-96` ; correctif prêt dans `tools/causal_htf.py` (issu de cet audit) |
| **Réussite** | Le test de causalité ajouté échoue sur l'ancien code et passe sur le nouveau. |
| **Décision** | **Geler V33, et ne pas le relancer.** Ce n'est plus une question de goût : mesuré hors échantillon sur les 60 jours postérieurs à son dernier commit, V33 rend **PF = 0,17 et 2 gagnants sur 29** (§E.2bis). Recalibrer reviendrait à refaire 14 choix de paramètres sur une famille dont la traversée est par ailleurs fermée par 8 mesures indépendantes. |

### I.5 — Instrumenter les compteurs de rejet, si un moteur reprend du service (2 h)

| | |
|---|---|
| **Objectif** | Rendre « 0 signal » distinguable de « moteur muet ». |
| **Justification** | Le dépôt a déjà produit ce faux négatif (`5ef49cb` : « zéro déclenchement, pour toujours… faux négatif indiscernable d'un vrai résultat »). `scan_history_v33.json` n'enregistre rien d'autre que `signals: []`. |
| **Réussite** | Chaque scan journalise le compte de rejets par porte, et un test vérifie qu'une règle **peut** se déclencher sur des données éparses (la garde que V2 a ajoutée et que V33 n'a pas). |

### I.6 — Ce qu'il ne faut PAS faire

| action | pourquoi non |
|---|---|
| Construire le module 15 m Liquidity Sweep | Le mécanisme causal a été testé à la bonne résolution et **réfuté** (§H.1). Le 15 m ne peut pas voir le renversement de causalité qui se joue en 60 s. |
| Construire une infrastructure L2 lourde | Aucune hypothèse en attente n'en a besoin. Le seul besoin L2 identifié (§I.3) est ciblé : deux instruments, le touch, une semaine. |
| Recalibrer V33 après correction du look-ahead | Le look-ahead n'était pas le problème : le supprimer **améliore** le backtest. Le problème est que l'edge n'existe pas hors de la fenêtre d'ajustement (PF 1,58 → 0,17). |
| Relancer V33 en paper en espérant que « ça revienne » | 60 jours OOS, 29 trades, p = 0,0015. Ce n'est pas une série défavorable, c'est l'absence d'edge. |
| Réactiver MOM | Fréquence mesurée : **1 signal pour 3 884 barres-symbole** sur 252 432 barres. Un verdict statistique demanderait des années. |
| Chercher un barème de frais plus favorable | `SCAN_DONNEES.md` §3 bis a balayé le coût jusqu'à **zéro** : 0 cellule positive sur 56, et 12 instruments sur 13 exigeraient que la venue **paie** une remise. Fermeture solide. |
| Viser 272 bps/jour | Voir §J.1. |
---

## J. Conclusion

### J.1 — Qu'avons-nous réellement appris de V33 ?

Trois choses, et aucune n'est celle qu'on croit.

**1. V33 n'a pas d'edge, et c'est désormais mesuré.** Ce n'était pas le cas au
début de cet audit : zéro ordre réel, zéro trade sur la fenêtre conservée, aucun
artefact de performance. On pouvait seulement dire « non démontré ». La coupure
au 22/07/2026 — datée par git, donc non choisie sur un résultat — le tranche :
**PF 1,58 en échantillon, 0,17 hors échantillon, 2 gagnants sur 29, p = 0,0015.**
À fréquence inchangée. Le +327 % sur 13 mois est intégralement porté par la
fenêtre d'ajustement.

Et il faut le dire dans l'autre sens aussi : **deux des défauts que j'avais
signalés ne sont pas coupables.** Supprimer le look-ahead *améliore* le
backtest, et le biais TP-avant-SL vaut exactement zéro. Le problème de V33
n'est pas un bug de mesure qu'on pourrait corriger — c'est que l'edge n'existe
pas en dehors des données qui ont servi à le fabriquer.

**2. Le processus de recherche produisait des chiffres invalides par
construction.** Ce n'est pas un bug isolé, c'est une chaîne :
un look-ahead de 3 h dans la fonction la plus centrale (§D.1), la sélection des
paramètres sur l'OOS câblée dans l'outil nocturne (§D.2), 39 valeurs balayées par
nuit sans correction de multiplicité, la procédure de garde (« WFO 5 fenêtres »)
citée 7 fois et jamais implémentée, et zéro test comportemental. **Tout résultat
sorti de cette chaîne est non interprétable**, quelle que soit sa taille. Un
+290,9 % sur 31 trades issu d'un tel pipeline n'est pas un edge : c'est une
mesure du nombre d'essais.

**3. Le seuil d'échec était mal posé.** Le mandat de V2 fixe **272 bps/jour de
capital** (×5 en 60 jours). Des mécanismes mesurés à 10 ou 33 bps/jour ont été
déclarés morts parce qu'ils n'atteignaient pas ce seuil. Or 33 bps/jour de
capital, si c'était réel, serait un résultat exceptionnel. Le rapport final
écrit lui-même « distance : facteur 8,2 » comme si c'était un échec. **Un
critère d'arrêt calibré sur un objectif inatteignable ne distingue pas les
mauvaises idées des bonnes : il les tue toutes.** C'est le biais le plus coûteux
du projet, parce qu'il est invisible — il ressemble à de la rigueur.

À noter, en sens inverse : la partie V2 du travail est **de bonne qualité
méthodologique** (blocs disjoints, Benjamini-Hochberg sur tous les tests,
placebos, critères d'abandon déclarés d'avance, erreurs de l'auteur documentées
en entier). Les fermetures de familles qu'elle produit sont solides et je n'en
rouvre aucune. Le défaut de V2 n'est pas sa méthode, c'est son barème.

### J.2 — Quelle partie mérite d'être conservée ?

| à conserver | pourquoi |
|---|---|
| `prism_v2/margin.py`, `capital.py`, `contracts.py`, `instruments.py` | Barème de marge OKX réel, mécanique des inverses, `usd_notional`. Correct, testé, et c'est ce qui a rattrapé l'erreur du facteur 760. |
| `prism_v2/kill_registry.py` | Le registre des plafonds morts, qui refuse de comparer des dénominateurs différents. C'est ce qui a empêché quatre résultats spectaculaires de survivre. |
| `prism_v2/l2book.py`, `ws_collector.py`, `markout.py` | Nécessaires et suffisants pour l'expérience I.3. Ne rien construire de plus. |
| `tools/funding_collector.py` | Le collecteur de l'expérience I.2. |
| La discipline de V2 | Protocole gelé avant les données, critère d'abandon déclaré d'avance, placebo, blocs disjoints, correction de multiplicité. |

### J.3 — Quelle partie ne doit plus être considérée comme validée ?

- **Tous les paramètres de V33** (14 recensés, §D.3) : choisis sur des backtests
  contenant une fuite de 3 h, par un outil qui sélectionne sur l'OOS.
- **Toutes les performances V33** : +45,4 %, +90,9 %, +123,8 %, +76 % APY —
  introuvables dans le dépôt ; +290,9 % — chaîne codée en dur, 31 trades,
  pipeline contaminé. Et le +327 % que je reproduis moi-même sur 13 mois ne
  vaut pas mieux : il est **entièrement** dans l'échantillon d'ajustement.
- **La stratégie V33 elle-même**, désormais : pas « non validée » mais
  **invalidée**, sur un test unique et pré-spécifié.
- **La whitelist et les blacklists** : sélection d'actifs après observation,
  explicitement documentée.
- **Le « 0/10 MOM, −100 € »** comme verdict : c'est un rejeu, non versionné,
  sur un pattern retenu parmi 11 variantes et validé OOS sur N=3.
- **Le plafond de 33,3 bps/jour** comme référence crypto : il appartient à un
  univers d'actions tokenisées dont la profondeur est de 10 à 814 $.
- **Le gain de mutualisation ×2,21** en situation de stress : mesuré à
  corrélation médiane 0,056, il s'évapore dans l'heure qui décide (§H.2).

### J.4 — Le prochain test qui apporte le plus d'information au moindre coût

**Lancer aujourd'hui la collecte forward du funding (§I.2).**

C'est la seule action dont le coût d'omission **croît avec le temps**. Elle
demande 30 minutes, ne coûte rien, ne risque rien, et elle construit le seul
actif que ni l'argent ni le code ne peuvent acheter : de l'historique hors
échantillon sur le seul mécanisme encore ouvert. L'API plafonne à 95 jours ;
chaque jour non collecté est perdu définitivement.

En parallèle immédiat, **résoudre la question du netting de marge (§I.1)** :
1 à 2 heures pour lever un facteur 20 que deux documents du dépôt se disputent
sans trancher.

Puis, si et seulement si le netting est favorable, **mesurer le remplissage
maker (§I.3)** — la dernière hypothèse qui décide du signe.

### J.5 — Ce qui manque encore pour décider

| manquant | pourquoi ça bloque | récupérable ? |
|---|---|---|
| Historique de funding > 95 jours | À 60 jours de détention, les données disponibles donnent **1,6 fenêtre indépendante**. Aucune validation n'est possible. | **Non rétroactivement.** Uniquement par collecte forward. |
| Probabilité de remplissage passif | Décide du signe : taker ⇒ négatif sous 90 j ; maker ⇒ positif dès 60 j. | Oui, §I.3, une semaine. |
| Netting de marge OKX inverse/linéaire | Facteur 20 sur le rendement du capital ; deux documents du dépôt se contredisent. | Oui, §I.1, documentation. |
| Slippage et latence réels | Jamais mesurés. `RAPPORT_FINAL.md` le dit : « exige de vrais ordres ». | Oui, mais seulement en démo puis en réel de taille minimale. |
| Fréquence des cascades type 10/10/2025 | Un seul événement en 375 jours. Il décide du levier soutenable, donc du rendement (24× ⇒ ~10 %/an, 8× ⇒ ~3 %/an). | Partiellement : historique de bougies plus long, ou étude des cascades connues. |
| `okx_trader.py` | Aucun audit de plomberie n'est possible, et aucun ordre non plus. | À réécrire de zéro si l'exécution redevient un objectif. |

### J.6 — La réponse à votre objectif

Vous cherchez des gains significatifs dans un délai court, avec une fréquence et
une rotation du capital intéressantes.

**Rien dans ce dépôt ne va dans cette direction, et mes propres mesures n'y vont
pas non plus.** Les familles à haute fréquence et forte rotation — traversée,
tenue de marché, dislocation, flux de liquidation — sont fermées par des mesures
qui se répètent avec une régularité frappante : **tout ce qui est capturable vaut
1 à 6 bps, tout aller-retour en coûte 8 à 31.** Ce rapport de 1 pour 4 à 1 pour
20 apparaît sur des mécanismes sans rapport entre eux, il tient à frais nuls, et
il a une explication structurelle : le demi-spread est fixé par les teneurs de
marché pour couvrir la sélection adverse plus leurs coûts, et au tarif public on
est **sous** cette ligne par construction.

La seule famille ouverte est l'exact opposé de ce que vous demandez en
fréquence : **rotation quasi nulle**, environ 30 jours de détention, quatre
paires. En revanche son ordre de grandeur n'est pas négligeable —
**≈ 21 %/an à 10× de levier, ≈ 58 %/an à 24×** — à trois conditions qui ne sont
aucune d'elles acquises : que l'exécution soit **maker** (en taker c'est
négatif), que le différentiel **persiste** hors des 95 jours mesurés, et
qu'aucune cascade pire que celle du 10/10/2025 ne survienne pendant une
détention.

Je ne peux pas dire que rien n'existe ailleurs. Je peux dire ceci, et pas plus
large : **sur les instruments accessibles à ce compte, avec les données publiques
d'OKX, la fréquence et le rendement sont en opposition directe, et la mesure ne
laisse pas d'espace entre les deux.** Le choix n'est pas entre une bonne et une
mauvaise stratégie — il est entre un rendement lent et défendable, et
l'illusion d'un rendement rapide entretenue par un pipeline de mesure biaisé.

