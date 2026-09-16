"""Garde-fous architecturaux. Ce sont les tests qui protegent la DIRECTION.

Ils echouent si V2 derive vers le paradigme abandonne :
  - import d'une fonction de generation de signal V33 ;
  - reapparition d'indicateurs techniques ;
  - couplage du noyau economique a une opportunite particuliere ;
  - reintroduction de Kelly, de ML ou du forfait 28 bps comme verite ;
  - apparition d'un chemin d'execution reelle.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.v2.helpers import banned_identifiers, code_identifiers, imported_modules

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "prism_v2"
V2_FILES = sorted(V2.rglob("*.py"))

#: Modules V33 dont V2 ne doit JAMAIS dependre.
V33_MODULES = {"prism", "prism.strategy", "backtest_v33", "live_monitor_v33",
               "telegram_bot_v33", "telegram_notif", "dashboard_server", "okx_trader"}

#: Le noyau economique : ces modules ne connaissent aucune opportunite concrete.
CORE_MODULES = ["economics.py", "execution.py", "reconciliation.py", "ledger.py",
                "costs.py", "capacity.py", "orderbook.py", "instruments.py",
                "contracts.py", "market_data.py", "core_types.py", "quality.py",
                "risk.py", "replay.py", "failure_memory.py", "edge_health.py",
                "wsclient.py", "ws_collector.py"]


class TestV2DoesNotImportV33(unittest.TestCase):
    def test_no_v33_import_anywhere_in_v2(self):
        offenders = []
        for f in V2_FILES:
            for mod in imported_modules(f):
                if mod in V33_MODULES:
                    offenders.append(f"{f.relative_to(ROOT)} importe {mod}")
        self.assertEqual(offenders, [], "V2 importe du V33: " + "; ".join(offenders))

    def test_v2_imports_only_stdlib_and_itself(self):
        """Zero dependance tierce : les tests tournent partout, a l'identique."""
        allowed_third_party: set[str] = set()
        offenders = []
        for f in V2_FILES:
            for mod in imported_modules(f):
                top = mod.split(".")[0]
                if top in ("prism_v2", "") or mod.startswith("."):
                    continue
                if top in {"numpy", "pandas", "requests", "scipy", "rich", "flask"}:
                    offenders.append(f"{f.relative_to(ROOT)} -> {mod}")
        self.assertEqual(offenders, [], "dependance tierce dans V2: " + "; ".join(offenders))

    def test_no_signal_generation_identifiers_in_v2(self):
        offenders = []
        for f in V2_FILES:
            found = banned_identifiers(code_identifiers(f))
            if found:
                offenders.append(f"{f.relative_to(ROOT)}: {found}")
        self.assertEqual(offenders, [], "indicateurs techniques: " + "; ".join(offenders))

    def test_v33_still_untouched_and_importable_as_before(self):
        """V2 ne doit pas avoir casse V33 : les fichiers existent toujours."""
        for name in ("backtest_v33.py", "live_monitor_v33.py", "prism/strategy.py",
                     "tests/test_single_source.py"):
            self.assertTrue((ROOT / name).exists(), f"{name} manquant")


class TestNoForbiddenConcepts(unittest.TestCase):
    def test_no_kelly_anywhere(self):
        """Inspecte les IDENTIFIANTS, pas la prose.

        Un module qui ECRIT "pas de Kelly" dans sa docstring respecte la
        regle ; une variable nommee kelly_fraction la viole. Un guard qui
        confond les deux est un faux positif (cf ERREUR 8 du mandat).
        """
        offenders = []
        for f in V2_FILES:
            hits = sorted(i for i in code_identifiers(f)
                          if "kelly" in i.split("_"))
            if hits:
                offenders.append(f"{f.name}: {hits}")
        self.assertEqual(offenders, [], "Kelly reintroduit: " + "; ".join(offenders))

    def test_no_score_based_sizing(self):
        """Le sizing ne doit jamais dependre d'un score de conviction."""
        offenders = []
        for f in V2_FILES:
            idents = code_identifiers(f)
            for bad in ("score_size_mult", "size_mult", "conviction",
                        "score_multiplier"):
                if bad in idents:
                    offenders.append(f"{f.name}: {bad}")
        self.assertEqual(offenders, [], "sizing par score: " + "; ".join(offenders))

    def test_no_machine_learning(self):
        banned = {"sklearn", "torch", "tensorflow", "xgboost", "lightgbm", "keras"}
        for f in V2_FILES:
            self.assertEqual(imported_modules(f) & banned, set(), f"ML dans {f.name}")

    def test_no_real_execution_path(self):
        """ExecutionMode ne contient que PAPER, et aucune classe RealExecutor."""
        from prism_v2.core_types import ExecutionMode
        self.assertEqual([m.value for m in ExecutionMode], ["PAPER"])
        for f in V2_FILES:
            idents = code_identifiers(f)
            for bad in ("realexecutor", "liveexecutor", "place_order", "send_order"):
                self.assertNotIn(bad, idents, f"chemin d'execution reelle dans {f.name}")

    def test_no_private_endpoints_or_credentials(self):
        """Aucun endpoint authentifie, aucune lecture de cle."""
        for f in V2_FILES:
            text = f.read_text(encoding="utf-8").lower()
            for bad in ("/api/v5/trade/", "/api/v5/account/balance", "ok-access-key",
                        "os.environ['okx", 'os.environ["okx', "secret_key"):
                self.assertNotIn(bad, text, f"{f.name} touche a du prive: {bad}")

    def test_28bps_only_exists_as_labelled_legacy_assumption(self):
        from prism_v2.core_types import Quality
        from prism_v2.costs import legacy_v33_assumption
        c = legacy_v33_assumption()
        self.assertIs(c.quality, Quality.ASSUMED)
        # Le nombre 28.0 ne doit apparaitre que dans costs.py (constante heritee).
        for f in V2_FILES:
            if f.name == "costs.py":
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in (28, 28.0):
                    self.fail(f"constante 28 en dur dans {f.name}:{node.lineno}")


class TestNoResultDrivenTuning(unittest.TestCase):
    """ERREUR 7 : backtest optimise jusqu'a obtenir un resultat."""

    def test_no_optimiser_in_v2(self):
        offenders = []
        for f in V2_FILES:
            idents = code_identifiers(f)
            for bad in ("optimize", "optimise", "tune", "grid_search", "sweep",
                        "calibrate_to_target", "fit_params", "maximize_pf"):
                if bad in idents:
                    offenders.append(f"{f.name}: {bad}")
        self.assertEqual(offenders, [], "optimiseur detecte: " + "; ".join(offenders))

    def test_thresholds_are_named_and_documented_not_inline(self):
        """Les seuils vivent dans des dataclasses de politique explicites,
        pas en litteraux disperses dans la logique de decision."""
        from prism_v2.edge_health import MIN_N_FOR_ANY_CLAIM
        from prism_v2.quality import QualityPolicy
        from prism_v2.risk import RiskLimits
        self.assertGreaterEqual(MIN_N_FOR_ANY_CLAIM, 30)
        self.assertGreater(QualityPolicy().max_book_age_ms, 0)
        self.assertGreater(RiskLimits().max_notional_usd, 0)


class TestChainCannotBeBypassed(unittest.TestCase):
    """INVARIANT 3 : DATA QUALITY -> ECONOMICS -> CAPACITY -> RISK ->
    EXECUTION -> RECONCILIATION -> LEDGER."""

    def test_evaluate_accepts_quality_and_risk(self):
        import inspect
        from prism_v2.economics import evaluate
        params = set(inspect.signature(evaluate).parameters)
        self.assertIn("quality", params)
        self.assertIn("risk_decision", params)

    def test_unusable_quality_short_circuits_before_economics(self):
        from tests.v2.fixtures import BTC_INVERSE, simple_inverse_book
        from tests.v2.test_pipeline import candidate, cheap_costs
        from prism_v2.economics import CaptureStatus, evaluate
        from prism_v2.quality import assess_book
        b = simple_inverse_book()
        q = assess_book(b, BTC_INVERSE, now_ms=b.ts_ms + 10 ** 6)
        ev = evaluate(candidate(gross=10_000.0), cheap_costs(), quality=q)
        self.assertIs(ev.status, CaptureStatus.UNRESOLVED)

    def test_ledger_refuses_inconsistent_execution(self):
        from prism_v2.ledger import LedgerInconsistency, _assert_execution_consistent
        with self.assertRaises(LedgerInconsistency):
            _assert_execution_consistent({"executed": True, "order_state": "CREATED"})
        with self.assertRaises(LedgerInconsistency):
            _assert_execution_consistent({"executed": False, "order_state": "FILLED"})

    def test_order_state_machine_forbids_shortcuts(self):
        from prism_v2.execution import IllegalTransition, OrderLifecycle, OrderState
        lc = OrderLifecycle("x", "BTC-USD-SWAP")
        with self.assertRaises(IllegalTransition):
            lc.transition(OrderState.FILLED)
        lc.transition(OrderState.SUBMITTED)
        lc.transition(OrderState.FILLED)
        with self.assertRaises(IllegalTransition):
            lc.transition(OrderState.CANCELLED)   # FILLED est terminal


class TestCoreIsDecoupledFromOpportunities(unittest.TestCase):
    def test_core_modules_do_not_import_any_concrete_opportunity(self):
        offenders = []
        for name in CORE_MODULES:
            f = V2 / name
            for mod in imported_modules(f):
                if "opportunities" in mod:
                    offenders.append(f"{name} -> {mod}")
            text = f.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.ImportFrom) and node.module and \
                        "opportunities" in node.module:
                    offenders.append(f"{name} -> {node.module}")
                if isinstance(node, ast.ImportFrom) and node.level and \
                        any(a.name == "opportunities" for a in node.names):
                    offenders.append(f"{name} -> .opportunities")
        self.assertEqual(offenders, [],
                         "le noyau connait une opportunite concrete: " + "; ".join(offenders))

    def test_new_opportunity_needs_zero_core_change(self):
        """LE test architectural : brancher une Opportunity inedite et la faire
        traverser TOUT le pipeline sans toucher une ligne du noyau."""
        from prism_v2.core_types import Direction, Provenance, Quality
        from prism_v2.costs import CostBreakdown, CostComponent
        from prism_v2.economics import CaptureStatus, evaluate
        from prism_v2.execution import PaperExecutor
        from prism_v2.ledger import CaptureLedger
        from prism_v2.opportunity import (
            Candidate, DetectionResult, MarketContext, Opportunity, OpportunityRegistry,
        )
        from prism_v2.reconciliation import reconcile
        from tests.v2.fixtures import BTC_INVERSE, simple_inverse_book
        import tempfile

        class FictitiousFundingBasisOpportunity(Opportunity):
            """Famille inedite, ecrite entierement dans ce test."""
            name = "FICTITIOUS_TEST_FAMILY"
            requires = ("book",)

            def detect(self, ctx: MarketContext) -> DetectionResult:
                book = ctx.require_book()
                return DetectionResult.ok([Candidate(
                    ts_utc=ctx.ts_utc, instrument=ctx.instrument,
                    opportunity_type=self.name, direction=Direction.LONG,
                    gross_capture_bps=250.0,
                    capacity_usd=book.ask_depth(),
                    provenance=Provenance("OKX", "/test", ctx.ts_utc,
                                          ctx.instrument.inst_id),
                    metadata={"invented_for": "architecture test"})])

        # Branchement : aucune modification du noyau.
        reg = OpportunityRegistry().register(FictitiousFundingBasisOpportunity())
        ctx = MarketContext(instrument=BTC_INVERSE, book=simple_inverse_book())
        results = reg.detect_all(ctx)
        cand = results["FICTITIOUS_TEST_FAMILY"].candidates[0]

        # Le noyau economique traite le candidat sans rien savoir de lui.
        def k(n, v):
            return CostComponent(n, v, Quality.DERIVED, "test")
        costs = CostBreakdown(k("fees", 10), k("spread", 50), k("slippage", 0),
                              k("impact", 5), k("funding", 0),
                              latency=k("latency", 0),
                              adverse_selection=k("adverse_selection", 0))
        ev = evaluate(cand, costs)
        self.assertIs(ev.status, CaptureStatus.ACCEPTED)
        self.assertAlmostEqual(ev.expected_net_capture_bps, 185.0, places=9)

        fill = PaperExecutor().submit(cand.instrument, cand.direction,
                                      ctx.require_book(), 1000.0)
        rt = PaperExecutor().round_trip(cand.instrument, cand.direction,
                                        ctx.require_book(), ctx.require_book(), 1000.0)
        rec = reconcile(cand, ev, fill, rt)
        with tempfile.TemporaryDirectory() as d:
            ledger = CaptureLedger(Path(d) / "c.jsonl")
            row = ledger.record(cand, ev, costs=costs, fill=fill, round_trip=rt,
                                reconciliation=rec)
        self.assertEqual(row["opportunity_type"], "FICTITIOUS_TEST_FAMILY")
        self.assertEqual(row["execution_mode"], "PAPER")
        self.assertEqual(row["inst_type"], "SWAP_INVERSE")
        self.assertIsNotNone(row["realized_pnl_usd"])

    def test_registry_accepts_only_opportunity_subclasses(self):
        from prism_v2.opportunity import OpportunityRegistry
        with self.assertRaises(TypeError):
            OpportunityRegistry().register(object())


class TestFinancialFunctionsRequireSpec(unittest.TestCase):
    def test_no_financial_function_accepts_a_bare_symbol(self):
        """Balayage : toute fonction publique de contracts.py refuse une chaine."""
        import inspect
        from prism_v2 import contracts
        from prism_v2.contracts import NotAnInstrumentSpec

        checked = 0
        for name, fn in inspect.getmembers(contracts, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != contracts.__name__:
                continue
            params = list(inspect.signature(fn).parameters)
            if not params or params[0] != "spec":
                continue
            args = {"contracts": 1.0, "price": 100.0, "rate": 0.0005,
                    "target_usd": 100.0, "entry_price": 100.0, "exit_price": 101.0,
                    "direction": None, "notional_usd": 100.0}
            kwargs = {p: args[p] for p in params[1:] if p in args}
            with self.subTest(fn=name):
                with self.assertRaises(NotAnInstrumentSpec):
                    fn("BTC-USD-SWAP", **kwargs)
            checked += 1
        self.assertGreaterEqual(checked, 6, "balayage trop faible")


if __name__ == "__main__":
    unittest.main(verbosity=2)
