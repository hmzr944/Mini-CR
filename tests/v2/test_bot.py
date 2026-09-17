"""Boucle du bot. Testee sans reseau, via un feed injecte.

Deux proprietes priment sur toutes les autres :
  1. aucun chemin vers un ordre reel n'existe depuis la boucle ;
  2. l'absence de donnee produit UNRESOLVED, jamais un rejet economique —
     confondre « je ne peux pas mesurer » et « ce n'est pas rentable » fait
     abandonner une piste vivante ou poursuivre une piste morte.
"""
from __future__ import annotations

import unittest

from prism_v2.bot import Bot, CycleReport, FundingObservation, render
from prism_v2.core_types import Provenance
from prism_v2.execution import PaperExecutor
from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.opp_funding import VenueFunding
from prism_v2.orderbook import OrderBook

HOURS_PER_YEAR = 24 * 365


def spec(base="ADA"):
    return InstrumentSpec(
        inst_id=f"{base}-USDT-SWAP", exchange="OKX",
        inst_type=InstrumentType.SWAP_LINEAR, ct_type="linear", base=base,
        quote="USDT", settle_ccy="USDT", ct_val=0.01, ct_val_ccy=base,
        ct_mult=1.0, tick_size=0.0001, lot_size=1.0, min_size=1.0,
        state="live", fetched_at="2026-01-01T00:00:00Z")


def book(spec_, mid=100.0, spread=0.02, size=5000.0):
    payload = [{
        "bids": [[f"{mid - spread / 2:.4f}", f"{size}"],
                 [f"{mid - spread:.4f}", f"{size}"]],
        "asks": [[f"{mid + spread / 2:.4f}", f"{size}"],
                 [f"{mid + spread:.4f}", f"{size}"]],
        "ts": "1789600000000",
    }]
    prov = Provenance(exchange="OKX", endpoint="/books",
                      fetched_at="2026-01-01T00:00:00Z", inst_id=spec_.inst_id)
    return OrderBook.from_okx(spec_, payload, prov,
                              local_recv_ts_ms=1789600000100)


#: Un petit cap portant 100 %/an de funding ne cote PAS a 2 bps de spread.
#: Les deux regimes sont testes separement : un carnet realiste doit faire
#: refuser, un carnet exceptionnellement serre doit faire accepter — un bot
#: incapable d'accepter ne servirait a rien.
SPREAD_REALISTE = 0.60      # 60 bps sur un mid a 100
SPREAD_EXCEPTIONNEL = 0.02  # 2 bps


def obs(near_apr, far_apr, base="ADA", with_book=True, capacity=1e6,
        spread=SPREAD_REALISTE):
    s = spec(base)
    return FundingObservation(
        symbol=base, spec=s,
        near=VenueFunding("OKX", near_apr / HOURS_PER_YEAR * 8.0, 8.0, 1000),
        far=VenueFunding("HYPERLIQUID", far_apr / HOURS_PER_YEAR, 1.0, 1000),
        book=book(s, spread=spread) if with_book else None,
        capacity_usd=capacity)


class FakeFeed:
    def __init__(self, observations):
        self.observations = observations
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return list(self.observations)


class BrokenFeed:
    def snapshot(self):
        raise ConnectionError("venue injoignable")


class TestBoucle(unittest.TestCase):

    def test_un_cycle_compte_ce_qu_il_voit(self):
        bot = Bot(FakeFeed([obs(-0.5, 0.5), obs(0.0, 0.0, base="SOL")]))
        rep = bot.run_cycle()
        self.assertEqual(rep.n_instruments, 2)
        self.assertEqual(rep.n_detected, 1)     # le second est sous le bruit
        self.assertEqual(rep.n_evaluated, 1)

    def test_sur_carnet_realiste_le_differentiel_est_rejete(self):
        """Le comportement attendu : la famille est mesuree negative."""
        rep = Bot(FakeFeed([obs(-0.5, 0.5)])).run_cycle()
        self.assertEqual(rep.n_accepted, 0)
        self.assertEqual(rep.n_rejected, 1)
        self.assertTrue(any("net" in r for r in rep.rejection_reasons))

    def test_sur_carnet_exceptionnel_le_bot_SAIT_accepter(self):
        """Contre-epreuve indispensable : un bot qui refuse toujours ne
        criblerait rien. Avec un spread de 2 bps, l'economie passe.
        """
        rep = Bot(FakeFeed([obs(-0.5, 0.5, spread=SPREAD_EXCEPTIONNEL)])).run_cycle()
        self.assertEqual(rep.n_accepted, 1)
        self.assertEqual(rep.n_rejected, 0)

    def test_c_est_bien_le_cout_qui_fait_basculer_la_decision(self):
        """Meme signal, deux carnets : seul le spread change le verdict."""
        serre = Bot(FakeFeed([obs(-0.5, 0.5, spread=SPREAD_EXCEPTIONNEL)])).run_cycle()
        large = Bot(FakeFeed([obs(-0.5, 0.5, spread=SPREAD_REALISTE)])).run_cycle()
        self.assertEqual(serre.n_accepted, 1)
        self.assertEqual(large.n_accepted, 0)

    def test_sans_carnet_le_resultat_est_non_resolu_pas_un_rejet(self):
        rep = Bot(FakeFeed([obs(-0.5, 0.5, with_book=False)])).run_cycle()
        self.assertEqual(rep.n_unresolved, 1)
        self.assertEqual(rep.n_rejected, 0)
        self.assertEqual(rep.n_accepted, 0)

    def test_une_panne_de_collecte_ne_fabrique_aucune_candidate(self):
        rep = Bot(BrokenFeed()).run_cycle()
        self.assertEqual(rep.n_detected, 0)
        self.assertEqual(rep.n_accepted, 0)
        self.assertTrue(rep.errors)

    def test_une_erreur_sur_un_actif_n_arrete_pas_le_cycle(self):
        bad = obs(-0.5, 0.5, base="BAD")
        bad.spec = None                      # provoque une erreur ciblee
        rep = Bot(FakeFeed([bad, obs(-0.5, 0.5, base="ADA")])).run_cycle()
        self.assertTrue(rep.errors)
        self.assertEqual(rep.n_evaluated, 1)

    def test_le_meilleur_net_est_suivi_meme_quand_tout_est_rejete(self):
        rep = Bot(FakeFeed([obs(-0.5, 0.5),
                            obs(-1.2, 1.2, base="SOL")])).run_cycle()
        self.assertIsNotNone(rep.best_net_bps)
        self.assertEqual(rep.n_accepted, 0)


class TestAucunOrdreReel(unittest.TestCase):

    def test_sans_executeur_rien_n_est_execute(self):
        rep = Bot(FakeFeed([obs(-0.5, 0.5)])).run_cycle()
        self.assertEqual(rep.n_executed, 0)

    def test_l_executeur_branche_est_du_papier(self):
        bot = Bot(FakeFeed([obs(-0.5, 0.5)]), executor=PaperExecutor())
        self.assertEqual(bot.executor.mode.value, "PAPER")

    def test_rien_n_est_execute_sans_acceptation_economique(self):
        """Le lien est structurel : l'execution est dans la branche ACCEPTED."""
        bot = Bot(FakeFeed([obs(-0.5, 0.5)]), executor=PaperExecutor())
        rep = bot.run_cycle()
        self.assertEqual(rep.n_accepted, 0)
        self.assertEqual(rep.n_executed, 0)

    def test_une_acceptation_passe_par_le_PaperExecutor_et_rien_d_autre(self):
        bot = Bot(FakeFeed([obs(-0.5, 0.5, spread=SPREAD_EXCEPTIONNEL)]),
                  executor=PaperExecutor())
        rep = bot.run_cycle()
        self.assertEqual(rep.n_executed, 1)
        self.assertEqual(len(rep.executed), 1)
        self.assertIn("filled_usd", rep.executed[0])


class TestRapport(unittest.TestCase):

    def test_les_refus_sont_de_premiere_classe(self):
        rep = CycleReport(run_id="r", ts_utc="t")
        rep.note_rejection("motif A")
        rep.note_rejection("motif A")
        rep.note_rejection("motif B")
        self.assertEqual(rep.rejection_reasons["motif A"], 2)
        self.assertEqual(rep.rejection_reasons["motif B"], 1)

    def test_un_motif_vide_reste_nomme(self):
        rep = CycleReport(run_id="r", ts_utc="t")
        rep.note_rejection("")
        self.assertIn("non precise", rep.rejection_reasons)

    def test_le_rendu_dit_explicitement_qu_aucune_ne_couvre_ses_couts(self):
        texte = render(Bot(FakeFeed([obs(-0.5, 0.5)])).run_cycle())
        self.assertIn("aucune opportunite ne couvre ses couts", texte)

    def test_le_rapport_est_serialisable(self):
        d = Bot(FakeFeed([obs(-0.5, 0.5)])).run_cycle().to_dict()
        self.assertIn("rejection_reasons", d)
        self.assertIn("n_accepted", d)


if __name__ == "__main__":
    unittest.main()
