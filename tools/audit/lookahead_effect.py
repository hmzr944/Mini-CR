"""Contenu informationnel du look-ahead 4H (rapport section D.1).

Compare la porte directionnelle `ema20_4h > ema50_4h` telle qu'elle est livree
(qui voit jusqu'a 3 h en avant) a sa version causale, et mesure le rendement
de la barre SUIVANTE conditionnellement a chacune.

Si la fuite etait sans effet, les deux rendements seraient identiques.
"""
import glob, os, statistics as st, sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prism import strategy as S              # noqa: E402
from tools.causal_htf import causal_htf_ema  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    files = sorted(glob.glob(str(ROOT / "backtest_data" / "*_13m.csv")))
    if not files:
        print("aucune bougie dans backtest_data/ — voir tools/audit/README.md")
        return
    print(f"{len(files)} symboles\n")
    print(f"{'symbole':12s} {'desaccord%':>10s} {'rdt h+1 | fuite':>16s} "
          f"{'rdt h+1 | causal':>17s} {'edge de fuite':>14s}")
    d_all, l_all, c_all = [], [], []
    for f in files:
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        shipped = S.compute_indicators(df.copy())
        leaked = shipped["ema20_4h"] > shipped["ema50_4h"]
        e20, e50 = causal_htf_ema(df["close"], "4h", (20, 50), df.index)
        causal = e20 > e50
        fwd = df["close"].shift(-1) / df["close"] - 1.0
        m = fwd.notna() & e20.notna() & e50.notna()
        dis = 100 * (leaked[m] != causal[m]).mean()
        rl, rc = 1e4 * fwd[m & leaked].mean(), 1e4 * fwd[m & causal].mean()
        print(f"{os.path.basename(f).replace('_13m.csv',''):12s} {dis:10.2f} "
              f"{rl:16.3f} {rc:17.3f} {rl - rc:14.3f}")
        d_all.append(dis); l_all.append(rl); c_all.append(rc)
    print(f"\n{'MOYENNE':12s} {st.mean(d_all):10.2f} {st.mean(l_all):16.3f} "
          f"{st.mean(c_all):17.3f} {st.mean(l_all) - st.mean(c_all):14.3f}")
    print("\nrendements en bps. Un edge de fuite positif et de meme signe partout")
    print("signifie que la porte 'sait' quelque chose qu'elle ne peut pas savoir.")


if __name__ == "__main__":
    main()
