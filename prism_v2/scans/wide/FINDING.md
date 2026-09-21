# Balayage large sans famille — l'univers OKX entier, 0 survivant

**Statut : la capture de prix accessible reste à signe négatif, mesurée hors
famille, sur l'univers complet.**

## Pourquoi ce balayage existe

La consigne : abandonner le test famille-par-famille et poser une seule
question à *tout* l'univers atteignable — « existe-t-il, dans la série de prix
d'un instrument, une structure capturable dont le brut dépasse le coût d'un
aller-retour ? » Pas un indicateur choisi (« pas des patterns ») : un moment
mesuré, l'autocorrélation de lag 1 des rendements horaires, soumis à une
exécution réelle.

## Méthode (scripts versionnés ici, Node, endpoints publics, aucun ordre)

1. `screen_naive.mjs` — proxy brut `|rho| * sigma` contre coût réel
   (demi-spread + 2 frais taker). Sert à montrer le PIÈGE, pas à conclure.
2. `falsify.mjs` — le crible : signal décidé sur 60 % d'apprentissage, appliqué
   hors échantillon sur 40 %, coût payé à *chaque* barre, net et t mesurés.
3. `screen.mjs` — le balayage de production : falsification INTÉGRÉE. Un
   « survivant » = net moyen > 0 ET t_net > 3 hors échantillon. Rien d'autre
   n'est retenu.

## Le piège, démontré avant d'être évité

Le balayage naïf a signalé 4 « pépites » sur le top-40 :

| instrument | rho | brut proxy | net proxy |
|---|---|---|---|
| IOST-USDT-SWAP | +0,284 | 84 bps | +72,9 bps |
| ZAMA-USDT-SWAP | −0,211 | 50 bps | +38,6 bps |
| ZETA-USDT-SWAP | +0,194 | 50 bps | +29,2 bps |
| MUBARAK-USDT-SWAP | +0,177 | 26 bps | +9,6 bps |

Passées au crible d'une exécution réaliste, hors échantillon :

| instrument | net/trade | t_net | verdict |
|---|---|---|---|
| IOST | −4,16 bps | −0,34 | MORT |
| ZETA | −68,58 bps | −1,85 | MORT (rho change de signe train→test) |
| MUBARAK | −34,55 bps | −2,06 | MORT |
| ZAMA | +7,86 bps | +0,24 | non significatif |
| OFC | +96,57 bps | +2,37 | non significatif (bruit illiquide) |

Le rho de ces alts vient du rebond bid-ask et des prix périmés : le trade au
close est fictif, et l'exécution réelle paie le spread pour capturer le spread.

## Résultat sur l'univers complet

`screen.mjs` exécuté en 4 tranches parallèles (sous-agents) sur l'univers OKX
SWAP trié par volume :

| tranche | instruments mesurés | survivants |
|---|---|---|
| [0, 125) | 124 | 0 |
| [125, 250) | 125 | 0 |
| [250, 375) | 123 | 0 |
| [375, 520) | 106 | 0 |
| **total** | **478** | **0** |

Zéro cellule (instrument × structure) survit à une exécution réelle hors
échantillon. Les instruments les plus liquides (top-40) donnent zéro ; la queue
illiquide est structurellement pire — coûts plus hauts, prix plus périmés.

## Ce que cela établit, et ce que cela n'établit pas

ÉTABLIT : sur l'univers de perpétuels OKX accessible en données publiques au
tarif retail, une structure d'autocorrélation à l'horizon 1 h ne produit aucun
net positif robuste. C'est la mesure la plus LARGE du projet, et elle est
cohérente avec toutes les fermetures par famille.

N'ÉTABLIT PAS : que toute inefficience est morte à tout horizon sur toute
venue. Ce balayage teste un horizon (1 h), une structure (autocorrélation
lag 1), une venue (OKX). Les avenues restant ouvertes exigent ce que ce compte
n'a pas — latence, information, position du côté de la venue — et non un
balayage plus large du même type.

## Reproduire

    cd prism_v2/scans/wide
    node screen.mjs 0 520      # balayage complet, un seul processus
    node falsify.mjs           # le crible sur les leads du naïf
