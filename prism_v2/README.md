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
| `opportunities/dislocation.py` | adaptateur M2, isolé du noyau |

Dépendances : **stdlib seule**.

## Usage

```bash
python3 -m unittest discover -s tests/v2 -t . -p 'test_*.py'   # 137 tests
python3 tests/v2/test_contracts_reference.py --table           # table de référence
python3 -m prism_v2.smoke_test --duration 30 --instruments 5   # pipeline réel
```

## Lire un résultat

`ACCEPTED` ne veut **pas** dire rentable : seulement qu'après *ces* coûts-là,
sur *cette* observation, il reste quelque chose. Toujours lire `weakest_quality`
en même temps que le statut.

Voir [`INVERSE_MECHANICS.md`](INVERSE_MECHANICS.md) pour l'audit complet de la
mécanique `-USD-SWAP`, et [`ledger/README.md`](ledger/README.md) pour la lecture
du ledger.
