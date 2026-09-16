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
