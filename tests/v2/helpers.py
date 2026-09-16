"""Utilitaires d'inspection statique partages par les tests architecturaux."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Set

#: Jetons interdits dans un IDENTIFIANT de code V2, compares par TOKEN
#: (decoupage sur "_"), jamais par sous-chaine : "reversion_bps" contient
#: "rsi" et doit rester legitime. La prose (docstrings, commentaires) est
#: libre d'y faire reference pour expliquer l'interdit.
BANNED_TOKENS = frozenset({
    "rsi", "macd", "adx", "bollinger", "bb", "bbw", "stoch",
    "ema", "ema9", "ema21", "ema50", "sma", "atr",
})

#: NOTE sur "vwap" — volontairement ABSENT de la liste.
#: Le VWAP d'execution (prix moyen obtenu en marchant le carnet pour un
#: notionnel donne) est une mesure de COUT, et c'est l'une des grandeurs
#: centrales de V2. Il n'a rien a voir avec l'indicateur VWAP de V33
#: (moyenne glissante 24 barres du prix typique, utilisee comme composante
#: de score directionnel : `if cl > vw: bs += 10`).
#: La distinction porte sur l'USAGE, pas sur le nom, et ne peut donc pas
#: etre tranchee par inspection lexicale. Elle est garantie autrement :
#:   - test_no_signal_functions_exist : OrderBook n'expose aucun predicteur ;
#:   - test_no_v33_import_anywhere_in_v2 : aucune fonction de signal importee ;
#:   - Opportunity.detect ne recoit jamais de serie glissante d'indicateurs.

#: Identifiants complets interdits (heritage V33).
BANNED_EXACT = frozenset({
    "vol_ratio", "buy_sc", "sell_sc", "score_min", "macd_hist", "macd_slope",
    "bbw_q15", "bb_upper", "bb_lower", "di_plus", "di_minus", "squeeze_bars_c",
    "compute_indicators", "check_pattern_c", "check_pattern_d", "check_pattern_r",
    "check_pattern_s", "check_pattern_mom", "check_pattern_v", "_compute_scores",
    "_score_size_mult",
})


def banned_identifiers(names) -> list:
    """Identifiants violant l'interdit, par comparaison de tokens."""
    out = []
    for ident in names:
        if ident in BANNED_EXACT:
            out.append(ident)
            continue
        if any(part in BANNED_TOKENS for part in ident.split("_") if part):
            out.append(ident)
    return sorted(set(out))


def code_identifiers(path: Path) -> Set[str]:
    """Tous les identifiants reellement utilises dans le code, en minuscules.

    Exclut docstrings et commentaires : on juge ce que le code FAIT, pas ce
    qu'il dit.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg.lower())
    return names


def imported_modules(path: Path) -> Set[str]:
    """Modules importes par un fichier (noms de plus haut niveau et complets)."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    mods: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mods.add(node.module)
                mods.add(node.module.split(".")[0])
    return mods
