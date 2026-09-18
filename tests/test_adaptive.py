"""Le retour d'experience change-t-il vraiment le comportement ?

Le mandat est explicite : le feedback doit modifier le systeme, pas
alimenter un rapport. Un module d'adaptation qui se contente d'enregistrer
ses deceptions serait indiscernable d'un module inerte — et c'est
exactement ce que ces tests refusent de laisser passer.

LE TEMOIN D'ADAPTATION est le test central de ce fichier. On fabrique un
marche dont le signal DISPARAIT a mi-parcours. La politique gelee, ajustee
sur la premiere moitie, doit continuer a trader et a perdre. La politique
en marche en avant doit voir son realise deçevoir, perdre confiance, et
cesser de trader. Si les deux se comportent pareil, l'adaptation n'existe
pas, quoi qu'en disent les journaux.
"""
import random
import unittest

from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.margin import MarginSchedule, MarginTier
from prism_v2.policy.action import (EXECUTABLE, TAKER_ACTIONS, UPPER_BOUND,
                                    Action, FeeModel)
from prism_v2.policy.adaptive import (CONFIANCE_MAX, CONFIANCE_MIN, PENALITE,
                                      _maj_confiance, parcourir)
from prism_v2.policy.attribution import (Attribution, Cause, attribuer,
                                         attribuer_tout)
from prism_v2.policy.engine import Market, build_samples, run_policy, split_index
from prism_v2.policy.ledger import Decision, Ledger
from prism_v2.policy.state import FEATURES, StateBuilder, StateConfig, flow_median
from prism_v2.policy.value import select_features

FEES = FeeModel(maker_bps=2.0, taker_bps=5.0)
CFG = StateConfig(short_rows=4, long_rows=12)


def _spec():
    return InstrumentSpec(
        inst_id="TEST-USD-SWAP", exchange="TEST",
        inst_type=InstrumentType.SWAP_INVERSE, ct_type="inverse",
        base="TEST", quote="USD", settle_ccy="TEST", ct_val=1.0,
        ct_val_ccy="USD", ct_mult=1.0, tick_size=0.000001,
        lot_size=0.000001, min_size=0.000001, state="live",
        fetched_at="2026-09-18T00:00:00Z")


def _schedule():
    return MarginSchedule("TEST-USD", [MarginTier(1, 0.0, 1e9, 0.10, 0.05, 10.0)])


def _marche(n, seed, duree_signal):
    """Signal persistant pendant `duree_signal` lignes, bruit ensuite.

    Le carnet annonce la derive tant que le signal vit. Passe ce point, le
    desequilibre du carnet devient independant du prix : l'information
    disparait sans que rien d'autre ne change.
    """
    rng = random.Random(seed)
    rows, px = [], 100.0
    plage, retard = 60, 4
    signes = []
    while len(signes) < n:
        signes += [rng.choice((-1, 1))] * plage
    signes = signes[:n]
    for i in range(n):
        vivant = i < duree_signal
        if i >= retard:
            d = signes[i - retard] * 0.0006 if vivant else rng.gauss(0, 0.0006)
            px *= 1.0 + d + rng.gauss(0, 0.00005)
        bsz = 9 if signes[i] > 0 else 1
        asz = 1 if signes[i] > 0 else 9
        rows.append((i * 300, round(px * 0.99995, 6), round(px * 1.00005, 6),
                     bsz, asz, rng.random() * 50 + 1, rng.random() * 50 + 1))
    return rows


def _echantillons(rows):
    m = Market("TEST-USD-SWAP", _spec(), _schedule(), FEES, rows)
    b = StateBuilder(CFG, flow_median(rows, CFG, 0, len(rows) // 2) or 1.0)
    return build_samples(m, b, 20, 0, len(rows), 10_000.0,
                         max_capital_usd=1_000.0)


class TestConfiance(unittest.TestCase):
    """La regle de mise a jour fait-elle ce qu'elle annonce ?"""

    def test_un_segment_decevant_fait_baisser_la_confiance(self):
        self.assertAlmostEqual(_maj_confiance(1.0, 5.0, -2.0, 10),
                               PENALITE, places=9)

    def test_un_segment_tenu_la_fait_remonter_sans_depasser_un(self):
        self.assertGreater(_maj_confiance(0.5, 1.0, 2.0, 10), 0.5)
        self.assertLessEqual(_maj_confiance(0.9, 1.0, 2.0, 10), CONFIANCE_MAX)

    def test_un_segment_sans_trade_ne_change_rien(self):
        """Ne pas punir la prudence, ne pas recompenser l'inaction."""
        self.assertEqual(_maj_confiance(0.4, 3.0, 0.0, 0), 0.4)

    def test_la_confiance_ne_tombe_jamais_a_zero(self):
        c = 1.0
        for _ in range(50):
            c = _maj_confiance(c, 5.0, -5.0, 10)
        self.assertGreater(c, 0.0)


class TestAttribution(unittest.TestCase):
    """L'ecart est-il decompose, ou seulement constate ?"""

    def _d(self, **kw):
        base = dict(ts_ms=0, instrument="X", state={},
                    action=Action.LONG_TAKER, target_position_usd=100.0,
                    entry_px=100.0, exit_px=101.0, size_usd=100.0,
                    holding_s=60.0, gross_bps=20.0, fee_bps=10.0,
                    spread_bps=2.0, slippage_bps=0.0,
                    adverse_selection_bps=None, net_bps=10.0,
                    capital_usd=10.0, status=EXECUTABLE, predicted_bps=5.0)
        base.update(kw)
        return Decision(**base)

    def test_l_ecart_total_est_realise_moins_attendu(self):
        e = attribuer(self._d(), cout_attendu_bps=10.0)
        self.assertAlmostEqual(e.total_bps, 10.0 - 5.0, places=9)

    def test_les_postes_recomposent_l_ecart(self):
        """Aucune part de l'ecart ne doit disparaitre en chemin."""
        for pred, net, cout in ((5.0, 10.0, 10.0), (-3.0, -8.0, 7.0),
                                (0.0, 0.0, 10.0), (12.0, -4.0, 11.5)):
            e = attribuer(self._d(predicted_bps=pred, net_bps=net,
                                  gross_bps=net + 10.0), cout)
            self.assertAlmostEqual(sum(e.postes.values()), e.total_bps,
                                   places=9)

    def test_un_cout_conforme_ne_produit_aucun_ecart_de_cout(self):
        e = attribuer(self._d(), cout_attendu_bps=10.0)
        self.assertAlmostEqual(e.postes[Cause.COUT], 0.0, places=9)

    def test_un_cout_plus_eleve_que_prevu_est_impute_au_cout(self):
        e = attribuer(self._d(fee_bps=14.0, net_bps=6.0), cout_attendu_bps=10.0)
        self.assertAlmostEqual(e.postes[Cause.COUT], -4.0, places=9)

    def test_une_entree_passive_n_est_pas_imputable(self):
        """Une probabilite de remplissage INCONNUE n'est pas un diagnostic."""
        e = attribuer(self._d(action=Action.LONG_MAKER_IN, fee_bps=7.0,
                              net_bps=13.0, status=UPPER_BOUND), 7.0)
        self.assertFalse(e.imputable)

    def test_no_trade_n_a_pas_d_ecart(self):
        d = self._d(action=Action.NO_TRADE, entry_px=None, exit_px=None,
                    size_usd=0.0, capital_usd=0.0, net_bps=0.0,
                    gross_bps=0.0, fee_bps=0.0, spread_bps=0.0,
                    target_position_usd=0.0, predicted_bps=0.0)
        self.assertIsNone(attribuer(d, 10.0))

    def test_le_verdict_refuse_de_nommer_une_cause_non_imputable(self):
        a = attribuer_tout([self._d(action=Action.LONG_MAKER_IN, fee_bps=7.0,
                                    net_bps=13.0, status=UPPER_BOUND)], 7.0)
        self.assertIn("NON IMPUTABLE", a.verdict())

    def test_le_verdict_designe_le_modele_quand_le_cout_est_conforme(self):
        a = attribuer_tout([self._d(predicted_bps=8.0, net_bps=-2.0,
                                    gross_bps=8.0)] * 30, 10.0)
        self.assertIn("MODELE", a.verdict())


class TestTemoinAdaptation(unittest.TestCase):
    """LE TEST CENTRAL : l'adaptation se voit-elle dans le comportement ?"""

    def setUp(self):
        # Signal vivant sur la premiere moitie, mort sur la seconde.
        self.rows = _marche(12_000, seed=4, duree_signal=6_000)
        self.ech = _echantillons(self.rows)
        self.assertGreater(len(self.ech), 300)

    def _gelee(self):
        """Ajustee une fois sur le debut, puis jamais revue."""
        cut = split_index(len(self.ech), 0.5)
        appr, test = self.ech[:cut], self.ech[cut:]
        _, av, _ = select_features([(s.state, s.nets) for s in appr],
                                   FEATURES, TAKER_ACTIONS)
        self.assertIsNotNone(av, "le signal initial doit etre trouvable")
        return run_policy(test, av, TAKER_ACTIONS, 1_000.0)

    def test_la_politique_gelee_continue_de_trader_apres_la_mort_du_signal(self):
        s = self._gelee().summary(EXECUTABLE)
        self.assertGreater(s["trades"], 0,
                           "la gelee devrait continuer a trader")

    def test_l_adaptation_trade_moins_que_la_politique_gelee(self):
        gelee = self._gelee().summary(EXECUTABLE)
        ada = parcourir(self.ech, FEATURES, TAKER_ACTIONS, n_segments=6,
                        part_apprentissage=0.5, capital_usd=1_000.0,
                        cout_attendu_bps=10.0)
        a = ada.registre.summary(EXECUTABLE)
        self.assertLess(a["trades"], gelee["trades"],
                        "l'adaptation ne reduit pas l'activite : "
                        "le retour d'experience ne change rien")

    def test_la_confiance_finit_plus_bas_qu_elle_n_a_commence(self):
        ada = parcourir(self.ech, FEATURES, TAKER_ACTIONS, n_segments=6,
                        part_apprentissage=0.5, capital_usd=1_000.0,
                        cout_attendu_bps=10.0)
        self.assertLess(ada.confiance_finale(), CONFIANCE_MAX)

    def test_l_attribution_est_produite_et_recompose(self):
        ada = parcourir(self.ech, FEATURES, TAKER_ACTIONS, n_segments=6,
                        part_apprentissage=0.5, capital_usd=1_000.0,
                        cout_attendu_bps=10.0)
        self.assertIsNotNone(ada.attribution)
        for e in ada.attribution.ecarts:
            self.assertAlmostEqual(sum(e.postes.values()), e.total_bps,
                                   places=9)


class TestLeLevierQuiManquait(unittest.TestCase):
    """Le retrecissement seul ne peut pas arreter une politique.

    Ces deux tests existent parce que la premiere version de l'adaptation
    ne les avait pas. Sur le panneau reel, sa confiance est tombee d'un
    facteur cent — 1,000 puis 0,010 — pendant que le nombre de trades ne
    bougeait pas : 21, 25, 26, 32, 17, 10, 18, 29. Le mecanisme etait inerte
    et rien ne le disait.
    """

    def test_retrecir_ne_change_jamais_le_signe(self):
        """La demonstration arithmetique du defaut, figee ici pour de bon."""
        from prism_v2.policy.value import fit
        ech = [({"spread_bps": float(i % 3)}, {Action.LONG_TAKER: 40.0})
               for i in range(600)]
        for prior in (1.0, 50.0, 5_000.0, 1e9):
            av = fit(ech, ["spread_bps"], prior_n=prior)
            v = av.value({"spread_bps": 1.0}, Action.LONG_TAKER)
            self.assertGreater(v, 0.0, "retrecir a rendu la valeur negative ?")
            a, _ = av.best({"spread_bps": 1.0}, TAKER_ACTIONS, seuil_bps=0.0)
            self.assertIs(a, Action.LONG_TAKER,
                          f"prior={prior} : la cellule reste positive, donc "
                          "elle franchit encore un seuil de zero")
        # Et c'est la BARRE, non le prior, qui arrete la politique.
        av = fit(ech, ["spread_bps"], prior_n=1.0)
        a, _ = av.best({"spread_bps": 1.0}, TAKER_ACTIONS, seuil_bps=1_000.0)
        self.assertIs(a, Action.NO_TRADE)

    def test_apres_deception_severe_la_politique_cesse_de_trader(self):
        """Le comportement, pas le journal : des segments a zero trade."""
        rows = _marche(12_000, seed=4, duree_signal=6_000)
        ech = _echantillons(rows)
        ada = parcourir(ech, FEATURES, TAKER_ACTIONS, n_segments=6,
                        part_apprentissage=0.5, capital_usd=1_000.0,
                        cout_attendu_bps=10.0)
        joues = [s for s in ada.segments if s.n_decisions > 0]
        self.assertTrue(joues)
        self.assertTrue(any(s.trades == 0 for s in joues[1:]),
                        "aucun segment ne s'arrete : l'adaptation est inerte")

    def test_la_barre_monte_quand_la_politique_surestime(self):
        rows = _marche(12_000, seed=4, duree_signal=6_000)
        ada = parcourir(_echantillons(rows), FEATURES, TAKER_ACTIONS,
                        n_segments=6, part_apprentissage=0.5,
                        capital_usd=1_000.0, cout_attendu_bps=10.0)
        seuils = [s.seuil_entree_bps for s in ada.segments]
        self.assertEqual(seuils[0], 0.0, "aucun biais connu au depart")
        self.assertTrue(any(x > 0.0 for x in seuils[1:]),
                        "la barre n'a jamais monte malgre la deception")

    def test_une_barre_ne_descend_jamais_sous_zero(self):
        """Se croire modeste n'autorise pas a trader une valeur negative."""
        rows = _marche(9_000, seed=9, duree_signal=9_000)
        ada = parcourir(_echantillons(rows), FEATURES, TAKER_ACTIONS,
                        n_segments=5, part_apprentissage=0.4,
                        capital_usd=1_000.0, cout_attendu_bps=10.0)
        for s in ada.segments:
            self.assertGreaterEqual(s.seuil_entree_bps, 0.0)


class TestMarcheEnAvantIntegrite(unittest.TestCase):
    """Un segment peut-il lire son propre futur ?"""

    def test_chaque_segment_s_ajuste_uniquement_sur_ce_qui_le_precede(self):
        ech = _echantillons(_marche(9_000, seed=9, duree_signal=9_000))
        ada = parcourir(ech, FEATURES, TAKER_ACTIONS, n_segments=5,
                        part_apprentissage=0.4, capital_usd=1_000.0,
                        cout_attendu_bps=10.0)
        # La taille d'apprentissage doit croitre strictement : chaque segment
        # apprend de tout le passe, et d'aucun futur.
        tailles = [s.n_apprentissage for s in ada.segments]
        self.assertEqual(tailles, sorted(tailles))
        self.assertTrue(all(a < b for a, b in zip(tailles, tailles[1:])))

    def test_aucune_decision_n_est_jouee_deux_fois(self):
        ech = _echantillons(_marche(9_000, seed=9, duree_signal=9_000))
        ada = parcourir(ech, FEATURES, TAKER_ACTIONS, n_segments=5,
                        part_apprentissage=0.4, capital_usd=1_000.0,
                        cout_attendu_bps=10.0)
        cles = [(d.ts_ms, d.instrument) for d in ada.registre.decisions]
        self.assertEqual(len(cles), len(set(cles)))

    def test_un_echantillon_trop_petit_est_refuse(self):
        with self.assertRaises(ValueError):
            parcourir([], FEATURES, TAKER_ACTIONS, 3, 0.5, 1_000.0, 10.0)


if __name__ == "__main__":
    unittest.main()
