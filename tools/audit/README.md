# Scripts de l'audit indépendant du 21/09/2026

Chaque nombre de `AUDIT_V33_INDEPENDANT.md` est recalculé par l'un de ces
scripts. Aucun n'écrit dans le dépôt, aucun n'émet d'ordre, aucun n'a besoin
de credentials.

| script | section du rapport | ce qu'il recalcule | données requises |
|---|---|---|---|
| `gate_attribution.py` | F.2, F.4 | Rejets par porte des patterns C / D / MOM, avec et sans le look-ahead | `backtest_data/*.csv` (via `backtest_v33.load_or_fetch`) |
| `lookahead_effect.py` | D.1 | Contenu informationnel du look-ahead 4H : désaccord et rendement h+1 conditionnel | idem |
| `carry_measure.py` | E.4, E.5, H.2 | Différentiel de funding, structure de portefeuille, règle `carry_alloc`, économie, capacité, risque de queue | réseau (funding OKX) + `prism_v2/data/candles_1h.json` |
| `variant_sweep.py` | E.2 | Backtest V33 : livré vs causal vs SL-avant-TP vs funding vs sans whitelist | `backtest_data/*.csv` |
| `is_oos_split.py` | **E.2bis** | **Le test qui tranche** : coupure au 22/07/2026, date du dernier commit V33. IS PF = 1,58 / OOS PF = 0,17 | `backtest_data/*.csv` |

Récupérer les bougies au préalable (≈ 35 min, 26 symboles, 13 mois) :

```
python3 -c "import backtest_v33 as B; [B.load_or_fetch(s,13,False) for s in B.SYMBOLS]"
```
