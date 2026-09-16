#!/usr/bin/env python3
"""CLI d'inspection — repondre immediatement aux questions du laboratoire.

    python3 -m prism_v2.cli status        etat general
    python3 -m prism_v2.cli discoveries   entonnoir des decouvertes
    python3 -m prism_v2.cli failures      pourquoi les opportunites meurent
    python3 -m prism_v2.cli memory        conditions -> issues
    python3 -m prism_v2.cli ledger        opportunites enregistrees
    python3 -m prism_v2.cli modes         barrieres DISCOVERY/PAPER/DEMO/LIVE
    python3 -m prism_v2.cli all           tout

N'execute AUCUN ordre et n'appelle AUCUN endpoint prive : lecture seule des
fichiers de memoire produits par edge_hunt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2 import __version__
from prism_v2.discovery_memory import DiscoveryMemory
from prism_v2.edge_health import EdgeHealth
from prism_v2.failure_memory import FailureMemory
from prism_v2.ledger import CaptureLedger
from prism_v2.modes import MODE_PREREQUISITES, ModeGate, SystemMode
from prism_v2.research.discovery_ledger import DiscoveryLedger


def _hr(t: str) -> None:
    print(f"\n{'=' * 76}\n{t}\n{'=' * 76}")


def _dump(d: Dict[str, Any]) -> None:
    print(json.dumps(d, indent=1, ensure_ascii=False, default=str))


def cmd_status() -> None:
    _hr(f"PRISM V2 — etat (moteur {__version__})")
    dl = DiscoveryLedger()
    cl = CaptureLedger()
    dm = DiscoveryMemory()
    dsum, csum = dl.summary(), cl.summary()
    print(f"decouvertes enregistrees : {dsum['n_discoveries']}")
    print(f"  survivantes a la falsification : {dsum['n_survived']}")
    print(f"opportunites au ledger de capture : {csum['n_records']}")
    print(f"  executees en PAPER : {csum['n_executed_paper']}")
    print(f"  PnL PAPER cumule : {csum['sum_realized_pnl_usd']}")
    print(f"modes d'execution presents : {csum['execution_modes']}")
    print(f"\nmemoire de decouverte : {Path(dm.path).name}")
    print(f"mode systeme : DISCOVERY/PAPER implementes, DEMO/LIVE verrouilles")


def cmd_discoveries() -> None:
    _hr("ENTONNOIR DES DECOUVERTES")
    s = DiscoveryLedger().summary()
    print(f"total : {s['n_discoveries']}")
    print(f"\npar statut de falsification :")
    _dump(s["by_falsification"])
    print(f"\nraisons de rejet (red team) :")
    _dump(s["rejection_reasons"])
    print(f"\npar mecanisme economique propose :")
    _dump(s["by_mechanism"])
    print(f"\n{s['note']}")


def cmd_failures() -> None:
    _hr("POURQUOI LES OPPORTUNITES MEURENT")
    rows = CaptureLedger().read_all()
    if not rows:
        print("aucune opportunite enregistree — lancer edge_hunt d'abord")
        return
    fm = FailureMemory.from_records(rows)
    d = fm.to_dict()
    print("causes :")
    _dump(d["counts"])
    print("\nrepartition en trois causes (decide de l'action) :")
    _dump(d["three_way_split"])
    print("\npar instrument :")
    _dump(d["by_instrument"])


def cmd_memory() -> None:
    _hr("MEMOIRE DE DECOUVERTE — conditions -> issues")
    m = DiscoveryMemory().load()
    r = m.report(min_n=5)
    print(f"observations : {r['n_observations']} | concluantes : {r['n_conclusive']}")
    print(f"lisible : {r['readable']} — {r['note']}")
    if r["favourable_conditions"]:
        print("\nconditions les plus favorables (N suffisant) :")
        for c in r["favourable_conditions"][:10]:
            print(f"  {c['dimension']:<16}{c['bucket']:<26}"
                  f"survie {c['survival_rate']:.0%} (N={c['n_conclusive']})")
    if r["destructive_conditions"]:
        print("\nconditions qui detruisent systematiquement la capture :")
        for c in r["destructive_conditions"][:10]:
            print(f"  {c['dimension']:<16}{c['bucket']:<26}N={c['n_conclusive']}")


def cmd_ledger() -> None:
    _hr("LEDGER DE CAPTURE")
    cl = CaptureLedger()
    _dump(cl.summary())
    rows = cl.read_all()
    if rows:
        eh = EdgeHealth.from_records(rows)
        print("\nsante de l'edge :")
        _dump(eh.report()["evidence"])


def cmd_modes() -> None:
    _hr("BARRIERES D'EXECUTION")
    g = ModeGate()
    print(f"mode courant : {g.mode.value}")
    for m in SystemMode:
        print(f"  {m.value:<12} implemente={m.is_implemented} "
              f"capital_reel={m.touches_real_capital}")
    print(f"\npre-requis DEMO ({len(MODE_PREREQUISITES[SystemMode.DEMO])}) :")
    for c in MODE_PREREQUISITES[SystemMode.DEMO]:
        print(f"  - {c}")
    print(f"\npre-requis LIVE ({len(MODE_PREREQUISITES[SystemMode.LIVE])}) :")
    for c in MODE_PREREQUISITES[SystemMode.LIVE]:
        print(f"  - {c}")
    print("\nLIVE n'est pas implemente : aucun executeur reel n'existe dans V2.")


COMMANDS = {"status": cmd_status, "discoveries": cmd_discoveries,
            "failures": cmd_failures, "memory": cmd_memory,
            "ledger": cmd_ledger, "modes": cmd_modes}


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspection PRISM V2 (lecture seule)")
    ap.add_argument("command", choices=sorted(COMMANDS) + ["all"])
    a = ap.parse_args()
    if a.command == "all":
        for name in ("status", "discoveries", "failures", "memory", "ledger", "modes"):
            try:
                COMMANDS[name]()
            except Exception as exc:
                print(f"\n[{name}] indisponible: {type(exc).__name__}: {exc}")
    else:
        COMMANDS[a.command]()


if __name__ == "__main__":
    main()
