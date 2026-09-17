#!/usr/bin/env python3
"""CRIBLE DE MECANISMES — chercher dans l'espace des CAUSES, pas des strategies.

L'audit a montre que la Phase C entiere reposait sur une premisse causale
fausse et testable en vingt minutes : la liquidation SUIT le mouvement de prix
au lieu de le causer. Le cout de l'erreur n'etait pas la strategie, c'etait
d'avoir choisi un evenement sans verifier son sens causal.

Ce module inverse la demarche. Au lieu de choisir une famille et de la tester
pendant des semaines, il definit BEAUCOUP d'evenements candidats et ne mesure
qu'une chose : l'evenement PRECEDE-t-il le mouvement, ou le suit-il ?

    anteriorite = mouvement AVANT l'evenement, dans le sens qu'il annonce
    posteriorite = mouvement APRES

Un evenement dont l'anteriorite domine est un symptome deja price : aucune
strategie ne le rendra exploitable, et il ne merite pas une heure de plus.
Seuls les evenements dont la posteriorite domine sont des candidats.

CE QUE LE CRIBLE NE DIT PAS. Qu'un evenement precede le mouvement ne le rend
pas rentable : il faut encore que l'amplitude posterieure depasse les couts,
que la capacite suive, et que le signe tienne hors echantillon. Le crible
elimine, il ne selectionne pas.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prism_v2.sanity import CausalReport, check_causal_direction


@dataclass
class Tape:
    """Carnets et trades d'un instrument, indexes par temps."""
    inst: str
    poll_ms: List[int]
    mid: List[float]
    half_spread_bps: List[float]
    bid_depth_usd: List[float]
    ask_depth_usd: List[float]
    #: trades agreges a la seconde : ts -> (achat_usd, vente_usd, n)
    trades: Dict[int, Tuple[float, float, int]]

    def mid_at(self, ts: int) -> Optional[float]:
        """Mid au releve le plus proche a ou apres ts. None hors couverture."""
        if not self.poll_ms or ts < self.poll_ms[0] or ts > self.poll_ms[-1]:
            return None
        i = bisect_left(self.poll_ms, ts)
        if i >= len(self.poll_ms):
            return None
        return self.mid[i]


def load_tape(path: Path, ct_val: float = 1.0) -> Optional[Tape]:
    """Relit un fichier de collecte.

    `ct_val` convertit les tailles OKX, qui sont en CONTRATS, en unites de
    base. Le supposer a 1 surestimait la profondeur BTC d'un facteur 100 —
    c'est l'un des quatorze defauts de l'audit, et il est parametre ici.
    """
    polls: List[int] = []
    mids: List[float] = []
    hs: List[float] = []
    bd: List[float] = []
    ad: List[float] = []
    trades: Dict[int, List[float]] = {}
    inst = ""
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("bids") or not r.get("asks"):
            continue
        inst = r["inst"]
        b, a = r["bids"][0][0], r["asks"][0][0]
        if not (a > b > 0):
            continue
        mid = (a + b) / 2.0
        polls.append(int(r["poll_ms"]))
        mids.append(mid)
        hs.append((a - b) / 2.0 / mid * 10_000.0)
        bd.append(sum(px * sz * ct_val for px, sz in r["bids"][:5]))
        ad.append(sum(px * sz * ct_val for px, sz in r["asks"][:5]))
        for ts, side, sz, px in r.get("trades") or []:
            sec = int(ts) - int(ts) % 1000
            cell = trades.setdefault(sec, [0.0, 0.0, 0.0])
            notional = sz * px * ct_val
            if side == "buy":
                cell[0] += notional
            else:
                cell[1] += notional
            cell[2] += 1
    if len(polls) < 50:
        return None
    order = sorted(range(len(polls)), key=lambda i: polls[i])
    return Tape(inst, [polls[i] for i in order], [mids[i] for i in order],
                [hs[i] for i in order], [bd[i] for i in order],
                [ad[i] for i in order],
                {k: (v[0], v[1], int(v[2])) for k, v in trades.items()})


# ── Evenements candidats ─────────────────────────────────────────────────────
#
# Chacun rend une liste de (timestamp_ms, sens) ou sens = +1 si l'evenement
# « annonce » une hausse, -1 une baisse. Le crible mesure ensuite si le prix
# bougeait deja dans ce sens AVANT.

@dataclass(frozen=True)
class Event:
    ts_ms: int
    direction: int


def ev_large_trade(tape: Tape, quantile: float = 0.98) -> List[Event]:
    """Un trade agressif anormalement gros."""
    secs = sorted(tape.trades)
    tot = [tape.trades[s][0] + tape.trades[s][1] for s in secs]
    if len(tot) < 50:
        return []
    thr = sorted(tot)[int(quantile * len(tot))]
    out = []
    for s in secs:
        buy, sell, _ = tape.trades[s]
        if buy + sell < thr or buy + sell <= 0:
            continue
        imb = (buy - sell) / (buy + sell)
        if abs(imb) < 0.5:
            continue
        out.append(Event(s, 1 if imb > 0 else -1))
    return out


def ev_trade_burst(tape: Tape, quantile: float = 0.98) -> List[Event]:
    """Une rafale de trades : beaucoup d'impressions dans la meme seconde."""
    secs = sorted(tape.trades)
    counts = [tape.trades[s][2] for s in secs]
    if len(counts) < 50:
        return []
    thr = sorted(counts)[int(quantile * len(counts))]
    out = []
    for s in secs:
        buy, sell, n = tape.trades[s]
        if n < thr or buy + sell <= 0:
            continue
        imb = (buy - sell) / (buy + sell)
        if abs(imb) < 0.3:
            continue
        out.append(Event(s, 1 if imb > 0 else -1))
    return out


def ev_book_imbalance(tape: Tape, quantile: float = 0.98) -> List[Event]:
    """Desequilibre extreme de profondeur entre les deux cotes du carnet."""
    ratios = []
    for i in range(len(tape.poll_ms)):
        tot = tape.bid_depth_usd[i] + tape.ask_depth_usd[i]
        ratios.append(((tape.bid_depth_usd[i] - tape.ask_depth_usd[i]) / tot)
                      if tot > 0 else 0.0)
    if len(ratios) < 50:
        return []
    thr = sorted(abs(r) for r in ratios)[int(quantile * len(ratios))]
    return [Event(tape.poll_ms[i], 1 if ratios[i] > 0 else -1)
            for i in range(len(ratios)) if abs(ratios[i]) >= thr]


def ev_spread_widen(tape: Tape, quantile: float = 0.98) -> List[Event]:
    """Ecartement anormal du spread : les teneurs se retirent.

    Sans direction propre ; on lui donne le sens du desequilibre de carnet
    observe au meme instant, faute de mieux.
    """
    if len(tape.half_spread_bps) < 50:
        return []
    thr = sorted(tape.half_spread_bps)[int(quantile * len(tape.half_spread_bps))]
    out = []
    for i, h in enumerate(tape.half_spread_bps):
        if h < thr:
            continue
        tot = tape.bid_depth_usd[i] + tape.ask_depth_usd[i]
        if tot <= 0:
            continue
        d = tape.bid_depth_usd[i] - tape.ask_depth_usd[i]
        out.append(Event(tape.poll_ms[i], 1 if d > 0 else -1))
    return out


def ev_depth_drop(tape: Tape, quantile: float = 0.98) -> List[Event]:
    """Effondrement soudain de la profondeur d'un cote."""
    out = []
    n = len(tape.poll_ms)
    if n < 50:
        return []
    drops = []
    for i in range(1, n):
        pb = tape.bid_depth_usd[i - 1] or 1e-9
        pa = tape.ask_depth_usd[i - 1] or 1e-9
        drops.append((tape.bid_depth_usd[i] / pb, tape.ask_depth_usd[i] / pa))
    flat = sorted(min(a, b) for a, b in drops)
    thr = flat[int((1 - quantile) * len(flat))]
    for i, (rb, ra) in enumerate(drops, start=1):
        if rb <= thr and rb < ra:
            out.append(Event(tape.poll_ms[i], -1))   # bids partent : baisse
        elif ra <= thr and ra < rb:
            out.append(Event(tape.poll_ms[i], 1))
    return out


CANDIDATES: Dict[str, Callable[[Tape], List[Event]]] = {
    "gros_trade_agressif": ev_large_trade,
    "rafale_de_trades": ev_trade_burst,
    "desequilibre_carnet": ev_book_imbalance,
    "ecartement_spread": ev_spread_widen,
    "effondrement_profondeur": ev_depth_drop,
}


# ── Crible ───────────────────────────────────────────────────────────────────

def measure_event(tape: Tape, events: Sequence[Event], pre_s: int,
                  post_s: int) -> Tuple[List[float], List[float]]:
    """Mouvements avant et apres, orientes par le sens annonce.

    Rend les valeurs BRUTES, sans agreger ni seuiller. L'agregation appartient
    a l'appelant : une premiere version rendait un rapport par instrument et
    exigeait dix evenements CHACUN, ce qui eliminait silencieusement tous les
    evenements de carnet — rares par instrument mais nombreux au total.
    """
    pre: List[float] = []
    post: List[float] = []
    for e in events:
        p0 = tape.mid_at(e.ts_ms)
        pb = tape.mid_at(e.ts_ms - pre_s * 1000)
        pa = tape.mid_at(e.ts_ms + post_s * 1000)
        if not p0 or not pb or not pa or p0 <= 0 or pb <= 0:
            continue
        pre.append(e.direction * (p0 / pb - 1.0) * 10_000.0)
        post.append(e.direction * (pa / p0 - 1.0) * 10_000.0)
    return pre, post


def screen_event(tape: Tape, events: Sequence[Event],
                 pre_s: int = 30, post_s: int = 30) -> Optional[CausalReport]:
    pre, post = measure_event(tape, events, pre_s, post_s)
    if len(pre) < 10:
        return None
    return check_causal_direction("evenement", pre, post)


#: Cout d'aller-retour MESURE sur OKX : 2 x 5 bps de frais + 2 x demi-spread
#: reel. Un evenement dont le mouvement posterieur ne le depasse pas n'est
#: pas exploitable, meme s'il est parfaitement causal.
ROUND_TRIP_COST_BPS = 11.0


def screen_all(tapes: Sequence[Tape], pre_s: int = 30, post_s: int = 30,
               min_post_bps: float = 0.0) -> Dict[str, dict]:
    """Crible tous les evenements candidats sur tous les instruments.

    `min_post_bps` a 0 pose la question causale seule ; a ROUND_TRIP_COST_BPS
    il pose la question economique : reste-t-il quelque chose APRES les couts.
    """
    out: Dict[str, dict] = {}
    for name, fn in CANDIDATES.items():
        pre_all: List[float] = []
        post_all: List[float] = []
        n_inst = 0
        for tape in tapes:
            evs = fn(tape)
            if not evs:
                continue
            pre, post = measure_event(tape, evs, pre_s, post_s)
            if not pre:
                continue
            n_inst += 1
            # On met en commun les observations BRUTES. Agreger par instrument
            # puis dupliquer la moyenne ecrasait la dispersion et rendait tout
            # t-stat ulterieur faux.
            pre_all += pre
            post_all += post
        if len(pre_all) < 10:
            out[name] = {"n": len(pre_all), "statut": "ECHANTILLON_INSUFFISANT"}
            continue
        rep = check_causal_direction(name, pre_all, post_all,
                                     min_post_bps=min_post_bps)
        out[name] = {
            "n": rep.n, "instruments": n_inst,
            "avant_bps": round(rep.pre_move_bps, 3),
            "apres_bps": round(rep.post_move_bps, 3),
            "ratio": (None if rep.ratio is None or math.isinf(rep.ratio)
                      else round(rep.ratio, 2)),
            "statut": rep.verdict.status,
            "candidat": rep.verdict.status != "ALERTE",
        }
    return out
