// FALSIFICATION DES LEADS — le rho survit-il a une execution reelle ?
//
// Le balayage a signale des rho significatifs et « net-positifs ». Mais
// grossBps = |rho|*sigma suppose un trade AU close, gratuit. Ici on teste ce
// qu'un trader ferait vraiment :
//   - signal decide sur l'ECHANTILLON D'APPRENTISSAGE (60 %) : momentum si
//     rho_train > 0, reversion sinon ;
//   - applique HORS ECHANTILLON (40 %) : position = regle * signe(rendement
//     precedent), une barre de detention, cout paye a chaque changement de
//     position ;
//   - net par trade, moyenne et t hors echantillon.
//
// Si le rho etait un vrai edge, le net hors echantillon est positif avec un t
// franc. S'il venait du rebond bid-ask ou de prix perimes, il s'effondre.

const UA = { "User-Agent": "curl/8", "Accept": "application/json" };
const TAKER_BPS = 5.0;
const LEADS = ["IOST-USDT-SWAP", "ZAMA-USDT-SWAP", "ZETA-USDT-SWAP",
  "MUBARAK-USDT-SWAP", "ZIL-USDT-SWAP", "OFC-USDT-SWAP"];

async function get(url, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(30000) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) { if (i === tries - 1) throw e; await new Promise(s => setTimeout(s, 800 * (i + 1))); }
  }
}
const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
const std = a => { const m = mean(a); return Math.sqrt(mean(a.map(x => (x - m) ** 2))); };
function autocorr1(r) {
  const m = mean(r); let num = 0, den = 0;
  for (let i = 0; i < r.length; i++) den += (r[i] - m) ** 2;
  for (let i = 1; i < r.length; i++) num += (r[i] - m) * (r[i - 1] - m);
  return den > 0 ? num / den : 0;
}

async function cost_bps(id) {
  const b = (await get(`https://www.okx.com/api/v5/market/books?instId=${id}&sz=1`)).data[0];
  const bid = parseFloat(b.bids[0][0]), ask = parseFloat(b.asks[0][0]);
  const mid = (bid + ask) / 2;
  return (ask - bid) / mid * 1e4 + 2 * TAKER_BPS;
}

console.log("instrument".padEnd(20) + "rho_tr".padStart(8) + "regle".padStart(10) +
  "trades".padStart(8) + "brut/tr".padStart(9) + "net/tr".padStart(9) +
  "t_net".padStart(8) + "  verdict");

for (const id of LEADS) {
  try {
    const c = await get(`https://www.okx.com/api/v5/market/candles?instId=${id}&bar=1H&limit=300`);
    const closes = (c.data || []).map(k => parseFloat(k[4])).reverse();
    if (closes.length < 120) { console.log(id.padEnd(20) + "  donnees insuffisantes"); continue; }
    const r = [];
    for (let i = 1; i < closes.length; i++) r.push(Math.log(closes[i] / closes[i - 1]));
    const cut = Math.floor(r.length * 0.6);
    const train = r.slice(0, cut), test = r.slice(cut);
    const rhoTr = autocorr1(train);
    const rule = rhoTr >= 0 ? +1 : -1;      // +1 momentum, -1 reversion
    const cost = await cost_bps(id);

    // hors echantillon : position sur barre i decide par r[i-1] (test-local)
    const nets = [], gross = [];
    for (let i = 1; i < test.length; i++) {
      const pos = rule * Math.sign(test[i - 1]);
      if (pos === 0) continue;
      const g = pos * test[i] * 1e4;         // brut en bps
      gross.push(g);
      nets.push(g - cost);                    // cout paye chaque barre
    }
    const nMean = mean(nets), nStd = std(nets);
    const tNet = nStd > 0 ? nMean / (nStd / Math.sqrt(nets.length)) : 0;
    const verdict = (tNet > 3 && nMean > 0) ? "SURVIT" :
      (nMean <= 0 ? "MORT (net<=0)" : "non significatif");
    console.log(id.padEnd(20) + rhoTr.toFixed(3).padStart(8) +
      (rule > 0 ? "momentum" : "reversion").padStart(10) +
      String(nets.length).padStart(8) + mean(gross).toFixed(2).padStart(9) +
      nMean.toFixed(2).padStart(9) + tNet.toFixed(2).padStart(8) + "  " + verdict);
    await new Promise(s => setTimeout(s, 80));
  } catch (e) { console.log(id.padEnd(20) + "  erreur: " + e.message); }
}
console.log("\nRappel : le cout est paye a CHAQUE barre (position re-decidee).");
console.log("Un signal reel bat ce cout hors echantillon ; un artefact non.");
