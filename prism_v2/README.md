# PRISM V2 — moteur de capture économique

Isolé de V33. V33 n'est pas modifié et continue de fonctionner tel quel.

```
MARKET → MARKET STATE → OPPORTUNITY → CAPTURE → CAPACITY/FILL/COST
       → EXPECTED NET CAPTURE → EXECUTION (PAPER) → RECONCILIATION
       → REALIZED PnL → CAPTURE LEDGER
```

**PAPER uniquement.** Aucune classe `RealExecutor`, aucune clé, aucun endpoint
authentifié. `ExecutionMode` ne contient qu'une valeur : `PAPER`.

## Cinq règles, toutes vérifiées par des tests

1. V2 n'importe **rien** de V33.
2. Aucun indicateur technique n'est générateur de signal.
3. Chaque coût porte sa qualité : `OBSERVED` / `DERIVED` / `ASSUMED` / `UNKNOWN`.
4. Un coût `UNKNOWN` ne devient **jamais** zéro → statut `UNRESOLVED`.
5. Toute opportunité, **même rejetée**, produit un enregistrement au ledger.

## Chaîne non contournable

```
DATA QUALITY -> ECONOMICS -> CAPACITY -> RISK -> EXECUTION
             -> RECONCILIATION -> LEDGER -> FAILURE MEMORY
```

Une donnée inutilisable donne `UNRESOLVED`, **jamais** `REJECTED` : « je ne peux
pas mesurer » n'est pas « ce n'est pas rentable ». Confondre les deux fait
abandonner une piste vivante ou poursuivre une piste morte.

## Modules

| Module | Rôle |
|---|---|
| `core_types.py` | qualité, provenance, direction, mode d'exécution |
| `instruments.py` | `InstrumentSpec` + Registry — source unique de vérité |
| `contracts.py` | mécanique financière **inverse-aware** (notionnel, PnL, frais, quantification) |
| `market_data.py` | accès OKX publics, enveloppe `Observation` horodatée |
| `orderbook.py` | carnet L2, spread, VWAP, profondeur, impact — **aucun signal** |
| `costs.py` | modèle de coût typé, `UNKNOWN` propagé |
| `capacity.py` | courbe coût/capacité — **pas d'optimum** |
| `opportunity.py` | interface générique + registry pluggable |
| `economics.py` | Expected Net Capture, `ACCEPTED`/`REJECTED`/`UNRESOLVED` |
| `execution.py` | `PaperExecutor` — fills calculés sur carnet observé |
| `reconciliation.py` | attendu vs réalisé, imputation de l'écart |
| `ledger.py` | Capture Ledger append-only versionné |
| `collector.py` | collecteur L2, durée configurable |
| `quality.py` | qualité des données, **FAIL CLOSED** |
| `risk.py` | kill switches, sizing borné (**pas de Kelly**) |
| `wsclient.py` | client WebSocket RFC 6455, stdlib pure |
| `ws_collector.py` | collecte L2 + trades + liquidations, deux horodatages |
| `replay.py` | replay événementiel, grille de latence, **anti-look-ahead structurel** |
| `failure_memory.py` | taxonomie des causes de rejet |
| `edge_health.py` | distributions avec N, **aucun score magique** |
| `opportunities/dislocation.py` | adaptateur M2, isolé du noyau |

Dépendances : **stdlib seule**.

## Usage

```bash
python3 -m unittest discover -s tests/v2 -t . -p 'test_*.py'   # 227 tests
python3 tests/v2/test_contracts_reference.py --table           # table de référence
python3 -m prism_v2.smoke_test --duration 30 --instruments 5   # pipeline réel
```

## Lire un résultat

`ACCEPTED` ne veut **pas** dire rentable : seulement qu'après *ces* coûts-là,
sur *cette* observation, il reste quelque chose. Toujours lire `weakest_quality`
en même temps que le statut.

## Collecte longue durée

```bash
python3 -c "
from prism_v2.market_data import OKXPublicClient
from prism_v2.ws_collector import EventCollector
from prism_v2.instruments import InstrumentType
c = OKXPublicClient(); reg = c.load_registry(['SWAP'])
univ, _ = reg.executable_universe(InstrumentType.SWAP_INVERSE)
path, st = EventCollector().collect(univ, duration_s=3600)
print(path); print(st.to_dict())
"
```

## Replay causal sur les données collectées

```bash
python3 -c "
from prism_v2.ws_collector import load_events
from prism_v2.replay import EventTimeline, causal_capture, latency_decay_curve
from prism_v2.core_types import Direction
from prism_v2.market_data import OKXPublicClient
reg = OKXPublicClient().load_registry(['SWAP'])
spec = reg.require('BTC-USD-SWAP')
tl = EventTimeline.from_events(spec, load_events('CHEMIN.jsonl'))
print('carnets', len(tl), 'cadence', tl.update_interval_ms(), 'ms')
print(causal_capture(tl, tl.first_ts_ms + 10_000, Direction.LONG, 1000.0,
                     latency_ms=200, hold_ms=2000).to_dict())
"
```

## Passage ultérieur en DEMO

V2 n'a **aucun** chemin vers un ordre réel, par construction. Y aller
demanderait, dans cet ordre :

1. lire les frais réels (`/api/v5/account/trade-fee`) → `fees` passe `ASSUMED` → `OBSERVED` ;
2. exécuter en demo pour mesurer le **slippage réel** → `slippage` quitte `UNKNOWN` ;
3. seulement alors, une évaluation peut sortir de `UNRESOLVED` en posture stricte ;
4. écrire un `DemoExecutor` distinct — `ExecutionMode` devrait gagner une valeur,
   ce qui fera échouer `test_no_real_execution_path` : **c'est voulu**, ce test
   est la barrière.

Rien de tout cela n'a de sens avant qu'une opportunité atteigne `ACCEPTED`.

Voir [`INVERSE_MECHANICS.md`](INVERSE_MECHANICS.md) pour l'audit de la mécanique
`-USD-SWAP`, [`LIMITS.md`](LIMITS.md) pour les limites et UNKNOWN restants, et
[`ledger/README.md`](ledger/README.md) pour la lecture du ledger.
