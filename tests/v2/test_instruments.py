"""Registry, parsing et validation des instruments."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.v2.fixtures import BTC_INVERSE, BTC_LINEAR
from prism_v2.instruments import (
    InstrumentRegistry, InstrumentSpec, InstrumentType, InvalidInstrument,
    UnknownInstrument, parse_okx_instrument,
)

RAW_INVERSE = {"instType": "SWAP", "instId": "BTC-USD-SWAP", "ctType": "inverse",
               "ctVal": "100", "ctValCcy": "USD", "ctMult": "1", "uly": "BTC-USD",
               "settleCcy": "BTC", "tickSz": "0.1", "lotSz": "0.1", "minSz": "0.1",
               "state": "live", "lever": "100", "instFamily": "BTC-USD"}
RAW_LINEAR = {"instType": "SWAP", "instId": "BTC-USDT-SWAP", "ctType": "linear",
              "ctVal": "0.01", "ctValCcy": "BTC", "ctMult": "1", "uly": "BTC-USDT",
              "settleCcy": "USDT", "tickSz": "0.1", "lotSz": "0.01", "minSz": "0.01",
              "state": "live", "lever": "100", "instFamily": "BTC-USDT"}
RAW_SPOT = {"instType": "SPOT", "instId": "BTC-USDT", "baseCcy": "BTC",
            "quoteCcy": "USDT", "tickSz": "0.1", "lotSz": "0.00000001",
            "minSz": "0.00001", "state": "live"}


class TestParsing(unittest.TestCase):
    def test_inverse_parsed_with_all_critical_metadata(self):
        s = parse_okx_instrument(RAW_INVERSE)
        self.assertEqual(s.inst_type, InstrumentType.SWAP_INVERSE)
        self.assertEqual(s.ct_type, "inverse")
        self.assertEqual((s.ct_val, s.ct_mult, s.ct_val_ccy), (100.0, 1.0, "USD"))
        self.assertEqual(s.settle_ccy, "BTC")
        self.assertEqual((s.lot_size, s.min_size, s.tick_size), (0.1, 0.1, 0.1))
        self.assertEqual(s.lever, 100.0)
        self.assertTrue(s.is_inverse)

    def test_linear_parsed_as_distinct_type(self):
        s = parse_okx_instrument(RAW_LINEAR)
        self.assertEqual(s.inst_type, InstrumentType.SWAP_LINEAR)
        self.assertEqual(s.ct_val_ccy, "BTC")
        self.assertEqual(s.settle_ccy, "USDT")
        self.assertFalse(s.is_inverse)

    def test_unknown_ct_type_is_refused_not_guessed(self):
        self.assertIsNone(parse_okx_instrument({**RAW_INVERSE, "ctType": "quanto"}))
        self.assertIsNone(parse_okx_instrument({"instType": "OPTION", "instId": "X"}))
        self.assertIsNone(parse_okx_instrument({"instType": "SWAP", "instId": ""}))

    def test_spot_is_a_third_distinct_type(self):
        s = parse_okx_instrument(RAW_SPOT)
        self.assertEqual(s.inst_type, InstrumentType.SPOT)
        self.assertTrue(s.is_spot)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = InstrumentRegistry.from_okx_payload([RAW_INVERSE, RAW_LINEAR, RAW_SPOT])

    def test_three_instruments_are_distinct_entries(self):
        self.assertEqual(len(self.reg), 3)
        ids = {"BTC-USD-SWAP", "BTC-USDT-SWAP", "BTC-USDT"}
        self.assertEqual(set(self.reg.instruments), ids)

    def test_require_raises_on_unknown(self):
        with self.assertRaises(UnknownInstrument):
            self.reg.require("BTC-EUR-SWAP")

    def test_resolve_requires_explicit_type_no_implicit_fallback(self):
        self.assertEqual(self.reg.resolve("BTC", InstrumentType.SWAP_INVERSE).inst_id,
                         "BTC-USD-SWAP")
        self.assertEqual(self.reg.resolve("BTC", InstrumentType.SWAP_LINEAR).inst_id,
                         "BTC-USDT-SWAP")
        self.assertIsNone(self.reg.resolve("DOGE", InstrumentType.SWAP_INVERSE))

    def test_counterparts_exposes_gap_without_substituting(self):
        c = self.reg.counterparts("BTC")
        self.assertEqual(c["SWAP_INVERSE"], "BTC-USD-SWAP")
        self.assertEqual(c["SWAP_LINEAR"], "BTC-USDT-SWAP")
        self.assertNotEqual(c["SWAP_INVERSE"], c["SWAP_LINEAR"])

    def test_executable_universe_reports_exclusion_reasons(self):
        reg = InstrumentRegistry.from_okx_payload(
            [RAW_INVERSE, {**RAW_LINEAR, "instId": "DEAD-USDT-SWAP", "state": "suspend"}])
        kept, excluded = reg.executable_universe(InstrumentType.SWAP_LINEAR)
        self.assertEqual(kept, [])
        self.assertEqual(len(excluded), 1)
        self.assertIn("state", excluded[0]["reason"])

    def test_no_hardcoded_instrument_universe_anywhere(self):
        """Aucun module V2 ne definit d'univers d'instruments en dur.

        V33 portait INVERSE_AVAILABLE_SYMS = {11 symboles} figee au 30/06,
        jamais revalidee. L'univers V2 doit venir de l'API. On inspecte l'AST
        (pas le texte) pour qu'une mention en commentaire reste permise.
        """
        import ast
        src = Path(__file__).resolve().parents[2] / "prism_v2"
        offenders = []
        for py in sorted(src.rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
                    continue
                literals = [e.value for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                looks_like_universe = [s for s in literals
                                       if "-SWAP" in s or s.endswith("-USDT") or s.endswith("-USD")]
                if len(looks_like_universe) >= 2:
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    offenders.append(f"{py.name}:{node.lineno} {names} -> {looks_like_universe[:4]}")
        self.assertEqual(offenders, [],
                         "univers d'instruments code en dur detecte: " + "; ".join(offenders))

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.reg.save(Path(d) / "i.json")
            back = InstrumentRegistry.load(p)
            self.assertEqual(len(back), len(self.reg))
            a, b = back.require("BTC-USD-SWAP"), self.reg.require("BTC-USD-SWAP")
            self.assertEqual((a.ct_val, a.ct_mult, a.settle_ccy, a.inst_type),
                             (b.ct_val, b.ct_mult, b.settle_ccy, b.inst_type))


class TestValidation(unittest.TestCase):
    def test_valid_specs_pass(self):
        BTC_INVERSE.validate()
        BTC_LINEAR.validate()

    def test_each_missing_field_is_caught(self):
        for field, bad in (("ct_val", 0.0), ("ct_mult", 0.0), ("tick_size", 0.0),
                           ("lot_size", 0.0), ("min_size", 0.0), ("settle_ccy", "")):
            with self.subTest(field=field):
                spec = InstrumentSpec(**{**BTC_INVERSE.to_dict(),
                                         "inst_type": InstrumentType.SWAP_INVERSE,
                                         field: bad})
                with self.assertRaises(InvalidInstrument):
                    spec.validate()

    def test_inverse_with_linear_metadata_is_refused(self):
        spec = InstrumentSpec(**{**BTC_INVERSE.to_dict(),
                                 "inst_type": InstrumentType.SWAP_INVERSE,
                                 "ct_type": "linear"})
        with self.assertRaises(InvalidInstrument):
            spec.validate()

    def test_require_validates_by_default(self):
        reg = InstrumentRegistry.from_okx_payload([{**RAW_INVERSE, "state": "suspend"}])
        with self.assertRaises(InvalidInstrument):
            reg.require("BTC-USD-SWAP")
        self.assertIsNotNone(reg.require("BTC-USD-SWAP", validate=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
