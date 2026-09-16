"""Tests de reference de la mecanique contractuelle OKX.

Chaque cas est ecrit sous la forme inputs -> formule -> attendu -> obtenu,
avec des valeurs numeriques explicites. Les deux exemples chiffres de frais
proviennent VERBATIM de la documentation OKX
("How are futures trading fees calculated on OKX?").

Executer :  python3 -m unittest tests.v2.test_contracts_reference -v
Table :     python3 tests/v2/test_contracts_reference.py --table
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism_v2.contracts import (  # noqa: E402
    NotAnInstrumentSpec, OKX_LV1_MAKER_RATE, OKX_LV1_TAKER_RATE, build_order,
    coin_notional, contracts_for_usd_notional, fee, pnl, quantize_contracts,
    quantize_price, round_trip_fee_bps, usd_notional,
)
from prism_v2.core_types import Direction  # noqa: E402
from prism_v2.instruments import (  # noqa: E402
    InstrumentSpec, InstrumentType, InvalidInstrument,
)

FETCHED = "2026-09-16T00:00:00Z"

#: BTC-USD-SWAP — metadonnees REELLES lues sur /api/v5/public/instruments.
BTC_INVERSE = InstrumentSpec(
    inst_id="BTC-USD-SWAP", exchange="OKX", inst_type=InstrumentType.SWAP_INVERSE,
    ct_type="inverse", base="BTC", quote="USD", settle_ccy="BTC",
    ct_val=100.0, ct_val_ccy="USD", ct_mult=1.0,
    tick_size=0.1, lot_size=0.1, min_size=0.1, state="live", lever=100.0,
    family="BTC-USD", fetched_at=FETCHED,
)

#: BTC-USDT-SWAP — metadonnees REELLES. Instrument DIFFERENT, jamais substitue.
BTC_LINEAR = InstrumentSpec(
    inst_id="BTC-USDT-SWAP", exchange="OKX", inst_type=InstrumentType.SWAP_LINEAR,
    ct_type="linear", base="BTC", quote="USDT", settle_ccy="USDT",
    ct_val=0.01, ct_val_ccy="BTC", ct_mult=1.0,
    tick_size=0.1, lot_size=0.01, min_size=0.01, state="live", lever=100.0,
    family="BTC-USDT", fetched_at=FETCHED,
)

#: ADA-USD-SWAP — inverse a ctVal=10, lot entier. Verifie que BTC n'est pas
#: un cas particulier hardcode.
ADA_INVERSE = InstrumentSpec(
    inst_id="ADA-USD-SWAP", exchange="OKX", inst_type=InstrumentType.SWAP_INVERSE,
    ct_type="inverse", base="ADA", quote="USD", settle_ccy="ADA",
    ct_val=10.0, ct_val_ccy="USD", ct_mult=1.0,
    tick_size=0.0001, lot_size=1.0, min_size=1.0, state="live", lever=20.0,
    family="ADA-USD", fetched_at=FETCHED,
)


class TestNotional(unittest.TestCase):
    def test_inverse_notional_is_price_independent(self):
        """ctVal=100 USD : notionnel = ctVal*sz*ctMult, SANS le prix.
        inputs: sz=100, ctVal=100, ctMult=1
        formule: 100*100*1 = 10 000 USD, identique a tout prix
        """
        self.assertAlmostEqual(usd_notional(BTC_INVERSE, 100, 10_000.0), 10_000.0, places=9)
        self.assertAlmostEqual(usd_notional(BTC_INVERSE, 100, 90_000.0), 10_000.0, places=9)
        self.assertAlmostEqual(usd_notional(BTC_INVERSE, 100, 1.0), 10_000.0, places=9)

    def test_linear_notional_scales_with_price(self):
        """ctVal=0.01 BTC : notionnel = ctVal*sz*ctMult*prix.
        inputs: sz=100, ctVal=0.01, prix=10 000 -> 10 000 USD
                sz=100, ctVal=0.01, prix=90 000 -> 90 000 USD
        """
        self.assertAlmostEqual(usd_notional(BTC_LINEAR, 100, 10_000.0), 10_000.0, places=9)
        self.assertAlmostEqual(usd_notional(BTC_LINEAR, 100, 90_000.0), 90_000.0, places=9)

    def test_inverse_coin_notional(self):
        """Exposition coin = ctVal*sz*ctMult/prix = 10 000/50 000 = 0.2 BTC."""
        self.assertAlmostEqual(coin_notional(BTC_INVERSE, 100, 50_000.0), 0.2, places=12)

    def test_contracts_for_target_notional_roundtrip(self):
        for spec, price in ((BTC_INVERSE, 75_000.0), (BTC_LINEAR, 75_000.0),
                            (ADA_INVERSE, 0.42)):
            n = contracts_for_usd_notional(spec, 10_000.0, price)
            self.assertAlmostEqual(usd_notional(spec, n, price), 10_000.0, places=6,
                                   msg=f"aller-retour notionnel casse pour {spec.inst_id}")


class TestFeesAgainstOKXDocExamples(unittest.TestCase):
    """Les deux exemples chiffres publies par OKX, reproduits a l'identique."""

    def test_inverse_fee_okx_example(self):
        """OKX : "Contract value x Number of contracts / Opening price x Fee rate"
        inputs : 100 contrats BTC/USD, ctVal=100 USD, prix=10 000, taux=0.0005
        formule: 100 * 100 / 10 000 * 0.0005
        attendu: 0.0005 BTC
        """
        r = fee(BTC_INVERSE, contracts=100, price=10_000.0, rate=0.0005)
        self.assertAlmostEqual(r.fee_settle_ccy, 0.0005, places=12)
        self.assertEqual(r.settle_ccy, "BTC")
        self.assertAlmostEqual(r.fee_usd, 5.0, places=9)          # 0.0005 BTC * 10 000
        self.assertAlmostEqual(r.fee_bps_of_usd_notional, 5.0, places=9)

    def test_linear_fee_okx_example(self):
        """OKX : "Contract value x Number of contracts x Opening price x Fee rate"
        inputs : 100 contrats BTC/USDT, ctVal=0.01 BTC, prix=10 000, taux=0.0005
        formule: 0.01 * 100 * 10 000 * 0.0005
        attendu: 5 USDT
        """
        r = fee(BTC_LINEAR, contracts=100, price=10_000.0, rate=0.0005)
        self.assertAlmostEqual(r.fee_settle_ccy, 5.0, places=9)
        self.assertEqual(r.settle_ccy, "USDT")
        self.assertAlmostEqual(r.fee_bps_of_usd_notional, 5.0, places=9)

    def test_fee_currency_differs_even_when_bps_match(self):
        """Piege : en bps du notionnel USD les deux valent 5 bps, mais la
        DEVISE differe. Pour un inverse il faut detenir du BTC pour payer."""
        i = fee(BTC_INVERSE, 100, 10_000.0, OKX_LV1_TAKER_RATE)
        l = fee(BTC_LINEAR, 100, 10_000.0, OKX_LV1_TAKER_RATE)
        self.assertAlmostEqual(i.fee_bps_of_usd_notional, l.fee_bps_of_usd_notional, places=9)
        self.assertNotEqual(i.settle_ccy, l.settle_ccy)
        self.assertNotAlmostEqual(i.fee_settle_ccy, l.fee_settle_ccy, places=6)

    def test_round_trip_fee_bps_lv1_taker(self):
        """Aller-retour taker Lv1 a prix constant : 5 + 5 = 10 bps."""
        rt = round_trip_fee_bps(BTC_INVERSE, 100, 50_000.0, 50_000.0)
        self.assertAlmostEqual(rt, 10.0, places=9)

    def test_lv1_rates_are_documented_values(self):
        self.assertAlmostEqual(OKX_LV1_MAKER_RATE, 0.0002, places=12)
        self.assertAlmostEqual(OKX_LV1_TAKER_RATE, 0.0005, places=12)


class TestPnLInverse(unittest.TestCase):
    def test_inverse_long_doubling(self):
        """formule OKX long inverse : ctVal*|sz|*ctMult*(1/Pe - 1/Px)
        inputs : sz=100, ctVal=100, Pe=10 000, Px=20 000
        calcul  : 10 000 * (1/10 000 - 1/20 000) = 10 000 * 5e-5
        attendu : 0.5 BTC ; en USD au prix de sortie : 10 000 USD ; +100% du notionnel
        """
        r = pnl(BTC_INVERSE, Direction.LONG, 100, 10_000.0, 20_000.0)
        self.assertAlmostEqual(r.pnl_settle_ccy, 0.5, places=12)
        self.assertEqual(r.settle_ccy, "BTC")
        self.assertAlmostEqual(r.pnl_usd_at_exit, 10_000.0, places=6)
        self.assertAlmostEqual(r.return_bps_usd, 10_000.0, places=6)     # +100%
        self.assertAlmostEqual(r.return_bps_settle, 5_000.0, places=6)   # +50% en coin

    def test_inverse_short_halving(self):
        """formule OKX short inverse : ctVal*|sz|*ctMult*(1/Px - 1/Pe)
        inputs : sz=100, Pe=20 000, Px=10 000
        calcul  : 10 000 * (1/10 000 - 1/20 000) = 0.5 BTC
        attendu : +0.5 BTC ; +50% du notionnel USD ; +100% en coin
        """
        r = pnl(BTC_INVERSE, Direction.SHORT, 100, 20_000.0, 10_000.0)
        self.assertAlmostEqual(r.pnl_settle_ccy, 0.5, places=12)
        self.assertAlmostEqual(r.return_bps_usd, 5_000.0, places=6)
        self.assertAlmostEqual(r.return_bps_settle, 10_000.0, places=6)

    def test_inverse_long_loses_when_price_falls(self):
        r = pnl(BTC_INVERSE, Direction.LONG, 100, 20_000.0, 10_000.0)
        self.assertLess(r.pnl_settle_ccy, 0)
        self.assertAlmostEqual(r.pnl_settle_ccy, -0.5, places=12)

    def test_linear_pnl_is_linear_in_price(self):
        """formule OKX long lineaire : ctVal*|sz|*ctMult*(Px - Pe)
        inputs : sz=100, ctVal=0.01, Pe=10 000, Px=20 000
        calcul  : 0.01*100*(20 000-10 000) = 10 000 USDT
        """
        r = pnl(BTC_LINEAR, Direction.LONG, 100, 10_000.0, 20_000.0)
        self.assertAlmostEqual(r.pnl_settle_ccy, 10_000.0, places=6)
        self.assertEqual(r.settle_ccy, "USDT")

    def test_inverse_pnl_is_linear_in_reciprocal_price(self):
        """Propriete structurelle : le PnL inverse est affine en 1/Px.
        Deux ecarts egaux en 1/Px donnent deux PnL egaux, alors que les
        ecarts en prix, eux, sont tres differents."""
        f = lambda px: pnl(BTC_INVERSE, Direction.LONG, 100, 50_000.0, px).pnl_settle_ccy
        inv = lambda x: 1.0 / x
        px1, px2 = 50_000.0, inv(inv(50_000.0) - 2e-6)   # -2e-6 en 1/prix
        px3 = inv(inv(px2) - 2e-6)                        # encore -2e-6
        d1, d2 = f(px2) - f(px1), f(px3) - f(px2)
        self.assertAlmostEqual(d1, d2, places=12)
        # ...alors que les pas de prix correspondants different nettement.
        self.assertGreater(abs((px3 - px2) - (px2 - px1)), 1.0)


class TestInverseTreatedAsLinearIsCaught(unittest.TestCase):
    """C. Tests dedies a attraper l'erreur "traiter un inverse comme un lineaire"."""

    def test_applying_linear_notional_to_inverse_is_catastrophic(self):
        """Erreur classique : notionnel = prix * sz * ctVal sur un inverse.
        vrai    : 100*100        = 10 000 USD
        faux    : 100*100*100000 = 1 000 000 000 USD  (facteur 100 000)
        """
        price = 100_000.0
        true_notional = usd_notional(BTC_INVERSE, 100, price)
        wrong_linear = BTC_INVERSE.ct_val * 100 * BTC_INVERSE.ct_mult * price
        self.assertAlmostEqual(true_notional, 10_000.0, places=6)
        self.assertAlmostEqual(wrong_linear, 1_000_000_000.0, places=0)
        self.assertGreater(wrong_linear / true_notional, 1_000.0)

    def test_applying_linear_pnl_formula_to_inverse_is_catastrophic(self):
        """vrai (inverse) : 10 000*(1/100000 - 1/101000) = 9.90099e-4 BTC
        faux  (lineaire) : 10 000*(101000-100000)        = 10 000 000
        """
        pe, px = 100_000.0, 101_000.0
        true_pnl = pnl(BTC_INVERSE, Direction.LONG, 100, pe, px).pnl_settle_ccy
        wrong_pnl = BTC_INVERSE.ct_val * 100 * BTC_INVERSE.ct_mult * (px - pe)
        self.assertAlmostEqual(true_pnl, 9.9009900990099e-4, places=12)
        self.assertAlmostEqual(wrong_pnl, 10_000_000.0, places=0)
        self.assertGreater(abs(wrong_pnl / true_pnl), 1e9)

    def test_subtle_bias_return_on_exit_vs_entry_price(self):
        """Le piege SILENCIEUX, celui qui ne fait pas exploser un backtest.

        Pour un inverse, le rendement en coin vaut (Px-Pe)/Px, pas (Px-Pe)/Pe.
        inputs  : Pe=100 000, Px=101 000 (+1%)
        attendu : rendement coin = 1000/101000 = 99.0099 bps
                  hypothese lineaire naive     = 100.0000 bps
                  biais                        = 0.9901 bps
        Sur un edge de quelques bps, ce biais est du meme ordre que l'edge.
        """
        pe, px = 100_000.0, 101_000.0
        r = pnl(BTC_INVERSE, Direction.LONG, 100, pe, px)
        naive_linear_bps = (px - pe) / pe * 10_000.0
        self.assertAlmostEqual(r.return_bps_settle, 99.00990099009901, places=9)
        self.assertAlmostEqual(naive_linear_bps, 100.0, places=9)
        bias = naive_linear_bps - r.return_bps_settle
        self.assertAlmostEqual(bias, 0.990099009900991, places=9)
        self.assertGreater(bias, 0.5)  # non negligeable a l'echelle d'un edge en bps

    def test_inverse_and_linear_specs_are_never_interchangeable(self):
        """Meme sous-jacent, meme sz, meme prix -> notionnels differents.
        Aucun code ne doit pouvoir substituer l'un a l'autre."""
        price = 75_000.0
        self.assertNotAlmostEqual(usd_notional(BTC_INVERSE, 100, price),
                                  usd_notional(BTC_LINEAR, 100, price), places=2)
        self.assertNotEqual(BTC_INVERSE.inst_id, BTC_LINEAR.inst_id)
        self.assertNotEqual(BTC_INVERSE.settle_ccy, BTC_LINEAR.settle_ccy)
        self.assertNotEqual(BTC_INVERSE.inst_type, BTC_LINEAR.inst_type)

    def test_ada_inverse_uses_its_own_ctval_not_btc(self):
        """ctVal=10 pour ADA-USD-SWAP, pas 100 : aucune constante BTC codee en dur."""
        self.assertAlmostEqual(usd_notional(ADA_INVERSE, 100, 0.42), 1_000.0, places=9)
        self.assertAlmostEqual(usd_notional(BTC_INVERSE, 100, 0.42), 10_000.0, places=9)


class TestSpecIsMandatory(unittest.TestCase):
    """D. Un module financier qui ne recoit qu'un symbole doit echouer."""

    FINANCIAL_CALLS = (
        ("usd_notional", lambda s: usd_notional(s, 1, 100.0)),
        ("coin_notional", lambda s: coin_notional(s, 1, 100.0)),
        ("contracts_for_usd_notional", lambda s: contracts_for_usd_notional(s, 100.0, 100.0)),
        ("quantize_contracts", lambda s: quantize_contracts(s, 1.0)),
        ("quantize_price", lambda s: quantize_price(s, 100.0)),
        ("build_order", lambda s: build_order(s, 100.0, 100.0)),
        ("pnl", lambda s: pnl(s, Direction.LONG, 1, 100.0, 101.0)),
        ("fee", lambda s: fee(s, 1, 100.0, 0.0005)),
        ("round_trip_fee_bps", lambda s: round_trip_fee_bps(s, 1, 100.0, 101.0)),
    )

    def test_bare_symbol_string_is_rejected_everywhere(self):
        for bad in ("BTC", "BTC-USD-SWAP", "BTC-USDT-SWAP"):
            for name, call in self.FINANCIAL_CALLS:
                with self.subTest(fn=name, arg=bad):
                    with self.assertRaises(NotAnInstrumentSpec):
                        call(bad)

    def test_dict_lookalike_is_rejected(self):
        fake = {"inst_id": "BTC-USD-SWAP", "ct_val": 100, "ct_mult": 1}
        for name, call in self.FINANCIAL_CALLS:
            with self.subTest(fn=name):
                with self.assertRaises(NotAnInstrumentSpec):
                    call(fake)

    def test_error_message_names_the_missing_mechanics(self):
        with self.assertRaises(NotAnInstrumentSpec) as ctx:
            usd_notional("BTC", 1, 100.0)
        msg = str(ctx.exception)
        for token in ("InstrumentSpec", "ct_type", "ct_val", "settle_ccy"):
            self.assertIn(token, msg)


class TestOrderConstraints(unittest.TestCase):
    def test_lot_size_rounds_down_never_up(self):
        """lotSz=0.1 : 0.37 -> 0.3 (jamais 0.4 : pas plus de risque que demande)."""
        self.assertAlmostEqual(quantize_contracts(BTC_INVERSE, 0.37), 0.3, places=9)
        self.assertAlmostEqual(quantize_contracts(ADA_INVERSE, 2.9), 2.0, places=9)

    def test_tick_size_rounds_conservatively_by_direction(self):
        """tickSz=0.1 : un acheteur paie plus, un vendeur recoit moins."""
        self.assertAlmostEqual(quantize_price(BTC_INVERSE, 75_000.04, Direction.LONG), 75_000.1, places=6)
        self.assertAlmostEqual(quantize_price(BTC_INVERSE, 75_000.06, Direction.SHORT), 75_000.0, places=6)

    def test_order_below_min_size_is_refused_not_rounded_up(self):
        """min_size=0.1 contrat = 10 USD de notionnel. 5 USD est REFUSE."""
        o = build_order(BTC_INVERSE, target_usd=5.0, price=75_000.0)
        self.assertFalse(o.is_executable)
        self.assertEqual(o.contracts, 0.0)
        self.assertIn("min_size", o.reason)

    def test_minimum_executable_notional_is_ten_usd_for_btc_inverse(self):
        o = build_order(BTC_INVERSE, target_usd=10.0, price=75_000.0)
        self.assertTrue(o.is_executable)
        self.assertAlmostEqual(o.contracts, 0.1, places=9)
        self.assertAlmostEqual(o.usd_notional, 10.0, places=9)

    def test_quantization_residual_is_reported_not_hidden(self):
        """ADA : lot=1 contrat=10 USD. Viser 25 USD donne 20 USD + 5 USD de residu."""
        o = build_order(ADA_INVERSE, target_usd=25.0, price=0.42)
        self.assertTrue(o.is_executable)
        self.assertAlmostEqual(o.contracts, 2.0, places=9)
        self.assertAlmostEqual(o.usd_notional, 20.0, places=9)
        self.assertAlmostEqual(o.residual_usd, 5.0, places=9)


class TestSpecValidation(unittest.TestCase):
    def test_non_live_instrument_is_refused(self):
        dead = InstrumentSpec(**{**BTC_INVERSE.to_dict(), "inst_type": InstrumentType.SWAP_INVERSE,
                                 "state": "suspend"})
        with self.assertRaises(InvalidInstrument) as c:
            dead.validate()
        self.assertIn("state", str(c.exception))

    def test_incoherent_inverse_metadata_is_refused(self):
        bad = InstrumentSpec(**{**BTC_INVERSE.to_dict(), "inst_type": InstrumentType.SWAP_INVERSE,
                                "ct_val_ccy": "BTC"})
        with self.assertRaises(InvalidInstrument):
            bad.validate()

    def test_missing_ctval_is_refused(self):
        bad = InstrumentSpec(**{**BTC_INVERSE.to_dict(), "inst_type": InstrumentType.SWAP_INVERSE,
                                "ct_val": 0.0})
        with self.assertRaises(InvalidInstrument):
            bad.validate()

    def test_zero_or_negative_price_is_refused(self):
        for bad_price in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(price=bad_price):
                with self.assertRaises((ValueError, TypeError)):
                    usd_notional(BTC_INVERSE, 1, bad_price)


def _print_reference_table() -> None:
    rows = [
        ("notionnel USD inverse @10k", "ctVal*sz*ctMult = 100*100*1",
         10_000.0, usd_notional(BTC_INVERSE, 100, 10_000.0)),
        ("notionnel USD inverse @90k", "identique (prix absent)",
         10_000.0, usd_notional(BTC_INVERSE, 100, 90_000.0)),
        ("notionnel USD lineaire @90k", "ctVal*sz*ctMult*P = 0.01*100*90000",
         90_000.0, usd_notional(BTC_LINEAR, 100, 90_000.0)),
        ("frais inverse (exemple OKX)", "100*100/10000*0.0005 [BTC]",
         0.0005, fee(BTC_INVERSE, 100, 10_000.0, 0.0005).fee_settle_ccy),
        ("frais lineaire (exemple OKX)", "0.01*100*10000*0.0005 [USDT]",
         5.0, fee(BTC_LINEAR, 100, 10_000.0, 0.0005).fee_settle_ccy),
        ("PnL long inverse 10k->20k", "10000*(1/10000-1/20000) [BTC]",
         0.5, pnl(BTC_INVERSE, Direction.LONG, 100, 10_000.0, 20_000.0).pnl_settle_ccy),
        ("PnL short inverse 20k->10k", "10000*(1/10000-1/20000) [BTC]",
         0.5, pnl(BTC_INVERSE, Direction.SHORT, 100, 20_000.0, 10_000.0).pnl_settle_ccy),
        ("PnL long lineaire 10k->20k", "0.01*100*(20000-10000) [USDT]",
         10_000.0, pnl(BTC_LINEAR, Direction.LONG, 100, 10_000.0, 20_000.0).pnl_settle_ccy),
        ("rendement coin inverse +1%", "(Px-Pe)/Px = 1000/101000",
         99.00990099009901,
         pnl(BTC_INVERSE, Direction.LONG, 100, 100_000.0, 101_000.0).return_bps_settle),
        ("hypothese lineaire naive", "(Px-Pe)/Pe = 1000/100000",
         100.0, (101_000.0 - 100_000.0) / 100_000.0 * 10_000.0),
        ("aller-retour taker Lv1", "5 bps + 5 bps",
         10.0, round_trip_fee_bps(BTC_INVERSE, 100, 50_000.0, 50_000.0)),
        ("notionnel min BTC inverse", "min_size 0.1 * ctVal 100",
         10.0, build_order(BTC_INVERSE, 10.0, 75_000.0).usd_notional),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"\n{'CAS':<{w}}  {'FORMULE':<40}  {'ATTENDU':>18}  {'OBTENU':>18}  OK")
    print("-" * (w + 90))
    for label, formula, expected, got in rows:
        ok = "OK" if math.isclose(expected, got, rel_tol=1e-9, abs_tol=1e-12) else "ECHEC"
        print(f"{label:<{w}}  {formula:<40}  {expected:>18.10g}  {got:>18.10g}  {ok}")
    print()


if __name__ == "__main__":
    if "--table" in sys.argv:
        _print_reference_table()
    else:
        unittest.main(verbosity=2)
