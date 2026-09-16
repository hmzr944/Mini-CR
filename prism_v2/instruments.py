"""InstrumentSpec + Registry — source unique de verite sur ce qui est tradable.

Raison d'etre : dans V33, les signaux etaient calcules sur des bougies SPOT
(instId "BTC-USDT") tandis que l'execution visait un perpetuel, et sous
OKX_CONTRACT_TYPE=inverse un perpetuel INVERSE ("BTC-USD-SWAP") — trois
instruments differents confondus par un simple suffixe de chaine.

V2 rend cette confusion structurellement impossible :
  - un instrument est un InstrumentSpec, jamais une chaine ;
  - il porte son type (SPOT / SWAP_LINEAR / SWAP_INVERSE) explicitement ;
  - il porte ct_val, ct_mult, ct_val_ccy, settle_ccy, lot/min/tick, lever ;
  - toute fonction financiere exige un InstrumentSpec (cf contracts.py) ;
  - un spec incomplet, incoherent ou non-live est REFUSE, pas devine.

SPOT, -USDT-SWAP et -USD-SWAP sont trois instruments distincts. Aucune
conversion automatique de l'un vers l'autre n'existe dans ce module.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .core_types import Provenance, utc_now_iso


class InstrumentType(str, enum.Enum):
    SPOT = "SPOT"
    SWAP_LINEAR = "SWAP_LINEAR"
    SWAP_INVERSE = "SWAP_INVERSE"

    @property
    def is_swap(self) -> bool:
        return self in (InstrumentType.SWAP_LINEAR, InstrumentType.SWAP_INVERSE)


class UnknownInstrument(KeyError):
    """Un module reclame un instrument absent du Registry.

    Erreur dure volontaire : mieux vaut un crash qu'un calcul silencieux sur
    le mauvais produit.
    """


class InvalidInstrument(ValueError):
    """Metadonnees absentes, incoherentes, ou state != live."""


@dataclass(frozen=True)
class InstrumentSpec:
    """Specification complete d'un instrument. Immuable.

    Tous les champs necessaires au calcul exact du notionnel, de la quantite,
    du PnL et des frais sont presents. Aucune fonction financiere n'a le droit
    de travailler avec moins que cet objet.
    """

    inst_id: str
    exchange: str
    inst_type: InstrumentType
    ct_type: str                # "inverse" | "linear" | "" (spot) — verbatim OKX
    base: str
    quote: str
    settle_ccy: str             # devise de reglement du PnL et des frais
    ct_val: float               # valeur faciale d'un contrat
    ct_val_ccy: str             # devise de ct_val ("USD" si inverse)
    ct_mult: float              # multiplicateur de contrat
    tick_size: float
    lot_size: float
    min_size: float
    state: str
    fetched_at: str
    lever: float = 0.0          # levier max affiche par l'exchange
    family: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ---- typage --------------------------------------------------------------
    @property
    def is_inverse(self) -> bool:
        return self.inst_type is InstrumentType.SWAP_INVERSE

    @property
    def is_linear(self) -> bool:
        return self.inst_type is InstrumentType.SWAP_LINEAR

    @property
    def is_spot(self) -> bool:
        return self.inst_type is InstrumentType.SPOT

    @property
    def is_live(self) -> bool:
        return self.state == "live"

    # ---- validation ----------------------------------------------------------
    def validate(self, require_live: bool = True) -> "InstrumentSpec":
        """Refuse proprement un spec inutilisable. Retourne self si valide."""
        errs: List[str] = []
        if not self.inst_id:
            errs.append("inst_id vide")
        if self.ct_val <= 0:
            errs.append(f"ct_val invalide ({self.ct_val})")
        if self.ct_mult <= 0:
            errs.append(f"ct_mult invalide ({self.ct_mult})")
        if self.tick_size <= 0:
            errs.append(f"tick_size invalide ({self.tick_size})")
        if self.lot_size <= 0:
            errs.append(f"lot_size invalide ({self.lot_size})")
        if self.min_size <= 0:
            errs.append(f"min_size invalide ({self.min_size})")
        if not self.settle_ccy:
            errs.append("settle_ccy vide")
        if not self.ct_val_ccy:
            errs.append("ct_val_ccy vide")
        # Coherence type <-> mecanique
        if self.is_inverse:
            if self.ct_val_ccy != "USD":
                errs.append(f"inverse mais ct_val_ccy={self.ct_val_ccy!r} (attendu USD)")
            if self.settle_ccy == self.quote:
                errs.append(f"inverse mais settle_ccy={self.settle_ccy!r} == quote")
            if self.ct_type != "inverse":
                errs.append(f"inst_type INVERSE mais ct_type={self.ct_type!r}")
        if self.is_linear:
            if self.ct_val_ccy != self.base:
                errs.append(f"linear mais ct_val_ccy={self.ct_val_ccy!r} (attendu {self.base})")
            if self.ct_type != "linear":
                errs.append(f"inst_type LINEAR mais ct_type={self.ct_type!r}")
        if require_live and not self.is_live:
            errs.append(f"state={self.state!r} != 'live'")
        if errs:
            raise InvalidInstrument(f"{self.inst_id or '<sans id>'}: " + "; ".join(errs))
        return self

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "InstrumentSpec":
        """Reconstruit un spec depuis sa forme serialisee, et le VALIDE.

        Sert au replay : rejouer une collecte exige EXACTEMENT le spec sous
        lequel elle a ete faite. Reconstruire un spec incoherent, ou tolerer
        un champ manquant en lui donnant une valeur par defaut, ferait rejouer
        un inverse avec la mecanique d'un lineaire — precisement ce que le
        contrat d'instrument interdit. On leve plutot que de deviner.
        """
        missing = [k for k in ("inst_id", "inst_type", "ct_val", "ct_val_ccy",
                               "ct_mult", "settle_ccy", "tick_size", "lot_size",
                               "min_size", "state")
                   if k not in d]
        if missing:
            raise InvalidInstrument(
                f"{d.get('inst_id', '?')}: champs absents {missing} — "
                "un spec incomplet ne peut pas etre reconstruit")
        spec = cls(
            inst_id=d["inst_id"], exchange=d.get("exchange", "OKX"),
            inst_type=InstrumentType(d["inst_type"]), ct_type=d.get("ct_type", ""),
            base=d.get("base", ""), quote=d.get("quote", ""),
            settle_ccy=d["settle_ccy"], ct_val=float(d["ct_val"]),
            ct_val_ccy=d["ct_val_ccy"], ct_mult=float(d["ct_mult"]),
            tick_size=float(d["tick_size"]), lot_size=float(d["lot_size"]),
            min_size=float(d["min_size"]), state=d["state"],
            lever=float(d.get("lever", 0.0)), family=d.get("family", ""),
            fetched_at=d.get("fetched_at", ""))
        spec.validate()
        return spec

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        d = {
            "inst_id": self.inst_id, "exchange": self.exchange,
            "inst_type": self.inst_type.value, "ct_type": self.ct_type,
            "base": self.base, "quote": self.quote, "settle_ccy": self.settle_ccy,
            "ct_val": self.ct_val, "ct_val_ccy": self.ct_val_ccy, "ct_mult": self.ct_mult,
            "tick_size": self.tick_size, "lot_size": self.lot_size,
            "min_size": self.min_size, "state": self.state, "lever": self.lever,
            "family": self.family, "fetched_at": self.fetched_at,
        }
        if include_raw:
            d["raw"] = self.raw
        return d


#: Alias historique. `Instrument` et `InstrumentSpec` sont le meme objet.
Instrument = InstrumentSpec


def _f(raw: Dict[str, Any], key: str, default: float = 0.0) -> float:
    v = raw.get(key, "")
    try:
        return float(v) if v not in ("", None) else default
    except (TypeError, ValueError):
        return default


def parse_okx_instrument(raw: Dict[str, Any],
                         fetched_at: Optional[str] = None) -> Optional[InstrumentSpec]:
    """Traduit une ligne OKX /public/instruments en InstrumentSpec.

    Retourne None pour les types non supportes (FUTURES, OPTION, ctType
    inconnu) plutot que de deviner : un instrument mal type est pire qu'un
    instrument absent.
    """
    inst_type_raw = raw.get("instType", "")
    inst_id = raw.get("instId", "")
    if not inst_id:
        return None

    ct_type = raw.get("ctType", "") or ""
    if inst_type_raw == "SPOT":
        itype = InstrumentType.SPOT
        base, quote = raw.get("baseCcy", ""), raw.get("quoteCcy", "")
        settle = quote
        ct_val, ct_val_ccy = 1.0, base
    elif inst_type_raw == "SWAP":
        if ct_type == "linear":
            itype = InstrumentType.SWAP_LINEAR
        elif ct_type == "inverse":
            itype = InstrumentType.SWAP_INVERSE
        else:
            return None
        uly = raw.get("uly", "") or raw.get("instFamily", "")
        parts = uly.split("-")
        base = parts[0] if parts else ""
        quote = parts[1] if len(parts) > 1 else ""
        settle = raw.get("settleCcy", "")
        ct_val = _f(raw, "ctVal", 0.0)
        ct_val_ccy = raw.get("ctValCcy", "")
    else:
        return None

    return InstrumentSpec(
        inst_id=inst_id, exchange="OKX", inst_type=itype, ct_type=ct_type,
        base=base, quote=quote, settle_ccy=settle,
        ct_val=ct_val, ct_val_ccy=ct_val_ccy, ct_mult=_f(raw, "ctMult", 1.0),
        tick_size=_f(raw, "tickSz"), lot_size=_f(raw, "lotSz"),
        min_size=_f(raw, "minSz"), state=raw.get("state", ""),
        lever=_f(raw, "lever"), family=raw.get("instFamily", ""),
        fetched_at=fetched_at or utc_now_iso(), raw=dict(raw),
    )


@dataclass
class InstrumentRegistry:
    """Catalogue des instruments reels, decouvert depuis l'API.

    V33 portait une whitelist INVERSE_AVAILABLE_SYMS ecrite en dur le
    30/06/2026, jamais revalidee, contenant un contrat inexistant et en
    omettant cinq reels. Ici la verite vient de l'exchange, avec sa date.
    Aucune whitelist heritee n'est copiee.
    """

    instruments: Dict[str, InstrumentSpec] = field(default_factory=dict)
    provenance: Optional[Provenance] = None
    rejected: List[Dict[str, str]] = field(default_factory=list)

    # ---- construction --------------------------------------------------------
    @classmethod
    def from_okx_payload(cls, payloads: Iterable[Dict[str, Any]],
                         provenance: Optional[Provenance] = None) -> "InstrumentRegistry":
        fetched = provenance.fetched_at if provenance else utc_now_iso()
        reg = cls(provenance=provenance)
        for raw in payloads:
            spec = parse_okx_instrument(raw, fetched_at=fetched)
            if spec is None:
                reg.rejected.append({"inst_id": raw.get("instId", "?"),
                                     "reason": f"type non supporte "
                                               f"(instType={raw.get('instType')!r}, "
                                               f"ctType={raw.get('ctType')!r})"})
                continue
            reg.instruments[spec.inst_id] = spec
        return reg

    def merge(self, other: "InstrumentRegistry") -> "InstrumentRegistry":
        self.instruments.update(other.instruments)
        self.rejected.extend(other.rejected)
        return self

    # ---- acces ---------------------------------------------------------------
    def get(self, inst_id: str) -> Optional[InstrumentSpec]:
        return self.instruments.get(inst_id)

    def require(self, inst_id: str, validate: bool = True) -> InstrumentSpec:
        """Acces strict : leve si absent, et par defaut si invalide/non-live."""
        spec = self.instruments.get(inst_id)
        if spec is None:
            raise UnknownInstrument(
                f"{inst_id} absent du Registry ({len(self.instruments)} instruments connus). "
                "Aucun module V2 n'a le droit de supposer son existence.")
        return spec.validate() if validate else spec

    def by_type(self, inst_type: InstrumentType, tradable_only: bool = True) -> List[InstrumentSpec]:
        out = [i for i in self.instruments.values() if i.inst_type is inst_type]
        if tradable_only:
            out = [i for i in out if i.is_live]
        return sorted(out, key=lambda i: i.inst_id)

    def executable_universe(self, inst_type: InstrumentType) -> tuple[List[InstrumentSpec],
                                                                     List[Dict[str, str]]]:
        """Univers reellement executable + motifs d'exclusion explicites.

        Le filtrage est EXPLICITE : chaque exclusion porte sa raison, aucune
        n'est silencieuse.
        """
        kept: List[InstrumentSpec] = []
        excluded: List[Dict[str, str]] = []
        for spec in sorted((i for i in self.instruments.values() if i.inst_type is inst_type),
                           key=lambda i: i.inst_id):
            try:
                kept.append(spec.validate())
            except InvalidInstrument as exc:
                excluded.append({"inst_id": spec.inst_id, "reason": str(exc)})
        return kept, excluded

    def resolve(self, base: str, inst_type: InstrumentType) -> Optional[InstrumentSpec]:
        """Trouve l'instrument d'un sous-jacent pour un type DONNE.

        Le type est obligatoire : il n'existe aucune resolution 'par defaut',
        donc aucun repli implicite vers le spot ou vers -USDT-SWAP.
        """
        for spec in self.by_type(inst_type):
            if spec.base == base:
                return spec
        return None

    def counterparts(self, base: str) -> Dict[str, Optional[str]]:
        """Les trois faces d'un meme sous-jacent, pour rendre l'ecart visible.

        Fonction de DIAGNOSTIC uniquement. Elle n'autorise aucune substitution.
        """
        return {t.value: (s.inst_id if (s := self.resolve(base, t)) else None)
                for t in InstrumentType}

    # ---- persistance ---------------------------------------------------------
    def save(self, path: Path, include_raw: bool = False) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "count": len(self.instruments),
            "instruments": [i.to_dict(include_raw) for i in
                            sorted(self.instruments.values(), key=lambda x: x.inst_id)],
        }
        path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "InstrumentRegistry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        reg = cls()
        for d in data.get("instruments", []):
            d = dict(d)
            d["inst_type"] = InstrumentType(d["inst_type"])
            d.pop("raw", None)
            reg.instruments[d["inst_id"]] = InstrumentSpec(**d)
        if (p := data.get("provenance")):
            reg.provenance = Provenance(**p)
        return reg

    def __len__(self) -> int:
        return len(self.instruments)
