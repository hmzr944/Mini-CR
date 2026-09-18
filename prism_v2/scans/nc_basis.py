"""Le perp OKX d'une action tokenisee revient-il vers son index EXOGENE ?

POURQUOI CETTE QUESTION EST NOUVELLE. Toutes les mesures fermees jusqu'ici
portaient sur des perpetuels crypto : un sous-jacent negociable 24 h/24,
arbitre en continu, dont le carnet est fixe par l'equilibre demi-spread =
selection adverse. Les 45 perpetuels d'ACTIONS, ETF, MATIERES et PRE-IPO
cotes par OKX ne sont pas dans ce regime :

  - leur index n'est PAS le perp lui-meme. `index-components` le donne :
    Hyperliquid_Oracle 25 %, Binance_Index 15 %, Pyth 12,5 %, Kaiko 12,5 %,
    Massive 12,5 %, Ondo 12,5 %, OKX spot 5 %, OKX perp 5 %.
    Le perp OKX pese 5 % de son propre index : l'ecart mesure ici est a 95 %
    un ecart a une reference EXOGENE.
  - leurs demi-spreads sont d'un ordre de grandeur plus serres que ceux des
    alts crypto deja mesures (0,05 a 0,5 bps contre 0,4 a 6,6).

LE PIEGE DE DEVISE, TRAITE EN PREMIER. Le perp est cote en USDT, l'index en
USD. Au moment de l'inventaire, USDT/USD valait 0,99912 : lire la base sans
convertir fabrique une prime de +8,8 bps qui n'existe pas. C'est exactement la
classe d'erreur qui avait produit 1 902 faux survivants. La base est donc

    b = 1e4 * ln( P_usdt * (USDT/USD) / X_usd )

et le facteur de conversion est l'index OKX `USDT-USD`, lu par heure.

CE QUE LA MESURE TESTE, ET CE QU'ELLE NE TESTE PAS. Elle ne teste pas si la
BASE se referme : une base peut se refermer parce que l'INDEX rejoint le perp,
auquel cas le perp ne bouge pas et il n'y a rien a encaisser. Elle teste le
rendement du PERP, seul instrument negociable ici. Un ecart n'a de valeur que
si c'est le perp qui revient.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import os
import statistics as st
from typing import Dict, List, Optional, Sequence, Tuple

HOUR_MS = 3_600_000

#: Taille de barre, en ms, par nom OKX. Toute fenetre temporelle du module est
#: exprimee en BARRES et convertie ici : melanger « 24 » lu comme 24 heures et
#: « 24 » lu comme 24 barres de 5 minutes est la meme classe d'erreur que
#: melanger capital et notionnel.
BAR_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
          "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000}

# ---------------------------------------------------------------- chargement


def load_raw(raw_dir: str, bar: str = "1H") -> Tuple[Dict[str, Dict[int, list]],
                                                     Dict[str, Dict[int, list]],
                                                     Dict[int, float]]:
    """Rend (perp, index, peg). Les cles temps sont des entiers ms."""
    perp: Dict[str, Dict[int, list]] = {}
    index: Dict[str, Dict[int, list]] = {}
    peg: Dict[int, float] = {}
    for fn in sorted(os.listdir(raw_dir)):
        if not fn.endswith(f"_{bar}.json"):
            continue
        sym = fn[2:-len(f"_{bar}.json")]
        with open(os.path.join(raw_dir, fn)) as f:
            rows = {int(k): v for k, v in json.load(f).items()}
        if fn.startswith("P_"):
            perp[sym] = rows
        elif fn.startswith("X_"):
            if sym == "USDT":
                peg = {t: v[3] for t, v in rows.items()}
            else:
                index[sym] = rows
    return perp, index, peg


# ------------------------------------------------------------------- qualite


def staleness(perp_rows: Dict[int, list], bar_ms: int = HOUR_MS
              ) -> Tuple[float, float]:
    """(part de barres a volume nul, part de rendements EXACTEMENT nuls).

    Un prix perime fabrique de la reversion : le retour apparent revient a zero
    non parce que le marche revient mais parce que le prix n'avait pas bouge.
    C'est l'artefact qui avait fait croire a rho = +0,13 sur les stablecoins.
    """
    ts = sorted(perp_rows)
    if len(ts) < 2:
        return 1.0, 1.0
    zero_vol = sum(1 for t in ts if perp_rows[t][4] == 0) / len(ts)
    zr = n = 0
    for a, b in zip(ts, ts[1:]):
        if b - a != bar_ms:
            continue
        n += 1
        if perp_rows[b][3] == perp_rows[a][3]:
            zr += 1
    return zero_vol, (zr / n if n else 1.0)


# ---------------------------------------------------------------------- base


def basis_series(perp_rows: Dict[int, list], idx_rows: Dict[int, list],
                 peg: Dict[int, float]
                 ) -> Dict[int, Tuple[float, float, float, float]]:
    """{t: (cloture perp, base en bps corrigee du peg, OUVERTURE perp)}.

    L'ouverture est conservee parce qu'elle seule fournit un prix d'entree
    honnete. La cloture de la barre qui porte le signal est le DERNIER
    echange de cette barre : entrer a ce prix, c'est supposer qu'on a vendu
    exactement au sommet de la meche qui a declenche le signal. Sur un ecart
    de 6 sigma, cette meche est typiquement une liquidation de quelques
    millisecondes, et personne ne l'obtient. L'ouverture de la barre suivante
    est le premier prix reellement disponible apres coup.
    """
    out: Dict[int, Tuple[float, float, float, float]] = {}
    for t in sorted(set(perp_rows) & set(idx_rows) & set(peg)):
        o = perp_rows[t][0]
        p = perp_rows[t][3]
        x = idx_rows[t][3]
        u = peg[t]
        if p <= 0 or x <= 0 or u <= 0 or o <= 0:
            continue
        vol = perp_rows[t][4] if len(perp_rows[t]) > 4 else 1.0
        out[t] = (p, 1e4 * math.log(p * u / x), o, vol)
    return out


def causal_z(bas: Dict[int, Tuple[float, float, float, float]],
             window_bars: int = 24, bar_ms: int = HOUR_MS) -> Dict[int, float]:
    """Ecart de la base a son niveau persistant, estime UNIQUEMENT sur le passe.

    Le niveau persistant (prime de funding, biais de peg residuel) n'est pas
    negociable : le vendre rapporte le funding, pas une convergence. Ce qui
    peut se refermer est l'ecart a ce niveau. La mediane glissante est bornee
    au passe INCLUS t : b(t) est connu a t.

    La fenetre est tenue TRIEE de facon incrementale. La reconstruire et la
    retrier a chaque barre coute O(n*w log w) : a cinq minutes, sur quatre-
    vingts jours et quarante-cinq instruments, cela ne finit pas.
    """
    ts = sorted(bas)
    z: Dict[int, float] = {}
    win: List[float] = []                  # valeurs de la fenetre, triees
    order: List[Tuple[int, float]] = []    # (t, valeur) dans l'ordre d'arrivee
    head = 0
    need = max(2, window_bars // 2)
    for t in ts:
        v = bas[t][1]
        bisect.insort(win, v)
        order.append((t, v))
        lo = t - window_bars * bar_ms
        while head < len(order) and order[head][0] <= lo:
            old = order[head][1]
            i = bisect.bisect_left(win, old)
            if i < len(win) and win[i] == old:
                win.pop(i)
            head += 1
        n = len(win)
        if n < need:
            continue
        med = win[n // 2] if n % 2 else 0.5 * (win[n // 2 - 1] + win[n // 2])
        z[t] = v - med
    return z


def fwd_return_bps(bas: Dict[int, Tuple[float, float]], t: int, h: int,
                   bar_ms: int = HOUR_MS) -> Optional[float]:
    """Rendement du PERP de t a t+h barres, en bps. None si la barre manque."""
    u = t + h * bar_ms
    if t not in bas or u not in bas:
        return None
    return 1e4 * math.log(bas[u][0] / bas[t][0])


# ------------------------------------------------------------------ stats


def tstat(xs: Sequence[float]) -> Tuple[float, float, float]:
    """(moyenne, erreur-type, t). Echantillon suppose i.i.d. : a l'appelant
    de fournir des observations DISJOINTES."""
    n = len(xs)
    if n < 3:
        return (st.fmean(xs) if xs else 0.0), float("inf"), 0.0
    m = st.fmean(xs)
    sd = st.stdev(xs)
    se = sd / math.sqrt(n)
    return m, se, (m / se if se > 0 else 0.0)


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def benjamini_hochberg(pvals: Sequence[float], q: float = 0.10) -> List[bool]:
    """Vrai pour les tests retenus. Applique a TOUS les tests menes."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    keep = [False] * m
    kmax = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            kmax = rank
    for rank, i in enumerate(order, start=1):
        if rank <= kmax:
            keep[i] = True
    return keep


def session_of(t_ms: int) -> str:
    """Session du sous-jacent AMERICAIN a l'heure t (UTC).

    RTH   13:30-20:00 UTC (EDT) : le marche reel cote.
    EXT   08:00-13:30 et 20:00-24:00 : pre/post marche, liquidite reelle mince.
    DARK  00:00-08:00 en semaine : aucune cotation reguliere.
    WKND  du samedi 00:00 UTC au lundi 08:00 UTC : aucune action ne cote nulle
          part. C'est la seule fenetre ou le sous-jacent est vraiment fige.
    """
    d = dt.datetime.utcfromtimestamp(t_ms / 1000)
    wd, h = d.weekday(), d.hour
    if wd >= 5 or (wd == 0 and h < 8) or (wd == 4 and h >= 24):
        return "WKND"
    if h < 8:
        return "DARK"
    if 13 <= h < 20:
        return "RTH"
    return "EXT"


# ------------------------------------------------------- seuil causal


def causal_sigma(z: Dict[int, float], window_bars: int = 720,
                 min_obs: int = 240, bar_ms: int = HOUR_MS) -> Dict[int, float]:
    """Ecart-type de z estime UNIQUEMENT sur le passe glissant STRICT.

    Prendre l'ecart-type de l'echantillon COMPLET pour fixer le seuil est un
    lookahead : il utilise la dispersion future pour decider aujourd'hui. Sur
    un actif dont la volatilite change, cela seul suffit a fabriquer un edge.

    Somme et somme des carres tenues de facon incrementale : la fenetre vaut
    8 640 barres a cinq minutes, la recalculer a chaque pas ne finit pas.
    """
    ts = sorted(z)
    out: Dict[int, float] = {}
    s1 = s2 = 0.0
    head = 0
    for i, t in enumerate(ts):
        lo = t - window_bars * bar_ms
        while head < i and ts[head] <= lo:
            v = z[ts[head]]
            s1 -= v
            s2 -= v * v
            head += 1
        n = i - head
        if n >= min_obs:
            mean = s1 / n
            out[t] = math.sqrt(max(0.0, s2 / n - mean * mean))
        v = z[t]                  # t n'entre qu'APRES avoir servi : passe STRICT
        s1 += v
        s2 += v * v
    return out


#: Prix utilise pour entrer et sortir.
#:   "close" : cloture de la barre decalee de `lag`. Optimiste : sur la barre
#:             du signal (lag=0) c'est le prix qui a DECLENCHE le signal.
#:   "open"  : ouverture de la barre suivant le signal, decalee de `lag`.
#:             Premier prix reellement disponible apres coup.
FILL_CLOSE = "close"
FILL_OPEN = "open"


def trades(bas: Dict[int, Tuple[float, float, float, float]], z: Dict[int, float],
           sig: Dict[int, float], k: float, h: int, lag: int,
           cost_bps: float, bar_ms: int = HOUR_MS,
           fill: str = FILL_CLOSE) -> List[Tuple[int, float, float]]:
    """Liste de (t_signal, brut_bps, net_bps), blocs DISJOINTS.

    `lag` est le nombre de barres entre la barre qui porte le signal et la
    barre qui porte le prix d'entree. Avec fill="open", l'entree se fait a
    l'OUVERTURE de la barre t+1+lag : lag=0 signifie alors « des la barre
    suivante », ce qui est executable, et non « au prix qui a declenche le
    signal », qui ne l'est pas.
    """
    out: List[Tuple[int, float, float]] = []
    free_at = -(10 ** 18)
    col = 2 if fill == FILL_OPEN else 0
    shift = 1 if fill == FILL_OPEN else 0
    for t in sorted(z):
        if t < free_at or t not in sig or sig[t] <= 0:
            continue
        if abs(z[t]) < k * sig[t]:
            continue
        e = t + (lag + shift) * bar_ms
        x = e + h * bar_ms
        if e not in bas or x not in bas:
            continue
        # Une barre sans echange ne porte pas de prix : son ouverture est la
        # cloture precedente recopiee. Entrer ou sortir dessus mesure une
        # reversion de COTATION, pas de marche. C'est l'artefact qui avait
        # produit rho = +0,13 sur les stablecoins ; il est exclu ici a la
        # barre, pas a l'instrument, parce qu'a 5 minutes toute serie en
        # contient quelques-unes.
        if bas[e][3] <= 0 or bas[x][3] <= 0:
            continue
        s = 1.0 if z[t] > 0 else -1.0
        gross = -s * 1e4 * math.log(bas[x][col] / bas[e][col])
        out.append((t, gross, gross - cost_bps))
        free_at = x
    return out
