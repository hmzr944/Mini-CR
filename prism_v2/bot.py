#!/usr/bin/env python3
"""BOT — boucle autonome de detection, evaluation et execution PAPIER.

PAS DE TRADING REEL. Aucune classe RealExecutor, aucune cle, aucun ordre
reel. Le bot ne lit que des endpoints PUBLICS et n'ecrit que dans le ledger.

CE QUE CE BOT EST, ET CE QU'IL N'EST PAS.
Ce n'est pas un bot de strategie : le depot n'a trouve aucune strategie dont
l'economie justifie un deploiement (six familles mesurees, toutes entre 0,01x
et 0,35x de l'objectif, plafond de categorie etabli a 16 %/an par HLP). Un
bot qui traderait quand meme mentirait sur la preuve.

C'est un bot de CRIBLAGE : il surveille en continu des instruments reels sur
des venues reelles, chiffre chaque candidate contre ses couts reels, et
n'execute — en papier — que ce qui franchit le seuil economique. Compte tenu
de la mesure, il refusera la quasi-totalite du temps.

CE REFUS EST LE PRODUIT. Un criblage qui accepterait souvent sur une famille
mesuree negative signalerait un bug dans les couts, pas une opportunite. Le
bot rend donc ses refus aussi visibles que ses acceptations, avec leur motif.

ORDRE NON CONTOURNABLE, herite du noyau :
    QUALITE DES DONNEES -> ECONOMIE -> CAPACITE -> RISQUE -> EXECUTION
Une donnee inutilisable produit UNRESOLVED, jamais un rejet economique.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.core_types import Direction, Quality, utc_now_iso
from prism_v2.costs import (
    ExecutionStyle, build_breakdown, funding_not_applicable,
    latency_not_applicable, slippage_excluded_for_paper_validation,
)
from prism_v2.modes import EvaluationMode
from prism_v2.economics import CaptureStatus, Evaluation, evaluate
from prism_v2.instruments import InstrumentSpec
from prism_v2.opp_funding import (
    FundingSpreadOpportunity, VenueFunding, HOURS_PER_YEAR,
)
from prism_v2.opportunity import (
    Candidate, DetectionStatus, MarketContext, OpportunityRegistry,
)
from prism_v2.orderbook import OrderBook
from prism_v2.risk import RiskGate, RiskLimits

MAX_BOOK_AGE_MS = 5_000


@dataclass
class CycleReport:
    """Ce qu'un cycle a vu et decide. Les refus y sont de premiere classe."""

    run_id: str
    ts_utc: str
    n_instruments: int = 0
    n_detected: int = 0
    n_insufficient_data: int = 0
    n_evaluated: int = 0
    n_accepted: int = 0
    n_rejected: int = 0
    n_unresolved: int = 0
    n_executed: int = 0
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    best_net_bps: Optional[float] = None
    best_candidate: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)

    def note_rejection(self, reason: str) -> None:
        key = (reason or "non precise")[:80]
        self.rejection_reasons[key] = self.rejection_reasons.get(key, 0) + 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id, "ts_utc": self.ts_utc,
            "n_instruments": self.n_instruments,
            "n_detected": self.n_detected,
            "n_insufficient_data": self.n_insufficient_data,
            "n_evaluated": self.n_evaluated,
            "n_accepted": self.n_accepted,
            "n_rejected": self.n_rejected,
            "n_unresolved": self.n_unresolved,
            "n_executed": self.n_executed,
            "rejection_reasons": self.rejection_reasons,
            "best_net_bps": self.best_net_bps,
            "best_candidate": self.best_candidate,
            "errors": self.errors[:10],
            "executed": self.executed,
        }


@dataclass
class FundingObservation:
    """Un actif observe sur les deux venues, a un instant."""
    symbol: str
    spec: InstrumentSpec
    near: VenueFunding          # la venue dont on porte l'instrument
    far: VenueFunding
    book: Optional[OrderBook] = None
    capacity_usd: Optional[float] = None

    def to_context(self) -> MarketContext:
        return MarketContext(
            instrument=self.spec, book=self.book,
            funding={"long_venue": self.near, "short_venue": self.far},
            extras={"capacity_usd": self.capacity_usd})


@dataclass
class FlowObservation:
    """Un actif et son etat de flux force, a un instant.

    `book` est le carnet REEL au moment du declenchement. C'est la grandeur
    que ce type d'observation existe pour porter : la profondeur et le spread
    a l'instant precis ou le flux force frappe sont invisibles dans toute
    rediffusion historique, et ils decident si le deplacement de prix est
    capturable ou seulement visible.
    """
    symbol: str
    spec: InstrumentSpec
    flow: Any                   # ForcedFlowState
    book: Optional[OrderBook] = None
    capacity_usd: Optional[float] = None

    def to_context(self) -> MarketContext:
        return MarketContext(
            instrument=self.spec, book=self.book,
            extras={"forced_flow": self.flow,
                    "capacity_usd": self.capacity_usd})


class Bot:
    """Boucle de criblage. Aucune dependance reseau en dur : la collecte est
    injectee, ce qui rend chaque decision testable sans marche."""

    def __init__(self, feed, *, risk: Optional[RiskGate] = None,
                 ledger=None, min_abs_apr: float = 0.20,
                 horizon_h: int = 168,
                 notional_usd: float = 1_000.0,
                 executor=None, registry=None):
        self.feed = feed
        if registry is None:
            registry = OpportunityRegistry().register(
                FundingSpreadOpportunity(min_abs_apr=min_abs_apr,
                                         horizon_h=horizon_h))
        self.registry = registry
        self.risk = risk or RiskGate(RiskLimits())
        self.ledger = ledger
        self.notional_usd = notional_usd
        self.executor = executor
        self.run_id = f"bot-{uuid.uuid4().hex[:10]}"

    # ---- un cycle ---------------------------------------------------------
    def run_cycle(self) -> CycleReport:
        rep = CycleReport(run_id=self.run_id, ts_utc=utc_now_iso())
        try:
            observations = self.feed.snapshot()
        except Exception as exc:                      # noqa: BLE001
            rep.errors.append(f"collecte: {type(exc).__name__}: {exc}")
            return rep

        rep.n_instruments = len(observations)
        for obs in observations:
            try:
                self._process(obs, rep)
            except Exception as exc:                  # noqa: BLE001
                rep.errors.append(f"{obs.symbol}: {type(exc).__name__}: {exc}")
        return rep

    def _process(self, obs, rep: CycleReport) -> None:
        ctx = obs.to_context()
        results = self.registry.detect_all(ctx)
        for name, res in results.items():
            if res.status is DetectionStatus.INSUFFICIENT_DATA:
                rep.n_insufficient_data += 1
                continue
            if res.status is DetectionStatus.ERROR:
                # Le registre absorbe l'exception pour ne pas tuer le noyau.
                # Il ne faut surtout pas qu'elle disparaisse pour autant :
                # un detecteur casse produirait sinon un cycle « propre » a
                # zero candidate, indiscernable d'un marche sans opportunite.
                rep.errors.append(f"{obs.symbol}/{name}: {res.reason}")
                continue
            for cand in res.candidates:
                rep.n_detected += 1
                self._evaluate_and_maybe_execute(cand, obs, rep)

    def _evaluate_and_maybe_execute(self, cand: Candidate, obs,
                                    rep: CycleReport) -> None:
        if obs.book is None:
            # Sans carnet on ne peut chiffrer ni spread ni impact. On ne
            # devine pas : la candidate reste non resolue.
            rep.n_unresolved += 1
            rep.note_rejection("carnet absent : couts non chiffrables")
            return

        side = cand.direction.taker_side
        # Frais ASSUMED depuis les baremes publics des DEUX venues, 2 jambes
        # a l'aller et 2 au retour. Spread et impact viennent du carnet REEL.
        #
        # Les trois composantes restantes sont resolues EXPLICITEMENT, chacune
        # avec sa justification. Aucune n'est mise a zero en silence :
        #
        #  - latence : la table de decroissance mesuree a ete construite avec
        #    une entree a t+1h. Le haircut EMBARQUE donc deja une heure de
        #    retard, sur un horizon de 168 h. Compter la latence en plus
        #    reviendrait a la facturer deux fois.
        #  - funding : c'est le REVENU de cette famille, deja porte par
        #    gross_capture_bps. Le recompter en cout serait un double comptage.
        #  - slippage : declare EXCLU, pas mesure a zero. Le resultat est donc
        #    une BORNE INFERIEURE du cout. Cette exclusion est interdite en
        #    mode EXECUTION, ce qui empeche structurellement d'en faire un
        #    feu vert de deploiement.
        costs = build_breakdown(
            obs.book, side, self.notional_usd,
            style=ExecutionStyle.TAKER, strict_fees=False, legs=4,
            latency=latency_not_applicable(
                "l'esperance mesuree integre deja le delai d'observation : "
                "entree a t+1h pour le funding, a la cloture de la minute "
                "pour le flux force"),
            funding=funding_not_applicable(
                "le funding net EST la capture de cette famille, deja compte "
                "dans gross_capture_bps"),
            slippage=slippage_excluded_for_paper_validation())

        risk_decision = self.risk.evaluate(
            obs.spec, self.notional_usd,
            capacity_usd=cand.capacity_usd,
            reference_price=obs.book.mid)

        ev = evaluate(cand, costs,
                      required_notional_usd=self.notional_usd,
                      risk_decision=risk_decision,
                      mode=EvaluationMode.CAPTURE_VALIDATION)
        rep.n_evaluated += 1

        net = ev.expected_net_capture_bps
        if net is not None and (rep.best_net_bps is None or net > rep.best_net_bps):
            rep.best_net_bps = net
            rep.best_candidate = _candidate_label(obs.symbol, cand)

        if ev.status is CaptureStatus.UNRESOLVED:
            rep.n_unresolved += 1
            rep.note_rejection(f"UNRESOLVED: {ev.rejection_reason or ev.blocked_by}")
        elif ev.status is CaptureStatus.REJECTED:
            rep.n_rejected += 1
            rep.note_rejection(ev.rejection_reason or "rejet economique")
        else:
            rep.n_accepted += 1
            self._execute_paper(cand, ev, costs, obs, rep)

        if self.ledger is not None:
            self.ledger.record(cand, ev, costs=costs, run_id=self.run_id,
                               measurement_mode="LIVE_PAPER",
                               notes="criblage funding inter-venues")

    def _execute_paper(self, cand: Candidate, ev: Evaluation, costs,
                       obs: FundingObservation, rep: CycleReport) -> None:
        """Execution PAPIER uniquement. Il n'existe aucun chemin vers un
        ordre reel depuis cette methode."""
        if self.executor is None:
            return
        notional = ev.approved_notional_usd or self.notional_usd
        fill = self.executor.submit(obs.spec, cand.direction, obs.book, notional)
        rep.n_executed += 1
        rep.executed.append({
            "symbol": obs.symbol,
            "direction": cand.direction.value,
            "net_bps": ev.expected_net_capture_bps,
            "filled_usd": fill.filled_notional_usd,
            "rejected": fill.is_rejected,
            "reason": fill.reject_reason,
        })


def _candidate_label(symbol: str, cand: Candidate) -> str:
    """Etiquette lisible d'une candidate, AGNOSTIQUE a la famille.

    POURQUOI CETTE FONCTION EXISTE. Le code de criblage est generique — il
    accepte tout detecteur enregistre — mais il lisait `observed_state['diff_apr']`
    en dur pour nommer la meilleure candidate. Ce champ n'existe QUE dans la
    famille funding. Des qu'une candidate d'une autre famille (flux force,
    liquidation) devenait la meilleure, la lecture levait KeyError, l'exception
    remontait au `try` du cycle, et les candidates SUIVANTES de la meme
    observation etaient perdues — un cycle « propre » a zero acceptation,
    strictement indiscernable d'un marche sans opportunite. C'est exactement le
    mode d'echec que ce bot dit refuser (voir _process). Un detecteur
    supplementaire, precisement ce qu'exige une detection large, suffisait a le
    declencher.

    L'etiquette garde la finesse de la famille funding quand elle est
    disponible, et retombe sur le type d'opportunite sinon.
    """
    diff = cand.observed_state.get("diff_apr")
    if isinstance(diff, (int, float)):
        return f"{symbol} ({diff * 100:+.0f} %/an)"
    return f"{symbol} ({cand.opportunity_type})"


# ---- rendu ---------------------------------------------------------------

def render(rep: CycleReport) -> str:
    lines = [
        f"cycle {rep.ts_utc}  run={rep.run_id}",
        f"  instruments surveilles : {rep.n_instruments}",
        f"  candidates detectees   : {rep.n_detected}"
        f"   (donnees insuffisantes : {rep.n_insufficient_data})",
        f"  evaluees               : {rep.n_evaluated}",
        f"    acceptees            : {rep.n_accepted}",
        f"    rejetees             : {rep.n_rejected}",
        f"    non resolues         : {rep.n_unresolved}",
        f"  executees (PAPIER)     : {rep.n_executed}",
    ]
    if rep.best_net_bps is not None:
        lines.append(f"  meilleure nette        : {rep.best_net_bps:+.2f} bps"
                     f"  [{rep.best_candidate}]")
    if rep.rejection_reasons:
        lines.append("  motifs de refus :")
        for reason, n in sorted(rep.rejection_reasons.items(),
                                key=lambda kv: -kv[1])[:6]:
            lines.append(f"    {n:>4}x  {reason}")
    for e in rep.errors[:5]:
        lines.append(f"  ERREUR {e}")
    if rep.n_accepted == 0 and rep.n_evaluated > 0:
        lines.append("  -> aucune opportunite ne couvre ses couts. "
                     "C'est le resultat attendu compte tenu de la mesure.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Bot de criblage — PAPIER uniquement, endpoints publics.")
    ap.add_argument("--cycles", type=int, default=1,
                    help="nombre de cycles (0 = boucle sans fin)")
    ap.add_argument("--interval", type=float, default=300.0,
                    help="secondes entre deux cycles")
    ap.add_argument("--symbols", type=int, default=40,
                    help="instruments surveilles par cycle")
    ap.add_argument("--books", type=int, default=10,
                    help="carnets collectes par cycle (les ecarts les plus larges)")
    ap.add_argument("--notional", type=float, default=500.0)
    ap.add_argument("--horizon-h", type=int, default=168)
    ap.add_argument("--ledger", type=str, default="",
                    help="chemin du Capture Ledger (vide = pas d'ecriture)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    # Import tardif : le bot reste testable sans reseau.
    from prism_v2.execution import PaperExecutor
    from prism_v2.funding_feed import CrossVenueFeed

    ledger = None
    if args.ledger:
        from prism_v2.ledger import CaptureLedger
        ledger = CaptureLedger(Path(args.ledger))

    feed = CrossVenueFeed(max_symbols=args.symbols, book_for_top=args.books)
    bot = Bot(feed, ledger=ledger, horizon_h=args.horizon_h,
              notional_usd=args.notional, executor=PaperExecutor())

    print(f"PAPIER — aucun ordre reel. {len(feed._okx_ids)} perps OKX connus, "
          f"univers commun avec Hyperliquid.", flush=True)
    n = 0
    while args.cycles == 0 or n < args.cycles:
        rep = bot.run_cycle()
        print(json.dumps(rep.to_dict(), ensure_ascii=False)
              if args.json else render(rep), flush=True)
        n += 1
        if args.cycles == 0 or n < args.cycles:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
