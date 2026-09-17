"""Le flux de funding rend-il ce qu'on croit, et la correction tient-elle ?

`funding_feed` alimente toutes les mesures de funding du projet et n'avait
AUCUN test. La correction la plus couteuse de l'histoire du depot y vit : la
cadence de funding d'OKX n'est pas 8 h partout — 90 instruments sur 142 payent
toutes les 4 h, certains toutes les heures. La supposer a 8 h divisait le taux
horaire par deux sur 63 % de l'univers et avait fabrique un +3 879 %/an
fantome. Rien n'empechait cette correction de regresser.

Aucun appel reseau ici : les reponses des venues sont injectees telles qu'elles
arrivent, y compris dans leurs formes degradees.
"""
import unittest
from unittest import mock

from prism_v2 import funding_feed as ff


def _okx(rate, period_h, ft=1_700_000_000_000):
    return {"data": [{"instId": "X-USDT-SWAP", "fundingRate": str(rate),
                      "fundingTime": str(ft),
                      "nextFundingTime": str(ft + int(period_h * 3_600_000))}]}


class TestCadenceOKX(unittest.TestCase):

    def _call(self, payload):
        with mock.patch.object(ff, "_http_json", return_value=payload):
            return ff.okx_funding("X-USDT-SWAP")

    def test_la_cadence_est_lue_pas_supposee(self):
        for h in (1.0, 2.0, 4.0, 8.0):
            v = self._call(_okx(0.0001, h))
            self.assertAlmostEqual(v.period_h, h, places=9,
                                   msg=f"cadence {h} h mal lue")

    def test_deux_cadences_meme_taux_donnent_des_annualises_differents(self):
        """C'est exactement ce que l'ancien code confondait."""
        q = self._call(_okx(0.0001, 4.0))
        h = self._call(_okx(0.0001, 8.0))
        self.assertAlmostEqual(q.rate / q.period_h, 2.0 * (h.rate / h.period_h),
                               places=12)

    def test_cadence_nulle_ou_negative_refusee(self):
        for bad in (0.0, -4.0):
            with self.assertRaises(ff.FeedError):
                self._call(_okx(0.0001, bad))

    def test_reponse_vide_refusee(self):
        with self.assertRaises(ff.FeedError):
            self._call({"data": []})
        with self.assertRaises(ff.FeedError):
            self._call({})

    def test_champ_illisible_refuse_pas_devine(self):
        bad = _okx(0.0001, 8.0)
        bad["data"][0]["fundingRate"] = "n/a"
        with self.assertRaises(ff.FeedError):
            self._call(bad)
        manquant = _okx(0.0001, 8.0)
        del manquant["data"][0]["nextFundingTime"]
        with self.assertRaises(ff.FeedError):
            self._call(manquant)

    def test_taux_negatif_conserve_son_signe(self):
        """Un funding negatif paie le long : le signe porte tout le sens."""
        v = self._call(_okx(-0.00042, 8.0))
        self.assertLess(v.rate, 0.0)
        self.assertAlmostEqual(v.rate, -0.00042, places=12)


class TestUniversHyperliquid(unittest.TestCase):

    BASE = [{"universe": [{"name": "BTC"}, {"name": "MORT", "isDelisted": True},
                          {"name": "ILLISIBLE"}]},
            [{"funding": "0.0000125", "markPx": "75000", "openInterest": "10",
              "dayNtlVlm": "1000000"},
             {"funding": "0.001", "markPx": "1", "openInterest": "1",
              "dayNtlVlm": "1"},
             {"funding": "0.0001", "markPx": "pas un nombre"}]]

    def _call(self, payload):
        with mock.patch.object(ff, "_http_json", return_value=payload):
            return ff.hyperliquid_universe()

    def test_funding_hyperliquid_est_horaire(self):
        """Une cadence horaire comparee a une cadence 8 h sans conversion
        donne un facteur 8 sur le differentiel."""
        u = self._call(self.BASE)
        self.assertAlmostEqual(u["BTC"]["funding"].period_h, 1.0, places=9)

    def test_delistes_exclus(self):
        self.assertNotIn("MORT", self._call(self.BASE))

    def test_actif_illisible_ignore_jamais_devine(self):
        u = self._call(self.BASE)
        self.assertNotIn("ILLISIBLE", u)
        self.assertIn("BTC", u)

    def test_notionnel_ouvert_en_dollars(self):
        u = self._call(self.BASE)
        self.assertAlmostEqual(u["BTC"]["oi_usd"], 10.0 * 75_000.0, places=6)

    def test_reponse_inattendue_refusee(self):
        for bad in ({}, [], [{"universe": []}]):
            with self.assertRaises(ff.FeedError):
                self._call(bad)

    def test_univers_vide_refuse(self):
        with self.assertRaises(ff.FeedError):
            self._call([{"universe": []}, []])


if __name__ == "__main__":
    unittest.main()
