#!/usr/bin/env python3
"""Garde anti-drift : la logique stratégie ne doit exister QUE dans prism/strategy.py.

Échoue si quelqu'un redéfinit une fonction check_* / prepare / compute_indicators
ou une constante stratégie directement dans backtest_v33.py ou live_monitor_v33.py
au lieu de modifier prism/strategy.py (single source of truth).

Usage : python tests/test_single_source.py  (exit 0 = OK, 1 = drift détecté)
"""
import ast, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SHARED_FUNCS = {
    "compute_indicators", "prepare", "_compute_scores", "_score_size_mult",
    "check_pattern_c", "check_pattern_d", "check_pattern_r", "check_pattern_s",
    "check_pattern_mom", "check_pattern_v",
}
SHARED_CONSTS = {
    "ADX_MIN_C", "ADX_MIN_MOM", "ADX_MIN_S", "RSI_LONG_MAX_D", "RSI_LONG_MIN_D",
    "RSI_MOM_MAX", "RSI_MOM_MIN", "RSI_SHORT_MAX_D", "RSI_SHORT_MIN_D",
    "RSI_S_MAX", "RSI_S_MIN", "SCORE_MIN_V", "SQUEEZE_BARS_C", "VOL_MULT_V",
    "VOL_RATIO_C", "VOL_RATIO_MOM", "_ADX_1BAR",
}

errors = []

for fname in ["backtest_v33.py", "live_monitor_v33.py"]:
    tree = ast.parse((ROOT / fname).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in SHARED_FUNCS:
            errors.append(f"{fname}:{node.lineno} redéfinit {node.name}() — "
                          f"modifier prism/strategy.py à la place")
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in SHARED_CONSTS:
                    errors.append(f"{fname}:{node.lineno} redéfinit {t.id} — "
                                  f"modifier prism/strategy.py à la place")
    src = (ROOT / fname).read_text(encoding="utf-8")
    if "from prism.strategy import" not in src:
        errors.append(f"{fname} n'importe plus prism.strategy")

tree = ast.parse((ROOT / "prism" / "strategy.py").read_text(encoding="utf-8"))
defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
missing = SHARED_FUNCS - defined
if missing:
    errors.append(f"prism/strategy.py : fonctions manquantes {missing}")

if errors:
    print("DRIFT DÉTECTÉ :")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
print("OK — single source of truth intact "
      f"({len(SHARED_FUNCS)} fonctions, {len(SHARED_CONSTS)} constantes)")
