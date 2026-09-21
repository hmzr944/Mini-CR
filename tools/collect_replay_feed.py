"""Collecte L1 + tape a ~1 s pour rejouer les detecteurs avec un AVENIR.

Pour mesurer une capture CAUSALE il faut, apres chaque detection, le prix
REELLEMENT executable plus tard. Une photo ne suffit pas : il faut une serie.
"""
import json, time, urllib.request, urllib.parse, os, sys
S=os.path.dirname(os.path.abspath(__file__))
INSTS=["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","XRP-USDT-SWAP",
       "DOGE-USDT-SWAP","ADA-USDT-SWAP","LTC-USDT-SWAP","LINK-USDT-SWAP",
       "AVAX-USDT-SWAP","NEAR-USDT-SWAP","SUI-USDT-SWAP","TRX-USDT-SWAP"]
MINUTES=float(sys.argv[1]) if len(sys.argv)>1 else 14.0
def get(p,**q):
    u="https://www.okx.com"+p+("?"+urllib.parse.urlencode(q) if q else "")
    for _ in range(2):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(u,headers={"User-Agent":"prism/1.0"}),timeout=8))
        except Exception: pass
    return {}
last={i:None for i in INSTS}
out=open(f"{S}/replay_feed.jsonl","w")
deadline=time.time()+MINUTES*60; n=0
while time.time()<deadline:
    for i in INSTS:
        b=(get("/api/v5/market/books",instId=i,sz=5).get("data") or [{}])[0]
        if not b.get("bids") or not b.get("asks"): continue
        t=get("/api/v5/market/trades",instId=i,limit=100).get("data") or []
        fresh=[]
        for r in t:
            if last[i] is not None and r.get("tradeId")==last[i]: break
            fresh.append([int(r["ts"]),float(r["px"]),float(r["sz"]),r["side"]])
        if t: last[i]=t[0].get("tradeId")
        out.write(json.dumps({"ts":int(time.time()*1000),"i":i,
            "b":[[float(x[0]),float(x[1])] for x in b["bids"][:5]],
            "a":[[float(x[0]),float(x[1])] for x in b["asks"][:5]],
            "tr":fresh},separators=(",",":"))+"\n")
        n+=1
    out.flush()
print(f"DONE {n} releves, {len(INSTS)} instruments, {MINUTES:.0f} min",flush=True)
