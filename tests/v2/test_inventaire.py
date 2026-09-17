"""Inventaire de l'existant — empeche de reconstruire ce qui est deja la.

L'audit total a montre que j'avais reconstruit quatre detecteurs sur cinq
dans `mechanism_screen`, et toute la Phase C par-dessus un `ForcedFlowDetector`
existant. Cout : du temps, mais surtout un risque d'incoherence silencieuse
entre deux implementations du meme concept.

Ces tests ne verifient pas un calcul : ils verifient que je SAIS ce que le
depot contient. C'est la classe d'erreur « outil existant mais jamais
utilise », qui m'a aussi coute le WebSocket.
"""
from __future__ import annotations

import unittest
from pathlib import Path

V2 = Path(__file__).resolve().parents[2] / "prism_v2"


class TestOutilsExistants(unittest.TestCase):

    def test_le_client_websocket_existe(self):
        """Mon audit d'architecture a affirme « REST uniquement, aucun
        WebSocket ». C'etait faux, et le delai de publication en REST vaut
        2 434 s contre 1,231 s en WebSocket — un facteur 2 000."""
        ws = V2 / "wsclient.py"
        self.assertTrue(ws.exists())
        src = ws.read_text(encoding="utf-8")
        self.assertIn("RFC 6455", src)

    def test_le_collecteur_websocket_capte_les_trois_canaux(self):
        """Carnets, trades ET liquidations, avec double horodatage."""
        src = (V2 / "ws_collector.py").read_text(encoding="utf-8")
        for canal in ("books", "trades", "liquidation"):
            self.assertIn(canal, src)
        self.assertIn("local_recv_ts", src)

    def test_les_neuf_detecteurs_sont_inventories(self):
        from prism_v2.detectors import ALL_DETECTORS
        noms = {d.family.value if hasattr(d.family, "value") else str(d.family)
                for d in ALL_DETECTORS}
        self.assertEqual(len(ALL_DETECTORS), 9)
        # Les quatre que j'ai reconstruits dans mechanism_screen sans le savoir
        for attendu in ("BOOK_IMBALANCE", "DEPTH_WITHDRAWAL",
                        "AGGRESSIVE_FLOW", "SPREAD_DISLOCATION"):
            self.assertIn(attendu, noms, f"{attendu} doit rester inventorie")
        # Celui sur lequel j'ai reconstruit toute la Phase C
        self.assertIn("FORCED_FLOW", noms)

    def test_le_moteur_edge_hunt_existe_et_orchestre(self):
        """746 lignes jamais executees dans mes sessions : detection sur
        9 familles, replay causal, routeur, papier, memoire."""
        src = (V2 / "edge_hunt.py").read_text(encoding="utf-8")
        for piece in ("ForcedFlowDetector", "CrossMarketDetector",
                      "CapitalRouter", "PaperExecutor", "MarketStateTracker"):
            self.assertIn(piece, src, f"{piece} doit rester orchestre")


class TestLacunesConnues(unittest.TestCase):
    """Ce que l'audit a trouve MANQUANT, et qui doit le rester visible."""

    def test_le_slippage_n_est_jamais_mesure(self):
        """`slippage_observed()` n'est appele nulle part. Tant que c'est
        vrai, TOUT chiffre de cout du depot est une BORNE INFERIEURE, et ce
        test existe pour que ce fait ne se perde pas.

        Le jour ou des fills reels seront mesures, ce test echouera — et sa
        correction consistera a retirer la borne, pas a l'ignorer.
        """
        appels = []
        for p in V2.rglob("*.py"):
            if p.name == "costs.py":
                continue
            src = p.read_text(encoding="utf-8", errors="ignore")
            if "slippage_observed(" in src:
                appels.append(p.name)
        self.assertEqual(
            appels, [],
            "slippage_observed() est desormais appele : les couts ne sont "
            "plus des bornes inferieures, mettre a jour AUDIT_TOTAL.md")

    def test_l_exclusion_de_slippage_est_interdite_en_execution(self):
        """La borne inferieure ne doit jamais servir de feu vert."""
        from prism_v2.costs import is_excluded, slippage_excluded_for_paper_validation
        self.assertTrue(is_excluded(slippage_excluded_for_paper_validation()))

    def test_cross_market_est_la_famille_jamais_mesuree(self):
        """Dislocation du basis : detecteur present et teste, aucune mesure
        de capture. C'est la piste que l'audit designe."""
        from prism_v2.detectors import ALL_DETECTORS
        noms = {d.family.value if hasattr(d.family, "value") else str(d.family)
                for d in ALL_DETECTORS}
        self.assertIn("CROSS_MARKET", noms)
        src = (V2 / "detectors" / "cross_market.py").read_text(encoding="utf-8")
        # Le detecteur mesure un ECART au niveau recent, pas le niveau lui-meme
        self.assertIn("mediane", src.lower())


if __name__ == "__main__":
    unittest.main()
