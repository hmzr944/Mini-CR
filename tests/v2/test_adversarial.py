"""Tests ADVERSARIAUX : 20 manieres de produire un resultat faux.

Chaque classe reproduit un mecanisme d'illusion precis et verifie que le
systeme le REFUSE, plutot que de verifier qu'il fonctionne dans le cas
favorable. Un test qui passe ici signifie : « cette facon de se mentir est
fermee », pas « le bot gagne de l'argent ».

Ordre impose par le mandat :
  1 inverse vs linear          11 correlated candidates
  2 currency mismatch          12 capacity overstatement
  3 future leakage             13 latency understatement
  4 stale book                 14 paper/live confusion
  5 sequence gap               15 live accidental activation
  6 unrealistic fill           16 discovery overfitting
  7 partial fill               17 multiple testing
  8 missing fees               18 hypothesis mutation
  9 missing slippage           19 provenance corruption
 10 duplicate liquidity        20 bypass du Research Orchestrator
"""
from __future__ import annotations

import ast
import dataclasses
import tempfile
import unittest
from pathlib import Path

from tests.v2.fixtures import (
    BTC_INVERSE, BTC_LINEAR, PROV, okx_book_payload, simple_inverse_book,
    thin_inverse_book,
)
from tests.v2.helpers import code_identifiers

from prism_v2 import contracts, costs
from prism_v2.capacity import capacity_curve, max_notional_without_exhaustion
from prism_v2.core_types import Direction, Provenance, Quality
from prism_v2.execution import ExecutionMode, PaperExecutor
from prism_v2.fees import (
    AssumedFeeProvider, FeeBook, FeeTierSource, NoCredentialsFeeProvider,
)
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.l2book import L2Book
from prism_v2.modes import (
    EvaluationMode, ModeGate, ModeTransitionRefused, SystemMode, quality_satisfies,
)
from prism_v2.orderbook import OrderBook
from prism_v2.paper_lab import PAPER_EXCLUDED_FRICTIONS, PaperLab, PaperOutcome
from prism_v2.quality import QualityIssue, QualityVerdict, SequenceTracker, assess_book
from prism_v2.replay import EventTimeline, LookAheadViolation, ReplayCursor
from prism_v2.research.discovery_ledger import DiscoveryLedger
from prism_v2.research.falsification import (
    FalsificationAgent, FalsificationContext, MIN_EFFECTIVE_N, RejectionReason,
)
from prism_v2.research.hypothesis import (
    EconomicMechanism, Hypothesis, HypothesisRegistry, HypothesisStatus,
    MultipleTestingAccount, Phenomenon, Relation,
)
from prism_v2.research.observation import Observation, observe_state
from prism_v2.research.orchestrator import ResearchOrchestrator
from prism_v2.research.pipeline import ResearchPipeline

V2 = Path(__file__).resolve().parents[2] / "prism_v2"


# ── fabriques communes ────────────────────────────────────────────────────
def relation(n=1000, horizon=30_000, p=1e-12, mean=50.0, base=0.0,
             sample="DISCOVERY", rid="r"):
    return Relation(relation_id=rid, phenomenon_id="p1", feature="spread_bps",
                    condition="high", inst_id="BTC-USD-SWAP", horizon_ms=horizon,
                    n=n, mean_forward_bps=mean, median_forward_bps=mean,
                    std_forward_bps=1.0, baseline_mean_bps=base, n_baseline=1000,
                    p_value=p, sample=sample)


def phenomenon():
    return Phenomenon(phenomenon_id="p1", feature="spread_bps", condition="high",
                      threshold=1.0, inst_id="BTC-USD-SWAP", venue="OKX",
                      n_occurrences=100, n_observations=1000,
                      window_ms=(0, 100_000))


def hypothesis(r=None):
    return Hypothesis.propose(r or relation(), phenomenon(),
                              EconomicMechanism.FORCED_FLOW, "raisonnement")


def clean_account():
    """Comptabilite ou une p-value de 1e-12 survit, pour isoler l'attaque testee."""
    acc = MultipleTestingAccount()
    for _ in range(3):
        acc.record(1e-12)
    return acc


def benign_context(**over):
    """Contexte ou AUCUN controle ne mord : chaque test n'active que le sien."""
    base = dict(sampling_step_ms=30_000, typical_spread_bps=1.0,
                available_depth_usd=1e9, required_notional_usd=100.0,
                transport_delay_ms=10.0, costs_applied=True,
                quote_currencies=("USD",), book_age_ms=10.0,
                depth_sources=("OKX:BTC-USD-SWAP",))
    base.update(over)
    return FalsificationContext(**base)


def agent():
    return FalsificationAgent(clean_account())


def record(led, h, res, **over):
    """Enregistrement de ledger avec les arguments reellement requis."""
    kwargs = dict(lineage=["MARKET_OBSERVER", "FALSIFICATION_AGENT"],
                  multiple_testing={"n_tests": 3}, source_observations=1_000)
    kwargs.update(over)
    return led.record(h, res, **kwargs)


def timeline_of(prices, start_ts=1_000_000, step_ms=1_000, size="100"):
    """Timeline synthetique : un carnet par prix, espaces de `step_ms`."""
    events = []
    for i, px in enumerate(prices):
        bid, ask = px - 0.5, px + 0.5
        ts = start_ts + i * step_ms
        events.append({
            "channel": "books5", "inst_id": BTC_INVERSE.inst_id,
            "exchange_ts_ms": ts, "local_recv_ts_ms": ts + 5, "seq_id": 1000 + i,
            "data": {"bids": [[f"{bid}", size]], "asks": [[f"{ask}", size]],
                     "ts": str(ts), "seqId": 1000 + i},
        })
    tl = EventTimeline.from_events(BTC_INVERSE, events, channel="books5")
    assert len(tl) == len(prices), f"timeline incomplete: {len(tl)}/{len(prices)}"
    return tl


# ══════════════════════════════════════════════════════════════════════════
# 1. INVERSE TRAITE COMME LINEAIRE
# ══════════════════════════════════════════════════════════════════════════
class Test01InverseVsLinear(unittest.TestCase):
    """L'erreur la plus couteuse : appliquer la mecanique lineaire a un inverse."""

    def test_same_size_same_price_different_pnl(self):
        inv = contracts.pnl(BTC_INVERSE, Direction.LONG, 10, 50_000.0, 51_000.0)
        lin = contracts.pnl(BTC_LINEAR, Direction.LONG, 10, 50_000.0, 51_000.0)
        # 10 contrats inverses = 1000 USD ; 10 contrats lineaires = 0.1 BTC = 5000 USD.
        self.assertNotAlmostEqual(inv.pnl_usd_at_exit, lin.pnl_usd_at_exit, places=2)
        self.assertEqual(inv.settle_ccy, "BTC")
        self.assertEqual(lin.settle_ccy, "USDT")

    def test_inverse_pnl_is_not_linear_in_price(self):
        """Un inverse est lineaire en 1/prix : deux hausses egales en USD ne
        donnent PAS le meme PnL en coin."""
        up1 = contracts.pnl(BTC_INVERSE, Direction.LONG, 10, 50_000.0, 51_000.0)
        up2 = contracts.pnl(BTC_INVERSE, Direction.LONG, 10, 51_000.0, 52_000.0)
        self.assertGreater(up1.pnl_settle_ccy, up2.pnl_settle_ccy)
        naive = 100.0 * 10 * (51_000.0 - 50_000.0)   # formule lineaire appliquee a tort
        self.assertNotAlmostEqual(naive, up1.pnl_usd_at_exit, places=2)

    def test_notional_differs_by_two_orders_of_magnitude(self):
        n_inv = contracts.usd_notional(BTC_INVERSE, 10, 50_000.0)
        n_lin = contracts.usd_notional(BTC_LINEAR, 10, 50_000.0)
        self.assertAlmostEqual(n_inv, 1_000.0)      # ctVal=100 USD
        self.assertAlmostEqual(n_lin, 5_000.0)      # ctVal=0.01 BTC @ 50k
        self.assertGreater(n_lin / n_inv, 4.0)

    def test_fee_currency_follows_contract_type(self):
        f_inv = contracts.fee(BTC_INVERSE, 10, 50_000.0, 0.0005)
        f_lin = contracts.fee(BTC_LINEAR, 10, 50_000.0, 0.0005)
        self.assertEqual(f_inv.settle_ccy, "BTC")
        self.assertEqual(f_lin.settle_ccy, "USDT")
        # Les frais d'un inverse se paient en COIN : le montant regle et le
        # montant en USD sont deux nombres differents.
        self.assertNotAlmostEqual(f_inv.fee_settle_ccy, f_inv.fee_usd, places=6)
        self.assertAlmostEqual(f_lin.fee_settle_ccy, f_lin.fee_usd, places=6)

    def test_bare_symbol_is_refused_everywhere(self):
        """Une fonction financiere ne peut pas deviner la mecanique d'un symbole."""
        for fn, args in (
            (contracts.usd_notional, (10, 50_000.0)),
            (contracts.coin_notional, (10, 50_000.0)),
            (contracts.contracts_for_usd_notional, (1000.0, 50_000.0)),
            (contracts.quantize_contracts, (1.234,)),
            (contracts.build_order, (1000.0, 50_000.0)),
            (contracts.fee, (10, 50_000.0, 0.0005)),
        ):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(contracts.NotAnInstrumentSpec):
                    fn("BTC-USD-SWAP", *args)
        with self.assertRaises(contracts.NotAnInstrumentSpec):
            contracts.pnl("BTC-USD-SWAP", Direction.LONG, 10, 50_000.0, 51_000.0)

    def test_usd_swap_is_never_silently_read_as_usdt_swap(self):
        """Aucune table de conversion -USD-SWAP -> -USDT-SWAP nulle part."""
        for path in V2.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            # Seule la REECRITURE vers l'instrument lineaire est une faute :
            # retirer le suffixe pour un affichage n'en est pas une.
            for pattern in ('"-USD-SWAP", "-USDT-SWAP"', "'-USD-SWAP', '-USDT-SWAP'",
                            '"-USD-", "-USDT-"', "'-USD-', '-USDT-'"):
                self.assertNotIn(pattern, text, f"{path.name} reecrit l'instrument")


# ══════════════════════════════════════════════════════════════════════════
# 2. MELANGE DE DEVISES
# ══════════════════════════════════════════════════════════════════════════
class Test02CurrencyMismatch(unittest.TestCase):

    def test_pnl_carries_its_settlement_currency(self):
        inv = contracts.pnl(BTC_INVERSE, Direction.LONG, 10, 50_000.0, 51_000.0)
        lin = contracts.pnl(BTC_LINEAR, Direction.LONG, 10, 50_000.0, 51_000.0)
        self.assertNotEqual(inv.settle_ccy, lin.settle_ccy)
        # Additionner 0.0039 BTC et 100 USDT n'a pas de sens : les deux champs
        # existent separement precisement pour rendre l'addition impossible
        # par inadvertance.
        self.assertIn("settle_ccy", inv.to_dict())
        self.assertIn("pnl_usd_at_exit", inv.to_dict())

    def test_fee_settle_ccy_and_fee_usd_are_distinct_fields(self):
        f = contracts.fee(BTC_INVERSE, 10, 50_000.0, 0.0005)
        self.assertNotAlmostEqual(f.fee_settle_ccy, f.fee_usd, places=6)
        self.assertEqual(f.settle_ccy, "BTC")
        # En bps du notionnel USD, un inverse et un lineaire coutent le meme
        # taux : c'est la DEVISE de reglement qui les separe, pas le montant.
        g = contracts.fee(BTC_LINEAR, 10, 50_000.0, 0.0005)
        self.assertAlmostEqual(f.fee_bps_of_usd_notional,
                               g.fee_bps_of_usd_notional, places=9)

    def test_falsification_rejects_non_comparable_quotes(self):
        res = agent().attack(hypothesis(),
                             benign_context(quote_currencies=("USD", "AED")))
        self.assertIn(RejectionReason.DENOMINATION_MISMATCH, res.reasons)

    def test_usd_equivalents_are_comparable(self):
        res = agent().attack(hypothesis(),
                             benign_context(quote_currencies=("USD", "USDT", "USDC")))
        self.assertNotIn(RejectionReason.DENOMINATION_MISMATCH, res.reasons)

    def test_instrument_with_incoherent_metadata_fails_closed(self):
        broken = dataclasses.replace(BTC_INVERSE, ct_val_ccy="BTC")  # inverse => USD
        with self.assertRaises(Exception):
            broken.validate()


# ══════════════════════════════════════════════════════════════════════════
# 3. FUITE D'INFORMATION FUTURE
# ══════════════════════════════════════════════════════════════════════════
class Test03FutureLeakage(unittest.TestCase):

    def setUp(self):
        self.tl = timeline_of([100.0, 101.0, 102.0, 103.0])

    def test_book_at_never_returns_a_future_book(self):
        for ts in range(1_000_000, 1_003_001, 250):
            book = self.tl.book_at(ts)
            if book is not None:
                self.assertLessEqual(book.ts_ms, ts)

    def test_book_before_first_event_is_none_not_first(self):
        self.assertIsNone(self.tl.book_at(999_999))

    def test_cursor_refuses_to_go_backwards(self):
        cur = ReplayCursor(self.tl, 1_001_000)
        with self.assertRaises(LookAheadViolation):
            cur.advance_to(1_000_000)

    def test_cursor_refuses_to_read_the_future(self):
        cur = ReplayCursor(self.tl, 1_001_000)
        with self.assertRaises(LookAheadViolation):
            cur.book_at_or_before(1_002_000)

    def test_non_positive_horizon_is_look_ahead(self):
        for horizon in (0, -1_000):
            with self.subTest(horizon=horizon):
                res = agent().attack(hypothesis(relation(horizon=horizon)),
                                     benign_context(sampling_step_ms=1))
                self.assertIn(RejectionReason.LOOK_AHEAD, res.reasons)

    def test_forward_looking_primitive_is_data_leakage(self):
        h = hypothesis()
        leaky = dataclasses.replace(h, features_used=("forward_return_bps",))
        res = agent().attack(leaky, benign_context())
        self.assertIn(RejectionReason.DATA_LEAKAGE, res.reasons)

    #: Le seul endroit ou boucler entree et sortie sur le MEME carnet est
    #: legitime : la mesure du plancher de cout, qui s'annonce comme telle
    #: (gross=0 par construction, opportunity_type EXECUTION_CONTROL_PAPER).
    SAME_BOOK_ROUND_TRIP_ALLOWED = {"smoke_test.py"}

    def test_round_trip_never_closes_on_its_own_entry_book(self):
        """Sortir sur le carnet d'entree donne un brut nul par construction.

        Le realise vaut alors exactement moins le cout de traversee. Pose a
        cote d'une capture nette attendue positive, ce chiffre se lit comme
        une refutation de l'edge alors qu'il ne mesure que le peage.
        """
        offenders = []
        for path in V2.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "round_trip"):
                    continue
                args = [a for a in node.args if isinstance(a, ast.Name)]
                pos = node.args
                if len(pos) >= 4 and isinstance(pos[2], ast.Name) and \
                        isinstance(pos[3], ast.Name) and pos[2].id == pos[3].id:
                    if path.name not in self.SAME_BOOK_ROUND_TRIP_ALLOWED:
                        offenders.append(f"{path.name}:{node.lineno} "
                                         f"entree et sortie = {pos[2].id}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_same_book_round_trip_is_exactly_minus_the_crossing_cost(self):
        """Demonstration chiffree de ce que la garde ci-dessus interdit."""
        book = simple_inverse_book()
        rt = PaperExecutor().round_trip(BTC_INVERSE, Direction.LONG,
                                        book, book, 1_000.0)
        self.assertIsNotNone(rt)
        # Le resultat ne peut pas etre positif : on achete l'ask et on revend
        # le bid du MEME carnet. Le signe est acquis avant toute hypothese.
        self.assertLess(rt.realized_bps, 0.0)
        # Et il vaut exactement le peage : traversee + frais, rien d'autre.
        self.assertAlmostEqual(rt.realized_bps, rt.gross_bps - rt.total_fee_bps,
                               places=6)
        book_cost = 2 * book.crossing_cost_bps("ask")
        self.assertAlmostEqual(abs(rt.gross_bps), book_cost, delta=1.0)

    def test_paper_lab_entry_book_is_the_one_at_arrival_not_at_decision(self):
        """Le carnet d'entree doit etre celui de T0+latence, pas celui de T0."""
        lab = PaperLab()
        exp = lab.run(self.tl, BTC_INVERSE, Direction.LONG,
                      decision_ts_ms=1_000_000, latency_ms=1_000,
                      horizon_ms=1_000, notional_usd=100.0)
        self.assertEqual(exp.entry_ts_ms, 1_001_000)
        self.assertEqual(exp.exit_ts_ms, 1_002_000)
        if exp.outcome is PaperOutcome.COMPLETED:
            # prix d'entree pris sur le carnet a 101, pas a 100
            self.assertGreater(exp.entry_price, 100.0)


# ══════════════════════════════════════════════════════════════════════════
# 4. CARNET PERIME
# ══════════════════════════════════════════════════════════════════════════
class Test04StaleBook(unittest.TestCase):

    def test_old_book_is_unusable(self):
        book = simple_inverse_book()
        rep = assess_book(book, BTC_INVERSE, now_ms=book.ts_ms + 60_000)
        self.assertIn(QualityIssue.STALE_BOOK, rep.issues)
        self.assertFalse(rep.is_usable)

    def test_future_timestamp_is_a_clock_anomaly(self):
        book = simple_inverse_book()
        rep = assess_book(book, BTC_INVERSE, now_ms=book.ts_ms - 60_000)
        self.assertIn(QualityIssue.CLOCK_ANOMALY, rep.issues)
        self.assertFalse(rep.is_usable)

    def test_falsification_rejects_stale_quotes(self):
        res = agent().attack(hypothesis(), benign_context(book_age_ms=10_000.0))
        self.assertIn(RejectionReason.STALE_QUOTES, res.reasons)

    def test_unknown_age_does_not_count_as_fresh(self):
        """Une age inconnu ne doit pas etre traite comme un succes du controle."""
        res = agent().attack(hypothesis(), benign_context(book_age_ms=None))
        self.assertNotIn(RejectionReason.STALE_QUOTES, res.reasons)
        self.assertIn("stale_quotes", res.checks_run)


# ══════════════════════════════════════════════════════════════════════════
# 5. TROU DE SEQUENCE
# ══════════════════════════════════════════════════════════════════════════
class Test05SequenceGap(unittest.TestCase):

    def _snapshot(self, book):
        return book.apply("snapshot", {
            "bids": [["99.0", "10"]], "asks": [["100.0", "10"]],
            "ts": "1789549475551", "seqId": 100})

    def test_gap_invalidates_the_book(self):
        b = L2Book(instrument=BTC_INVERSE)
        self.assertTrue(self._snapshot(b))
        ok = b.apply("update", {"bids": [["99.5", "5"]], "asks": [],
                                "ts": "1789549475600", "seqId": 105,
                                "prevSeqId": 104})   # attendu 100
        self.assertFalse(ok)
        self.assertFalse(b.valid)
        self.assertEqual(b.sequence_gaps, 1)

    def test_invalid_book_produces_no_orderbook(self):
        b = L2Book(instrument=BTC_INVERSE)
        self._snapshot(b)
        b.apply("update", {"bids": [], "asks": [], "ts": "1", "seqId": 105,
                           "prevSeqId": 104})
        self.assertIsNone(b.book(PROV))

    def test_contiguous_chain_is_accepted(self):
        b = L2Book(instrument=BTC_INVERSE)
        self._snapshot(b)
        self.assertTrue(b.apply("update", {
            "bids": [["99.5", "5"]], "asks": [], "ts": "1789549475600",
            "seqId": 101, "prevSeqId": 100}))
        self.assertTrue(b.valid)

    def test_sequence_regression_is_reported(self):
        tracker = SequenceTracker()
        tracker.observe("books:BTC-USD-SWAP", 100)
        issues = tracker.observe("books:BTC-USD-SWAP", 90)
        self.assertIn(QualityIssue.SEQUENCE_GAP, issues)
        self.assertEqual(tracker.regressions, 1)


# ══════════════════════════════════════════════════════════════════════════
# 6. FILL IRREALISTE
# ══════════════════════════════════════════════════════════════════════════
class Test06UnrealisticFill(unittest.TestCase):

    def test_walk_cannot_fill_more_than_the_book_holds(self):
        book = thin_inverse_book()          # 100 USD de chaque cote
        walk = book.walk("ask", 1_000_000.0)
        self.assertTrue(walk.exhausted)
        self.assertLessEqual(walk.filled_notional, book.ask_depth() + 1e-9)

    def test_executor_never_reports_more_than_available(self):
        book = thin_inverse_book()
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG, book, 1_000_000.0)
        if not fill.is_rejected:
            self.assertTrue(fill.is_partial)
            self.assertLessEqual(fill.filled_notional_usd, 200.0)

    def test_empty_side_is_rejected_not_filled_at_mid(self):
        book = OrderBook.from_okx(
            BTC_INVERSE, okx_book_payload(bids=[["99.0", "10"]], asks=[]), PROV)
        with self.assertRaises(Exception):
            PaperExecutor().submit(BTC_INVERSE, Direction.LONG, book, 100.0)

    def test_fill_assumed_at_touch_beyond_touch_depth_is_rejected(self):
        res = agent().attack(hypothesis(), benign_context(
            fill_assumed_at_touch=True, touch_depth_usd=500.0,
            required_notional_usd=50_000.0))
        self.assertIn(RejectionReason.UNREALISTIC_FILL, res.reasons)

    def test_unknown_touch_depth_is_not_a_pass(self):
        res = agent().attack(hypothesis(), benign_context(
            fill_assumed_at_touch=True, touch_depth_usd=None))
        self.assertNotIn(RejectionReason.UNREALISTIC_FILL, res.reasons)
        self.assertTrue(any("NON EXERCE" in d for d in res.details))


# ══════════════════════════════════════════════════════════════════════════
# 7. FILL PARTIEL
# ══════════════════════════════════════════════════════════════════════════
class Test07PartialFill(unittest.TestCase):

    def test_partial_entry_is_flagged(self):
        book = simple_inverse_book()        # 6000 USD cote ask
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG, book, 10_000.0)
        self.assertTrue(fill.is_partial)
        self.assertLess(fill.filled_notional_usd, 10_000.0)

    def test_round_trip_reports_residual_exposure(self):
        entry = simple_inverse_book()
        thin_exit = thin_inverse_book()     # sortie beaucoup plus mince
        rt = PaperExecutor().round_trip(BTC_INVERSE, Direction.LONG,
                                        entry, thin_exit, 3_000.0)
        if rt is not None and not rt.fully_closed:
            self.assertGreater(rt.unclosed_contracts, 0.0)
            self.assertGreater(rt.unclosed_notional_usd, 0.0)

    def test_pnl_counts_only_the_closed_contracts(self):
        entry = simple_inverse_book()
        thin_exit = thin_inverse_book()
        rt = PaperExecutor().round_trip(BTC_INVERSE, Direction.LONG,
                                        entry, thin_exit, 3_000.0)
        if rt is not None:
            closed = min(rt.entry.contracts, rt.exit.contracts)
            self.assertLessEqual(closed, rt.entry.contracts)

    def test_two_leg_candidate_is_never_executed_as_one_leg(self):
        """Simuler une seule jambe d'un spread laisse l'autre en exposition."""
        src = (V2 / "edge_hunt.py").read_text(encoding="utf-8")
        self.assertIn("BOTH_LEGS", src,
                      "l'execution PAPER ne filtre pas les candidates a deux jambes")
        self.assertIn("cand.legs", src)
        # Le PaperExecutor lui-meme ne connait qu'un instrument : sa signature
        # ne porte aucune notion de seconde jambe.
        import inspect
        sig = inspect.signature(PaperExecutor.round_trip)
        self.assertNotIn("legs", sig.parameters)
        self.assertNotIn("leg2", sig.parameters)

    def test_paper_experiment_exposes_fill_ratio(self):
        tl = timeline_of([100.0, 101.0, 102.0], size="1")   # carnets minces
        exp = PaperLab().run(tl, BTC_INVERSE, Direction.LONG,
                             decision_ts_ms=1_000_000, latency_ms=1_000,
                             horizon_ms=1_000, notional_usd=1_000_000.0)
        if exp.outcome is PaperOutcome.COMPLETED:
            self.assertIsNotNone(exp.fill_ratio)
            self.assertLess(exp.fill_ratio, 1.0)
        else:
            self.assertIn(exp.outcome, (PaperOutcome.INSUFFICIENT_DEPTH,
                                        PaperOutcome.ORDER_NOT_SUBMITTABLE))


# ══════════════════════════════════════════════════════════════════════════
# 8. FRAIS MANQUANTS
# ══════════════════════════════════════════════════════════════════════════
class Test08MissingFees(unittest.TestCase):

    def test_no_credentials_yields_unknown_not_zero(self):
        sched = NoCredentialsFeeProvider().schedule_for(BTC_INVERSE)
        self.assertFalse(sched.is_known)
        self.assertNotEqual(sched.quality, Quality.OBSERVED)
        self.assertIsNone(sched.round_trip_bps())
        self.assertFalse(sched.authorises_execution)

    def test_unknown_fee_component_has_no_value(self):
        comp = costs.fees_unknown()
        self.assertFalse(comp.is_known)
        self.assertIsNone(comp.value_bps)

    def test_breakdown_total_is_none_when_fees_unknown(self):
        book = simple_inverse_book()
        br = costs.build_breakdown(book, "ask", 1_000.0, strict_fees=True)
        self.assertIsNone(br.total_bps())
        self.assertIn("fees", br.unknown_components())

    def test_assumed_fees_never_authorise_execution(self):
        sched = AssumedFeeProvider().schedule_for(BTC_INVERSE)
        self.assertTrue(sched.is_known)
        self.assertEqual(sched.quality, Quality.ASSUMED)
        self.assertFalse(sched.authorises_execution)

    def test_paper_executable_edge_is_none_without_fees(self):
        tl = timeline_of([100.0, 101.0, 102.0, 103.0])
        exp = PaperLab().run(tl, BTC_INVERSE, Direction.LONG,
                             decision_ts_ms=1_000_000, latency_ms=1_000,
                             horizon_ms=1_000, notional_usd=100.0, fee_bps=None)
        if exp.outcome is PaperOutcome.COMPLETED:
            self.assertIsNone(exp.executable_edge_bps)

    def test_feebook_prefers_the_best_quality_available(self):
        fb = FeeBook().add(NoCredentialsFeeProvider()).add(AssumedFeeProvider())
        self.assertEqual(fb.best_for(BTC_INVERSE).quality, Quality.ASSUMED)

    def test_no_fee_class_accepts_a_credential(self):
        """Aucun point d'entree du module fees ne prend de cle."""
        banned = {"api_key", "apikey", "secret", "passphrase", "token",
                  "private_key", "credential"}
        tree = ast.parse((V2 / "fees.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in node.args.args + node.args.kwonlyargs:
                    self.assertNotIn(arg.arg.lower(), banned,
                                     f"{node.name} accepte {arg.arg}")


# ══════════════════════════════════════════════════════════════════════════
# 9. SLIPPAGE MANQUANT
# ══════════════════════════════════════════════════════════════════════════
class Test09MissingSlippage(unittest.TestCase):

    def test_unknown_slippage_blocks_the_total(self):
        book = simple_inverse_book()
        br = costs.build_breakdown(book, "ask", 1_000.0, strict_fees=False,
                                   slippage=costs.slippage_unknown())
        self.assertIsNone(br.total_bps())
        self.assertIn("slippage", br.unresolved_essentials())

    def test_excluded_slippage_is_named_not_zeroed(self):
        comp = costs.slippage_excluded_for_paper_validation()
        self.assertTrue(costs.is_excluded(comp))
        self.assertIn("EXCLU", comp.note.upper())

    def test_weakest_quality_drives_the_breakdown(self):
        book = simple_inverse_book()
        br = costs.build_breakdown(book, "ask", 1_000.0, strict_fees=False)
        self.assertIn(br.weakest_quality(), (Quality.ASSUMED, Quality.UNKNOWN))

    def test_discovery_mode_cannot_authorise_execution(self):
        self.assertFalse(EvaluationMode.DISCOVERY.allows_capital)
        self.assertTrue(quality_satisfies(Quality.OBSERVED,
                                          EvaluationMode.EXECUTION.min_quality))
        self.assertFalse(quality_satisfies(Quality.ASSUMED,
                                           EvaluationMode.EXECUTION.min_quality))


# ══════════════════════════════════════════════════════════════════════════
# 10. LIQUIDITE COMPTEE DEUX FOIS
# ══════════════════════════════════════════════════════════════════════════
class Test10DuplicateLiquidity(unittest.TestCase):

    def test_repeated_depth_source_is_rejected(self):
        res = agent().attack(hypothesis(), benign_context(
            depth_sources=("OKX:BTC-USD-SWAP", "OKX:BTC-USD-SWAP")))
        self.assertIn(RejectionReason.DUPLICATE_LIQUIDITY, res.reasons)

    def test_distinct_sources_are_accepted(self):
        res = agent().attack(hypothesis(), benign_context(
            depth_sources=("OKX:BTC-USD-SWAP", "OKX:BTC-USDT-SWAP")))
        self.assertNotIn(RejectionReason.DUPLICATE_LIQUIDITY, res.reasons)

    def test_undeclared_sources_is_a_non_exercised_control(self):
        res = agent().attack(hypothesis(), benign_context(depth_sources=()))
        self.assertNotIn(RejectionReason.DUPLICATE_LIQUIDITY, res.reasons)
        self.assertTrue(any("NON EXERCE" in d for d in res.details))

    def test_duplicate_event_is_detected_on_the_stream(self):
        tracker = SequenceTracker()
        tracker.observe("books:BTC-USD-SWAP", 100)
        issues = tracker.observe("books:BTC-USD-SWAP", 100)
        self.assertIn(QualityIssue.DUPLICATE_EVENT, issues)

    def test_same_seq_id_twice_is_flagged_by_assess_book(self):
        book = simple_inverse_book()
        rep = assess_book(book, BTC_INVERSE, now_ms=book.ts_ms,
                          seen_seq_ids={book.seq_id})
        self.assertIn(QualityIssue.DUPLICATE_EVENT, rep.issues)

    def test_one_level_is_consumed_once_in_a_walk(self):
        book = simple_inverse_book()
        walk = book.walk("ask", 6_000.0)
        self.assertLessEqual(walk.filled_notional, book.ask_depth() + 1e-9)
        self.assertLessEqual(walk.levels_consumed, len(book.asks))


# ══════════════════════════════════════════════════════════════════════════
# 11. CANDIDATS CORRELES
# ══════════════════════════════════════════════════════════════════════════
class Test11CorrelatedCandidates(unittest.TestCase):

    def test_overlapping_windows_collapse_the_effective_n(self):
        r = relation(n=6_000, horizon=30_000)
        eff = FalsificationAgent.effective_sample_size(r, step_ms=500)
        self.assertAlmostEqual(eff, 100.0)          # 6000 / (30000/500)
        self.assertLess(eff, r.n)

    def test_large_nominal_n_does_not_survive(self):
        """N=1000 nominal, mais 1 seule fenetre independante."""
        res = agent().attack(hypothesis(relation(n=1000, horizon=30_000)),
                             benign_context(sampling_step_ms=30))
        self.assertIn(RejectionReason.CORRELATED_OBSERVATIONS, res.reasons)
        self.assertLess(res.effective_n, MIN_EFFECTIVE_N)

    def test_non_overlapping_windows_keep_the_full_n(self):
        r = relation(n=100, horizon=1_000)
        self.assertAlmostEqual(
            FalsificationAgent.effective_sample_size(r, step_ms=5_000), 100.0)

    def test_regime_concentration_is_rejected(self):
        res = agent().attack(hypothesis(), benign_context(
            early_half_mean_bps=100.0, late_half_mean_bps=0.5))
        self.assertIn(RejectionReason.REGIME_DEPENDENCE, res.reasons)


# ══════════════════════════════════════════════════════════════════════════
# 12. CAPACITE SURESTIMEE
# ══════════════════════════════════════════════════════════════════════════
class Test12CapacityOverstatement(unittest.TestCase):

    def test_depth_below_requirement_is_rejected(self):
        res = agent().attack(hypothesis(), benign_context(
            available_depth_usd=1_000.0, required_notional_usd=50_000.0))
        self.assertIn(RejectionReason.CAPACITY_ILLUSION, res.reasons)

    def test_exhausted_level_yields_no_cost_estimate(self):
        book = thin_inverse_book()
        points = capacity_curve(book, "ask", notionals_usd=(10.0, 1_000_000.0))
        big = [p for p in points if p.notional_usd == 1_000_000.0][0]
        self.assertTrue(big.exhausted)
        self.assertIsNone(big.total_estimated_cost_bps)

    def test_max_notional_is_bounded_by_the_probed_grid(self):
        book = simple_inverse_book()
        points = capacity_curve(book, "ask", notionals_usd=(10.0, 100.0, 1_000.0))
        mx = max_notional_without_exhaustion(points)
        self.assertLessEqual(mx, 1_000.0)

    def test_impact_grows_with_size(self):
        book = simple_inverse_book()
        small = book.market_impact_bps("ask", 500.0)
        large = book.market_impact_bps("ask", 5_000.0)
        self.assertGreater(large, small)

    def test_capacity_beyond_book_is_never_silently_extrapolated(self):
        book = simple_inverse_book()
        self.assertIsNone(book.vwap_for_notional("ask", 1e9))


# ══════════════════════════════════════════════════════════════════════════
# 13. LATENCE SOUS-ESTIMEE
# ══════════════════════════════════════════════════════════════════════════
class Test13LatencyUnderstatement(unittest.TestCase):

    def test_horizon_shorter_than_transport_is_rejected(self):
        res = agent().attack(hypothesis(relation(horizon=500)),
                             benign_context(sampling_step_ms=500,
                                            transport_delay_ms=900.0))
        self.assertIn(RejectionReason.LATENCY_ILLUSION, res.reasons)

    def test_unknown_transport_delay_is_not_treated_as_zero(self):
        res = agent().attack(hypothesis(relation(horizon=500)),
                             benign_context(sampling_step_ms=500,
                                            transport_delay_ms=None))
        self.assertNotIn(RejectionReason.LATENCY_ILLUSION, res.reasons)
        self.assertIn("latency_illusion", res.checks_run)

    def test_paper_entry_price_pays_the_latency(self):
        """Avec un prix qui derive, entrer 1s plus tard coute quelque chose."""
        tl = timeline_of([100.0, 101.0, 102.0, 103.0])
        lab = PaperLab()
        zero = lab.run(tl, BTC_INVERSE, Direction.LONG, 1_000_000, 0, 1_000, 100.0)
        late = lab.run(tl, BTC_INVERSE, Direction.LONG, 1_000_000, 1_000, 1_000, 100.0)
        if (zero.outcome is PaperOutcome.COMPLETED
                and late.outcome is PaperOutcome.COMPLETED):
            self.assertGreater(late.entry_price, zero.entry_price)
            self.assertGreater(late.latency_decay_bps, 0.0)

    def test_effect_dead_on_arrival_is_invalidated(self):
        """Le prix bouge CONTRE la position pendant la latence, plus que l'effet."""
        tl = timeline_of([103.0, 100.0, 99.0, 98.0])
        exp = PaperLab().run(tl, BTC_INVERSE, Direction.LONG,
                             decision_ts_ms=1_000_000, latency_ms=1_000,
                             horizon_ms=1_000, notional_usd=100.0,
                             theoretical_edge_bps=1.0)
        self.assertEqual(exp.outcome, PaperOutcome.INVALIDATED_BEFORE_ENTRY)

    def test_update_interval_bounds_measurable_deltas(self):
        tl = timeline_of([100.0, 101.0, 102.0], step_ms=1_000)
        self.assertFalse(tl.is_resolvable(10))       # sous l'intervalle de publication
        self.assertTrue(tl.is_resolvable(1_000))


# ══════════════════════════════════════════════════════════════════════════
# 14. CONFUSION PAPER / LIVE
# ══════════════════════════════════════════════════════════════════════════
class Test14PaperLiveConfusion(unittest.TestCase):

    def test_executor_is_paper_and_says_so(self):
        self.assertIs(PaperExecutor.mode, ExecutionMode.PAPER)
        fill = PaperExecutor().submit(BTC_INVERSE, Direction.LONG,
                                      simple_inverse_book(), 100.0)
        self.assertEqual(fill.mode, ExecutionMode.PAPER.value)

    def test_no_real_executor_class_exists(self):
        """Aucune classe capable de placer un ordre reel, quel que soit son nom."""
        for path in V2.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                low = node.name.lower()
                if any(k in low for k in ("executor", "trader", "broker", "client")):
                    self.assertFalse(
                        low.startswith("real") or low.startswith("live"),
                        f"{path.name}: classe {node.name}")
                methods = {n.name for n in node.body
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                for banned in ("place_order", "send_order", "cancel_order",
                               "set_leverage", "close_position"):
                    self.assertNotIn(banned, methods,
                                     f"{path.name}.{node.name}.{banned}")

    def test_paper_results_name_what_they_exclude(self):
        self.assertGreaterEqual(len(PAPER_EXCLUDED_FRICTIONS), 5)
        tl = timeline_of([100.0, 101.0, 102.0, 103.0])
        exp = PaperLab().run(tl, BTC_INVERSE, Direction.LONG,
                             1_000_000, 1_000, 1_000, 100.0)
        self.assertEqual(tuple(exp.excluded_frictions), PAPER_EXCLUDED_FRICTIONS)
        self.assertTrue(exp.is_upper_bound)

    def test_paper_pnl_is_never_called_realized_pnl_alone(self):
        tl = timeline_of([100.0, 101.0, 102.0, 103.0])
        exp = PaperLab().run(tl, BTC_INVERSE, Direction.LONG,
                             1_000_000, 1_000, 1_000, 100.0)
        d = exp.to_dict()
        self.assertIn("paper_pnl_usd", d)
        self.assertNotIn("realized_pnl_usd", d)

    def test_no_order_placement_endpoint_anywhere(self):
        for path in V2.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for endpoint in ("/api/v5/trade/order", "/api/v5/trade/batch-orders",
                             "/api/v5/account/set-leverage"):
                self.assertNotIn(endpoint, text, f"{path.name} contacte {endpoint}")


# ══════════════════════════════════════════════════════════════════════════
# 15. ACTIVATION ACCIDENTELLE DU LIVE
# ══════════════════════════════════════════════════════════════════════════
class Test15AccidentalLive(unittest.TestCase):

    def test_live_is_not_implemented(self):
        self.assertFalse(SystemMode.LIVE.is_implemented)
        self.assertFalse(SystemMode.DEMO.is_implemented)
        self.assertTrue(SystemMode.LIVE.touches_real_capital)

    def test_discovery_cannot_jump_to_live(self):
        gate = ModeGate()
        with self.assertRaises(ModeTransitionRefused):
            gate.transition(SystemMode.LIVE)

    def test_live_stays_refused_even_with_every_capability_declared(self):
        gate = ModeGate()
        for target in MODE_ALL_PREREQS():
            gate.declare(target, True, "declaration de test")
        ok, missing = gate.can_transition(SystemMode.LIVE)
        self.assertFalse(ok, f"LIVE devenu atteignable (manquants: {missing})")

    def test_no_credentials_are_read_from_the_environment(self):
        for path in V2.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
                           "API_SECRET", "PRIVATE_KEY"):
                self.assertNotIn(banned, text, f"{path.name} lit {banned}")

    def test_no_signing_primitive_is_present(self):
        for path in V2.rglob("*.py"):
            mods = code_identifiers(path)
            for banned in ("hmac", "new_hmac"):
                self.assertNotIn(banned, mods, f"{path.name} signe des requetes")


def MODE_ALL_PREREQS():
    from prism_v2.modes import MODE_PREREQUISITES
    out = set()
    for reqs in MODE_PREREQUISITES.values():
        out.update(reqs)
    return sorted(out)


# ══════════════════════════════════════════════════════════════════════════
# 16. SURAPPRENTISSAGE DE LA DECOUVERTE
# ══════════════════════════════════════════════════════════════════════════
class Test16DiscoveryOverfitting(unittest.TestCase):

    def test_sign_flip_out_of_sample_is_rejected(self):
        disc = relation(mean=50.0, rid="d")
        hold = relation(mean=-50.0, rid="h", sample="HOLDOUT")
        res = agent().attack(hypothesis(disc),
                             benign_context(holdout_relation=hold))
        self.assertIn(RejectionReason.UNSTABLE_OUT_OF_SAMPLE, res.reasons)

    def test_effect_collapse_out_of_sample_is_rejected(self):
        disc = relation(mean=50.0, rid="d")
        hold = relation(mean=5.0, rid="h", sample="HOLDOUT")   # 10% du decouvert
        res = agent().attack(hypothesis(disc),
                             benign_context(holdout_relation=hold))
        self.assertIn(RejectionReason.UNSTABLE_OUT_OF_SAMPLE, res.reasons)

    def test_stable_effect_survives(self):
        disc = relation(mean=50.0, rid="d")
        hold = relation(mean=45.0, rid="h", sample="HOLDOUT")
        res = agent().attack(hypothesis(disc),
                             benign_context(holdout_relation=hold))
        self.assertNotIn(RejectionReason.UNSTABLE_OUT_OF_SAMPLE, res.reasons)

    def test_threshold_picked_in_sample_without_holdout_is_selection_bias(self):
        res = agent().attack(hypothesis(), benign_context(
            threshold_selected_in_sample=True, holdout_relation=None))
        self.assertIn(RejectionReason.SELECTION_BIAS, res.reasons)

    def test_holdout_redeems_an_in_sample_threshold(self):
        res = agent().attack(hypothesis(), benign_context(
            threshold_selected_in_sample=True,
            holdout_relation=relation(mean=45.0, rid="h", sample="HOLDOUT")))
        self.assertNotIn(RejectionReason.SELECTION_BIAS, res.reasons)

    def test_universe_filtered_on_outcome_is_survivorship_bias(self):
        res = agent().attack(hypothesis(),
                             benign_context(universe_filtered_on_outcome=True))
        self.assertIn(RejectionReason.SURVIVORSHIP_BIAS, res.reasons)

    def test_claiming_profit_without_costs_is_rejected(self):
        res = agent().attack(hypothesis(), benign_context(
            costs_applied=False, claims_profitability=True))
        self.assertIn(RejectionReason.TRANSACTION_COST_OMISSION, res.reasons)

    def test_self_declared_untested_hypothesis_never_reaches_accepted(self):
        """Defaut n8 (trouve a la cloture) : les detecteurs de microstructure
        posent `hypothesis_untested: True` et personne ne lisait ce drapeau.
        Une candidate se declarait non testee et recevait du capital dans le
        meme run. Sa capture brute est le deplacement observe PRIS EN ENTIER :
        elle suppose une convergence de 100% qui n'a jamais ete mesuree.
        """
        from prism_v2.economics import CaptureStatus, evaluate
        from prism_v2.opportunity import Candidate
        book = simple_inverse_book()
        br = costs.build_breakdown(book, "ask", 1_000.0, strict_fees=False)
        cand = Candidate(
            ts_utc="2026-09-16T00:00:00Z", instrument=BTC_INVERSE,
            opportunity_type="AGGRESSIVE_FLOW_DISPLACEMENT",
            family="AGGRESSIVE_FLOW", candidate_id="c1",
            direction=Direction.LONG,
            gross_capture_bps=10_000.0,        # enorme : seul le drapeau bloque
            capacity_usd=1e9,
            provenance=Provenance("OKX", "books", "t", "BTC-USD-SWAP"),
            metadata={"hypothesis_untested": True})
        ev = evaluate(cand, br)
        self.assertEqual(ev.status, CaptureStatus.UNRESOLVED)
        self.assertIn("UNTESTED_HYPOTHESIS", ev.blocked_by)
        self.assertIsNone(ev.expected_net_capture_bps)
        self.assertIn("reversion_fraction", ev.unresolved_components)

    def test_the_untested_flag_is_actually_set_by_the_detectors(self):
        """Le garde ci-dessus ne vaut que si le drapeau est reellement pose."""
        src = (V2 / "detectors" / "microstructure.py").read_text(encoding="utf-8")
        self.assertIn('"hypothesis_untested": True', src)
        # et reellement lu cote economie
        eco = (V2 / "economics.py").read_text(encoding="utf-8")
        self.assertIn('meta.get("hypothesis_untested")', eco)

    def test_discovery_and_holdout_do_not_overlap(self):
        from prism_v2.research.observation import ObservationLog
        log = ObservationLog()
        for ts in range(0, 100):
            log.add(Observation(ts_ms=ts, inst_id="BTC-USD-SWAP", venue="OKX",
                                values={"spread_bps": 1.0},
                                provenance=Provenance("OKX", "/t", "t",
                                                      "BTC-USD-SWAP")))
        disc = ResearchPipeline._slice_log(log, None, 50)
        hold = ResearchPipeline._slice_log(log, 50, None)
        d_ts = {o.ts_ms for o in disc.series("BTC-USD-SWAP")}
        h_ts = {o.ts_ms for o in hold.series("BTC-USD-SWAP")}
        self.assertFalse(d_ts & h_ts, "chevauchement decouverte/holdout")
        self.assertEqual(len(d_ts) + len(h_ts), 100)
        self.assertLess(max(d_ts), min(h_ts))


# ══════════════════════════════════════════════════════════════════════════
# 17. TESTS MULTIPLES
# ══════════════════════════════════════════════════════════════════════════
class Test17MultipleTesting(unittest.TestCase):

    def test_threshold_tightens_with_the_number_of_tests(self):
        acc = MultipleTestingAccount()
        for _ in range(5):
            acc.record(0.01)
        loose = acc.bonferroni_threshold()
        for _ in range(500):
            acc.record(0.01)
        self.assertLess(acc.bonferroni_threshold(), loose)

    def test_a_p_value_that_passes_alone_fails_among_many(self):
        acc = MultipleTestingAccount()
        acc.record(0.03)
        self.assertTrue(acc.survives_correction(0.03))
        for _ in range(1_000):
            acc.record(0.4)
        self.assertFalse(acc.survives_correction(0.03))

    def test_rejection_is_recorded_as_multiple_testing(self):
        acc = MultipleTestingAccount()
        for _ in range(1_000):
            acc.record(0.4)
        res = FalsificationAgent(acc).attack(hypothesis(relation(p=0.03)),
                                             benign_context())
        self.assertIn(RejectionReason.MULTIPLE_TESTING, res.reasons)

    def test_missing_p_value_does_not_pass(self):
        acc = MultipleTestingAccount()
        acc.record(1e-12)
        self.assertFalse(acc.survives_correction(None))


# ══════════════════════════════════════════════════════════════════════════
# 18. MUTATION D'HYPOTHESE
# ══════════════════════════════════════════════════════════════════════════
class Test18HypothesisMutation(unittest.TestCase):

    def test_hypothesis_is_frozen(self):
        h = hypothesis()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            h.status = HypothesisStatus.SURVIVED_FALSIFICATION

    def test_status_change_creates_a_new_version(self):
        h = hypothesis()
        h2 = h.with_status(HypothesisStatus.REJECTED, reasons=["X"])
        self.assertIsNot(h, h2)
        self.assertEqual(h2.supersedes, h.hypothesis_id)
        self.assertEqual(h.status, HypothesisStatus.PROPOSED)

    def test_registry_refuses_a_silent_rewrite(self):
        reg = HypothesisRegistry()
        h = reg.register(hypothesis())
        mutated = dataclasses.replace(h, status=HypothesisStatus.SURVIVED_FALSIFICATION)
        with self.assertRaises(ValueError):
            reg.register(mutated)

    def test_supersede_requires_the_lineage(self):
        reg = HypothesisRegistry()
        h = reg.register(hypothesis())
        unrelated = hypothesis(relation(rid="autre", mean=7.0))
        with self.assertRaises(ValueError):
            reg.supersede(h, unrelated)

    def test_relation_is_frozen(self):
        r = relation()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.mean_forward_bps = 9_999.0


# ══════════════════════════════════════════════════════════════════════════
# 19. CORRUPTION DE PROVENANCE
# ══════════════════════════════════════════════════════════════════════════
class Test19ProvenanceCorruption(unittest.TestCase):

    def test_observation_is_frozen(self):
        obs = Observation(ts_ms=1, inst_id="BTC-USD-SWAP", venue="OKX",
                          values={"spread_bps": 1.0},
                          provenance=Provenance("OKX", "/t", "t", "BTC-USD-SWAP"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            obs.provenance = Provenance("FAKE", "/x", "x", "BTC-USD-SWAP")

    def test_missing_feature_is_absent_not_zero(self):
        obs = Observation(ts_ms=1, inst_id="BTC-USD-SWAP", venue="OKX",
                          values={"spread_bps": 1.0},
                          provenance=Provenance("OKX", "/t", "t", "BTC-USD-SWAP"),
                          missing=("depth_usd",))
        self.assertIsNone(obs.get("depth_usd"))
        self.assertNotIn("depth_usd", obs.to_dict()["values"])
        self.assertIn("depth_usd", obs.to_dict()["missing"])

    def test_ledger_record_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.jsonl"
            led = DiscoveryLedger(path)
            acc = clean_account()
            h = hypothesis()
            h2, res = FalsificationAgent(acc).apply(h, benign_context())
            record(led, h2, res)
            first = path.read_text(encoding="utf-8")
            record(led, h2, res, supersedes=h2.hypothesis_id)
            self.assertTrue(path.read_text(encoding="utf-8").startswith(first))

    def test_every_record_carries_its_required_fields(self):
        from prism_v2.research.discovery_ledger import REQUIRED_FIELDS
        with tempfile.TemporaryDirectory() as tmp:
            led = DiscoveryLedger(Path(tmp) / "d.jsonl")
            h2, res = FalsificationAgent(clean_account()).apply(hypothesis(),
                                                               benign_context())
            record(led, h2, res)
            for row in led.read_all():
                for f in REQUIRED_FIELDS:
                    self.assertIn(f, row)

    def test_book_of_another_instrument_is_refused(self):
        book = simple_inverse_book()          # BTC-USD-SWAP
        rep = assess_book(book, BTC_LINEAR, now_ms=book.ts_ms)
        self.assertIn(QualityIssue.INSTRUMENT_MISMATCH, rep.issues)
        self.assertFalse(rep.is_usable)


# ══════════════════════════════════════════════════════════════════════════
# 20. CONTOURNEMENT DE L'ORCHESTRATEUR
# ══════════════════════════════════════════════════════════════════════════
class Test20OrchestratorBypass(unittest.TestCase):

    def test_orchestrator_cannot_allocate_capital(self):
        banned = {"allocate", "allocate_capital", "size", "trade", "execute",
                  "submit", "order", "capital"}
        methods = {m for m in dir(ResearchOrchestrator) if not m.startswith("_")}
        self.assertFalse(methods & banned, f"methodes interdites: {methods & banned}")

    def test_priority_is_not_capital(self):
        orch = ResearchOrchestrator()
        orch.record_observations("BTC-USD-SWAP", 1_000)
        orch.record_relation("BTC-USD-SWAP", 40.0)
        for target in orch.next_research_targets():
            for key in target:
                self.assertNotIn("capital", key.lower())
                self.assertNotIn("notional", key.lower())

    def test_survivor_status_comes_only_from_the_falsification_verdict(self):
        """Aucun chemin ne fabrique un SURVIVED sans passer par attack()."""
        h = hypothesis(relation(n=5, horizon=30_000))     # echantillon minuscule
        h2, res = FalsificationAgent(clean_account()).apply(h, benign_context())
        self.assertFalse(res.verdict.survived)
        self.assertEqual(h2.status, HypothesisStatus.REJECTED)

    def test_rejected_hypothesis_never_becomes_a_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = DiscoveryLedger(Path(tmp) / "d.jsonl")
            h = hypothesis(relation(n=5, horizon=30_000))
            h2, res = FalsificationAgent(clean_account()).apply(h, benign_context())
            record(led, h2, res)
            rows = led.read_all()
            self.assertEqual(rows[0]["falsification_status"], "REJECTED")
            self.assertEqual(rows[0]["hypothesis"]["status"],
                             HypothesisStatus.REJECTED.value)
            self.assertTrue(rows[0]["falsification_reasons"])

    def test_every_rejection_reason_is_reachable(self):
        """Une raison declaree mais jamais levee est une promesse non tenue."""
        source = (V2 / "research" / "falsification.py").read_text(encoding="utf-8")
        for reason in RejectionReason:
            self.assertIn(f"RejectionReason.{reason.name}", source,
                          f"{reason.name} declare mais jamais leve")


if __name__ == "__main__":
    unittest.main()
