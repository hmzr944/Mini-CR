"""Historique de prix horaire, pour mesurer le coussin de survie.

Les 30 h de donnees tick du depot ne peuvent pas porter un quantile sur une
detention de plusieurs jours. Les bougies historiques d'OKX sont publiques et
remontent loin : c'est la source honnete pour ce calcul.

On prend HAUT et BAS de chaque bougie, pas seulement la cloture : une
liquidation se declenche au plus bas traverse, pas au prix de fin d'heure.
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, '.')
from prism_v2.funding_feed import _http_json, OKX_BASE

INSTS = sys.argv[1].split(",") if len(sys.argv) > 1 else [
    "BTC-USD-SWAP", "BTC-USDT-SWAP", "ETH-USD-SWAP", "ETH-USDT-SWAP",
    "SOL-USD-SWAP", "SOL-USDT-SWAP", "XRP-USD-SWAP", "XRP-USDT-SWAP",
    "DOGE-USD-SWAP", "DOGE-USDT-SWAP", "ADA-USD-SWAP", "ADA-USDT-SWAP",
    "LTC-USD-SWAP", "LTC-USDT-SWAP", "BCH-USD-SWAP", "BCH-USDT-SWAP",
    "LINK-USD-SWAP", "LINK-USDT-SWAP", "ETC-USD-SWAP", "ETC-USDT-SWAP",
    "DOT-USD-SWAP", "DOT-USDT-SWAP", "FIL-USD-SWAP", "FIL-USDT-SWAP",
]
PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 90   # 100 bougies/page

out = {}
for inst in INSTS:
    rows, after = {}, None
    for _ in range(PAGES):
        u = (f"{OKX_BASE}/api/v5/market/history-candles?instId={inst}"
             f"&bar=1H&limit=100")
        if after:
            u += f"&after={after}"
        try:
            d = (_http_json(u).get("data") or [])
        except Exception:
            break
        if not d:
            break
        for r in d:
            try:
                rows[int(r[0])] = (float(r[2]), float(r[3]), float(r[4]))
            except (ValueError, IndexError):
                continue
        nb = min(int(r[0]) for r in d)
        if after and nb >= after:
            break
        after = nb
        time.sleep(0.05)
    if rows:
        out[inst] = [[t, *rows[t]] for t in sorted(rows)]
        span = (max(rows) - min(rows)) / 86_400_000
        print(f"{inst:<18}{len(rows):>7} bougies  {span:>6.0f} jours", flush=True)

p = Path("prism_v2/data/candles_1h.json")
p.write_text(json.dumps({"bar": "1H", "fields": ["ts", "high", "low", "close"],
                         "source": "OKX /api/v5/market/history-candles",
                         "data": out}, separators=(",", ":")))
print(f"\n{len(out)} instruments, {p.stat().st_size/1e6:.1f} Mo")
