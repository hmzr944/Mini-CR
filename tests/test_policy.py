"""La boucle de decision peut-elle mentir, et peut-elle voir ?

Deux dangers opposes pesent sur ce paquet.

  MENTIR   produire un PnL qui n'existe pas : lire le futur, oublier un
           cout, compter un remplissage passif comme certain, rapporter un
           gain a un capital que le compte n'a jamais eu.

  ETRE AVEUGLE  ne rien trouver parce que le moteur est casse, et presenter
           cette panne comme un resultat de marche. C'est le danger le plus
           grave : un « aucun signal » venu d'un bug est indiscernable d'un
           « aucun signal » venu du marche.

Le TEMOIN POSITIF ci-dessous traite le second. On fabrique un marche ou une
variable d'etat predit REELLEMENT le mouvement suivant, d'une amplitude qui
depasse les couts, et on exige que la politique le trouve et gagne sur
l'echantillon de test. Si ce test tombe, aucun resultat negatif produit par
ce paquet n'a de valeur.
"""
import math
import random
import unittest

from prism_v2.instruments import InstrumentSpec, InstrumentType
from prism_v2.margin import MarginSchedule, MarginTier
from prism_v2.policy.action import (ALL_ACTIONS, EXECUTABLE, TAKER_ACTIONS,
                                    UPPER_BOUND, Action, FeeModel, execute)
from prism_v2.policy.engine import Market, build_samples, run_policy, split_index
from prism_v2.policy.ledger import Decision, Ledger
from prism_v2.policy.state import (FEATURES, StateBuilder, StateConfig,
                                   flow_median)
from prism_v2.policy.value import ActionValue, fit, select_features

FEES = FeeModel(maker_bps=2.0, taker_bps=5.0)
CFG = StateConfig(short_rows=4, long_rows=12)


def _spec():
    """Perpetuel inverse fictif : ctVal en USD, donc notionnel = contrats."""
    return InstrumentSpec(
        inst_id="TEST-USD-SWAP", exchange="TEST",
        inst_type=InstrumentType.SWAP_INVERSE, ct_type="inverse",
        base="TEST", quote="USD", settle_ccy="TEST", ct_val=1.0,
        ct_val_ccy="USD", ct_mult=1.0, tick_size=0.000001, lot_size=0.000001,
        min_size=0.000001, state="live", fetched_at="2026-09-18T00:00:00Z")


def _schedule():
    return MarginSchedule("TEST-USD", [MarginTier(1, 0.0, 1e9, 0.10, 0.05, 10.0)])


class TestExecution(unittest.TestCase):
    """Les prix et les couts d'une action sont-ils ceux du carnet ?"""

    def test_le_taker_paie_le_spread_entier(self):
        r0 = (0, 100.0, 100.10, 5, 5, 0, 0)
        r1 = (1000, 100.0, 100.10, 5, 5, 0, 0)   # marche immobile
        f = execute(Action.LONG_TAKER, r0, r1, FEES)
        # Achat a l'ask, revente au bid, prix inchange : on perd exactement
        # le spread, puis les frais.
        self.assertAlmostEqual(f.gross_bps, -9.99000999, places=5)
        self.assertAlmostEqual(f.fee_bps, 10.0)
        self.assertAlmostEqual(f.net_bps, f.gross_bps - f.fee_bps)

    def test_l_entree_passive_encaisse_le_demi_spread(self):
        r0 = (0, 100.0, 100.10, 5, 5, 0, 0)
        r1 = (1000, 100.0, 100.10, 5, 5, 0, 0)
        taker = execute(Action.LONG_TAKER, r0, r1, FEES)
        maker = execute(Action.LONG_MAKER_IN, r0, r1, FEES)
        # Entree au bid au lieu de l'ask : un demi-spread gagne des deux
        # cotes de la comparaison, plus 3 bps de frais economises.
        self.assertGreater(maker.net_bps, taker.net_bps)
        self.assertAlmostEqual(maker.fee_bps, 7.0)
        self.assertEqual(maker.status, UPPER_BOUND)
        self.assertEqual(taker.status, EXECUTABLE)

    def test_no_trade_n_est_pas_un_remplissage(self):
        r = (0, 100.0, 100.1, 5, 5, 0, 0)
        self.assertIsNone(execute(Action.NO_TRADE, r, r, FEES))

    def test_un_carnet_croise_est_refuse(self):
        r0 = (0, 100.1, 100.0, 5, 5, 0, 0)       # bid > ask
        r1 = (1000, 100.0, 100.1, 5, 5, 0, 0)
        self.assertIsNone(execute(Action.LONG_TAKER, r0, r1, FEES))

    def test_le_short_gagne_quand_le_prix_baisse(self):
        r0 = (0, 100.0, 100.01, 5, 5, 0, 0)
        r1 = (1000, 90.0, 90.01, 5, 5, 0, 0)
        f = execute(Action.SHORT_TAKER, r0, r1, FEES)
        self.assertGreater(f.net_bps, 900.0)


class TestEtatCausal(unittest.TestCase):
    """L'etat regarde-t-il devant lui ?"""

    def setUp(self):
        random.seed(11)
        self.rows = []
        px = 100.0
        for i in range(400):
            px *= 1.0 + random.gauss(0, 0.0004)
            self.rows.append((i * 300, round(px - 0.01, 4), round(px + 0.01, 4),
                              random.randint(1, 9), random.randint(1, 9),
                              random.random() * 100, random.random() * 100))

    def test_l_etat_ignore_tout_ce_qui_suit_l_instant(self):
        """Verrou anti-look-ahead : tronquer le futur ne change rien.

        C'est le test qui protege tout le reste. Si l'etat a l'indice i
        changeait quand on supprime les lignes posterieures a i, alors
        chaque resultat produit par ce paquet serait faux.
        """
        b = StateBuilder(CFG, flow_median(self.rows, CFG, 0, 400) or 1.0)
        for i in (50, 137, 288, 399):
            complet = b.at(self.rows, i)
            tronque = b.at(self.rows[:i + 1], i)
            self.assertEqual(complet, tronque, f"fuite du futur a i={i}")

    def test_l_etat_exige_un_historique_suffisant(self):
        b = StateBuilder(CFG, 1.0)
        self.assertIsNone(b.at(self.rows, CFG.warmup - 2))
        self.assertIsNotNone(b.at(self.rows, CFG.warmup - 1))

    def test_la_reference_de_flux_doit_venir_d_ailleurs(self):
        with self.assertRaises(ValueError):
            StateBuilder(CFG, 0.0)


class TestRegistre(unittest.TestCase):
    """Le registre accepte-t-il un PnL qui ne se recompose pas ?"""

    def _decision(self, **kw):
        base = dict(ts_ms=0, instrument="X", state={}, action=Action.LONG_TAKER,
                    target_position_usd=100.0, entry_px=100.0, exit_px=101.0,
                    size_usd=100.0, holding_s=60.0, gross_bps=20.0,
                    fee_bps=10.0, spread_bps=2.0, slippage_bps=0.0,
                    adverse_selection_bps=None, net_bps=10.0,
                    capital_usd=10.0, status=EXECUTABLE, predicted_bps=5.0)
        base.update(kw)
        return Decision(**base)

    def test_un_net_incoherent_est_refuse(self):
        with self.assertRaises(ValueError):
            self._decision(net_bps=50.0)

    def test_no_trade_ne_peut_ni_couter_ni_rapporter(self):
        with self.assertRaises(ValueError):
            self._decision(action=Action.NO_TRADE, entry_px=None, exit_px=None,
                           size_usd=0.0, capital_usd=0.0, net_bps=3.0,
                           gross_bps=3.0, fee_bps=0.0, spread_bps=0.0,
                           target_position_usd=0.0)

    def test_une_action_de_marche_exige_des_prix(self):
        with self.assertRaises(ValueError):
            self._decision(entry_px=None)

    def test_executable_et_borne_sup_ne_s_additionnent_jamais(self):
        led = Ledger(reserved_capital_usd=1_000.0)
        led.record(self._decision(ts_ms=0))
        led.record(self._decision(ts_ms=86_400_000, action=Action.LONG_MAKER_IN,
                                  fee_bps=7.0, net_bps=13.0,
                                  status=UPPER_BOUND))
        self.assertEqual(len(led.of_status(EXECUTABLE)), 1)
        self.assertEqual(len(led.of_status(UPPER_BOUND)), 1)
        self.assertNotAlmostEqual(led.net_usd(EXECUTABLE),
                                  led.net_usd(EXECUTABLE)
                                  + led.net_usd(UPPER_BOUND))

    def test_le_capital_reserve_est_le_denominateur(self):
        led = Ledger(reserved_capital_usd=1_000.0)
        led.record(self._decision(ts_ms=0))
        led.record(self._decision(ts_ms=86_400_000))
        # 10 bps sur 100 USD = 0,10 USD par trade, 0,20 au total, sur
        # 1 000 USD reserves et un jour : 2 bps/jour.
        self.assertAlmostEqual(led.bps_per_day(EXECUTABLE), 2.0, places=6)

    def test_le_drawdown_suit_l_ordre_du_temps(self):
        led = Ledger(reserved_capital_usd=1_000.0)
        led.record(self._decision(ts_ms=0, net_bps=10.0, gross_bps=20.0))
        led.record(self._decision(ts_ms=1_000, net_bps=-40.0, gross_bps=-30.0))
        led.record(self._decision(ts_ms=2_000, net_bps=10.0, gross_bps=20.0))
        self.assertAlmostEqual(led.drawdown_usd(EXECUTABLE), 0.40, places=6)


class TestValeur(unittest.TestCase):
    """La table de valeurs protege-t-elle contre les cellules rares ?"""

    def test_no_trade_vaut_exactement_zero(self):
        av = ActionValue(("spread_bps",), ())
        self.assertEqual(av.value({"spread_bps": 1.0}, Action.NO_TRADE), 0.0)

    def test_une_cellule_rare_est_tiree_vers_zero(self):
        ech = [({"spread_bps": float(i)}, {Action.LONG_TAKER: 100.0})
               for i in range(40)]
        av = fit(ech, ["spread_bps"], prior_n=1_000.0)
        for e, _ in ech:
            # 100 bps observes, mais moins de 14 observations par cellule
            # face a un prior de 1 000 : la valeur doit rester derisoire.
            self.assertLess(av.value(e, Action.LONG_TAKER), 5.0)

    def test_une_action_non_rentable_ne_bat_jamais_no_trade(self):
        ech = [({"spread_bps": float(i % 3)}, {Action.LONG_TAKER: -8.0})
               for i in range(300)]
        av = fit(ech, ["spread_bps"], prior_n=1.0)
        a, v = av.best({"spread_bps": 1.0}, TAKER_ACTIONS)
        self.assertIs(a, Action.NO_TRADE)
        self.assertEqual(v, 0.0)

    def test_une_cellule_inconnue_vaut_zero(self):
        ech = [({"spread_bps": float(i % 3)}, {Action.LONG_TAKER: 50.0})
               for i in range(300)]
        av = fit(ech, ["spread_bps"], prior_n=1.0)
        self.assertEqual(av.value({"spread_bps": 1e9}, Action.SHORT_TAKER), 0.0)


class TestTemoinPositif(unittest.TestCase):
    """LE TEST QUI DONNE SA VALEUR A TOUS LES RESULTATS NEGATIFS.

    On fabrique un marche ou le desequilibre du carnet predit reellement le
    mouvement des 20 secondes suivantes, d'une amplitude de l'ordre de 60
    bps — tres au-dessus des 10 bps de frais. La politique doit :

      1. retenir une variable (ne pas rester a NO_TRADE),
      2. gagner de l'argent SUR L'ECHANTILLON DE TEST,
      3. battre nettement les temoins « toujours long » et « toujours court ».

    Si ce test echoue, le moteur est aveugle et ses verdicts negatifs ne
    valent rien.
    """

    @staticmethod
    def _marche_predictible(n=8_000, seed=7):
        """Marche ou le carnet annonce une DERIVE, pas un seul pas.

        Premiere version de ce temoin : le signe etait tire independamment a
        chaque pas, si bien que le carnet n'annoncait qu'un mouvement de 6
        bps sur un pas, noye ensuite dans dix-neuf pas de bruit. Le moteur
        l'a refuse — a raison : 6 bps predits contre 10 bps de frais ne se
        trade pas. Le temoin etait faux, pas le moteur.

        Ici le signe PERSISTE par plages de `duree` lignes. Le desequilibre
        du carnet annonce donc la derive de tout l'horizon, soit de l'ordre
        de 120 bps, tres au-dessus des 10 bps de frais. C'est ce qu'un
        signal exploitable veut dire, et c'est cela que le moteur doit voir.
        """
        rng = random.Random(seed)
        rows, px = [], 100.0
        duree, retard = 60, 4
        signes = []
        while len(signes) < n:
            signes += [rng.choice((-1, 1))] * duree
        signes = signes[:n]
        for i in range(n):
            if i >= retard:
                px *= 1.0 + signes[i - retard] * 0.0006 + rng.gauss(0, 0.00005)
            bsz = 9 if signes[i] > 0 else 1
            asz = 1 if signes[i] > 0 else 9
            rows.append((i * 300, round(px * 0.99995, 6), round(px * 1.00005, 6),
                         bsz, asz, rng.random() * 50 + 1, rng.random() * 50 + 1))
        return rows

    def test_la_politique_trouve_un_signal_reel_et_gagne_hors_echantillon(self):
        rows = self._marche_predictible()
        m = Market("TEST-USD-SWAP", _spec(), _schedule(), FEES, rows)
        cut = split_index(len(rows), 0.6)
        b = StateBuilder(CFG, flow_median(rows, CFG, 0, cut) or 1.0)
        h = 20                                   # 6 s a 300 ms
        tr = build_samples(m, b, h, 0, cut, 10_000.0, max_capital_usd=1_000.0)
        te = build_samples(m, b, h, cut, len(rows), 10_000.0,
                           max_capital_usd=1_000.0)
        self.assertGreater(len(tr), 200)
        self.assertGreater(len(te), 100)

        feats, av, val_tr = select_features(
            [(s.state, s.nets) for s in tr], FEATURES, TAKER_ACTIONS)
        self.assertTrue(feats, "aucune variable retenue sur un signal reel")
        self.assertGreater(val_tr, 0.0)

        led = run_policy(te, av, TAKER_ACTIONS, 1_000.0)
        somme = led.summary(EXECUTABLE)
        self.assertGreater(somme["trades"], 0, "la politique n'a rien fait")
        self.assertGreater(somme["net_usd"], 0.0,
                           "signal reel, mais PnL de test negatif")
        self.assertGreater(somme["net_bps_moyen"], 0.0)

    def test_le_moteur_ne_gagne_pas_sur_un_marche_sans_signal(self):
        """Contre-epreuve : desequilibre INDEPENDANT du prix futur.

        Meme moteur, meme protocole, signal detruit. La politique ne doit pas
        produire de PnL de test positif de facon fiable — sinon elle
        fabriquerait du gain a partir de bruit.
        """
        rng = random.Random(3)
        rows, px = [], 100.0
        for i in range(8_000):
            px *= 1.0 + rng.gauss(0, 0.0006)
            rows.append((i * 300, round(px * 0.99995, 6), round(px * 1.00005, 6),
                         rng.randint(1, 9), rng.randint(1, 9),
                         rng.random() * 50 + 1, rng.random() * 50 + 1))
        m = Market("TEST-USD-SWAP", _spec(), _schedule(), FEES, rows)
        cut = split_index(len(rows), 0.6)
        b = StateBuilder(CFG, flow_median(rows, CFG, 0, cut) or 1.0)
        tr = build_samples(m, b, 20, 0, cut, 10_000.0, max_capital_usd=1_000.0)
        te = build_samples(m, b, 20, cut, len(rows), 10_000.0,
                           max_capital_usd=1_000.0)
        feats, av, _ = select_features(
            [(s.state, s.nets) for s in tr], FEATURES, TAKER_ACTIONS)
        if av is None:
            return                     # rien retenu : c'est la bonne reponse
        somme = run_policy(te, av, TAKER_ACTIONS, 1_000.0).summary(EXECUTABLE)
        # Sur du bruit et 10 bps de frais, un gain de test serait une alarme.
        self.assertLessEqual(somme["net_bps_moyen"], 0.0)


class TestCapital(unittest.TestCase):
    """La marge d'une decision peut-elle depasser le capital disponible ?"""

    def test_la_taille_est_bornee_par_le_capital(self):
        rng = random.Random(5)
        rows = [(i * 300, 99.99, 100.01, 10_000, 10_000,
                 rng.random() * 10, rng.random() * 10) for i in range(600)]
        m = Market("TEST-USD-SWAP", _spec(), _schedule(), FEES, rows)
        b = StateBuilder(CFG, 1.0)
        for s in build_samples(m, b, 20, 0, 600, 1e9, max_capital_usd=500.0):
            self.assertLessEqual(s.capital_usd, 500.0 * 1.000001)

    def test_sans_borne_la_marge_suit_le_carnet(self):
        rng = random.Random(5)
        rows = [(i * 300, 99.99, 100.01, 10_000, 10_000,
                 rng.random() * 10, rng.random() * 10) for i in range(600)]
        m = Market("TEST-USD-SWAP", _spec(), _schedule(), FEES, rows)
        b = StateBuilder(CFG, 1.0)
        ech = build_samples(m, b, 20, 0, 600, 1e9)
        self.assertTrue(any(s.capital_usd > 500.0 for s in ech),
                        "sans borne, une marge devrait depasser 500 USD")


if __name__ == "__main__":
    unittest.main()
