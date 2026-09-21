"""Segmentation IS / OOS VRAI de V33.

Le dernier commit V33 est du 22/07/2026. Tout ce qui precede a pu influencer le
choix des 14 parametres ; rien de ce qui suit ne l'a pu. La coupure n'est donc
pas choisie sur un resultat : elle est datee par git.
"""
import sys, collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
import pandas as pd
import backtest_v33 as B
from prism import strategy as S
from tools.causal_htf import patch

CUT = pd.Timestamp("2026-07-22")

def load(causal):
    S.compute_indicators = ORIG
    if causal: patch(S)
    B.prepare.__globals__['compute_indicators']=S.compute_indicators
    sd={}
    for sym in B.SYMBOLS:
        df=B.load_or_fetch(sym,13,no_fetch=True)
        if df is not None and len(df)>300: sd[sym]=B.prepare(sym,df.copy())
    return sd
ORIG=S.compute_indicators

def stats(tr,label,days):
    if not tr: return print(f"{label:34s} N=0")
    w=[t for t in tr if t["pnl"]>0]
    gl=abs(sum(t["pnl"] for t in tr if t["pnl"]<=0)); gw=sum(t["pnl"] for t in w)
    pnl=sum(t["pnl"] for t in tr)
    print(f"{label:34s} {len(tr):4d} {100*len(w)/len(tr):6.1f} {gw/(gl+1e-9):6.2f} "
          f"{pnl:9.0f} {pnl/days*365:10.0f} {len(tr)/days*365:8.1f}")

for causal in (False, True):
    sd=load(causal)
    btc=sorted(sd["BTC-USDT"]["ts_index"])
    a,z=pd.Timestamp(btc[0]),pd.Timestamp(btc[-1])
    tr,_,_=B.run_backtest(sd,a,z,btc_filter=False,btc_1h_filter=True,
                          asian_filter=True,next_bar_entry=True)
    tag = "CAUSAL" if causal else "LIVRE "
    print(f"\n=== {tag} — fenetre {str(a)[:10]} -> {str(z)[:10]} ===")
    print(f"{'segment':34s} {'N':>4s} {'WR%':>6s} {'PF':>6s} {'PnL EUR':>9s} {'PnL/an':>10s} {'trades/an':>8s}")
    IS=[t for t in tr if pd.Timestamp(t["exit_ts"])< CUT]
    OS=[t for t in tr if pd.Timestamp(t["exit_ts"])>=CUT]
    dI=(CUT-a).days; dO=(z-CUT).days
    stats(tr,f"total ({(z-a).days} j)",(z-a).days)
    stats(IS,f"IS  avant 22/07/26 ({dI} j)",dI)
    stats(OS,f"OOS apres 22/07/26 ({dO} j)",dO)
    if OS:
        c=collections.Counter(t["pattern"] for t in OS)
        pn=collections.defaultdict(float)
        for t in OS: pn[t["pattern"]]+=t["pnl"]
        print("   OOS par pattern : "+"  ".join(f"{k}: N={c[k]} PnL={pn[k]:+.0f}" for k in sorted(c)))
