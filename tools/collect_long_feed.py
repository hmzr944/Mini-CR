"""Collecte longue pour le test directionnel de BOOK_IMBALANCE.

6 instruments, parallele, ~3 s, 62 minutes -> ~1 200 releves/instrument.
Permet une coupure IS/OOS temporelle avec ~31 min de chaque cote.
"""
import json, time, urllib.request, urllib.parse, os, sys
from concurrent.futures import ThreadPoolExecutor
S=os.path.dirname(os.path.abspath(__file__))
INSTS=["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP",
       "XRP-USDT-SWAP","DOGE-USDT-SWAP","ADA-USDT-SWAP"]
MIN=float(sys.argv[1]) if len(sys.argv)>1 else 62.0
def get(p,**q):
    u="https://www.okx.com"+p+("?"+urllib.parse.urlencode(q) if q else "")
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(u,headers={"User-Agent":"prism/1.0"}),timeout=5))
    except Exception: return {}
last={i:None for i in INSTS}
def snap(i):
    b=(get("/api/v5/market/books",instId=i,sz=5).get("data") or [{}])[0]
    if not b.get("bids") or not b.get("asks"): return None
    t=get("/api/v5/market/trades",instId=i,limit=100).get("data") or []
    fresh=[]
    for r in t:
        if last[i] is not None and r.get("tradeId")==last[i]: break
        fresh.append([int(r["ts"]),float(r["px"]),float(r["sz"]),r["side"]])
    if t: last[i]=t[0].get("tradeId")
    return {"ts":int(time.time()*1000),"i":i,
            "b":[[float(x[0]),float(x[1])] for x in b["bids"][:5]],
            "a":[[float(x[0]),float(x[1])] for x in b["asks"][:5]],"tr":fresh}
out=open(f"{S}/long_feed.jsonl","w"); n=0
deadline=time.time()+MIN*60
with ThreadPoolExecutor(max_workers=len(INSTS)) as ex:
    while time.time()<deadline:
        t0=time.time()
        for r in ex.map(snap, INSTS):
            if r: out.write(json.dumps(r,separators=(",",":"))+"\n"); n+=1
        out.flush()
        time.sleep(max(0.0, 2.5-(time.time()-t0)))
print(f"DONE {n} releves, {len(INSTS)} instruments, {MIN:.0f} min",flush=True)
