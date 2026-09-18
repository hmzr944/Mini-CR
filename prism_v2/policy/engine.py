"""LA BOUCLE : observer, evaluer les actions, choisir, executer, compter.

Ce module ne contient aucune opinion sur le marche. Il fabrique des
echantillons de decision a partir d'un panneau, applique une politique, et
remplit un registre auditable.

QUATRE REGLES DE CONSTRUCTION, toutes destinees a empecher un chiffre faux.

  GRILLE DISJOINTE. Les decisions sont espacees d'exactement un horizon.
  Deux positions ne se recouvrent donc jamais sur le meme instrument : le
  capital se compte sans double emploi et les observations ne sont pas des
  copies les unes des autres.

  TAILLE BORNEE PAR LE CARNET. La taille d'une decision ne depasse jamais la
  profondeur affichee au touch du cote traverse. Le glissement au-dela du
  touch vaut donc zero PAR CONSTRUCTION, et non par hypothese — au prix
  d'une capacite limitee a ce que le carnet montre, ce qui est la verite.

  CONTREFACTUEL EXACT. A chaque instant de decision, le resultat net de
  TOUTES les actions est calcule, pas seulement celui de l'action choisie.
  L'apprentissage porte donc sur des resultats reellement observes pour
  chaque action, jamais sur une reconstruction.

  SEPARATION TEMPORELLE STRICTE. L'apprentissage precede le test dans le
  temps, sans chevauchement d'un seul instant.
"""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from prism_v2.contracts import usd_notional
from prism_v2.instruments import InstrumentSpec
from prism_v2.margin import MarginSchedule
from prism_v2.policy.action import (ALL_ACTIONS, EXECUTABLE, Action, FeeModel,
                                    execute)
from prism_v2.policy.ledger import Decision, Ledger
from prism_v2.policy.state import StateBuilder, StateConfig
from prism_v2.policy.value import ActionValue


@dataclass(frozen=True)
class Market:
    """Tout ce qu'il faut savoir d'un instrument pour agir dessus."""
    inst_id: str
    spec: InstrumentSpec
    schedule: MarginSchedule
    fees: FeeModel
    rows: Sequence[Sequence[float]]


@dataclass(frozen=True)
class Sample:
    """Un instant de decision, avec le resultat net de chaque action."""
    ts_ms: int
    inst_id: str
    index: int
    state: Dict[str, float]
    nets: Dict[Action, float]        # bps nets, par action
    size_usd: float
    contracts: float
    capital_usd: float
    holding_s: float
    fills: Dict[Action, object]


def build_samples(m: Market, builder: StateBuilder, horizon_rows: int,
                  lo: int, hi: int, max_size_usd: float,
                  max_capital_usd: Optional[float] = None,
                  actions: Sequence[Action] = ALL_ACTIONS) -> List[Sample]:
    """Echantillons de decision sur [lo, hi), grille disjointe.

    `hi` est exclusif et la sortie doit tenir dans la fenetre : une decision
    dont l'horizon depasserait `hi` n'est pas fabriquee. C'est ce qui empeche
    une decision d'apprentissage de lire un prix de test.

    `max_capital_usd` borne la MARGE d'une decision. Sans cette borne, le
    carnet peut autoriser un notionnel dont la marge depasse le capital
    disponible, et le PnL serait ensuite rapporte a un capital que le compte
    n'a jamais eu. La taille est donc reduite jusqu'a ce que la marge tienne,
    et la decision est ecartee si meme la taille minimale ne tient pas.
    """
    out: List[Sample] = []
    rows = m.rows
    i = max(lo, builder.cfg.warmup - 1)
    while i + horizon_rows < hi:
        etat = builder.at(rows, i)
        if etat is None:
            i += horizon_rows
            continue
        r0, r1 = rows[i], rows[i + horizon_rows]
        px = (r0[1] + r0[2]) / 2.0
        # Taille bornee par le cote le plus mince du touch : la meme taille
        # doit pouvoir entrer ET sortir sans creuser le carnet.
        ct = min(r0[3], r0[4])
        if ct <= 0:
            i += horizon_rows
            continue
        taille = usd_notional(m.spec, ct, px)
        if taille > max_size_usd:
            taille = max_size_usd
            ct = taille / usd_notional(m.spec, 1.0, px)
        marge = m.schedule.initial_margin_usd(m.spec, ct, px)
        if marge is None or marge <= 0:
            i += horizon_rows
            continue
        if max_capital_usd is not None and marge > max_capital_usd:
            # La marge est proportionnelle au notionnel a l'interieur d'un
            # palier ; reduire le notionnel dans ce rapport ramene la marge
            # sous la borne, et le palier obtenu ne peut qu'etre plus doux.
            ct *= max_capital_usd / marge
            taille = usd_notional(m.spec, ct, px)
            marge = m.schedule.initial_margin_usd(m.spec, ct, px)
            if marge is None or marge <= 0 or marge > max_capital_usd * 1.000001:
                i += horizon_rows
                continue
        nets, fills = {}, {}
        ok = True
        for a in actions:
            if not a.is_trade:
                continue
            f = execute(a, r0, r1, m.fees)
            if f is None:
                ok = False
                break
            nets[a] = f.net_bps
            fills[a] = f
        if ok and nets:
            out.append(Sample(
                ts_ms=int(r0[0]), inst_id=m.inst_id, index=i, state=etat,
                nets=nets, size_usd=taille, contracts=ct, capital_usd=marge,
                holding_s=(r1[0] - r0[0]) / 1000.0, fills=fills))
        i += horizon_rows
    return out


def run_policy(samples: Sequence[Sample], av: ActionValue,
               actions: Sequence[Action], reserved_capital_usd: float
               ) -> Ledger:
    """Applique la politique et remplit le registre.

    Une decision NO_TRADE est enregistree elle aussi : sans elle on ne peut
    pas savoir combien de fois le systeme a choisi de ne rien faire, et le
    mandat exige que ce choix soit visible.
    """
    led = Ledger(reserved_capital_usd=reserved_capital_usd)
    for s in samples:
        a, predite = av.best(s.state, actions)
        if not a.is_trade:
            led.record(Decision(
                ts_ms=s.ts_ms, instrument=s.inst_id, state=s.state,
                action=a, target_position_usd=0.0, entry_px=None,
                exit_px=None, size_usd=0.0, holding_s=0.0, gross_bps=0.0,
                fee_bps=0.0, spread_bps=0.0, slippage_bps=0.0,
                adverse_selection_bps=None, net_bps=0.0, capital_usd=0.0,
                status=EXECUTABLE, predicted_bps=0.0))
            continue
        f = s.fills[a]
        # Selection adverse : pertinente seulement quand l'entree est
        # passive. Pour une traversee, le mouvement du prix EST deja le
        # resultat : il n'y a pas de terme separe a soustraire, et en
        # inventer un compterait le meme cout deux fois.
        adv = None
        if a.needs_passive_fill:
            derive = (f.mid_out - f.mid_in) / f.mid_in * 10_000.0
            adv = -derive if a.is_long else derive
        led.record(Decision(
            ts_ms=s.ts_ms, instrument=s.inst_id, state=s.state, action=a,
            target_position_usd=(s.size_usd if a.is_long else -s.size_usd),
            entry_px=f.entry_px, exit_px=f.exit_px, size_usd=s.size_usd,
            holding_s=s.holding_s, gross_bps=f.gross_bps, fee_bps=f.fee_bps,
            spread_bps=f.spread_bps, slippage_bps=0.0,
            adverse_selection_bps=adv, net_bps=f.net_bps,
            capital_usd=s.capital_usd, status=f.status,
            predicted_bps=predite))
    return led


def split_index(n_rows: int, train_fraction: float) -> int:
    """Indice de coupure temporelle. L'apprentissage precede le test."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction doit etre dans ]0, 1[")
    return int(n_rows * train_fraction)
