# EDGE_HUNT — Rapport final de clôture (A–AI)

Commit de référence : voir `git log -1` à la fin du document.
Branche : `claude/v2-project-reconstruction-66p4nx`.
Tous les chiffres de ce rapport ont été relus dans le code ou dans les
artefacts de run. Aucune affirmation n'a été reprise d'un rapport antérieur
sans vérification.

---

## A. Executive summary

EDGE_HUNT est un **moteur de recherche à espace d'hypothèses borné, avec
falsification obligatoire et verrouillage économique**. Il n'a découvert
aucun edge. Ce n'est pas un échec : sur quatre runs consécutifs en données
réelles, il a produit **6 739 hypothèses et en a rejeté 6 739**, et il a
refusé d'allouer du capital à chaque fois que la preuve manquait.

Ce que le projet démontre aujourd'hui, c'est **la machinerie de réfutation**,
pas la rentabilité. Les trois faits qui structurent tout le reste :

1. L'entonnoir de recherche multi-agent a donné **0 survivant sur 4 runs / 4**.
   C'est le seul résultat stable du projet.
2. L'entonnoir des 9 détecteurs a donné **161 → 2 → 0 → 5** survivants aux
   bornes sur quatre fenêtres de 7 minutes. Cette instabilité, et le fait que
   la famille survivante **change d'un run à l'autre**, interdisent d'y lire
   un phénomène persistant.
3. Un **8ᵉ défaut** a été trouvé pendant cette clôture : les détecteurs
   posaient `hypothesis_untested: True` sur leurs candidates et **personne ne
   lisait ce drapeau**. Une candidate pouvait se déclarer non testée et
   recevoir du capital dans le même run. C'est ce défaut qui explique comment
   les 161 ont atteint l'allocation.

Aucun frais n'est `OBSERVED`. `EvaluationMode.EXECUTION` et `SystemMode.LIVE`
sont donc structurellement bloqués, et le restent.

---

## B. Architecture

```
MARKET DATA (OKX public, Hyperliquid)
  └─ instruments.py      InstrumentSpec, FAIL CLOSED, pas de whitelist
  └─ contracts.py        toute la mécanique financière (inverse ≠ linéaire)
  └─ l2book.py           carnet incrémentiel, chaînage prevSeqId/seqId
  └─ quality.py          9 problèmes, dont 10 bloquants
  └─ market_state.py     état observable, sans interprétation
       ├──► detectors/   9 familles codées  ──► discovery_economics (bornes)
       └──► research/    6 agents            ──► falsification (18 contrôles)
                                                     │
                            economics.py ◄───────────┘
                            router.py / risk.py / sizing.py
                            paper_lab.py (PAPER causal)
                            ledger.py / discovery_ledger.py / failure_memory.py
```

Deux entonnoirs **parallèles et indépendants** partent de `market_state` :
les détecteurs codés et la recherche multi-agent. **Ils ne communiquent pas.**
Cette séparation est la source de la confusion « 161 → 0 » traitée en W.

Contrainte respectée : `prism_v2` n'importe ni numpy, ni pandas, ni requests —
stdlib seule. Aucun import de `prism`/`backtest_v33`/`live_monitor_v33`
(vérifié par `test_no_v33_import_anywhere_in_v2`).

---

## C. Market Observatory

`ws_collector.py` + `wsclient.py` (client WebSocket stdlib, écrit parce que
`ws.okx.com:8443` est bloqué depuis cet environnement et `:443` passe).

Run de clôture (`hunt-20260916T155232Z`, 420 s, 18 instruments) :

| Grandeur | Valeur |
|---|---|
| événements | 48 374 |
| canaux | books 41 132, trades 7 202, liquidation-orders 40 |
| reconnexions | 0 |
| doublons détectés | 3 296 |
| régressions de séquence | 0 |
| délai de transport | médiane 94 ms, p90 101 ms (N = 48 334) |
| carnets incrémentiels | 18 suivis, 41 103 updates chaînées, **0 trou** |
| checksum | **non disponible** (`checksum: 0` sur le canal `books`) |

Le délai de transport est un délai **exchange → local**, soumis à la dérive
d'horloge. **Ce n'est pas la latence aller-retour d'un ordre**, qui n'est pas
mesurable sans compte.

---

## D. Market State

`MarketState` expose des grandeurs observables et **aucun prédicteur** :
spread, profondeur par côté, déséquilibre, microprix, coût de traversée
exécutable, volatilité réalisée, intensité de trades, flux agressif, funding.

Garanti par test : `OrderBook` n'expose aucune fonction de signal
(`test_no_signal_functions_exist`), et aucun identifiant de code ne contient
de jeton d'indicateur V33 (`rsi`, `macd`, `bbw`, …), comparaison **par jeton**
et non par sous-chaîne, sur l'AST (`tests/v2/helpers.py`).

---

## E. Discovery Engine (les 9 détecteurs)

Raisonnement par **bornes** (`discovery_economics.py`), jamais par point :

- `DEAD_EVEN_AT_BEST` : net optimiste ≤ 0 — même en supposant **nuls** tous
  les coûts inconnus, la capture ne couvre pas les coûts connus. Verdict
  définitif.
- `SURVIVES_ALL_BOUNDS` : net pessimiste > 0 — survit aux **majorants**
  documentés. Ce n'est pas une preuve de rentabilité.
- `NEEDS_MEASUREMENT` : l'intervalle contient zéro. Le résultat **nomme** la
  grandeur à mesurer.

Les majorants sont documentés et, quand c'est possible, **dérivés d'une donnée
observée** : frais 5 bps/jambe (barème public Lv1, tier le plus cher des
standards) ; slippage ≤ 1 spread/jambe ; latence ≤ 1 spread ; adverse
selection ≤ 1 spread. Aucun n'est une estimation.

---

## F. Open Discovery — verdict

**EDGE_HUNT n'est PAS un Open Discovery Engine.**
**Il n'est pas non plus « Open-Discovery-capable ».**

Formulation honnête retenue :

> **MOTEUR DE RECHERCHE À ESPACE D'HYPOTHÈSES BORNÉ, AVEC SEUILS MESURÉS ET
> FALSIFICATION OBLIGATOIRE.**

### Le fait décisif : l'espace est énumérable avant le run

```
FEATURE_SPACE            = 15 primitives, écrites à la main
DEFAULT_HORIZONS_MS      = (1000, 5000, 15000, 30000)  → 4
conditions               = ("low", "high")              → 2
                           ──────────────────────────────
                           120 gabarits par instrument
                         × 18 instruments = 2 160 hypothèses possibles
```

Observé au run de clôture : **1 678 hypothèses proposées** — cohérent avec un
espace de 2 160 partiellement rempli (une primitive absente sur un instrument
ne produit pas de gabarit). **Le système ne peut pas produire une hypothèse
hors de ces 2 160 emplacements.** Il n'existe ni opérateur de composition
(« A et B simultanément »), ni synthèse de primitive, ni relation
inter-instruments (« A précède B ») par ce chemin. La capacité est **absente**,
pas seulement inexercée — d'où le refus de « capable ».

### Les 10 questions, une par une

| Question | Réponse vérifiée | Où |
|---|---|---|
| Phénomène découvrable sans nom codé ? | **Partiellement.** Le triplet (primitive, condition, seuil) n'est pas nommé ; la **primitive** l'est. | `observation.py:FEATURE_SPACE` |
| Famille inconnue avant découverte ? | **Oui** côté recherche : un phénomène n'a pas de famille. | `hypothesis.py:Phenomenon` |
| BUY/SELL dérivé ? | **Oui.** `direction = LONG si excess_bps > 0 sinon SHORT`. | `agents.py` |
| Seuils émergents ? | **Mesurés** (décile de l'échantillon). La *règle* décile est fixe. | `agents.py:105-116` |
| Mécanisme postérieur ? | **Oui**, 4ᵉ étage. Hors table → `UNEXPLAINED`. | `agents.py:MechanismAgent` |
| Patterns pré-écrits ? | **Balayage mécanique**, pas d'intuition — mais énumérable. | ci-dessus |
| Exploration réelle d'un espace ? | **Oui, d'un espace borné de 2 160.** | ci-dessus |
| Hypothèse inexistante comme règle codée ? | **Oui pour le contenu** (seuil, sens, taille d'effet, p-value). **Non pour la forme.** | — |
| Le red team peut-il la réfuter ? | **Oui**, démontré 1 678/1 678. | `falsification.py` |
| Provenance retraçable ? | **Oui**, 17 champs obligatoires. | `discovery_ledger.py` |

### Les quatre niveaux, jamais confondus

| Niveau | Statut | Preuve |
|---|---|---|
| 1. Capacité architecturale d'Open Discovery | **PARTIELLE** | espace borné, pas de composition |
| 2. Test synthétique d'Open Discovery | **ABSENT** | `open_discovery_test.audit()` retourne `DiscoveryClass.STRUCTURED` **en dur** — c'est une auto-évaluation documentée, **pas un test empirique**. Aucun phénomène connu n'a été planté pour vérifier qu'il serait retrouvé. |
| 3. Démonstration sur données réelles | **OUI** | 1 678 hypothèses générées mécaniquement, mesurées, falsifiées, journalisées |
| 4. Découverte économiquement survivante | **AUCUNE** | 0 survivant sur 4 runs |

Le niveau 2 est le manque le plus net du projet côté méthode.

---

## G. Les 6 agents

| Agent | Entrée | Sortie | Peut proposer | Ne peut PAS décider |
|---|---|---|---|---|
| `MarketObserverAgent` | `MarketState` | `Observation` immuable | un relevé | aucune direction, aucun signal |
| `PhenomenonAgent` | `ObservationLog` | `Phenomenon` | un seuil **mesuré** (décile) | que le phénomène soit exploitable |
| `RelationAgent` | phénomènes + mids | `Relation` | une issue conditionnelle + p-value (Welch) | qu'elle soit causale |
| `MechanismAgent` | `Relation` | `EconomicMechanism` | une hypothèse de mécanisme | rien ; hors table → `UNEXPLAINED` |
| `FalsificationAgent` | `Hypothesis` + contexte | verdict + raisons | **rejeter** | jamais « accepter comme vrai » |
| `CaptureResearchAgent` | survivants | `CaptureStudy` | une étude de capturabilité | aucune allocation |

**Ce ne sont pas six variantes d'un BUY/SELL.** Preuves :
- `Observation` ne contient aucune clé `direction`/`side`/`signal`/`buy`/`sell`
  (`test_observation_carries_no_direction`) ;
- la direction n'apparaît qu'au 3ᵉ étage, **dérivée du signe** de l'excès mesuré ;
- le seul chemin vers `SURVIVED_FALSIFICATION` passe par `attack()` ;
- `ResearchOrchestrator` n'expose **aucune** méthode d'allocation
  (`test_orchestrator_cannot_allocate_capital`) ; il ordonne des **priorités de
  recherche**, jamais du capital.

Risque de « pattern factory » : contenu par l'immutabilité (`Hypothesis` gelée,
révision par `supersedes`), le registre qui refuse une réécriture silencieuse,
et la comptabilité de tests multiples. **Mais l'espace borné de 2 160 gabarits
est lui-même une usine à gabarits** — c'est précisément pour cela que la
correction pour tests multiples est indispensable, pas optionnelle.

---

## H. Hypothesis Registry

`Hypothesis` est un dataclass **gelé**. Changer un statut crée une **nouvelle
version** portant `supersedes`. `HypothesisRegistry.register()` lève sur une
réécriture silencieuse ; `supersede()` exige le lignage.

Run de clôture : 1 678 hypothèses, **3 356 versions stockées** (= 1 678 × 2 :
proposition puis verdict). Le doublement est la preuve que le versionnage
fonctionne.

---

## I. Discovery Ledger

`discovery_ledger.py` : JSONL **append-only**, 17 champs obligatoires, refus
d'un doublon sans `supersedes`. Le fichier produit (~6 Mo/run) est une sortie
de run : il est `.gitignore` — le code qui le produit est versionné, pas lui.

---

## J. Les 9 familles

### Productives pendant les runs (5)

| Famille | Primitive observée | Statut au run de clôture |
|---|---|---|
| `SPREAD_DISLOCATION` | spread vs son niveau habituel | 12 438 cand. — 11 875 morts, 563 à mesurer, **0 survivant** |
| `CROSS_MARKET` | écart inverse/linéaire vs médiane glissante | 6 856 cand. — 22 morts, 6 834 à mesurer, **0** |
| `SHORT_HORIZON_REVERSION` | écart du mid à sa moyenne 30 s | 5 559 cand. — 1 373 morts, 4 186 à mesurer, **0** |
| `AGGRESSIVE_FLOW` | déplacement réalisé 5 s + déséquilibre de flux | 2 896 cand. — 904 morts, 1 987 à mesurer, **5 survivants aux bornes** |
| `FUNDING_BASIS` | funding du contrat **exécuté** | 2 417 cand. — 763 morts, 1 654 à mesurer, **0** |

### Implémentées, aucune candidate pendant ces runs (4)

`FORCED_FLOW`, `BOOK_IMBALANCE`, `DEPTH_WITHDRAWAL`, `CROSS_VENUE`.

**Aucune preuve qu'elles soient cassées.** Raisons documentées : `FORCED_FLOW`
exige des liquidations (40 événements seulement sur 420 s) ; `CROSS_VENUE`
exige deux venues simultanément exploitables et Binance (451) / Bybit (403)
sont géo-bloquées ; `BOOK_IMBALANCE` et `DEPTH_WITHDRAWAL` n'ont pas franchi
leur plancher de spread. Leur priorité de recherche reste à 0,800–1,000 :
l'orchestrateur ne les pénalise pas pour une absence de données.

Aucune famille productive n'est déclarée rentable : les 5 survivants aux
bornes d'`AGGRESSIVE_FLOW` ont tous été bloqués en aval (§ M).

---

## K. Économie — comptabilité par qualité

Run de clôture, style TAKER. Composantes **essentielles** :
`fees, spread, impact, slippage, latency`.

| Composante | Qualité | Origine |
|---|---|---|
| spread | **DERIVED** | carnet observé, coût de traversée réel |
| impact | **DERIVED** | marche du carnet ; **UNKNOWN** si profondeur insuffisante |
| fees | **ASSUMED** (18/18 instruments) | barème public Lv1 ; `OBSERVED` exige `/api/v5/account/trade-fee` (authentifié) |
| latency | **DERIVED** | décroissance mesurée par replay ; **UNKNOWN** sans échantillon |
| slippage | **EXCLUDED** en CAPTURE_VALIDATION, **UNKNOWN** ailleurs | exige des fills réels horodatés |
| adverse selection | **N/A** en TAKER (fill immédiat), **UNKNOWN** en MAKER | — |
| funding | **N/A** sur horizon intra-période, **UNKNOWN** sinon | lu sur le **même instId** exécuté |
| fill probability | **UNKNOWN** | nommée dans `PAPER_EXCLUDED_FRICTIONS` |
| depth | **OBSERVED** | carnet 400 niveaux |
| exit economics | **DERIVED** si carnet de sortie disponible, sinon **rien n'est produit** | § M |
| capital constraint | **OBSERVED** | `SizingLimits`, plafond 100 USD |

**Aucune inconnue n'est traitée comme zéro.** `CostBreakdown.total_bps()`
retourne `None` dès qu'une composante essentielle est `UNKNOWN`. Trois défauts
de ce type exact ont été trouvés et corrigés (§ U, n° 3, 5, et le `or 0.0` de
`paper_lab`).

### Pourquoi les verdicts

- `DEAD_EVEN_AT_BEST` (14 937) : capture brute ≤ coûts **connus**, même en
  supposant nuls tous les inconnus. Majoritairement `SPREAD_DISLOCATION` :
  l'excès de spread ne couvre pas le spread qu'il faut traverser deux fois.
- `NEEDS_MEASUREMENT` (15 224) : l'intervalle contient zéro. La grandeur
  manquante est nommée — le plus souvent les frais (`ASSUMED`, majorant 10 bps
  aller-retour) et le slippage.
- **Aucune candidate n'est actionnable** : les 5 qui survivent aux bornes sont
  bloquées par `UNTESTED_HYPOTHESIS` (§ U n° 8) — leur capture brute est le
  déplacement observé **pris en entier**, donc un majorant.

### Ce qu'il faudrait mesurer pour changer le verdict

Une seule grandeur domine : **la fraction de réversion réellement récupérée**
après un déplacement observé. Elle transforme un majorant en capture. Tout le
reste (frais `OBSERVED`, slippage `OBSERVED`) est nécessaire pour exécuter,
mais ne changerait pas le verdict de recherche.

---

## L. Abstraction des frais

`fees.py` : `FeeSchedule` porte sa `Quality`. `NoCredentialsFeeProvider` →
`UNKNOWN` + `MISSING_CAPABILITIES`. `AssumedFeeProvider` → `ASSUMED`.
`RealisedFeeProvider` → `OBSERVED` après ≥ 10 fills réels.

**Seul `OBSERVED` autorise un engagement de capital** (`authorises_execution`).
Aucun point d'entrée du module n'accepte de clé, secret, passphrase ou token —
vérifié par AST (`test_no_fee_class_accepts_a_credential`).

Défaut corrigé pendant la clôture : `round_trip_bps` offrait gratuitement une
jambe **demandée** dont le taux était absent. Retourne `None`.

---

## M. Validation causale — ce que fait réellement l'étage 6

**Constat d'audit important.** L'étage intitulé « 6. VALIDATION CAUSALE »
n'effectue **aucune simulation temporelle**. Il calcule, sur **un seul
carnet** : `capture brute − (frais + spread + impact + latence)`, slippage
explicitement **exclu**. C'est un second crible économique, pas une validation
causale.

La validation réellement causale est le **laboratoire PAPER** (§ N), qui
s'exécute **après** et qui, lui, avance dans le temps.

Le libellé est conservé tel quel dans le code pour ne pas toucher au
comportement en fin de mandat, mais **ce rapport fait foi** : ce que produit
l'étage 6 est un crible, et son `ACCEPTED` ne signifie pas « causalement
validé ». Depuis le correctif n° 8, aucune candidate de microstructure ne peut
plus y atteindre `ACCEPTED` de toute façon.

---

## N. Laboratoire PAPER

Quatre notions **séparées**, jamais additionnées :

| Notion | Définition | Qui la produit |
|---|---|---|
| **DISCOVERY EDGE** | excès mesuré vs référence, sur échantillon | `RelationAgent` |
| **EXPECTED CAPTURE** | capture brute − coûts, sur un carnet | `economics.evaluate` |
| **CAUSAL PAPER RESULT** | mouvement réel entre carnet d'entrée (T0+latence) et carnet de sortie (+horizon) | `PaperLab.run` |
| **REALIZED EXECUTION** | **n'existe pas** — aucun ordre réel n'a jamais été placé | — |

`PaperLab.run` : T0 figé, avance de `latency_ms`, carnet **réellement
disponible** à l'arrivée, invalidation si l'écart s'est refermé pendant le
trajet, ordre quantifié, fill partiel, débouclage sur le carnet de sortie,
coûts constatés, puis **quatre edges séparés**.

### Cas où PAPER refuse volontairement de calculer

| Refus | Condition |
|---|---|
| `OUTSIDE_WINDOW` | la fenêtre collectée ne couvre pas décision et/ou sortie |
| `NO_BOOK_AT_DECISION / ENTRY / EXIT` | carnet absent à l'un des trois instants |
| `INVALIDATED_BEFORE_ENTRY` | le prix a bougé **contre** la position pendant la latence, plus que l'effet attendu |
| `ORDER_NOT_SUBMITTABLE` | quantification impossible (lot/min size) |
| `INSUFFICIENT_DEPTH` | aller-retour impossible à cette taille |
| `executable_edge_bps = None` | frais `UNKNOWN` — refus de compléter plutôt que de mettre 0 |
| **aucun chiffre** | pas de référence causale, ou candidate à deux jambes |

Run de clôture : `allers-retours PAPER CAUSAUX executes: 0`. Aucun chiffre
fabriqué à la place. **Le refus est le résultat.**

`PAPER_EXCLUDED_FRICTIONS` nomme 5 frictions non simulables (probabilité de
fill maker, position dans la file, adverse selection réelle, latence
aller-retour d'un ordre réel, rejets/re-soumissions). **Tout résultat PAPER
est une borne supérieure.**

---

## O. Capital Router

`router.py` : tri économique (capture nette, puis capacité, puis horizon
court), plafond global, plafond par groupe de corrélation.

Décisions observées : `ALLOCATE`, `SKIP_CORRELATED`, `SKIP_NO_CAPITAL`,
`SKIP_NOT_ACTIONABLE`. **Le router refuse — et l'a fait.** Run de clôture :
`refused_everything: true`, 5 candidates, 0 allouée.

> Un run sans trade **n'est pas une panne du moteur**. C'est le résultat
> attendu quand la preuve manque.

---

## P. Risque

14 kill switches : `STALE_DATA`, `REGISTRY_MISMATCH`, `ABNORMAL_SPREAD`,
`EXECUTION_MISMATCH`, `RECONCILIATION_FAILURE`, `SEQUENCE_GAP`,
`CLOCK_ANOMALY`, `MAX_NOTIONAL`, `MAX_CONCURRENT_POSITIONS`,
`MAX_LOSS_PER_OPPORTUNITY`, `MAX_DAILY_LOSS`, `CAPACITY_EXCEEDED`,
`ORDER_NOT_EXECUTABLE`, `EMERGENCY_STOP`.

Ordre impératif non contournable : **QUALITÉ → ÉCONOMIE → CAPACITÉ → RISQUE**.
Une donnée inutilisable produit `UNRESOLVED`, **jamais** un rejet économique :
confondre « je ne peux pas mesurer » et « ce n'est pas rentable » fait
abandonner une piste vivante ou poursuivre une piste morte.

`max_capacity_fraction = 0.10` : un ordre ne consomme au plus qu'un dixième de
la profondeur affichée, parce que la profondeur à T0 n'est pas garantie à
T0+latence.

---

## Q. Exécution

`PaperExecutor` uniquement. `mode = ExecutionMode.PAPER`, porté dans chaque
`PaperFill`. Machine à états d'ordre avec transitions autorisées explicites.

Garanti par test (`test_no_real_executor_class_exists`) : aucune classe du
paquet n'expose `place_order`, `send_order`, `cancel_order`, `set_leverage`,
`close_position`. Aucun endpoint `/api/v5/trade/*`. Aucune primitive de
signature (`hmac`). Aucune lecture de `OKX_API_KEY`/`OKX_SECRET`/
`OKX_PASSPHRASE`.

---

## R. Mémoire

- `failure_memory.py` : 21 raisons d'échec, séparation données / économie.
- `discovery_memory.py` : 30 166 observations au run de clôture, conditions
  favorables par tranche (profondeur, instrument, taille, heure) avec N minimum.
- `ledger.py` : journal des captures, append-only.

Lecture honnête du run de clôture : `venue OKX → survie 0% (N=14 937)`. La
mémoire enregistre l'échec aussi fidèlement que le succès.

---

## S. Tests multiples

Run de clôture : **3 099 relations testées**.

| Correction | Seuil |
|---|---|
| Bonferroni | 1,613 × 10⁻⁵ |
| Benjamini-Hochberg | 0,020 47 |
| survivent à BH | 1 269 |

Note portée dans le JSON lui-même : *« les relations survivantes restent des
statistiques d'échantillon, pas des edges démontrés »*.

**Limite à énoncer explicitement.** Les protections sont **structurellement
correctes mais pas statistiquement suffisantes** pour une validation
scientifique complète :
- BH suppose l'indépendance ou une dépendance positive ; **1 269 relations
  mesurées sur les mêmes 14 904 observations sont fortement dépendantes** ;
- le split découverte/holdout est **temporel et unique** (moitié/moitié), pas
  un walk-forward à plusieurs plis ;
- le N effectif corrige le chevauchement des fenêtres, pas la corrélation
  **entre primitives** (profondeur bid et déséquilibre de profondeur sont deux
  vues du même carnet) ;
- aucun test de permutation, aucun bootstrap par blocs.

C'est pourquoi `survived_falsification: 0` est un résultat rassurant et
`survived > 0` aurait exigé une prudence bien supérieure.

---

## T. Red Team

`FalsificationAgent`, **18 contrôles**. Résultat du run de clôture :

| Raison | Occurrences |
|---|---|
| `CORRELATED_OBSERVATIONS` | 1 599 |
| `EFFECT_BELOW_SPREAD` | 1 241 |
| `MULTIPLE_TESTING` | 1 017 |
| `UNSTABLE_OUT_OF_SAMPLE` | 666 |
| `INSUFFICIENT_SAMPLE` | 55 |

(Une hypothèse peut cumuler plusieurs raisons.)

**Cinq contrôles ne mordent que si l'appelant déclare la donnée**
(`typical_spread_bps`, `depth_sources`, `touch_depth_usd`,
`holdout_relation`, `book_age_ms`). En son absence, le contrôle est marqué
`NON EXERCÉ` dans les `details` — **jamais compté comme un succès**.

Conséquence à retenir : **survivre n'est pas être vrai**. C'est n'avoir pas
été réfuté par les contrôles réellement exerçables.

---

## U. Les 8 défauts adversariaux

| # | Défaut | Pourquoi c'était dangereux | Correctif | Test | Permanent ? |
|---|---|---|---|---|---|
| 1 | 5 `RejectionReason` déclarées, jamais levées | l'enum promettait des contrôles inexistants ; un lecteur du code croyait le red team plus complet qu'il n'était | contrôles 12, 15–18 implémentés | `test_every_rejection_reason_is_reachable` | **permanent** (test structurel) |
| 2 | `vwap_for_notional` / `market_impact_bps` / `slippage_vs_touch_bps` rendaient un prix pour une taille non absorbée | le coût d'une **fraction** était lu comme le coût du notionnel demandé → capacité surestimée | retournent `None` si `walk.exhausted` | `test_capacity_beyond_book_is_never_silently_extrapolated` | **permanent** |
| 3 | `paper_lab` : `(imp_in or 0.0) + (imp_out or 0.0)` | un impact non mesurable devenait **gratuit** — le coût le plus favorable offert | propagation de `None` | `test_paper_executable_edge_is_none_without_fees` | **permanent** |
| 4 | `CLOCK_ANOMALY` non bloquant | horodatage dans le futur ⇒ l'âge du carnet est **indéterminable**, donc toute mesure de fraîcheur et de latence | ajouté à `BLOCKING_ISSUES` | `test_future_timestamp_is_a_clock_anomaly` | **permanent** |
| 5 | `FeeSchedule.round_trip_bps` offrait une jambe sans taux | total faux **par défaut**, dans le sens favorable | `None` si une jambe demandée n'a pas de taux | `test_breakdown_total_is_none_when_fees_unknown` | **permanent** |
| 6 | aller-retour PAPER sur le **même** carnet | brut nul par construction, réalisé = −péage ; posé à côté d'une capture attendue de +4,42 bps, se lisait comme une réfutation de l'edge | aller-retour **causal** (entrée T0+latence, sortie +horizon) ; sinon **aucun chiffre** | `test_round_trip_never_closes_on_its_own_entry_book` + `test_same_book_round_trip_is_exactly_minus_the_crossing_cost` | **permanent**, avec une exception nommée : `smoke_test.py`, où la mesure est étiquetée `EXECUTION_CONTROL_PAPER` avec `gross = 0` |
| 7 | candidate à deux jambes atteignant l'exécuteur mono-instrument | PnL d'une position **inexistante**, seconde jambe en exposition muette | refus explicite sur `legs > 1` ou `*_BOTH_LEGS` | `test_two_leg_candidate_is_never_executed_as_one_leg` | **permanent** tant que `PaperExecutor` reste mono-instrument |
| 8 | **`hypothesis_untested: True` déclaré par les détecteurs, lu par personne** | une candidate se **déclarait non testée** et recevait du capital dans le même run ; sa capture brute est le déplacement observé **pris en entier**, donc une hypothèse de convergence à 100 % jamais mesurée | `evaluate()` honore le drapeau → `UNRESOLVED` + `blocked_by="UNTESTED_HYPOTHESIS"` | `test_self_declared_untested_hypothesis_never_reaches_accepted` + `test_the_untested_flag_is_actually_set_by_the_detectors` | **conditionnel** : dépend du fait que les détecteurs continuent à poser le drapeau — d'où le second test |

Le défaut n° 8 a été trouvé **pendant cette clôture**, en instruisant la
question « 161 → 0 ». Effet mesuré en données réelles : les 5 survivants aux
bornes du run de clôture ont été bloqués, allocation **0**. Sous le code
d'avant, ils auraient été `ACCEPTED` et auraient reçu du capital.

---

## V. Runs de bout en bout

Quatre runs, tous OKX public + Hyperliquid, 420 s, 6 sous-jacents,
18 instruments, plafond de capital 100 USD.

| # | run_id | Événements | Hypothèses | Survivants falsification | `SURVIVES_ALL_BOUNDS` | Alloué | État du code |
|---|---|---|---|---|---|---|---|
| R1 | `hunt-20260916T150108Z` | 62 237 | 1 693 | **0** | **161** (SHR 85 + AF 76) | 2 | avant correctifs 6, 7, 8 |
| R2 | `hunt-20260916T151832Z` | 52 193 | 1 678 | **0** | **2** (CROSS_MARKET) | 1 | après 6 |
| R3 | `hunt-20260916T153021Z` | 55 630 | 1 690 | **0** | **0** | 0 | après 6, 7 |
| R4 | `hunt-20260916T155232Z` | 48 374 | 1 678 | **0** | **5** (AF) | **0** | après 6, 7, 8 |

**Total : 6 739 hypothèses proposées, 6 739 rejetées, 0 survivant, 4 runs / 4.**

---

## W. La variance 161 → 0 — analyse

C'est le point le plus important du rapport, et il commence par une
**rectification**.

### W.1 Deux entonnoirs, pas un

« 161 survivants puis 0 » **ne décrit pas le même entonnoir aux deux dates**.

- **Entonnoir RECHERCHE** (6 agents → falsification) : **0 survivant aux
  4 runs**. Parfaitement stable. Les 161 n'y sont jamais entrés.
- **Entonnoir DÉTECTEURS** (9 familles → bornes économiques) : 161 → 2 → 0 → 5.

Les 161 étaient des candidates au verdict `SURVIVES_ALL_BOUNDS`, c'est-à-dire
*« capture brute > tous les majorants documentés »*. Ce sont des **survivants
intermédiaires** d'un crible économique, **jamais** des hypothèses ayant
survécu à une falsification.

### W.2 D'où vient l'instabilité

Trois causes, hiérarchisées.

**1. La famille survivante change d'un run à l'autre.** C'est décisif.

| Run | Famille(s) survivante(s) |
|---|---|
| R1 | `SHORT_HORIZON_REVERSION` (85) + `AGGRESSIVE_FLOW` (76) |
| R2 | `CROSS_MARKET` (2) |
| R3 | aucune |
| R4 | `AGGRESSIVE_FLOW` (5) |

Si un phénomène persistant était capté, on attendrait la **même** famille.
Un phénomène qui change de famille toutes les 7 minutes est un **effet de
queue de distribution**, pas une structure de marché.

**2. Ce n'est pas le volume qui bouge, c'est la queue.** Le nombre total de
candidates est remarquablement stable (31 594 / 31 810 / 32 345 / 30 166) et
les proportions par famille aussi. Ce qui varie, c'est le nombre de candidates
franchissant le seuil `capture > 10 bps + ~3 × spread + impact` :
161 / 31 594 = **0,51 %**, puis 0,006 %, 0 %, 0,017 %. On observe la variation
d'une **queue à ~0,5 %**, sur quatre échantillons de 7 minutes. Ce taux n'est
pas estimable à cette durée.

**3. Un artefact de comparaison à l'étage 6.** `validation_pool = survivors or
needs_measurement[:50]`. En R3, faute de survivant, le pool **bascule** sur les
candidates `NEEDS_MEASUREMENT` — une population **plus faible par définition**.
Le « 0 alloué » de R3 ne signifie donc pas « les mêmes candidates ont échoué »,
mais « aucune n'a atteint la barre, et le repli a échoué comme attendu ».
R1 et R3 ne comparent pas la même population.

À quoi s'ajoute que le cap `[:50]` sélectionne les **50 premières rencontrées
dans l'ordre du scan**, pas les 50 meilleures. Le « ACCEPTED 50/50 » de R1
n'est donc pas un classement.

### W.3 Ce qui a été écarté

- **Pas les gardes** : R1 et R2 précèdent des correctifs, mais ceux-ci
  (n° 6, 7) touchent l'exécution PAPER **en aval** du verdict de bornes. Le
  correctif n° 8 agit lui aussi en aval (étage 6), pas sur le comptage des
  bornes — R4 produit 5 survivants aux bornes **malgré** le correctif.
- **Pas les coûts** : les majorants sont fixes (frais 10 bps) ou dérivés du
  spread observé. Pas de changement de règle entre les runs.
- **Pas la latence** : délai de transport médian 122 / — / — / 94 ms, même
  ordre de grandeur.
- **Reste les données et la fenêtre** : volatilité et spreads du moment.
  C'est la seule explication compatible avec les trois observations ci-dessus.

### W.4 Conclusion

> Les 161 n'établissent **rien**. Ils étaient des survivants **intermédiaires**
> d'un crible économique par bornes, jamais validés causalement, et le
> correctif n° 8 montre qu'ils n'auraient même pas dû atteindre l'allocation :
> leur capture supposait une convergence de 100 % jamais mesurée.
>
> La variance 161 → 2 → 0 → 5, avec **changement de famille**, est une
> information sur **l'instabilité de la mesure à 7 minutes d'observation**,
> pas sur l'existence d'un edge.

---

## X. Qualité des données

| Élément | Statut | Détail |
|---|---|---|
| OKX REST `/public/instruments` | **VALIDÉ** | registre chargé, `FAIL CLOSED` sur métadonnées incohérentes ou `state ≠ live` |
| OKX WS `books` (400 niveaux) | **VALIDÉ** | 41 103 updates chaînées, 0 trou |
| OKX WS `books-l2-tbt` | **NON DISPONIBLE** | exige un login VIP |
| checksum OKX | **NON DISPONIBLE** | `checksum: 0` = absent sur le canal `books` ; on ne fabrique pas un échec sur une garantie non offerte |
| OKX `trades` | **VALIDÉ** | 7 202 événements |
| OKX `liquidation-orders` | **PARTIELLEMENT VALIDÉ** | 40 événements / 420 s — trop rare pour `FORCED_FLOW` |
| OKX funding | **VALIDÉ** | lu pour 12 swaps, chacun **sur son propre instId** |
| Hyperliquid | **PARTIELLEMENT VALIDÉ** | joignable, délai de transport ~ 360–800 ms |
| Binance | **NON DISPONIBLE** | HTTP 451 (restriction géographique) |
| Bybit | **NON DISPONIBLE** | HTTP 403 |
| séquence | **VALIDÉ** | régressions et doublons détectés (3 296 doublons au run de clôture) |
| carnet périmé | **VALIDÉ** | `STALE_BOOK` bloquant |
| anomalie d'horloge | **VALIDÉ** | `CLOCK_ANOMALY` bloquant depuis le correctif n° 4 |
| latence aller-retour d'un ordre | **UNKNOWN** | non mesurable sans compte |
| probabilité de fill | **UNKNOWN** | non mesurable sans ordres réels |
| normalisation d'instrument | **VALIDÉ** | spot / `-USDT-SWAP` / `-USD-SWAP` traités comme trois instruments distincts ; aucune table de réécriture (test) |

---

## Y. Latence

Mesurée : **délai de transport exchange → local**, médiane 94 ms, p90 101 ms
(N = 48 334). Sert de `latency_ms` au laboratoire PAPER.

Non mesurée : la **latence aller-retour d'un ordre**. Elle est strictement
supérieure et reste `UNKNOWN`.

Borne de résolution : `EventTimeline.update_interval_ms()` — sous la cadence
de publication, comparer T0 et T0+Δ compare un carnet à lui-même et produit un
**zéro artefactuel**. `is_resolvable(Δ)` refuse ces mesures.

---

## Z. Capacité

`capacity_curve` sonde une grille de notionnels. `total_estimated_cost_bps`
est `None` dès que le carnet est épuisé — **jamais extrapolé**.
`max_notional_without_exhaustion` est la borne de la **grille sondée**, pas un
optimum ni une recommandation de taille.

Le correctif n° 2 a fermé la voie par laquelle un coût mesuré sur une fraction
pouvait être lu comme le coût du notionnel demandé.

---

## AA. CLI

`python3 -m prism_v2.cli <commande>` — **6 commandes, toutes en lecture seule**,
plus `all` :

| Commande | Ce qu'elle montre |
|---|---|
| `status` | état global du moteur |
| `discoveries` | journal des découvertes |
| `failures` | mémoire d'échec, données vs économie |
| `memory` | mémoire de découverte, conditions favorables |
| `ledger` | journal des captures |
| `modes` | barrières d'exécution et pré-requis DEMO/LIVE |

Vérifié en exécution. **Aucune commande promise ailleurs n'est absente** ;
inversement, il n'existe **pas** de commande de rejeu d'un run passé ni
d'export, et ce rapport ne prétend pas le contraire.

---

## AB. Observabilité

Inspectables : runs (JSON complet par run), hypothèses (registre + versions),
rejets (raisons comptées), ledger de découverte (17 champs, append-only),
causes (`blocked_by` / `rejection_reason` / `unresolved_components`), inconnues
(`unknown_components`, `unresolved_essentials`), économie (breakdown par
composante avec qualité), provenance (`Provenance` sur chaque objet),
taux de survie (`survival_rate`, `dominant_rejection`).

Trou connu : les JSON de run sont écrits où l'appelant le demande
(`--json`) et **ne sont pas versionnés**. Un run non sauvegardé est perdu.

---

## AC. Blocages actuels

1. **Frais non `OBSERVED`** (18/18 `ASSUMED`) → `EvaluationMode.EXECUTION`
   bloqué, `SystemMode.DEMO` et `LIVE` bloqués. Exige
   `GET /api/v5/account/trade-fee` (authentifié), absent de V2 **par
   construction**.
2. **Fraction de réversion non mesurée** → toute candidate de microstructure
   est `UNRESOLVED` (correctif n° 8).
3. **Durée d'observation** → N effectif insuffisant ; `CORRELATED_OBSERVATIONS`
   est la raison de rejet dominante (1 599/1 678).
4. **Slippage non `OBSERVED`** → exige des fills réels horodatés.
5. **Deux venues seulement** → Binance et Bybit géo-bloquées.
6. **Pas de test synthétique d'Open Discovery** (§ F, niveau 2).

---

## AD. UNKNOWN restants

`slippage` réel · `fill probability` · `queue position` · `adverse selection`
réelle · `latence aller-retour d'un ordre` · `rejets et re-soumissions
exchange` · `tier de frais réel du compte` · `fraction de réversion` ·
`funding sur horizon > 1 période` · `profondeur à T0+latence`.

Aucun n'est converti en valeur. Chacun rend `total_bps()` ou
`executable_edge_bps` égal à `None`, ou déclenche `UNRESOLVED`.

---

## AE. Préparation DEMO

**BLOQUÉ.** 13 pré-requis, manquants :
`observed_fees`, `account_state_channel`, `order_state_channel`,
`execution_reconciliation`, `api_permissions_checked`.

Satisfaits : `instrument_registry_live`, `data_quality_gate`,
`max_notional_limit`, `daily_loss_limit`, `kill_switch`, `stale_book_guard`,
`latency_guard`, `partial_fill_guard`.

`SystemMode.DEMO.is_implemented == False`.

---

## AF. Préparation LIVE

**BLOQUÉ, et doit le rester.**

| Pré-requis | Statut |
|---|---|
| `instrument_registry_live` | ✅ |
| `data_quality_gate` | ✅ |
| `observed_fees` | ❌ endpoint authentifié absent |
| `observed_slippage` | ❌ exige des fills réels |
| `account_state_channel` | ❌ non implémenté |
| `order_state_channel` | ❌ non implémenté |
| `execution_reconciliation` | ⚠️ `reconciliation.py` existe, jamais exercé sur du réel |
| `api_permissions_checked` | ❌ aucune clé, par conception |
| `max_notional_limit` / `daily_loss_limit` / `kill_switch` | ✅ |
| `stale_book_guard` / `latency_guard` / `partial_fill_guard` | ✅ |
| `demo_validated` | ❌ DEMO non implémenté |
| `human_acknowledgement` | ❌ non fourni |

`MODE_TRANSITIONS` interdit `DISCOVERY → LIVE` ; il faut passer par PAPER puis
DEMO. `SystemMode.LIVE.is_implemented == False`.
Test : `test_live_stays_refused_even_with_every_capability_declared` — LIVE
reste refusé **même si toutes les capacités sont déclarées**.

**Aucun garde n'a été affaibli pendant ce mandat.**

---

## AG. Reproductibilité

**Le pipeline est reproductible ; les runs ne le sont pas.**

Reproductible : le code, les tests (478, déterministes, aucun appel réseau),
les seuils, les majorants, la logique de falsification.

Non reproductible : les runs eux-mêmes. Ils consomment un **flux de marché en
direct** ; `--duration 420` relancé maintenant donnera d'autres chiffres. C'est
précisément ce que démontre § W. Les événements bruts sont écrits dans
`prism_v2/data/events/` (140 Mo, `.gitignore`) et permettent un **replay
identique** tant que le fichier existe ; ils ne survivent pas au conteneur.

Commande : `python3 -m prism_v2.edge_hunt --duration 420 --instruments 6 --json out.json`

---

## AH. Limites connues

1. Espace d'hypothèses **borné à 2 160 gabarits** ; pas de composition, pas de
   synthèse de primitive, pas de relation inter-instruments.
2. Corrections de multiplicité **structurellement correctes, statistiquement
   insuffisantes** (§ S).
3. Split découverte/holdout **temporel unique**, pas de walk-forward multi-plis.
4. L'étage 6 s'appelle « validation causale » et n'est qu'un crible (§ M).
5. `open_discovery_test.audit()` retourne un verdict **en dur**.
6. Cap `[:50]` par **ordre d'arrivée**, pas par qualité.
7. Le correctif n° 8 dépend d'un drapeau posé par les détecteurs (mitigé par
   un second test).
8. `PaperExecutor` est mono-instrument : `CROSS_MARKET`, `CROSS_VENUE`,
   `FUNDING_BASIS` ne sont **jamais** exécutées, même en PAPER.
9. Fenêtres d'observation de 7 minutes — trop courtes, le moteur le dit
   lui-même : *« collecter plus longtemps (N effectif insuffisant) »*.

---

## AI. Statut final

### DISCOVERY STATUS — **OPÉRATIONNEL**
Observe, mesure, propose, falsifie, journalise. 4 runs, 6 739 hypothèses,
6 739 rejets, provenance complète.

### PAPER STATUS — **OPÉRATIONNEL, ET CORRECTEMENT SILENCIEUX**
Aller-retour causal implémenté. Refuse de produire un chiffre sans carnet de
sortie ou pour une candidate à deux jambes. 0 aller-retour exécuté au run de
clôture — par refus, pas par panne.

### DEMO STATUS — **BLOQUÉ** (non implémenté ; 5 pré-requis manquants)

### LIVE STATUS — **BLOQUÉ** (non implémenté ; 8 pré-requis manquants)

---

### CE QUI EST PROUVÉ

- La mécanique **inverse** est correcte et testée contre les formules
  officielles OKX, avec des nombres explicites (31 tests).
- Aucune fonction financière n'accepte un symbole nu ; toutes exigent un
  `InstrumentSpec`.
- Un `UNKNOWN` ne devient jamais 0 — vérifié sur 11 composantes économiques,
  et **trois tentatives réelles de contournement ont été trouvées et fermées**.
- Le red team réfute : 6 739 / 6 739, avec raisons nommées.
- Le router **refuse** : `refused_everything: true` au run de clôture.
- LIVE est inatteignable, même toutes capacités déclarées.
- Aucun secret, aucune clé, aucun endpoint de trading dans le paquet.
- 478 tests verts, dont **108 adversariaux**.

### CE QUI N'EST PAS PROUVÉ

- **Qu'un edge existe.** Zéro hypothèse a survécu, sur 4 runs.
- Que les 161 de R1 valaient quoi que ce soit — § W montre le contraire.
- Que le moteur trouverait un phénomène réel s'il y en avait un : **aucun test
  synthétique n'a été fait** (§ F, niveau 2).
- Que les familles à 0 candidate fonctionnent — non falsifiées faute de données.
- Que la réconciliation tient sur du réel — jamais exercée hors PAPER.

### CE QUI RESTE INCONNU

Voir § AD. En une phrase : **tout ce qui exige soit un compte authentifié,
soit des ordres réellement placés, soit une fenêtre d'observation
substantiellement plus longue.**

### LA MESURE UNIQUE QUI RÉDUIRAIT LE PLUS D'INCERTITUDE

**Mesurer la fraction de réversion réellement récupérée après un déplacement
observé, sur `AGGRESSIVE_FLOW` et `SHORT_HORIZON_REVERSION`, par replay causal
sur une fenêtre longue (≥ 6 heures) d'événements déjà collectables sans
compte.**

Pourquoi celle-ci, dérivée du repo et des données :

1. C'est **la seule inconnue qui bloque les deux entonnoirs à la fois** : le
   correctif n° 8 met en `UNRESOLVED` toutes les candidates de microstructure
   pour cette raison précise, et `EFFECT_BELOW_SPREAD` (1 241 rejets) est
   exactement la question « l'effet dépasse-t-il le spread **après** réversion
   partielle ».
2. Elle ne demande **aucune clé** : le laboratoire PAPER causal existe, le
   collecteur existe, seule la durée manque.
3. Elle attaque `CORRELATED_OBSERVATIONS` (1 599 rejets, raison dominante) :
   à horizon 30 s et pas 500 ms, il faut ~6 h pour atteindre N effectif ≥ 30
   par gabarit — contre 7 minutes aujourd'hui.
4. Son résultat est **décisif dans les deux sens** : une fraction mesurée
   proche de 0 tue définitivement ces deux familles ; une fraction stable et
   significative donnerait, pour la première fois, une capture attendue qui ne
   repose pas sur une hypothèse.

À l'inverse, obtenir des frais `OBSERVED` débloquerait l'exécution mais ne
changerait **aucun** verdict de recherche : ce serait lever un verrou sur une
porte qui ne mène nulle part tant que la mesure ci-dessus n'est pas faite.

---

## Réponse à la question de clôture

> **« Qu'est-ce que EDGE_HUNT prouve réellement aujourd'hui, qu'est-ce qu'il ne
> prouve pas, et pourquoi ? »**

**Il prouve qu'il sait réfuter.** Sur 6 739 hypothèses générées mécaniquement
en données réelles, il en a rejeté 6 739 avec des raisons nommées et
traçables ; il a refusé d'allouer du capital ; il a refusé de produire un
chiffre quand le carnet de sortie manquait ; et pendant cette clôture il a
attrapé un défaut qui laissait une candidate se déclarer non testée tout en
recevant du capital.

**Il ne prouve l'existence d'aucun edge.** Aucune hypothèse n'a survécu. Les
161 survivants d'un run étaient des survivants intermédiaires d'un crible
économique par bornes — pas des découvertes — et ils changeaient de famille
d'un run à l'autre.

**Pourquoi :** parce que sept minutes d'observation ne donnent pas un N
effectif suffisant pour distinguer une queue de distribution d'une structure,
et parce que la grandeur dont tout dépend — la fraction de réversion
réellement récupérée — n'a jamais été mesurée.

*Un moteur qui ne trouve rien et le dit vaut mieux qu'un moteur qui trouve
quelque chose et se trompe. Mais « il n'a rien trouvé » n'est pas « il n'y a
rien à trouver » : c'est « il n'a pas encore regardé assez longtemps ».*
