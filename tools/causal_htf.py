"""Correctif du look-ahead 4H/1D de `prism/strategy.compute_indicators`.

FOURNI, PAS APPLIQUE. `prism/strategy.py` conserve son comportement d'origine ;
ce module permet de le corriger explicitement, et de mesurer l'ecart.

LE DEFAUT (audit du 21/09/2026, cf. AUDIT_V33_INDEPENDANT.md section D.1)

    df_4h    = df[["close"]].resample("4h").last().dropna()
    ema20_4h = df_4h["close"].ewm(span=20, adjust=False).mean()
    df["ema20_4h"] = ema20_4h.reindex(df.index, method="ffill")

`resample("4h")` etiquette chaque seau par son DEBUT, alors que `.last()` rend
la cloture de sa DERNIERE heure. Le `ffill` attribue donc a la barre de 00:00
une valeur qui n'est connue qu'a 03:00 : jusqu'a 3 h de look-ahead sur
`ema20_4h` / `ema50_4h`, et jusqu'a 23 h sur `ema50d` / `ema200d`.

Ces series ne sont pas decoratives : `asset_4h_bull = ema20_4h > ema50_4h` est
la condition directionnelle des patterns C, D et MOM, et la base de
`bear_macro` / `bull_macro`.

LE CORRECTIF. Etiqueter chaque seau a l'instant ou sa valeur devient
observable -- debut du seau + (duree - 1 barre) -- avant le `ffill`.

USAGE

    from prism import strategy
    from tools.causal_htf import patch
    patch(strategy)          # a partir d'ici, plus de look-ahead

Note : corriger le look-ahead N'EST PAS suffisant pour rendre V33 valide. Les
14 parametres de la strategie ont ete choisis sur des backtests qui contenaient
la fuite (section D.3), et par un outil qui selectionne sur l'OOS (section D.2).
Le correctif rend le code honnete, pas la calibration valide.
"""
import pandas as pd

#: Pour une resample de duree `rule`, decalage entre l'etiquette du seau et
#: l'instant ou la valeur `.last()` devient observable, en heures.
_OBSERVABLE_LAG_H = {"4h": 3, "1D": 23}


def causal_htf_ema(close: pd.Series, rule: str, spans, index) -> list:
    """EMA d'une serie re-echantillonnee, sans look-ahead.

    `close` est horaire. Le seau etiquete T couvre [T, T+rule) et sa derniere
    cloture horaire tombe a T + rule - 1h : c'est la que sa valeur devient
    connue, et c'est donc la qu'elle est etiquetee.
    """
    lag = _OBSERVABLE_LAG_H[rule]
    buckets = close.resample(rule).last().dropna()
    buckets.index = buckets.index + pd.Timedelta(hours=lag)
    return [buckets.ewm(span=s, adjust=False).mean().reindex(index, method="ffill")
            for s in spans]


def patch(strategy_module):
    """Remplace `compute_indicators` par une version causale. Rend l'originale."""
    original = strategy_module.compute_indicators

    def causal_compute_indicators(df):
        df = original(df)          # tous les indicateurs intra-barre inchanges
        e20_4h, e50_4h = causal_htf_ema(df["close"], "4h", (20, 50), df.index)
        e50_d, e200_d = causal_htf_ema(df["close"], "1D", (50, 200), df.index)
        df["ema20_4h"], df["ema50_4h"] = e20_4h, e50_4h
        df["ema50d"], df["ema200d"] = e50_d, e200_d
        return df

    strategy_module.compute_indicators = causal_compute_indicators
    return original
