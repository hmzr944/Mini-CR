"""Full variant sweep. Each row differs from the previous by ONE named change."""
import sys, json, collections, importlib.util, re, tempfile, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pandas as pd
from prism import strategy as S
from tools import causal_htf as causal

ORIG_CI = S.compute_indicators

_TP_FIRST = """            if side == "long":
                if hi >= tp and not pos.get("trailing"):
                    exit_price, exit_reason = tp, "take_profit"
                elif lo <= sl:
                    exit_reason = "trail_stop" if pos.get("trailing") else "stop_loss"
                    exit_price  = sl
            else:
                if lo <= tp and not pos.get("trailing"):
                    exit_price, exit_reason = tp, "take_profit"
                elif hi >= sl:
                    exit_reason = "trail_stop" if pos.get("trailing") else "stop_loss"
                    exit_price  = sl"""

_SL_FIRST = """            if side == "long":
                if lo <= sl:
                    exit_reason = "trail_stop" if pos.get("trailing") else "stop_loss"
                    exit_price  = sl
                elif hi >= tp and not pos.get("trailing"):
                    exit_price, exit_reason = tp, "take_profit"
            else:
                if hi >= sl:
                    exit_reason = "trail_stop" if pos.get("trailing") else "stop_loss"
                    exit_price  = sl
                elif lo <= tp and not pos.get("trailing"):
                    exit_price, exit_reason = tp, "take_profit\""""


def _make_slfirst():
    """Copie de backtest_v33.py ou le SL est teste AVANT le TP.

    Le moteur livre teste le TP d'abord ; quand une barre couvre les deux, il
    accorde le TP. Avec RR = 4 le TP est 4x plus loin que le SL, donc une telle
    barre a bien plus probablement touche le SL en premier. Cette variante
    borne l'effet par l'hypothese inverse.
    """
    src = (ROOT / "backtest_v33.py").read_text()
    if src.count(_TP_FIRST) != 1:
        raise SystemExit("backtest_v33.py a change : bloc de sortie introuvable")
    path = os.path.join(tempfile.mkdtemp(), "backtest_v33_slfirst.py")
    open(path, "w").write(src.replace(_TP_FIRST, _SL_FIRST))
    return path

def get_engine(slfirst):
    if not slfirst:
        import backtest_v33 as B; return B
    spec = importlib.util.spec_from_file_location("bt_slfirst", _make_slfirst())
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    # la copie vit dans un tempdir : la reramener sur les donnees du depot
    m.BASE_DIR = ROOT
    m.DATA_DIR = ROOT / "backtest_data"
    m.RESULT_DIR = ROOT / "backtest_results"
    return m

def load(B, causal_mode):
    S.compute_indicators = ORIG_CI
    if causal_mode: causal.patch(S)
    B.prepare.__globals__['compute_indicators'] = S.compute_indicators
    sd={}
    for sym in B.SYMBOLS:
        df=B.load_or_fetch(sym,13,no_fetch=True)
        if df is not None and len(df)>300: sd[sym]=B.prepare(sym,df.copy())
    return sd

def metrics(trades,curve,eq,label,B,funding_bps_8h=0.0):
    pnl_adj=0.0
    for t in trades:
        h=(pd.Timestamp(t["exit_ts"])-pd.Timestamp(t["entry_ts"])).total_seconds()/3600
        notional=t["margin"]*t["leverage"]
        pnl_adj -= notional*(funding_bps_8h/1e4)*(h/8.0)     # paid by the long side
    if not trades: return dict(label=label,N=0,WR=float('nan'),PF=float('nan'),pnl=0.,equity=eq,maxdd=0.,ret=0.)
    w=[t for t in trades if t["pnl"]>0]
    gl=abs(sum(t["pnl"] for t in trades if t["pnl"]<=0)); gw=sum(t["pnl"] for t in w)
    e=[c["equity"] for c in curve] or [eq]; peak=-1e18; dd=0.
    for x in e: peak=max(peak,x); dd=max(dd,(peak-x)/peak if peak>0 else 0)
    tot=sum(t["pnl"] for t in trades)+pnl_adj
    return dict(label=label,N=len(trades),WR=100*len(w)/len(trades),PF=gw/(gl+1e-9),
                pnl=tot,equity=B.INITIAL_CAPITAL+tot,maxdd=100*dd,
                ret=100*((B.INITIAL_CAPITAL+tot)/B.INITIAL_CAPITAL-1),trades=trades)

def run(B,sd,label,funding=0.0,**kw):
    btc=sorted(sd["BTC-USDT"]["ts_index"]); a,b=pd.Timestamp(btc[0]),pd.Timestamp(btc[-1])
    base=dict(btc_filter=False,btc_1h_filter=True,asian_filter=True,next_bar_entry=True); base.update(kw)
    tr,cv,eq=B.run_backtest(sd,a,b,**base)
    return metrics(tr,cv,eq,label,B,funding)

def show(rows):
    print(f"\n{'variant':50s} {'N':>5s} {'WR%':>6s} {'PF':>6s} {'PnL EUR':>10s} {'equity':>9s} {'maxDD%':>7s} {'ret%':>9s}")
    print("-"*112)
    for m in rows:
        print(f"{m['label']:50s} {m['N']:5d} {m['WR']:6.1f} {m['PF']:6.2f} {m['pnl']:10.0f} "
              f"{m['equity']:9.0f} {m['maxdd']:7.1f} {m['ret']:9.1f}")

if __name__=="__main__":
    out=[]
    B=get_engine(False)
    sd0=load(B,False); print(f"loaded {len(sd0)} symbols, {len(sd0['BTC-USDT']['ts_index'])} bars")
    out.append(run(B,sd0,"A. shipped engine (as-is)"))
    sd1=load(B,True)
    out.append(run(B,sd1,"B. A + 4H/1D look-ahead removed"))
    BP=get_engine(True)
    sdp=load(BP,True)
    out.append(run(BP,sdp,"C. B + SL-before-TP on same bar"))
    out.append(run(BP,sdp,"D. C + funding 1bp/8h on notional",funding=1.0))
    allx=set(B.SYMBOLS)
    oW,oC,oS=BP.PATTERN_D_WHITELIST,BP.PATTERN_C_BLACKLIST,BP.PATTERN_S_BLACKLIST
    BP.PATTERN_D_WHITELIST=allx; BP.PATTERN_D_BULL_EXTRA=allx
    BP.PATTERN_C_BLACKLIST=set(); BP.PATTERN_S_BLACKLIST=set()
    out.append(run(BP,sdp,"E. D + post-hoc whitelist/blacklist removed",funding=1.0,c_blacklist=set()))
    BP.PATTERN_D_WHITELIST,BP.PATTERN_C_BLACKLIST,BP.PATTERN_S_BLACKLIST=oW,oC,oS; BP.PATTERN_D_BULL_EXTRA=oW
    show(out)
    for m in out:
        c=collections.Counter(t["pattern"] for t in m.get("trades",[]))
        p=collections.defaultdict(float)
        for t in m.get("trades",[]): p[t["pattern"]]+=t["pnl"]
        print(f"\n{m['label']}\n   "+"  ".join(f"{k}: N={c[k]} PnL={p[k]:+.0f}" for k in sorted(c)))
    json.dump([{k:v for k,v in m.items() if k!="trades"} for m in out],
              open("/tmp/variant_sweep.json","w"),indent=1)
