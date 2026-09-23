# Carry EEA — le premier rendement net positif sur instruments ACCESSIBLES

**Statut : `CANDIDAT — 3 contrats passent tous les filtres declares`**
**Verdict economique : `REEL, LEGAL, MESURE — ET 1,4 % DE L'OBJECTIF`**

Scan : `prism_v2/scans/carry_eea.py`. Aucun ordre, aucune cle.

## Pourquoi ce scan et pas `legal_carry.py`

Le module existant mesure OKX **global**. Un resident francais n'y a pas acces
aux perpetuels : l'entite qui le sert est OKX Europe, dont les X-Perps ne sont
**pas exposes par l'API publique** (486 instruments SWAP verifies, zero
X-Perp). Mesurer le rendement d'un instrument qu'on ne peut pas traiter est
l'erreur que ce depot documente depuis le debut.

## Ce qui a ete verifie avant de mesurer (source primaire)

`eu.support.backpack.exchange` :

| | |
|---|---|
| entite | Trek Labs Europe Ltd, CySEC 273/15 |
| France | **pays supporte** |
| jambes | **spot ET perp** sur le meme compte |
| marge | **cross-margin** : « all eligible assets are used as collateral by default » |
| frais Tier 1 | maker 2 bps / taker 5 bps, **spot ET perp** |
| frais de liquidation | 1 % par fill |

Le cross-margin tranche la question du « facteur 2 » laissee ouverte par
`CARRY_FINDING.md` : le spot collateralise le short, donc le rendement sur
capital ne vaut pas la moitie du rendement sur notionnel. **C'est verifie.**

## Le piege d'unite, mesure et non suppose

`legal_carry.py` annualise en multipliant par 3 x 365 — convention **8 h**
d'OKX. Backpack regle le funding **TOUTES LES HEURES** : intervalle median de
1,0 h mesure sur les horodatages de trois symboles independants.

**Appliquer la convention d'OKX aurait sous-estime d'un facteur 8.** Le scan
mesure donc l'intervalle par symbole ; un symbole dont l'intervalle n'est pas
determinable rend UNKNOWN. Quatre tests figent ce point.

## Resultat

Detention 60 jours, cout des **quatre** traversees (entrer spot, entrer perp,
sortir spot, sortir perp), spreads LUS au carnet.

| perp | vol 24 h | funding %/an | stabilite | cout bps | net 60 j | defavorable |
|---|---|---|---|---|---|---|
| BTC | 185,0 M$ | 8,63 | 1,37 | 20,02 | **+1,22 %** | −0,20 % |
| ETH | 41,3 M$ | 9,50 | 1,95 | 20,07 | **+1,36 %** | −0,20 % |
| BNB | 6,1 M$ | 11,37 | 1,10 | 20,25 | **+1,67 %** | −0,20 % |

Soit **7,4 a 10,1 %/an net**, au-dessus des ~5,5 % rapportes par le tour
precedent — et cette fois sur des instruments qu'un compte francais peut
reellement traiter.

Rejets : SOL et HYPE sur **signe instable** (0,89 et 0,09 — HYPE a un funding
NEGATIF, donc un cout pour un short). Les autres sur liquidite.

## Sensibilite du seuil de liquidite — declaree, non retouchee

`MIN_VOL_USD = 5 M$` a ete fixe AVANT le scan, herite de `legal_carry` ou il
valait 50 M$ pour OKX. Il est mal calibre pour 1 000 EUR : une position de
1 000 EUR sur un marche a 1 M$/jour represente 0,1 % du volume. Ce que le
seuil coute, plutot que de le deplacer en silence :

| seuil | candidats | net moyen |
|---|---|---|
| 5,0 M$ (declare) | 3 — BTC, ETH, BNB | 8,62 %/an |
| 1,0 M$ | 5 — + XRP, DOGE | 8,94 %/an |
| 0,3 M$ | 7 — + LINK, AAVE | 8,47 %/an |

Le rendement ne monte pas en descendant le seuil. Ce qu'un panier plus large
achete, c'est de la **diversification contre l'inversion du funding** — le
seul risque que la stabilite mesure au passe et ne garantit pas au futur.

## Route maker, non retenue comme resultat

Coter au lieu de traverser ferait passer le cout de 20 a 8 bps. Gain : +0,12
point sur 60 jours. **Le fill maker n'est pas garanti** et le scan ne le
suppose pas — c'est note comme une amelioration possible, pas comptee.

## La confrontation qui decide

| | gain sur 1 000 EUR / 60 j | part de l'objectif (+1 000 EUR) |
|---|---|---|
| BTC | 12,20 EUR | **1,22 %** |
| ETH | 13,60 EUR | **1,36 %** |
| BNB | 16,70 EUR | **1,67 %** |
| panier 7 | 13,90 EUR | **1,39 %** |

**Le carry est reel et il rapporte 1,4 % de l'objectif.** Les deux enonces
sont vrais ensemble, et c'est tout le resultat de ce scan.

## Ce que le net ne compte PAS

- inversion future du funding — la stabilite mesure le passe ;
- risque de liquidation (1 %/fill), malgre le cross-margin ;
- risque de contrepartie sur une venue detenant les deux jambes ;
- base spot-perp a la sortie ;
- impot francais sur les plus-values crypto.

## Decision

Le carry passe le filtre economique : **il n'est pas ferme.** Il est
disqualifie comme reponse a l'objectif (facteur 70), et qualifie comme source
de rendement modeste. La suite logique n'est pas une mesure de plus, c'est une
verification de faisabilite d'execution — et elle n'a de sens que si un carry
a 8 %/an interesse, sachant qu'il ne repond pas a la question posee.
