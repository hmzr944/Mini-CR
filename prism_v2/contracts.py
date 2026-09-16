"""Mecanique financiere des contrats. INVERSE-AWARE par construction.

Toute fonction de ce module exige un InstrumentSpec complet. Passer un
simple symbole ("BTC") leve TypeError : c'est la garde qui rend impossible
de rejouer l'erreur V33.

FORMULES OFFICIELLES OKX (verifiees dans la documentation, cf AUDIT ci-dessous) :

  PnL, crypto-margine (INVERSE), en devise de reglement (le coin) :
      long  = ctVal * |sz| * ctMult * (1/prix_ouverture - 1/prix_mark)
      short = ctVal * |sz| * ctMult * (1/prix_mark - 1/prix_ouverture)

  PnL, USDT-margine (LINEAR), en USDT :
      long  = ctVal * |sz| * ctMult * (prix_mark - prix_ouverture)
      short = ctVal * |sz| * ctMult * (prix_ouverture - prix_mark)

  Frais, crypto-margine (INVERSE), en coin :
      ctVal * sz * ctMult / prix * taux
  Frais, USDT-margine (LINEAR), en USDT :
      ctVal * sz * ctMult * prix * taux

  Taux par defaut Lv1 : maker 0.02% (2 bps), taker 0.05% (5 bps).

PIEGE CENTRAL : pour un INVERSE, le PnL est lineaire en 1/prix, pas en prix.
Le rendement exprime dans le coin vaut (Px-Pe)/Px et NON (Px-Pe)/Pe. Sur un
mouvement de 1%, l'ecart est d'environ 1 bp — negligeable pour un swing,
determinant pour un edge de quelques bps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .core_types import Direction
from .instruments import InstrumentSpec, InstrumentType

#: Bareme public OKX niveau de base (Lv1), perpetuels. NON verifie sur le
#: compte : /api/v5/account/trade-fee exige une authentification absente de V2.
OKX_LV1_MAKER_RATE = 0.0002   # 2 bps
OKX_LV1_TAKER_RATE = 0.0005   # 5 bps


class NotAnInstrumentSpec(TypeError):
    """Une fonction financiere a recu autre chose qu'un InstrumentSpec.

    Typiquement `symbol="BTC"` ou `"BTC-USD-SWAP"`. Refus dur : c'est
    exactement la porte par laquelle V33 a confondu spot, lineaire et inverse.
    """


def _require_spec(spec: Any, fn: str) -> InstrumentSpec:
    if not isinstance(spec, InstrumentSpec):
        raise NotAnInstrumentSpec(
            f"{fn}() exige un InstrumentSpec complet, recu {type(spec).__name__} "
            f"({spec!r}). Un symbole ne suffit pas : la mecanique depend de "
            f"ct_type/ct_val/ct_mult/settle_ccy. Utiliser registry.require(inst_id)."
        )
    return spec


def _require_price(price: float, name: str = "price") -> float:
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        raise TypeError(f"{name} doit etre numerique, recu {type(price).__name__}")
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"{name} doit etre fini et > 0 (recu {price})")
    return float(price)


# ══════════════════════════════════════════════════════════════════════════
# NOTIONNEL ET QUANTITE
# ══════════════════════════════════════════════════════════════════════════
def usd_notional(spec: InstrumentSpec, contracts: float, price: float) -> float:
    """Exposition notionnelle en USD.

    INVERSE : ctVal est en USD -> notionnel = ctVal * sz * ctMult, INDEPENDANT
              du prix. C'est la propriete que V33 ignorait.
    LINEAR  : ctVal est en ccy de base -> notionnel = ctVal * sz * ctMult * prix.
    SPOT    : taille deja en ccy de base -> notionnel = sz * prix.
    """
    _require_spec(spec, "usd_notional")
    _require_price(price)
    if contracts < 0:
        raise ValueError(f"contracts doit etre >= 0 (recu {contracts})")
    if spec.inst_type is InstrumentType.SWAP_INVERSE:
        return spec.ct_val * contracts * spec.ct_mult
    if spec.inst_type is InstrumentType.SWAP_LINEAR:
        return spec.ct_val * contracts * spec.ct_mult * price
    return contracts * price


def coin_notional(spec: InstrumentSpec, contracts: float, price: float) -> float:
    """Exposition en devise de base (le coin). Pour un INVERSE c'est la
    grandeur qui porte reellement le risque de marge."""
    _require_spec(spec, "coin_notional")
    _require_price(price)
    if spec.inst_type is InstrumentType.SWAP_INVERSE:
        return spec.ct_val * contracts * spec.ct_mult / price
    if spec.inst_type is InstrumentType.SWAP_LINEAR:
        return spec.ct_val * contracts * spec.ct_mult
    return contracts


def contracts_for_usd_notional(spec: InstrumentSpec, target_usd: float,
                               price: float) -> float:
    """Nombre de contrats (non quantifie) pour viser un notionnel USD."""
    _require_spec(spec, "contracts_for_usd_notional")
    _require_price(price)
    if target_usd <= 0:
        raise ValueError(f"target_usd doit etre > 0 (recu {target_usd})")
    if spec.inst_type is InstrumentType.SWAP_INVERSE:
        return target_usd / (spec.ct_val * spec.ct_mult)
    if spec.inst_type is InstrumentType.SWAP_LINEAR:
        return target_usd / (spec.ct_val * spec.ct_mult * price)
    return target_usd / price


# ══════════════════════════════════════════════════════════════════════════
# CONTRAINTES D'ORDRE (lotSz / minSz / tickSz)
# ══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class QuantizedOrder:
    contracts: float
    usd_notional: float
    residual_usd: float          # notionnel demande non atteint par la quantification
    is_executable: bool
    reason: str = ""


def quantize_contracts(spec: InstrumentSpec, contracts: float) -> float:
    """Arrondit VERS LE BAS au pas de lot. Jamais vers le haut : on ne prend
    pas plus de risque que demande a cause d'un arrondi."""
    _require_spec(spec, "quantize_contracts")
    if spec.lot_size <= 0:
        raise ValueError(f"{spec.inst_id}: lot_size invalide")
    steps = math.floor(contracts / spec.lot_size + 1e-9)
    q = steps * spec.lot_size
    # Reconstruit la precision decimale du pas pour eviter les residus binaires.
    decimals = max(0, -math.floor(math.log10(spec.lot_size))) if spec.lot_size < 1 else 0
    return round(q, decimals + 2)


def quantize_price(spec: InstrumentSpec, price: float, direction: Optional[Direction] = None) -> float:
    """Arrondit le prix au pas de cotation. Conservateur si direction fournie :
    un acheteur arrondit vers le haut (paie plus), un vendeur vers le bas."""
    _require_spec(spec, "quantize_price")
    _require_price(price)
    if spec.tick_size <= 0:
        raise ValueError(f"{spec.inst_id}: tick_size invalide")
    n = price / spec.tick_size
    if direction is Direction.LONG:
        steps = math.ceil(n - 1e-9)
    elif direction is Direction.SHORT:
        steps = math.floor(n + 1e-9)
    else:
        steps = round(n)
    decimals = max(0, -math.floor(math.log10(spec.tick_size))) if spec.tick_size < 1 else 0
    return round(steps * spec.tick_size, decimals + 2)


def build_order(spec: InstrumentSpec, target_usd: float, price: float) -> QuantizedOrder:
    """Traduit un notionnel USD vise en ordre reellement soumettable.

    Verifie minSz et lotSz. Un ordre sous la taille minimale est REFUSE
    (is_executable=False) — il n'est pas arrondi vers le haut en silence.
    """
    _require_spec(spec, "build_order")
    _require_price(price)
    raw = contracts_for_usd_notional(spec, target_usd, price)
    q = quantize_contracts(spec, raw)
    if q < spec.min_size - 1e-12:
        min_usd = usd_notional(spec, spec.min_size, price)
        return QuantizedOrder(
            contracts=0.0, usd_notional=0.0, residual_usd=target_usd,
            is_executable=False,
            reason=(f"{q:g} contrats < min_size {spec.min_size:g} "
                    f"(notionnel minimum ~${min_usd:,.2f} a {price:g})"))
    got = usd_notional(spec, q, price)
    return QuantizedOrder(contracts=q, usd_notional=got,
                          residual_usd=max(0.0, target_usd - got),
                          is_executable=True)


# ══════════════════════════════════════════════════════════════════════════
# PnL
# ══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class PnLResult:
    pnl_settle_ccy: float        # grandeur faisant foi (formule OKX)
    settle_ccy: str
    pnl_usd_at_exit: float       # converti au prix de sortie
    return_bps_settle: float     # rendement dans la devise de reglement
    return_bps_usd: float        # rendement sur le notionnel USD d'entree
    entry_price: float
    exit_price: float
    contracts: float
    direction: str

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def pnl(spec: InstrumentSpec, direction: Direction, contracts: float,
        entry_price: float, exit_price: float) -> PnLResult:
    """PnL d'une position, selon la formule officielle du type de contrat.

    Le resultat faisant foi est `pnl_settle_ccy` (BTC pour BTC-USD-SWAP,
    USDT pour BTC-USDT-SWAP). Les conversions USD sont derivees, explicites,
    et datees par le prix utilise.
    """
    _require_spec(spec, "pnl")
    _require_price(entry_price, "entry_price")
    _require_price(exit_price, "exit_price")
    if not isinstance(direction, Direction):
        raise TypeError(f"direction doit etre Direction, recu {type(direction).__name__}")
    if contracts <= 0:
        raise ValueError(f"contracts doit etre > 0 (recu {contracts})")

    face = spec.ct_val * contracts * spec.ct_mult
    if spec.inst_type is InstrumentType.SWAP_INVERSE:
        # PnL en coin, lineaire en 1/prix.
        if direction is Direction.LONG:
            p_settle = face * (1.0 / entry_price - 1.0 / exit_price)
        else:
            p_settle = face * (1.0 / exit_price - 1.0 / entry_price)
        notion_settle = face / entry_price              # exposition coin a l'entree
        notion_usd = face                                # independant du prix
    elif spec.inst_type is InstrumentType.SWAP_LINEAR:
        if direction is Direction.LONG:
            p_settle = face * (exit_price - entry_price)
        else:
            p_settle = face * (entry_price - exit_price)
        notion_settle = face * entry_price
        notion_usd = notion_settle
    else:  # SPOT
        if direction is Direction.LONG:
            p_settle = contracts * (exit_price - entry_price)
        else:
            p_settle = contracts * (entry_price - exit_price)
        notion_settle = contracts * entry_price
        notion_usd = notion_settle

    pnl_usd = p_settle * exit_price if spec.inst_type is InstrumentType.SWAP_INVERSE else p_settle
    return PnLResult(
        pnl_settle_ccy=p_settle,
        settle_ccy=spec.settle_ccy,
        pnl_usd_at_exit=pnl_usd,
        return_bps_settle=p_settle / notion_settle * 10_000.0,
        return_bps_usd=pnl_usd / notion_usd * 10_000.0,
        entry_price=entry_price, exit_price=exit_price,
        contracts=contracts, direction=direction.value,
    )


# ══════════════════════════════════════════════════════════════════════════
# FRAIS
# ══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class FeeResult:
    fee_settle_ccy: float
    settle_ccy: str
    fee_usd: float
    fee_bps_of_usd_notional: float
    rate: float
    price: float


def fee(spec: InstrumentSpec, contracts: float, price: float, rate: float) -> FeeResult:
    """Frais d'une jambe, selon la formule officielle du type de contrat.

    INVERSE : ctVal * sz * ctMult / prix * taux   -> en coin
    LINEAR  : ctVal * sz * ctMult * prix * taux   -> en USDT

    Propriete utile : en bps du notionnel USD, les deux valent `rate`. Mais la
    DEVISE differe, et pour un inverse il faut detenir le coin pour payer.
    """
    _require_spec(spec, "fee")
    _require_price(price)
    if contracts < 0:
        raise ValueError(f"contracts doit etre >= 0 (recu {contracts})")
    if rate < 0:
        raise ValueError(f"rate doit etre >= 0 (recu {rate})")

    face = spec.ct_val * contracts * spec.ct_mult
    if spec.inst_type is InstrumentType.SWAP_INVERSE:
        f_settle = face / price * rate
        f_usd = f_settle * price
    elif spec.inst_type is InstrumentType.SWAP_LINEAR:
        f_settle = face * price * rate
        f_usd = f_settle
    else:
        f_settle = contracts * price * rate
        f_usd = f_settle

    notion = usd_notional(spec, contracts, price)
    return FeeResult(fee_settle_ccy=f_settle, settle_ccy=spec.settle_ccy,
                     fee_usd=f_usd,
                     fee_bps_of_usd_notional=(f_usd / notion * 10_000.0) if notion else 0.0,
                     rate=rate, price=price)


def round_trip_fee_bps(spec: InstrumentSpec, contracts: float,
                       entry_price: float, exit_price: float,
                       entry_rate: float = OKX_LV1_TAKER_RATE,
                       exit_rate: float = OKX_LV1_TAKER_RATE) -> float:
    """Frais d'aller-retour en bps du notionnel USD d'ENTREE.

    Les deux jambes sont calculees a leur prix propre : pour un inverse, la
    jambe de sortie n'a pas le meme cout en coin que celle d'entree.
    """
    _require_spec(spec, "round_trip_fee_bps")
    f_in = fee(spec, contracts, entry_price, entry_rate)
    f_out = fee(spec, contracts, exit_price, exit_rate)
    notion_in = usd_notional(spec, contracts, entry_price)
    if notion_in <= 0:
        raise ValueError("notionnel d'entree nul")
    return (f_in.fee_usd + f_out.fee_usd) / notion_in * 10_000.0
