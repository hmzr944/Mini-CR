// BALAYAGE LARGE v2 — falsification INTEGREE. Un « lead » = survit hors
// echantillon a une execution reelle, pas un proxy brut positif.
//
// Pour chaque instrument du shard [start,end) de l'univers OKX SWAP trie par
// volume :
//   1. 300 barres 1H, rendements log ;
//   2. rho sur les 60 % d'apprentissage -> regle (momentum si >=0, sinon
//      reversion) ;
//   3. hors echantillon (40 %) : position = regle * signe(rendement
//      precedent), 1 barre, cout reel (demi-spread + 2 taker) paye chaque
//      barre ;
//   4. survivant SI net moyen > 0 ET t_net > 3.
//
// Sortie : JSON compact. Aucun ordre, endpoints publics.
// Usage : node screen_v2.mjs <start> <end>

const UA = { "User-Agent": "curl/8", "Accept": "application/json" };
const TAKER_BPS = 5.0;

async function get(url, tries = 5) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(30000) });
      if (r.status === 429) { await new Promise(s => setTimeout(s, 1200 * (i + 1))); continue; }
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) { if (i === tries - 1) throw e; await new Promise(s => setTimeout(s, 700 * (i + 1))); }
  }
}
const mean = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;
const std = a => { const m = mean(a); return Math.sqrt(mean(a.map(x => (x - m) ** 2))); };
function autocorr1(r) {
  const m = mean(r); let num = 0, den = 0;
  for (let i = 0; i < r.length; i++) den += (r[i] - m) ** 2;
  for (let i = 1; i < r.length; i++) num += (r[i] - m) * (r[i - 1] - m);
  return den > 0 ? num / den : 0;
}

const start = parseInt(process.argv[2] || "0", 10);
const end = parseInt(process.argv[3] || "40", 10);

const tickers = (await get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")).data;
const perps = tickers.map(t => ({ id: t.instId, vol: parseFloat(t.volCcy24h || 0) }))
  .sort((a, b) => b.vol - a.vol).slice(start, end);

let measured = 0;
const survivors = [];
for (const p of perps) {
  try {
    const c = await get(`https://www.okx.com/api/v5/market/candles?instId=${p.id}&bar=1H&limit=300`);
    const closes = (c.data || []).map(k => parseFloat(k[4])).reverse();
    if (closes.length < 150) continue;
    const r = [];
    for (let i = 1; i < closes.length; i++) r.push(Math.log(closes[i] / closes[i - 1]));
    const cut = Math.floor(r.length * 0.6);
    const rhoTr = autocorr1(r.slice(0, cut));
    const rule = rhoTr >= 0 ? +1 : -1;
    const test = r.slice(cut);

    const b = (await get(`https://www.okx.com/api/v5/market/books?instId=${p.id}&sz=1`)).data[0];
    const bid = parseFloat(b.bids[0][0]), ask = parseFloat(b.asks[0][0]);
    const cost = (ask - bid) / ((ask + bid) / 2) * 1e4 + 2 * TAKER_BPS;

    const nets = [];
    for (let i = 1; i < test.length; i++) {
      const pos = rule * Math.sign(test[i - 1]);
      if (pos !== 0) nets.push(pos * test[i] * 1e4 - cost);
    }
    measured++;
    const nMean = mean(nets), s = std(nets);
    const tNet = s > 0 ? nMean / (s / Math.sqrt(nets.length)) : 0;
    if (nMean > 0 && tNet > 3) {
      survivors.push({ id: p.id, rhoTr: +rhoTr.toFixed(3),
        rule: rule > 0 ? "mom" : "rev", netPerTrade: +nMean.toFixed(2),
        tNet: +tNet.toFixed(2), trades: nets.length, costBps: +cost.toFixed(2) });
    }
    await new Promise(s => setTimeout(s, 55));
  } catch (e) { /* saute */ }
}
survivors.sort((a, b) => b.tNet - a.tNet);
console.log(JSON.stringify({ shard: [start, end], measured,
  survivors: survivors.length, detail: survivors }));
