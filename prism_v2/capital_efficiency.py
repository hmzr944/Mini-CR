#!/usr/bin/env python3
"""CAPITAL EFFICIENCY — le critere qui reclasse tout ce qui precede.

Jusqu'ici chaque famille etait jugee sur son rendement PAR TRADE : +4 bps,
-3,34 bps, 191 bps/an. Ce critere est le mauvais. Il ne dit pas combien de
capital reste bloque pendant qu'on encaisse, ni combien de fois par jour le
capital peut resservir.

    critere = PnL net attendu / capital immobilise / jour

Une famille a 0,5 bps par aller-retour qui recycle son capital 200 fois par
jour bat une famille a 200 bps qui immobilise son capital un an. Inversement
un carry a 191 bps/an, aussi reel soit-il, ne rend que 0,52 bps par jour,
parce que le capital dort entre les paiements.

CE MODULE NE DEMONTRE RIEN DE NEUF. Il reexprime des mesures deja etablies
ailleurs dans le depot dans l'unite ou l'objectif est formule. Chaque entree
porte donc la SOURCE de son chiffre et son NIVEAU DE PREUVE ; aucune n'est
inventee ici. Une entree dont le rendement est une hypothese est marquee
comme telle et le reste dans la sortie.

ATTENTION A LA TENTATION. Convertir un rendement en bps/jour rend n'importe
quelle famille comparable a n'importe quelle autre, y compris quand la
comparaison n'a pas de sens : une famille dont le capital n'est pas
reellement recyclable au rythme suppose produira ici un chiffre flatteur et
faux. Le champ `recycles_per_day` est donc explicite et classe, jamais
implicite.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DAYS_PER_YEAR = 365.0

# Niveaux de preuve. Identiques a ceux employes dans les notes de recherche :
# on ne cree pas un second vocabulaire.
OBSERVED = "OBSERVE"        # mesure sur donnees, dans ce depot
LITERATURE = "LITTERATURE"  # mesure publiee, methodologie lue
HYPOTHESIS = "HYPOTHESE"    # plausible, non demontre


@dataclass(frozen=True)
class Family:
    """Une famille economique, exprimee en rendement par jour de capital.

    `gross_bps_per_cycle` est le PnL NET DE COUTS d'un cycle complet, en bps
    du capital immobilise pendant ce cycle. `recycles_per_day` est le nombre
    de cycles qu'un meme euro peut effectuer en 24 h.
    """
    name: str
    net_bps_per_cycle: float
    recycles_per_day: float
    evidence: str
    source: str
    note: str = ""

    def bps_per_day(self) -> float:
        return self.net_bps_per_cycle * self.recycles_per_day

    def bps_per_year(self) -> float:
        return self.bps_per_day() * DAYS_PER_YEAR


@dataclass(frozen=True)
class Objective:
    """L'objectif, traduit en seuil mesurable.

    « Beaucoup de gains, peu de capital, horizon court » n'est pas testable.
    Multiplier un capital par `multiple` en `days` jours l'est.
    """
    capital_eur: float
    multiple: float
    days: float

    def required_bps_per_day(self) -> float:
        """Seuil en interet COMPOSE : volontairement le plus indulgent.

        Pour un multiple > 1, le compose exige MOINS par jour que le simple
        (x10 en un an : 63 bps/j en compose, 247 en simple). Je prends donc
        le compose, qui est le plus favorable aux strategies jugees.

        Il l'est meme trop : composer suppose que la capacite suit le
        capital, ce qui est faux pour toute famille de microstructure — le
        carnet n'absorbe pas dix fois plus parce que le compte a grossi. Le
        vrai seuil est donc PLUS HAUT que celui-ci. Aucune famille echouant
        contre cette borne ne peut reussir contre la vraie.
        """
        if self.days <= 0:
            raise ValueError("days doit etre > 0")
        if self.multiple <= 0:
            raise ValueError("multiple doit etre > 0")
        return (self.multiple ** (1.0 / self.days) - 1.0) * 10_000.0

    def ratio_for(self, family: Family) -> float:
        return family.bps_per_day() / self.required_bps_per_day()


def established_families() -> List[Family]:
    """Toutes les familles dont ce depot possede une mesure.

    L'ordre est celui de la decouverte, pas celui du resultat.
    """
    return [
        Family(
            name="carry inverse/lineaire (1x, sans netting)",
            net_bps_per_cycle=191.0 / DAYS_PER_YEAR * 1.0,
            recycles_per_day=1.0,
            evidence=OBSERVED,
            source="prism_v2/CARRY_FINDING.md — 94 j, 15 paires, 8 330 obs",
            note="regle figee en DISCOVERY, rendement lu en HOLDOUT",
        ),
        Family(
            name="carry, netting de marge parfait (20x)",
            net_bps_per_cycle=3826.0 / DAYS_PER_YEAR,
            recycles_per_day=1.0,
            evidence=HYPOTHESIS,
            source="prism_v2/CARRY_FINDING.md — inconnue decisive",
            note="le netting 20x n'est PAS confirme par OKX ; borne haute",
        ),
        Family(
            name="funding inter-venues OKX/Hyperliquid (1x, taker)",
            net_bps_per_cycle=-1.19,
            recycles_per_day=1.0,
            evidence=OBSERVED,
            source="prism_v2/FUNDING_ARB_PROTOCOL.md — 142 actifs, 45 j",
            note="le differentiel persiste (38,7 %/an a 24 h) mais ne couvre "
                 "pas les 39 bps d'aller-retour ; validation 47 % de positifs",
        ),
        Family(
            name="maker OKX (fills passifs)",
            net_bps_per_cycle=-3.34,
            recycles_per_day=1.0,
            evidence=OBSERVED,
            source="prism_v2/MAKER_FINDING.md — 8 465 fills, t=-94,68",
            note="demi-spread +1,40, selection adverse -2,74, frais -2,00",
        ),
        Family(
            name="taker microstructure OKX (reversion)",
            net_bps_per_cycle=-10.35,
            recycles_per_day=1.0,
            evidence=OBSERVED,
            source="prism_v2/EXPERIMENT_REPORT.md — 617 820 evenements",
            note="net a 0 ms de latence et 0 bps de frais : la contrainte "
                 "n'est ni la vitesse ni le cout, c'est l'absence de signal",
        ),
        Family(
            name="sniping longshot <10c (marches de prediction)",
            net_bps_per_cycle=-1930.0,
            recycles_per_day=1.0,
            evidence=LITERATURE,
            source="prism_v2/POLYMARKET_RESEARCH.md — perte 19,3 c par $",
            note="ferme par la litterature, aucune collecte engagee",
        ),
        Family(
            name="maker marche de prediction (jusqu'a resolution)",
            net_bps_per_cycle=-1000.0,
            recycles_per_day=1.0,
            evidence=LITERATURE,
            source="prism_v2/POLYMARKET_RESEARCH.md — Kalshi, makers -10 %",
            note="le markout court est aveugle au vrai risque : la resolution",
        ),
    ]


BENCHMARK = "BENCHMARK"  # PnL realise d'un acteur tiers, pas une simulation


def professional_benchmark() -> Family:
    """HLP (Hyperliquid) — le plafond observable de la categorie extraction.

    HLP est le vehicule le mieux place du marche crypto pour extraire de la
    valeur sans prevision : il fait du market making, il absorbe les
    liquidations en tant que backstop du protocole, il est opere par des
    professionnels, et il deploie 187 M$. Son PnL est realise et verifiable
    on-chain — ce n'est ni un backtest, ni une simulation, ni un README.

    Si une strategie systematique et neutre pouvait rendre beaucoup plus que
    cela, HLP le rendrait. C'est pourquoi ce chiffre sert de plafond et non
    d'objectif.

    On retient la fenetre MENSUELLE perp (4,38 bps/j, 16 %/an). Le chiffre
    « allTime » de 43,9 %/an est ecarte : l'encours a cru d'un ordre de
    grandeur sur la periode, donc rapporter le PnL cumule a un encours moyen
    surestime le rendement. Prendre le chiffre flatteur aurait ete le
    reflexe exactement inverse de celui qu'exige ce depot.
    """
    return Family(
        name="HLP Hyperliquid (extraction professionnelle, 187 M$)",
        net_bps_per_cycle=4.38,
        recycles_per_day=1.0,
        evidence=BENCHMARK,
        source="api.hyperliquid.xyz vaultDetails — perpMonth, 30,4 j realises",
        note="market making + backstop de liquidation, operateurs pro ; "
             "fenetre allTime ecartee car biaisee par la croissance d'encours",
    )


def required_leverage(family: Family, objective: Objective) -> float:
    """Levier necessaire pour qu'une famille atteigne l'objectif.

    Le levier ne cree aucun edge : il multiplie le rendement ET le risque.
    Cette fonction existe pour chiffrer ce que « atteindre l'objectif »
    exigerait reellement, pas pour suggerer d'y recourir.
    """
    bpd = family.bps_per_day()
    if bpd <= 0:
        return float("inf")
    return objective.required_bps_per_day() / bpd


def latency_arb_counterfactual() -> Family:
    """La famille dont la FORME correspondait a l'objectif — et son cout reel.

    Elle est isolee des autres parce qu'elle n'est pas comparable : son
    capital recycle des centaines de fois par jour. C'est la seule rencontree
    dans ce depot qui pouvait arithmetiquement atteindre le seuil. Le chiffre
    ci-dessous n'est pas un rendement observe : c'est le COUT IMPOSE par le
    bareme taker de Polymarket depuis janvier 2026, calcule a partir de la
    formule officielle. Il dit ce que la strategie doit capturer AVANT de
    gagner quoi que ce soit, donc pourquoi elle est fermee.
    """
    return Family(
        name="arbitrage de latence, marches crypto 15 min (post-bareme)",
        net_bps_per_cycle=-polymarket_taker_fee_bps(0.50, 0.07),
        recycles_per_day=200.0,
        evidence=OBSERVED,
        source="docs.polymarket.com/trading/fees — fee = C x rate x p x (1-p)",
        note="frais seuls, avant tout spread et toute selection adverse ; "
             "maximaux exactement a 50/50, la ou l'arbitrage operait",
    )


def polymarket_taker_fee_bps(price: float, fee_rate: float) -> float:
    """Frais taker Polymarket, en bps DU CAPITAL ENGAGE (pas de la part).

        fee_usdc = C x rate x p x (1-p)      pour C parts a p dollars
        capital  = C x p
        => fee / capital = rate x (1-p)

    Le denominateur est le point delicat : rapporter le frais a la valeur
    nominale (1 $ par part) donnerait un chiffre deux fois plus petit a 50 c
    et flatterait la strategie. Le capital reellement immobilise est `p` par
    part, c'est lui qui sert de base.
    """
    if not 0.0 < price < 1.0:
        raise ValueError("prix hors (0,1)")
    if fee_rate < 0.0:
        raise ValueError("fee_rate negatif")
    return fee_rate * (1.0 - price) * 10_000.0


def build_report(objective: Objective) -> dict:
    required = objective.required_bps_per_day()
    rows = []
    for fam in established_families() + [professional_benchmark(),
                                        latency_arb_counterfactual()]:
        rows.append({
            "famille": fam.name,
            "bps_par_jour": round(fam.bps_per_day(), 4),
            "bps_par_an": round(fam.bps_per_year(), 1),
            "recyclages_par_jour": fam.recycles_per_day,
            "ratio_objectif": round(objective.ratio_for(fam), 4),
            "atteint_le_seuil": fam.bps_per_day() >= required,
            "preuve": fam.evidence,
            "source": fam.source,
            "note": fam.note,
            "levier_requis": (None if required_leverage(fam, objective) == float("inf")
                              else round(required_leverage(fam, objective), 1)),
        })
    rows.sort(key=lambda r: -r["bps_par_jour"])
    return {
        "objectif": {
            "capital_eur": objective.capital_eur,
            "multiple": objective.multiple,
            "jours": objective.days,
            "seuil_bps_par_jour": round(required, 2),
        },
        "familles": rows,
        "aucune_famille_atteint_le_seuil":
            not any(r["atteint_le_seuil"] for r in rows),
        "meilleur_ratio": max(r["ratio_objectif"] for r in rows),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--multiple", type=float, default=10.0,
                    help="multiple de capital vise")
    ap.add_argument("--jours", type=float, default=365.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    obj = Objective(args.capital, args.multiple, args.jours)
    rep = build_report(obj)

    if args.json:
        print(json.dumps(rep, indent=1, ensure_ascii=False))
        return 0

    o = rep["objectif"]
    print(f"OBJECTIF  x{o['multiple']:g} en {o['jours']:g} jours "
          f"sur {o['capital_eur']:,.0f} EUR")
    print(f"SEUIL     {o['seuil_bps_par_jour']:.2f} bps/jour "
          f"de capital immobilise\n")
    print(f"{'famille':<52}{'bps/j':>11}{'x objectif':>12}  preuve")
    print("-" * 92)
    for r in rep["familles"]:
        mark = "OK" if r["atteint_le_seuil"] else "--"
        print(f"{r['famille'][:51]:<52}{r['bps_par_jour']:>11.3f}"
              f"{r['ratio_objectif']:>11.4f}x  {r['preuve']:<12}{mark}")
    print()
    if rep["aucune_famille_atteint_le_seuil"]:
        print("AUCUNE famille mesuree n'atteint le seuil.")
        print(f"La meilleure en est a {rep['meilleur_ratio']:.4f}x, "
              "soit un a deux ordres de grandeur en dessous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
