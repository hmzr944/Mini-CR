// BALAYAGE LARGE — le net, mesure sur l'univers entier, sans famille choisie.
//
// Idee : abandonner le test famille-par-famille et poser UNE question a chaque
// instrument atteignable : « existe-t-il, dans sa serie de prix, une structure
// capturable dont le brut depasse le cout d'un aller-retour ? »
//
// Structure mesuree (pas un indicateur, un MOMENT) : l'autocorrelation de
// lag 1 des rendements horaires, rho. Un rho negatif = reversion, positif =
// momentum. Le brut capturable par aller-retour d'un tel signal vaut, en ordre
// de grandeur, |rho| * sigma (l'amplitude typique d'un pas, ponderee par la
// part previsible). C'est une BORNE SUPERIEURE du capturable : le vrai edge est
// plus petit. On la compare au cout reel (demi-spread + deux frais taker).
//
// Multiple testing : des centaines d'instruments produisent des rho grands par
// hasard. On garde le t de rho (~ rho*sqrt(N)) et on ne retient que |t| > 3.
//
// Aucun ordre. Endpoints publics OKX. Arguments : startIndex endIndex.

const UA = { "User-Agent": "curl/8", "Accept": "application/json" };
const TAKER_BPS = 5.0;

async function get(url, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(30000) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } catch (e) {
      if (i === tries - 1) throw e;
      await new Promise(s => setTimeout(s, 800 * (i + 1)));
    }
  }
}

function logrets(closes) {
  const r = [];
  for (let i = 1; i < closes.length; i++) {
    if (closes[i] > 0 && closes[i - 1] > 0) r.push(Math.log(closes[i] / closes[i - 1]));
  }
  return r;
}
function mean(a) { return a.reduce((x, y) => x + y, 0) / a.length; }
function std(a) { const m = mean(a); return Math.sqrt(mean(a.map(x => (x - m) ** 2))); }
function autocorr1(r) {
  const m = mean(r), n = r.length;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) den += (r[i] - m) ** 2;
  for (let i = 1; i < n; i++) num += (r[i] - m) * (r[i - 1] - m);
  return den > 0 ? num / den : 0;
}

const start = parseInt(process.argv[2] || "0", 10);
const end = parseInt(process.argv[3] || "40", 10);

const tickers = (await get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")).data;
const perps = tickers
  .map(t => ({ id: t.instId, vol: parseFloat(t.volCcy24h || 0) }))
  .sort((a, b) => b.vol - a.vol)
  .slice(start, end);

const rows = [];
for (const p of perps) {
  try {
    const c = await get(`https://www.okx.com/api/v5/market/candles?instId=${p.id}&bar=1H&limit=300`);
    const closes = (c.data || []).map(k => parseFloat(k[4])).reverse();
    if (closes.length < 100) continue;
    const r = logrets(closes);
    const sigmaBps = std(r) * 1e4;
    const rho = autocorr1(r);
    const t = rho * Math.sqrt(r.length);
    const grossBps = Math.abs(rho) * sigmaBps;          // borne sup capturable

    const b = (await get(`https://www.okx.com/api/v5/market/books?instId=${p.id}&sz=1`)).data[0];
    const bid = parseFloat(b.bids[0][0]), ask = parseFloat(b.asks[0][0]);
    const mid = (bid + ask) / 2;
    const spreadBps = (ask - bid) / mid * 1e4;
    const costBps = spreadBps + 2 * TAKER_BPS;
    const netBps = grossBps - costBps;

    rows.push({ id: p.id, rho: +rho.toFixed(4), t: +t.toFixed(2),
      sigmaBps: +sigmaBps.toFixed(1), grossBps: +grossBps.toFixed(2),
      costBps: +costBps.toFixed(2), netBps: +netBps.toFixed(2),
      signif: Math.abs(t) > 3 });
    await new Promise(s => setTimeout(s, 60));
  } catch (e) { /* instrument saute */ }
}

rows.sort((a, b) => b.netBps - a.netBps);
const signif = rows.filter(r => r.signif);
const posSignif = signif.filter(r => r.netBps > 0);

console.log(JSON.stringify({
  shard: [start, end], measured: rows.length,
  n_signif_rho: signif.length,
  n_net_positive_and_signif: posSignif.length,
  best: rows.slice(0, 8),
  leads: posSignif.slice(0, 10)
}, null, 1));
