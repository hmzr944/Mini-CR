"""Detecteurs microstructure — l'etat du carnet et du flux, jamais la forme des prix.

AVERTISSEMENT COMMUN, PORTE PAR CHAQUE CANDIDATE DE CE MODULE
Pour ces familles, le raw edge est une grandeur OBSERVABLE (deviation du
microprix, elargissement du spread, deplacement deja constate), mais
l'hypothese qu'elle se resorbe n'est PAS testee par le detecteur. Chaque
candidate porte `hypothesis_untested: True`.

Une FEATURE N'EST PAS UN ALPHA. Ces detecteurs proposent des situations a
tester causalement ; ils n'affirment rien. Une famille qui ne survit pas au
replay causal doit perdre sa priorite, pas etre reglee jusqu'a produire un
resultat favorable.

Aucun indicateur technique n'est utilise nulle part ici.
"""
from __future__ import annotations

from typing import List, Optional

from ..core_types import Direction, Provenance, ms_to_iso
from ..discovery import DetectionOutcome, Detector, Family
from ..market_state import MarketState
from ..opportunity import Candidate

PROBE_NOTIONAL_USD = 1_000.0


def _base_metadata(state: MarketState, extra: dict) -> dict:
    md = {
        "hypothesis_untested": True,
        "feature_is_not_alpha": (
            "grandeur observee, non validee comme edge. Seul le replay causal "
            "peut etablir si elle est capturable."),
        "no_technical_indicators": True,
        "spread_bps": state.spread_bps,
        "depth_imbalance": state.depth_imbalance,
        "realized_vol_bps_30s": state.realized_volatility_bps(),
    }
    md.update(extra)
    return md


def _mk(state: MarketState, det: Detector, opp_type: str, direction: Direction,
        edge_bps: float, side: str, horizon_ms: int, execution: str,
        extra: dict) -> Candidate:
    return Candidate(
        ts_utc=ms_to_iso(state.ts_ms), instrument=state.instrument,
        opportunity_type=opp_type, family=det.family.value,
        candidate_id=det.new_candidate_id(), direction=direction,
        gross_capture_bps=edge_bps, capacity_usd=state.depth_usd(side),
        causal_reference_ts_ms=state.ts_ms, expected_horizon_ms=horizon_ms,
        required_execution=execution,
        provenance=Provenance("OKX", f"books:{det.family.value}",
                              ms_to_iso(state.ts_ms), state.instrument.inst_id),
        invalidation_conditions={"edge_closes_below_bps": state.spread_bps},
        observed_state=state.snapshot(),
        metadata=_base_metadata(state, extra))


class BookImbalanceDetector(Detector):
    """Deviation du microprix par rapport au mid.

    Le microprix pondere le mid par les tailles opposees au touch. Son ecart
    au mid est une mesure de PRESSION de carnet directement observable.

    CORRIGE le 21/09/2026. La porte exigeait `|deviation| > spread`. Or le
    microprix est une moyenne ponderee de bid et ask : il vit dans [bid, ask],
    donc |deviation| <= spread/2 PAR CONSTRUCTION. La porte etait
    mathematiquement infranchissable et cette famille n'a jamais pu emettre un
    seul candidat -- verifie sur 20 000 carnets aleatoires couvrant sept ordres
    de grandeur : rapport maximal observe 0,500000 contre 1,0 exige.

    Le seuil etait de surcroit ECONOMIQUE (le cout d'une traversee taker) place
    dans un DETECTEUR, alors que l'architecture du depot enonce que le
    detecteur ne doit jamais decider qu'une opportunite est rentable. Le seuil
    est desormais un plancher de SIGNIFICATIVITE -- la deviation doit etre une
    fraction non negligeable du spread -- et c'est `economics.evaluate` qui
    tranche la rentabilite, avec l'hypothese d'execution explicite.
    """

    #: Fraction du demi-spread en dessous de laquelle la deviation n'est que du
    #: bruit d'arrondi de taille. Choisi avant mesure, non ajuste ensuite.
    MIN_DEVIATION_FRACTION_OF_HALF_SPREAD = 0.25

    family = Family.BOOK_IMBALANCE

    def detect(self, state: MarketState) -> DetectionOutcome:
        dev = state.microprice_deviation_bps
        if dev is None:
            return DetectionOutcome.insufficient(
                self.family, "microprix non calculable (carnet incomplet)")
        half = state.spread_bps / 2.0
        floor = half * self.MIN_DEVIATION_FRACTION_OF_HALF_SPREAD
        if half <= 0 or abs(dev) <= floor:
            return DetectionOutcome.nothing(
                self.family, f"deviation du microprix {dev:.4f} bps sous "
                             f"{self.MIN_DEVIATION_FRACTION_OF_HALF_SPREAD:.0%} "
                             f"du demi-spread ({floor:.4f} bps)")
        direction = Direction.LONG if dev > 0 else Direction.SHORT
        return DetectionOutcome.ok(self.family, [_mk(
            state, self, "MICROPRICE_DEVIATION", direction, abs(dev),
            direction.taker_side, 1_000, "TAKER_OR_MAKER",
            {"microprice": state.microprice, "mid": state.mid,
             "microprice_deviation_bps": dev,
             "aggressive_imbalance_5s": state.aggressive_imbalance()})])


class DepthWithdrawalDetector(Detector):
    """Retrait de liquidite : la profondeur fond par rapport a il y a 5 s.

    Le raw edge emis est l'ELARGISSEMENT DU SPREAD constate, une grandeur
    qu'un apporteur de liquidite pourrait capturer en cotant a l'interieur.
    Le retrait lui-meme n'est pas un edge : c'est le contexte.
    """

    family = Family.DEPTH_WITHDRAWAL
    requires = ("depth_history",)

    def __init__(self, lookback_ms: int = 5_000):
        self.lookback_ms = lookback_ms

    def detect(self, state: MarketState) -> DetectionOutcome:
        ratios = {s: state.depth_change_ratio(s, self.lookback_ms)
                  for s in ("bid", "ask")}
        if all(v is None for v in ratios.values()):
            return DetectionOutcome.insufficient(
                self.family, f"historique de profondeur ne couvrant pas "
                             f"{self.lookback_ms}ms", ["depth_history"])
        withdrawn = {s: r for s, r in ratios.items() if r is not None and r < 1.0}
        if not withdrawn:
            return DetectionOutcome.nothing(
                self.family, f"aucun retrait de profondeur sur {self.lookback_ms}ms "
                             f"(ratios {ratios})")
        # Elargissement du spread par rapport au minimum REELLEMENT observe.
        past_spreads = self._past_spreads(state)
        if not past_spreads:
            # Donnee manquante -> le dire. La version precedente substituait le
            # spread COURANT a chaque observation passee et rendait « rien
            # trouve », ce qui est indiscernable d'un vrai resultat negatif.
            return DetectionOutcome.insufficient(
                self.family, "pas d'historique de spread sur la fenetre",
                ["spread_history"])
        tightest = min(past_spreads)
        excess = state.spread_bps - tightest
        if excess <= 0.0:
            return DetectionOutcome.nothing(
                self.family, f"spread {state.spread_bps:.4f} bps contre un minimum "
                             f"passe de {tightest:.4f} bps : aucun elargissement")
        return DetectionOutcome.ok(self.family, [_mk(
            state, self, "DEPTH_WITHDRAWAL_SPREAD_EXCESS", Direction.LONG, excess,
            "ask", self.lookback_ms, "MAKER",
            {"depth_change_ratios": ratios, "tightest_spread_bps": tightest,
             "current_spread_bps": state.spread_bps, "spread_excess_bps": excess,
             "maker_required": (
                 "cet ecart n'est capturable qu'en APPORTANT de la liquidite. "
                 "Le prendre en taker revient a payer le spread elargi.")})])

    def _past_spreads(self, state: MarketState) -> List[float]:
        """Spreads REELLEMENT observes sur la fenetre, hors instant courant.

        CORRIGE le 21/09/2026. La version precedente parcourait
        `depth_history` -- qui ne contient aucun spread -- et ajoutait
        `state.spread_bps` a chaque tour. `min(past)` valait donc le spread
        courant, `excess` valait 0, et la porte `excess <= tightest` etait
        vraie pour tout carnet non croise. Le detecteur rendait « rien trouve »
        sur N'IMPORTE QUELLE entree : 72 configurations balayees, retrait de
        profondeur jusqu'a 99 %, spread jusqu'a 5000 bps, zero candidat.
        """
        cutoff = state.ts_ms - self.lookback_ms
        return [sp for ts, sp in state.spread_history
                if cutoff <= ts < state.ts_ms and sp > 0]


class AggressiveFlowDetector(Detector):
    """Desequilibre du flux agressif et deplacement deja provoque.

    Le raw edge est le deplacement du mid DEJA CONSTATE sur la fenetre,
    concomitant a un flux fortement desequilibre. Rien n'est extrapole.
    """

    family = Family.AGGRESSIVE_FLOW
    requires = ("trades",)

    def __init__(self, lookback_ms: int = 5_000):
        self.lookback_ms = lookback_ms

    def detect(self, state: MarketState) -> DetectionOutcome:
        imb = state.aggressive_imbalance(self.lookback_ms)
        if imb is None:
            return DetectionOutcome.insufficient(
                self.family, f"aucun trade sur {self.lookback_ms}ms", ["trades"])
        disp = state.realized_displacement_bps(self.lookback_ms)
        if disp is None:
            return DetectionOutcome.insufficient(
                self.family, "historique de mid insuffisant", ["mid_history"])
        floor = state.spread_bps
        if abs(disp) <= floor:
            return DetectionOutcome.nothing(
                self.family, f"deplacement {disp:.4f} bps sous le spread "
                             f"({floor:.4f} bps), flux desequilibre a {imb:+.2f}")
        # Le flux a pousse le prix ; l'hypothese testable est un retour partiel.
        direction = Direction.SHORT if disp > 0 else Direction.LONG
        return DetectionOutcome.ok(self.family, [_mk(
            state, self, "AGGRESSIVE_FLOW_DISPLACEMENT", direction, abs(disp),
            direction.taker_side, self.lookback_ms, "TAKER",
            {"aggressive_imbalance": imb, "displacement_bps": disp,
             "flow": state.aggressive_flow_usd(self.lookback_ms),
             "trade_intensity": state.trade_intensity(self.lookback_ms),
             "trade_to_book_ratio": state.trade_to_book_ratio(self.lookback_ms)})])


class ShortHorizonReversionDetector(Detector):
    """Ecart du mid courant a sa moyenne sur une courte fenetre.

    Grandeur observable. Que le prix revienne est l'hypothese a tester.
    Le seuil est economique : sous le spread, aucune reversion n'est captable.
    """

    family = Family.SHORT_HORIZON_REVERSION
    requires = ("mid_history",)

    def __init__(self, lookback_ms: int = 30_000, min_points: int = 5):
        self.lookback_ms = lookback_ms
        self.min_points = min_points

    def detect(self, state: MarketState) -> DetectionOutcome:
        pts = [m for m in state.mid_history if m[0] >= state.ts_ms - self.lookback_ms]
        if len(pts) < self.min_points:
            return DetectionOutcome.insufficient(
                self.family, f"{len(pts)} points de mid < {self.min_points} requis",
                ["mid_history"])
        mean = sum(p[1] for p in pts) / len(pts)
        if mean <= 0:
            return DetectionOutcome.insufficient(self.family, "moyenne de mid invalide")
        dev_bps = (state.mid - mean) / mean * 10_000.0
        floor = state.spread_bps
        if abs(dev_bps) <= floor:
            return DetectionOutcome.nothing(
                self.family, f"ecart a la moyenne {dev_bps:.4f} bps sous le spread "
                             f"({floor:.4f} bps) sur {len(pts)} points")
        direction = Direction.SHORT if dev_bps > 0 else Direction.LONG
        return DetectionOutcome.ok(self.family, [_mk(
            state, self, "SHORT_HORIZON_DEVIATION", direction, abs(dev_bps),
            direction.taker_side, self.lookback_ms, "TAKER_OR_MAKER",
            {"window_mean_mid": mean, "deviation_bps": dev_bps,
             "n_points": len(pts), "window_ms": self.lookback_ms})])


class SpreadDislocationDetector(Detector):
    """Spread anormalement large par rapport a la volatilite realisee.

    Un spread large en marche calme est un exces qu'un apporteur de liquidite
    peut capturer. Un spread large en marche agite est le PRIX du risque, pas
    une anomalie. Cette distinction evite de confondre volatilite et edge.
    """

    family = Family.SPREAD_DISLOCATION
    requires = ("mid_history",)

    def __init__(self, lookback_ms: int = 30_000):
        self.lookback_ms = lookback_ms

    def detect(self, state: MarketState) -> DetectionOutcome:
        vol = state.realized_volatility_bps(self.lookback_ms)
        if vol is None:
            return DetectionOutcome.insufficient(
                self.family, "volatilite realisee non calculable "
                             "(moins de 3 observations)", ["mid_history"])
        spread = state.spread_bps
        # Critere economique : un spread qui excede la volatilite realisee
        # remunere plus que le risque de tenir la position un instant.
        if spread <= vol:
            return DetectionOutcome.nothing(
                self.family, f"spread {spread:.4f} bps <= volatilite realisee "
                             f"{vol:.4f} bps : le spread paie le risque, "
                             "ce n'est pas un exces")
        excess = spread - vol
        return DetectionOutcome.ok(self.family, [_mk(
            state, self, "SPREAD_EXCEEDS_REALIZED_VOL", Direction.LONG, excess,
            "ask", self.lookback_ms, "MAKER",
            {"spread_bps": spread, "realized_vol_bps": vol,
             "excess_bps": excess,
             "maker_required": (
                 "capturable uniquement en APPORTANT de la liquidite ; "
                 "en taker on PAIE ce spread au lieu de l'encaisser"),
             "adverse_selection_warning": (
                 "un maker rempli dans ces conditions peut l'etre precisement "
                 "parce que le marche continue contre lui")})])
