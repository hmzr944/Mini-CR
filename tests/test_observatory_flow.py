"""Le flux signe de l'observatoire est-il en vrais dollars ?

L'observatoire agregeait `px * sz` par intervalle. Sur un perpetuel INVERSE,
`sz` est un nombre de contrats et un contrat vaut ctVal USD : le notionnel ne
depend pas du prix. `px * sz` surevaluait donc BTC-USD-SWAP d'un facteur
75 200 / 100 = 752, et sous-evaluait ADA-USD-SWAP d'un facteur 0,35 / 10.

Consequence mesuree sur les donnees collectees : BTC-USD-SWAP ressortait a
9,57 milliards de dollars echanges par heure, soit environ cent fois le volume
reel de l'instrument. Toute mesure de flux batie dessus etait fausse, et le
classement des instruments par volume etait inverse entre pieces cheres et
pieces bon marche.

Ce test echoue sur l'ancien comportement.
"""
import unittest

from prism_v2.contracts import usd_notional
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.l2book import L2BookSet
from prism_v2.observatory import MarketObservatory, ObservatoryStats


def _spec(inst_id, inst_type, ct_val, base, quote, settle):
    return InstrumentSpec(
        inst_id=inst_id, exchange="OKX", inst_type=inst_type,
        ct_type="inverse" if inst_type is InstrumentType.SWAP_INVERSE else "linear",
        base=base, quote=quote, settle_ccy=settle, ct_val=ct_val,
        ct_val_ccy="USD" if inst_type is InstrumentType.SWAP_INVERSE else base,
        ct_mult=1.0, tick_size=0.1, lot_size=1.0, min_size=1.0,
        state="live", fetched_at="2026-01-01T00:00:00Z")


BTC = _spec("BTC-USD-SWAP", InstrumentType.SWAP_INVERSE, 100.0, "BTC", "USD", "BTC")
ADA = _spec("ADA-USD-SWAP", InstrumentType.SWAP_INVERSE, 10.0, "ADA", "USD", "ADA")
ETH_LIN = _spec("ETH-USDT-SWAP", InstrumentType.SWAP_LINEAR, 0.01, "ETH", "USDT", "USDT")


class _Msg:
    def __init__(self, payload):
        self.payload = payload
        self.local_recv_ts_ms = 1_700_000_000_000


def _fresh():
    return {"n": 0, "buy_usd": 0.0, "sell_usd": 0.0,
            "first_px": 0.0, "last_px": 0.0}


def _ingest(specs, rows, inst_id):
    obs = MarketObservatory()
    books = L2BookSet({s.inst_id: s for s in specs})
    trades, stats = {}, ObservatoryStats()
    obs._ingest(_Msg({"arg": {"channel": "trades", "instId": inst_id},
                      "data": rows}),
                books, trades, {}, _fresh, stats)
    return trades, stats


class TestFluxEnDollars(unittest.TestCase):

    def test_inverse_le_notionnel_ne_depend_pas_du_prix(self):
        """78,1 contrats BTC valent 7 810 USD, pas 5 873 127 USD."""
        rows = [{"instId": "BTC-USD-SWAP", "px": "75200.1", "sz": "78.1",
                 "side": "buy", "ts": "1"}]
        trades, _ = _ingest([BTC], rows, "BTC-USD-SWAP")
        got = trades["BTC-USD-SWAP"]["buy_usd"]
        self.assertAlmostEqual(got, 7810.0, places=6)
        # l'ancien calcul, explicitement rejete
        self.assertNotAlmostEqual(got, 75200.1 * 78.1, places=0)

    def test_inverse_meme_taille_prix_different_meme_notionnel(self):
        """Le prix ne doit pas entrer dans le notionnel d'un inverse."""
        a, _ = _ingest([BTC], [{"instId": "BTC-USD-SWAP", "px": "30000",
                                "sz": "5", "side": "buy"}], "BTC-USD-SWAP")
        b, _ = _ingest([BTC], [{"instId": "BTC-USD-SWAP", "px": "120000",
                                "sz": "5", "side": "buy"}], "BTC-USD-SWAP")
        self.assertEqual(a["BTC-USD-SWAP"]["buy_usd"],
                         b["BTC-USD-SWAP"]["buy_usd"])

    def test_piece_bon_marche_n_est_plus_sous_evaluee(self):
        """1 000 contrats ADA valent 10 000 USD, pas 350 USD."""
        rows = [{"instId": "ADA-USD-SWAP", "px": "0.35", "sz": "1000",
                 "side": "sell"}]
        trades, _ = _ingest([ADA], rows, "ADA-USD-SWAP")
        self.assertAlmostEqual(trades["ADA-USD-SWAP"]["sell_usd"], 10_000.0,
                               places=6)

    def test_lineaire_le_prix_entre_bien(self):
        """Sur un lineaire, ctVal est en ccy de base : le prix compte."""
        rows = [{"instId": "ETH-USDT-SWAP", "px": "2400", "sz": "3",
                 "side": "buy"}]
        trades, _ = _ingest([ETH_LIN], rows, "ETH-USDT-SWAP")
        self.assertAlmostEqual(trades["ETH-USDT-SWAP"]["buy_usd"],
                               0.01 * 3 * 2400, places=6)

    def test_le_flux_concorde_avec_la_fonction_dediee(self):
        """Aucune arithmetique locale : la meme fonction que le carnet."""
        for spec, px, sz in ((BTC, 75200.1, 78.1), (ADA, 0.35, 1000.0),
                             (ETH_LIN, 2400.0, 3.0)):
            rows = [{"instId": spec.inst_id, "px": str(px), "sz": str(sz),
                     "side": "buy"}]
            trades, _ = _ingest([spec], rows, spec.inst_id)
            self.assertAlmostEqual(trades[spec.inst_id]["buy_usd"],
                                   usd_notional(spec, sz, px), places=6)

    def test_instrument_sans_specification_est_ignore_et_compte(self):
        """Sans unite connue, on ne devine pas un notionnel."""
        rows = [{"instId": "INCONNU-USD-SWAP", "px": "10", "sz": "5",
                 "side": "buy"}]
        trades, stats = _ingest([BTC], rows, "INCONNU-USD-SWAP")
        self.assertNotIn("INCONNU-USD-SWAP", trades)
        self.assertEqual(stats.trades_without_spec, 1)
        self.assertEqual(stats.trades, 0)


if __name__ == "__main__":
    unittest.main()
