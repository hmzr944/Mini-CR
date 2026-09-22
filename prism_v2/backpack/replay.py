#!/usr/bin/env python3
"""Rejouer la bande contre un HISTORIQUE DE CARNET enregistre.

CE QUE CE MODULE CORRIGE. `passive.py` estimait le mid depuis la bande, faute
de mieux, et la garde `tape_mid_is_fit_for_level` a etabli que ce terme etait
un artefact de fraicheur. Ici le mid n'est plus estime : il est LU dans un
instantane enregistre a moins d'une cadence de sondage du fill. Le
demi-spread encaisse et le markout deviennent donc deux mesures, et non plus
une mesure et une reconstruction.

DEUX CHOSES QUE CE MODULE REFUSE DE FAIRE.

  1. INTERPOLER. Entre deux instantanes il n'y a aucune donnee. Une valeur
     interpolee aurait l'air d'une mesure et n'en serait pas ; un instantane
     trop vieux rend donc INCONNU, jamais une moyenne de ses voisins.

  2. REPETER LE DERNIER CARNET CONNU. Un carnet perime reconduit produirait
     un markout exactement nul — c'est-a-dire l'absence d'adverse selection,
     le resultat le plus flatteur possible, fabrique par un defaut de donnee.

LA FILE N'EST PLUS SUPPOSEE. `passive.py` donnait au maker la premiere place,
hypothese la plus genereuse et declaree comme telle. L'instantane porte la
taille AU TOUCHER : c'est la file reellement devant un ordre qui arrive, et
`subsidy.tape.simulate` l'attend deja sous le nom `queue_ahead`. Le
remplissage cesse d'etre une hypothese pour devenir une consequence du carnet
enregistre.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.backpack.collector import DEFAULT_POLL_S
from prism_v2.backpack.passive import (MARKOUT_HORIZONS_S, half_spread_earned_bps,
                                       markout_bps)
from prism_v2.backpack.venue import fetch_tape
from prism_v2.subsidy.tape import RestingQuote, Trade, simulate

#: Age maximal d'un instantane pour qu'il decrive encore le carnet demande,
#: exprime en multiples de la cadence de sondage. Deux cadences laissent
#: passer un cycle manque sans laisser passer un carnet d'il y a une minute.
MAX_SNAPSHOT_AGE_POLLS = 2.0

#: Nombre de fills minimal pour qu'une mediane veuille dire quelque chose.
#: Meme seuil que `scans/backpack_mm.py`, meme raison.
MIN_FILLS_FOR_MEDIAN = 30


@dataclass(frozen=True)
class Snap:
    ts: float
    bid: float
    ask: float
    bid_sz: float
    ask_sz: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread_bps(self) -> float:
        return 1e4 * (self.ask - self.bid) / self.mid


class BookHistory:
    """Les instantanes d'un symbole, indexes pour une lecture par temps."""

    def __init__(self, snaps: Sequence[Snap], poll_s: float = DEFAULT_POLL_S):
        self.snaps = sorted(snaps, key=lambda s: s.ts)
        self.ts = [s.ts for s in self.snaps]
        self.max_age_s = MAX_SNAPSHOT_AGE_POLLS * poll_s

    def __len__(self) -> int:
        return len(self.snaps)

    def window(self) -> Optional[Tuple[float, float]]:
        if not self.snaps:
            return None
        return self.ts[0], self.ts[-1]

    def before(self, at_ts: float) -> Optional[Snap]:
        """Dernier instantane STRICTEMENT anterieur a `at_ts`, s'il est frais.

        Strictement anterieur : un instantane pris au meme horodatage que
        l'echange pourrait deja contenir son effet, et le demi-spread mesure
        contre lui serait contamine par notre propre execution.
        """
        i = bisect_left(self.ts, at_ts) - 1
        if i < 0:
            return None
        s = self.snaps[i]
        return s if at_ts - s.ts <= self.max_age_s else None

    def at_or_after(self, at_ts: float) -> Optional[Snap]:
        """Premier instantane a `at_ts` ou apres, s'il n'est pas trop tardif.

        Sert a lire le mid A UN HORIZON : prendre l'instantane precedent
        raccourcirait l'horizon sans le dire.
        """
        i = bisect_right(self.ts, at_ts - 1e-9)
        if i >= len(self.snaps):
            return None
        s = self.snaps[i]
        return s if s.ts - at_ts <= self.max_age_s else None

    def median_spread_bps(self) -> Optional[float]:
        if len(self.snaps) < MIN_FILLS_FOR_MEDIAN:
            return None
        return statistics.median(s.spread_bps for s in self.snaps)


def load_books(path: Path, poll_s: float = DEFAULT_POLL_S
               ) -> Dict[str, BookHistory]:
    """Charge un JSONL d'instantanes. Une ligne tronquee est IGNOREE.

    Un collecteur tue en cours d'ecriture laisse une derniere ligne
    incomplete. La sauter est correct ; tenter de la reparer inventerait un
    carnet.
    """
    by_sym: Dict[str, List[Snap]] = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                s = Snap(d["ts"], d["bid"], d["ask"], d["bid_sz"], d["ask_sz"])
            except Exception:
                continue
            by_sym.setdefault(d["sym"], []).append(s)
    return {k: BookHistory(v, poll_s) for k, v in by_sym.items()}


def load_tape(path: Path) -> Dict[str, List[Trade]]:
    """Charge une bande CAPTUREE par le collecteur, par symbole.

    Preferer ce fichier a un appel live : `/trades` plafonne a 1 000 echanges
    et la bande d'un marche actif s'echappe de la fenetre en moins d'une heure.
    Une ligne tronquee est sautee, jamais reparee.
    """
    by_sym: Dict[str, List[Trade]] = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                t = Trade(d["ts"], d["price"], d["size"], d["taker_is_buy"])
            except Exception:
                continue
            by_sym.setdefault(d["sym"], []).append(t)
    return by_sym


def tape_covers_window(trades: Sequence[Trade],
                       window: Tuple[float, float]) -> bool:
    """La bande a-t-elle ECHAPPE la fenetre, ou la couvre-t-elle encore ?

    Le controle que le premier rejeu n'avait pas : sans lui, une bande trop
    courte rend « 0 echange » et se lit comme un marche mort au lieu d'un
    defaut de capture.

    SEUL LE BORD GAUCHE EST UN DEFAUT. `/trades` plafonne a 1 000 echanges :
    sur un marche actif, les plus anciens sortent de la fenetre et c'est
    exactement la faute qui avait perdu BTC, ETH et SOL. Le bord DROIT, lui,
    ne prouve rien — une bande dont le dernier echange precede le dernier
    instantane decrit un marche qui n'a simplement pas traite pendant
    quelques secondes, ce qui est le cas normal d'un marche calme.

    La premiere version exigeait les deux bords et rendait NON sur les six
    marches d'une collecte dont la bande avait pourtant ete capturee par le
    collecteur lui-meme. Une garde qui alarme sur du sain est une garde qu'on
    apprend a ignorer : elle est donc resserree sur ce qu'elle sait detecter.
    """
    if not trades:
        return False
    t0, _ = window
    return min(t.ts for t in trades) <= t0


@dataclass
class ReplayResult:
    """Ce que le carnet enregistre dit d'un marche, et ce qu'il laisse ouvert."""

    symbol: str
    snapshots: int
    window_s: Optional[float]
    median_spread_bps: Optional[float]
    trades_in_window: int
    half_spreads_bps: List[float]
    markouts_bps: Dict[float, List[float]]
    #: Fills d'une cotation au toucher, file REELLE devant, sur la fenetre.
    queue_aware_fills: Optional[int]

    def median_half_spread_bps(self) -> Optional[float]:
        if len(self.half_spreads_bps) < MIN_FILLS_FOR_MEDIAN:
            return None
        return statistics.median(self.half_spreads_bps)

    def median_markout_bps(self, horizon_s: float) -> Optional[float]:
        vals = self.markouts_bps.get(horizon_s, [])
        if len(vals) < MIN_FILLS_FOR_MEDIAN:
            return None
        return statistics.median(vals)

    def breakeven_fee_bps(self, horizon_s: float) -> Optional[float]:
        """Frais maker que le marche supporte. INCONNU si un terme manque."""
        hs = self.median_half_spread_bps()
        mo = self.median_markout_bps(horizon_s)
        if hs is None or mo is None:
            return None
        return hs + mo


def replay(symbol: str, books: BookHistory, tape: Sequence[Trade],
           horizons_s: Sequence[float] = MARKOUT_HORIZONS_S) -> ReplayResult:
    """Mesure le fill passif d'un symbole contre son carnet enregistre.

    Seuls les echanges TOMBANT DANS la fenetre de collecte comptent : hors
    d'elle il n'existe aucun carnet, et les inclure reviendrait a mesurer
    contre un carnet suppose.
    """
    win = books.window()
    if win is None:
        return ReplayResult(symbol, 0, None, None, 0, [], {}, None)
    t0, t1 = win
    in_win = [t for t in tape if t0 <= t.ts <= t1]

    half: List[float] = []
    mo: Dict[float, List[float]] = {h: [] for h in horizons_s}
    for t in in_win:
        s_before = books.before(t.ts)
        if s_before is None:
            continue
        passive_is_buy = not t.taker_is_buy
        half.append(half_spread_earned_bps(t.price, passive_is_buy,
                                           s_before.mid))
        for h in horizons_s:
            s_after = books.at_or_after(t.ts + h)
            if s_after is None:
                continue
            mo[h].append(markout_bps(t.price, passive_is_buy,
                                     s_before.mid, s_after.mid))

    return ReplayResult(symbol, len(books), t1 - t0,
                        books.median_spread_bps(), len(in_win), half, mo,
                        queue_aware_fills=_queue_aware_fills(books, in_win))


def _queue_aware_fills(books: BookHistory, trades: Sequence[Trade]
                       ) -> Optional[int]:
    """Fills d'un bid pose au toucher, DERRIERE la file reellement presente.

    On pose l'ordre au premier instantane de la fenetre, a son meilleur bid,
    avec pour `queue_ahead` la taille exacte qui y dort. C'est la premiere
    simulation de remplissage du projet dont la file ne soit pas supposee.
    """
    if not books.snaps or not trades:
        return None
    s0 = books.snaps[0]
    q = RestingQuote(price=s0.bid, size=s0.bid_sz, is_bid=True,
                     queue_ahead=s0.bid_sz, placed_ts=s0.ts)
    return len(simulate(q, list(trades)).fills)


def _fmt(v: Optional[float], width: int = 10, places: int = 2) -> str:
    return "INCONNU".rjust(width) if v is None else f"{v:>{width}.{places}f}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--books", type=Path, required=True)
    p.add_argument("--tape", type=Path, default=None,
                   help="bande CAPTUREE par le collecteur ; sinon appel live "
                        "(qui perd les marches actifs, voir capture_tape)")
    p.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S)
    p.add_argument("--horizon-s", type=float, default=60.0)
    a = p.parse_args(argv)

    books = load_books(a.books, a.poll_s)
    tapes = load_tape(a.tape) if a.tape else None
    print(f"symboles avec carnet enregistre : {len(books)}")
    print(f"bande : {'CAPTUREE ' + str(a.tape) if tapes else 'appel live'}")
    print(f"horizon de markout : {a.horizon_s:.0f} s   "
          f"seuil de mediane : {MIN_FILLS_FOR_MEDIAN} fills")
    print()
    head = (f"{'marche':<20}{'snaps':>7}{'min':>6}{'spread':>9}{'ech.':>7}"
            f"{'demi-spr':>10}{'markout':>10}{'equilibre':>11}{'N':>6}{'file':>7}"
            f"{'couvre?':>8}")
    print(head)
    print("-" * len(head))

    for sym in sorted(books):
        tape = tapes.get(sym, []) if tapes is not None else fetch_tape(sym)
        win = books[sym].window()
        couvre = tape_covers_window(tape, win) if win else False
        r = replay(sym, books[sym], tape, horizons_s=(a.horizon_s,))
        n = len(r.markouts_bps.get(a.horizon_s, []))
        print(f"{sym:<20}{r.snapshots:>7}"
              f"{(r.window_s or 0) / 60.0:>6.0f}"
              f"{_fmt(r.median_spread_bps, 9, 2)}{r.trades_in_window:>7}"
              f"{_fmt(r.median_half_spread_bps())}"
              f"{_fmt(r.median_markout_bps(a.horizon_s))}"
              f"{_fmt(r.breakeven_fee_bps(a.horizon_s), 11)}{n:>6}"
              f"{'-' if r.queue_aware_fills is None else r.queue_aware_fills:>7}"
              f"{'oui' if couvre else 'NON':>8}")

    print()
    print("spread    : mediane du spread REEL sur la fenetre (carnet lu).")
    print("demi-spr  : encaisse par une cotation au toucher, contre mid LU.")
    print("markout   : derive du mid entre le fill et l'horizon. Negatif =")
    print("            adverse selection.")
    print("equilibre : frais maker supporte. Negatif = il faut un REBATE.")
    print("file      : fills d'un ordre pose DERRIERE la file reelle.")
    print("couvre?   : la bande couvre-t-elle toute la fenetre de carnet ?")
    print("            NON = defaut de CAPTURE, pas un marche sans echange.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
