"""PRISM V2 — moteur de capture economique.

Paradigme (verrouille) :
    MARKET -> MARKET STATE -> OPPORTUNITY -> CAPTURE -> CAPACITY/FILL/COST
    -> EXPECTED NET CAPTURE -> (ROUTER) -> EXECUTION -> RECONCILIATION
    -> REALIZED PnL -> LEDGER

Regles structurelles, verifiees par tests/v2/test_architecture.py :
  1. V2 n'importe RIEN de V33 (pas de prism.strategy, pas de backtest_v33).
  2. Aucun indicateur technique n'est utilise comme generateur de signal.
  3. Aucun cout n'est suppose vrai : chaque composante porte sa qualite
     (OBSERVED / DERIVED / ASSUMED / UNKNOWN).
  4. Un cout UNKNOWN ne devient JAMAIS zero : l'evaluation reste UNRESOLVED.
  5. Toute opportunite, meme rejetee, produit un enregistrement au ledger.

Dependances : stdlib uniquement (pas de numpy/pandas/requests).
"""

__version__ = "2.0.0"
SCHEMA_VERSION = 1
