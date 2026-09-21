"""La resolution AUTORITAIRE du taux taker, et son refus de deviner.

subsidy_fills.py deduisait la categorie tarifaire par mots-cles ; 5 a 6
marches sur 10 restaient sans categorie, et le net du portefeuille restait
INCONNU faute d'un cout chiffrable. La venue publie pourtant `feeType` par
marche. Ces tests figent la table (bareme V2 verifie) et le principe : une
categorie non reconnue rend None, jamais zero.
"""
import unittest

from prism_v2.subsidy.fee_type import FEE_TYPE_RATES, rate_for


class TestResolution(unittest.TestCase):

    def test_marche_sans_frais_rend_zero(self):
        """feesEnabled False = marche geopolitique sans frais taker."""
        self.assertEqual(rate_for(None, False), 0.0)
        self.assertEqual(rate_for("politics_fees", False), 0.0)  # False prime

    def test_feetype_absent_rend_zero(self):
        self.assertEqual(rate_for(None, None), 0.0)
        self.assertEqual(rate_for("", True), 0.0)

    def test_categorie_connue_rend_son_taux(self):
        self.assertEqual(rate_for("politics_fees", True), 0.04)
        self.assertEqual(rate_for("crypto_fees_v2", True), 0.07)
        self.assertEqual(rate_for("sports_fees_v2", True), 0.03)

    def test_categorie_inconnue_rend_none_jamais_zero(self):
        """Le principe qui a manque : deviner un cout nul est interdit."""
        self.assertIsNone(rate_for("licorne_fees", True))

    def test_le_bareme_reconcilie_la_grille_publique(self):
        """taux 0,04 a p=0,50 -> 1,00 $/100 parts, comme annonce."""
        rate = FEE_TYPE_RATES["politics_fees"]
        fee_100_shares = 100 * rate * 0.5 * 0.5
        self.assertAlmostEqual(fee_100_shares, 1.00)
        # crypto : 0,07 -> 1,75 $/100 parts
        self.assertAlmostEqual(100 * FEE_TYPE_RATES["crypto_fees_v2"] * 0.25,
                               1.75)

    def test_sports_est_bien_a_0_03(self):
        """Bareme V2 : sports abaisse a 0,03 (etait 0,05 avant correction)."""
        self.assertEqual(FEE_TYPE_RATES["sports_fees_v2"], 0.03)
        self.assertEqual(FEE_TYPE_RATES["sports_fees_v3"], 0.03)


if __name__ == "__main__":
    unittest.main()
