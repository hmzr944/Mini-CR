# Limites, UNKNOWN restants et risques

Document de vérité. Ce qui n'est pas mesuré est écrit ici, pas dissimulé.

## 1. UNKNOWN structurels — ce que V2 ne peut pas mesurer aujourd'hui

| Grandeur | État | Pourquoi | Ce qu'il faudrait |
|---|---|---|---|
| **slippage d'exécution** | `UNKNOWN`, toujours | La simulation PAPER calcule le fill sur le carnet du même instant : elle **contourne** la friction d'arrivée, elle ne la mesure pas. | Des fills réels horodatés (compte demo financé) |
| **frais réels** | `ASSUMED` (Lv1 public : 2/5 bps) | `/api/v5/account/trade-fee` exige une authentification que V2 n'a pas. | Une clé API en lecture seule |
| **adverse selection** | `UNKNOWN` | Exige des fills maker réels et leur dérive post-fill. | Exécution maker réelle |
| **latence d'ordre** | partiellement | Le replay mesure la dégradation du **marché** pendant Δt. Il ne mesure pas la latence réelle d'un aller-retour d'ordre. | Horodatage d'ordres réels |

**Conséquence assumée : en posture stricte, toute évaluation est `UNRESOLVED`.**
C'est la vérité, pas un défaut. Un système qui afficherait `ACCEPTED` ici
mentirait sur ce qu'il sait.

## 2. Limites de mesure

**Résolution temporelle.** Le canal `books5` publie toutes les ~100 ms (BTC) à
~800 ms (DOT). Un Δt inférieur à cette cadence compare un carnet à lui-même :
le résultat serait un **0 artificiel**. Ces points sont marqués `SOUS-RESOLU`
et retournent `UNKNOWN`. La latence sub-100 ms n'est pas mesurable avec `books5`.

**Profondeur.** `books5` ne donne que 5 niveaux — suffisant pour le spread, la
fraîcheur et la dérive, **insuffisant pour la capacité profonde**. Celle-ci
vient du REST `/market/books` (400 niveaux), qui n'est pas un flux d'événements.
Les deux sources ne sont jamais mélangées dans une même mesure.

**Fenêtre.** Les smoke tests couvrent des minutes. Tout coût observé est un
**plancher sur cette fenêtre**, jamais une vérité tous régimes.

**Doublons `books5`.** Le flux renvoie parfois des snapshots au même `seqId`.
Détectés et comptés, sans effet sur les mesures (le carnet est identique).

## 3. Ce que les chiffres du smoke test sont — et ne sont pas

| Chiffre | Nature | Piège à éviter |
|---|---|---|
| Plancher de coût aller-retour | **Borne INFÉRIEURE** du coût | Exclut latence, file d'attente, adverse selection. Le coût réel sera **supérieur**. |
| `gross_capture` de M2 | **Borne SUPÉRIEURE** ex-post | Utilise l'information future. Structurellement non exécutable (`evaluate()` la force à `UNRESOLVED`). |
| Dérive adverse du mid | Mesure réelle | Ne vaut que pour la fenêtre et le notionnel sondés. |
| `sum_realized_pnl_usd` | PnL **PAPER** | Jamais un résultat réel. `ExecutionMode` ne contient que `PAPER`. |

## 4. Risques restants

1. **Aucun edge démontré.** Aucune opportunité n'a jamais atteint `ACCEPTED`.
   Le système n'a rien trouvé — il ne prétend rien avoir trouvé.
2. **M2 reste une hypothèse.** N est faible et la seule mesure disponible est
   une borne supérieure ex-post. `N=1` n'est pas une validation.
3. **Horloge.** Le délai de transport (~90 ms observé) mêle latence réseau et
   dérive d'horloge. Il n'est pas décomposé.
4. **Dépendance à un seul exchange.** Pas de vue cross-venue.
5. **La profondeur affichée n'est pas garantie.** Le `RiskGate` n'en autorise
   qu'une fraction (`max_capacity_fraction`), mais cette fraction est une
   **borne prudente choisie**, pas une mesure de survie de la liquidité.

## 5. Ce qui n'a pas été fait, et pourquoi

| Non fait | Raison |
|---|---|
| Capital Router | Aucune opportunité validée à router. Construire un routeur pour zéro candidat serait de l'infrastructure sans besoin. |
| Exécution maker réelle | Exige des clés API. V2 est PAPER par construction. |
| Autres familles d'opportunités | Aucune donnée ne les justifie aujourd'hui. L'interface les accepte sans modifier le noyau — c'est le point. |
| Backtest sur barres | Le problème est microstructurel. Un résultat issu de barres OHLC ne mesure pas une capture microstructurelle et serait trompeur. |
| ML / prédiction | Hors paradigme. Le système cherche des inefficiences capturables, pas des prédictions. |

## 6. Prochain incrément le moins cher

Laisser tourner le collecteur WebSocket plusieurs heures sur les 15 contrats
inverses, puis rejouer M2 en mode **causal** (`replay.causal_capture`) sur cet
échantillon. Cela donne un N exploitable et la première mesure non ex-post du
mécanisme. Le code est déjà là ; il manque uniquement des données.

---

# Limites du Discovery Engine (ajout)

## Venues

| Venue | État depuis cet environnement |
|---|---|
| OKX | joignable (REST + WebSocket) |
| Hyperliquid | joignable (REST POST), **délai de transport ~400–850 ms** |
| Binance | **HTTP 451** — restriction géographique |
| Bybit | **HTTP 403** |

Le cross-venue se limite donc à OKX ↔ Hyperliquid. Le délai Hyperliquid est
5 à 10× celui d'OKX : une comparaison à cette fraîcheur est structurellement
fragile, et c'est inscrit dans chaque candidate.

## Canaux L2 OKX

| Canal | Accès | Niveaux | Checksum |
|---|---|---|---|
| `books` | public | 400, incrémental | **absent** (`checksum: 0`) |
| `bbo-tbt` | public | 1, tick-by-tick | — |
| `books-l2-tbt` | **refusé** (« Please log in ») | 400 @ 10 ms | oui |
| `books50-l2-tbt` | **refusé** | 50 @ 10 ms | oui |

Les canaux tick-by-tick exigent un niveau VIP. L'intégrité du carnet repose
donc sur le **chaînage `prevSeqId`/`seqId` seul**. Le code vérifie le checksum
dès qu'il est fourni ; il ne revendique pas une garantie qu'il n'a pas.

## Ce que le Discovery Engine ne fait pas

- **Pas de latence sub-100 ms mesurable** : la cadence de `books` la borne.
- **Pas de maker réel** : `adverse_selection` reste `UNKNOWN`, donc les
  familles exigeant `MAKER` ne peuvent pas franchir CAPTURE_VALIDATION.
- **FORCED_FLOW dort le plus souvent** : les liquidations sont rares sur une
  fenêtre de minutes. Ce n'est pas un défaut, c'est la nature de la famille.
- **Le multi-leg n'est pas exécuté** : `CROSS_MARKET` et `CROSS_VENUE`
  produisent des candidates à deux jambes, mais le PaperExecutor n'exécute
  qu'une jambe. Le risque de jambe est **déclaré**, pas simulé.

---

# Limites de la couche RECHERCHE (V3)

## Ce que la falsification contrôle — et ce qu'elle ne peut pas contrôler

18 contrôles sont exercés (`research/falsification.py`). Cinq d'entre eux ne
mordent que si l'appelant **déclare** l'information correspondante ; en son
absence le contrôle est marqué `NON EXERCE` dans les `details` et n'est
**jamais** compté comme un succès :

| Contrôle | Exige | Sans cette donnée |
|---|---|---|
| `effect_below_spread` | `typical_spread_bps` | non exercé |
| `duplicate_liquidity` | `depth_sources` | non exercé |
| `unrealistic_fill` | `touch_depth_usd` | non exercé |
| `out_of_sample_stability` | `holdout_relation` | non exercé |
| `stale_quotes` | `book_age_ms` | non exercé |

Une hypothèse peut donc atteindre `SURVIVED_FALSIFICATION` en ayant échappé à
un contrôle faute de donnée. **Survivre n'est pas être vrai** : c'est n'avoir
pas été réfuté par les contrôles réellement exerçables.

## Ce que le PAPER ne mesure pas

`PAPER_EXCLUDED_FRICTIONS` nomme 5 frictions non simulables (probabilité de
fill maker, position dans la file, adverse selection réelle, latence
aller-retour d'un ordre réel, rejets/re-soumissions). Tout résultat PAPER est
une **borne supérieure**.

## Aller-retour sur un seul carnet

Boucler entrée et sortie sur le **même** carnet donne un brut nul par
construction et un réalisé égal à moins le péage. C'est une mesure de
**plancher de coût**, jamais une capture. Un test architectural
(`test_round_trip_never_closes_on_its_own_entry_book`) interdit ce motif
partout sauf dans `smoke_test.py`, où il est explicitement étiqueté
`EXECUTION_CONTROL_PAPER` avec `gross = 0`.

## Candidates à deux jambes

`PaperExecutor` est **mono-instrument**. Une candidate `TAKER_BOTH_LEGS`
(CROSS_MARKET, CROSS_VENUE) n'est pas exécutée en PAPER : n'en simuler qu'une
jambe produirait le PnL d'une position inexistante. Ces familles restent donc
mesurées mais **jamais exécutées**, y compris en PAPER.

## Frais

Aucun instrument ne dispose de frais `OBSERVED` : cela exige
`GET /api/v5/account/trade-fee`, endpoint **authentifié**, absent de V2 par
construction. `EvaluationMode.EXECUTION` est donc structurellement bloqué.
Les frais `ASSUMED` (barème public Lv1) n'autorisent jamais un engagement de
capital.
