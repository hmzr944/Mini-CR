# L'univers non-crypto : 45 perpétuels d'actions tokenisées, mesurés

Question posée : PRISM n'avait jamais mesuré que des perpétuels **crypto** —
sous-jacent négociable 24 h/24, arbitré en continu, carnet fixé par
l'équilibre *demi-spread = sélection adverse*. Le mandat dit que le marché est
une variable. **Existe-t-il, dans un régime différent, un écart mesurable qui
dépasse enfin son coût ?**

Rien de ce document n'est extrapolé. Chaque nombre est reproduit par un script
de `prism_v2/scans/`.

---

## 1. Ce que cet univers est, et pourquoi il n'est pas le précédent

OKX cote **45 perpétuels** sur actions, ETF, matières premières et noms
pré-IPO. Hyperliquid héberge un DEX déployé (`xyz`) qui en cote **123**.

| | OKX non-crypto | Hyperliquid `xyz` |
|---|---|---|
| marchés | 45 | 123 |
| volume 24 h | ~0,3 G$ | **2,12 G$** |
| taille minimale | **0,01 contrat ≈ 2 $** | variable |
| levier | 20 à 50× | 10 à 50× |
| frais taker / maker | **5,0 / 2,0 bps** | **4,5 / 1,5 bps** |

Les frais Hyperliquid sont **lus** (`userFees` : `cross 0.00045`,
`add 0.00015`), pas supposés. Les paliers VIP existent mais commencent à
5 M$ de volume mensuel : hors de portée de 1 000 €.

**Trois propriétés distinguent cet univers de tout ce que PRISM avait mesuré.**

**(a) Les demi-spreads sont d'un ordre de grandeur plus serrés.** Mesurés sur
relevés de carnet réels :

| instrument | demi-spread | | instrument | demi-spread |
|---|---|---|---|---|
| MU | **0,049 bps** | | ZHIPU | 1,06 |
| SPY | 0,066 | | KORU | 2,51 |
| QQQ | 0,069 | | SNXX | 2,77 |
| META | 0,073 | | *pour mémoire, alts crypto* | *0,4 – 6,6* |
| TSLA | 0,137 | | | |

**(b) L'index est une référence EXOGÈNE.** `index-components` donne sa
composition : Hyperliquid 25 %, Binance 15 %, Pyth 12,5 %, Kaiko 12,5 %,
Massive 12,5 %, Ondo 12,5 %, OKX spot 5 %, **OKX perp 5 %**. Le perp mesuré
pèse un vingtième de son propre index : l'écart perp/index est à 95 % un
écart à une référence extérieure. PRISM n'avait jamais eu cet instrument de
mesure.

**(c) L'écart à cette référence est dix fois plus grand qu'en crypto.**
Écart-type de `z` (base moins sa médiane glissante causale sur 24 h) :

| | z écart-type | |z| p90 |
|---|---|---|
| BTC | **1,13** bps | 1,82 |
| ETH | 1,11 | 1,77 |
| SOL | 1,57 | 2,54 |
| MU | 15,8 | 20,2 |
| SAMSUNG | 32,9 | 44,7 |
| **SKHYNIX** | **37,2** | **52,4** |
| ZHIPU | 42,0 | 50,6 |

**Pour la première fois dans ce projet, l'écart brut mesuré dépasse largement
le coût d'un aller-retour** (10,8 bps sur SKHYNIX) au lieu de valoir le
cinquième. Le rapport constant du projet — *tout ce qui est favorable vaut 1 à
6 bps, tout aller-retour en coûte 8 à 31* — semblait enfin inversé.

## 2. Le piège de devise, dit avant le reste

Le perp est coté en **USDT**, l'index en **USD**. USDT valait 0,99912 au
moment de l'inventaire. Lire la base sans convertir fabrique une prime de
**+8,8 bps** sur *tous* les instruments — et c'était exactement la « prime »
que NVDA affichait (base médiane +8 bps aux 24 heures de la journée). Ce
n'était pas une prime : c'était le peg.

La conversion passe par l'index OKX `USDT-USD`, lu par barre. C'est la même
classe d'erreur que les **1 902 faux survivants** de l'audit. Seize gardes
la verrouillent dans `tests/v2/test_nc_basis.py`.

Le même piège revient en inter-venues : OKX marge en USDT, Hyperliquid en
USDC. Sans conversion, tout l'univers paraît cher de ~2 bps sur OKX — du même
ordre de grandeur que le signal cherché.

## 3. L'écart est grand. Sa partie prévisible ne l'est pas.

C'est le résultat central, et il n'était pas prévisible depuis la section 1.

**Ce qui est testé** n'est pas « la base se referme-t-elle » — une base peut
se refermer parce que l'**index** rejoint le perp, auquel cas il n'y a rien à
encaisser. C'est : *en vendant le perp quand il est riche contre son index, le
**perp** rapporte-t-il plus qu'un aller-retour ne coûte ?*

**Discipline, déclarée avant les chiffres.** Entrée et sortie à l'**ouverture**
d'une barre **postérieure** à celle du signal — la clôture de la barre du
signal est le prix qui a *déclenché* le signal, et sur un écart de 6 σ c'est la
mèche d'une liquidation que personne n'obtient. Seuils estimés sur le passé
**strict**. Blocs **disjoints**. Barres sans échange exclues une à une. Coûts
pleins. Témoin crypto. Benjamini-Hochberg sur **tous** les tests menés.

### 3.1 La mesure dépend de la résolution — et s'effondre quand on l'affine

| résolution | fenêtre | brut, seuil 2 σ, tenue courte | t |
|---|---|---|---|
| 1 heure | 199 j | +5,5 bps | 6,1 |
| 5 minutes | 76 j | +0,9 à +2,9 bps | 2,9 à 3,9 |
| **1 minute** | **21 j** | **−0,8 à +0,5 bps** | **−1,5 à +0,4** |

Un écart réellement exploitable grandit quand la latence diminue : c'est la
définition d'une dislocation qu'on arbitre. Celui-ci **disparaît**. Ce que la
barre horaire mesurait n'était pas une dislocation actionnable mais le bruit
d'échantillonnage de la clôture horaire.

**Contrôle décisif** — mêmes instruments, même fenêtre de 10 jours, 1 min
contre 5 min, 24 cellules par panneau : les deux résolutions **se contredisent
cellule par cellule**, aucun |t| n'atteint 2,5, les médianes sont à zéro, et
le témoin crypto produit les mêmes amplitudes que les actions.

### 3.2 La porte économique, au complet

À 5 minutes, 60 cellules (seuil × tenue × décalage), 38 actions, 76 jours :

| seuil | tenue | décalage | n | /jour | brut | **net taker** | % > 0 |
|---|---|---|---|---|---|---|---|
| 2 σ | 2 barres | 0 | 16 716 | 219 | +1,75 | **−9,54** | 54 |
| 2 σ | 6 | 1 | 10 754 | 141 | +2,63 | **−8,67** | 52 |
| 4 σ | 6 | 0 | 1 829 | 24 | +4,36 | **−6,98** | 56 |
| 6 σ | 6 | 0 | 589 | 7,7 | +6,10 | **−5,29** | 59 |
| 2 σ | 12 | 0 | 8 852 | 116 | +1,11 | −10,21 | 55 |

**Net négatif dans 58 cellules sur 60. 0 / 75 survivent à Benjamini-Hochberg.**
Le témoin crypto donne un brut nul à négatif partout.

Les tenues longues révèlent la forme de la distribution : à 12 barres et
6 σ, le brut moyen vaut **−59 bps** pour une médiane de **+7 bps**. La
moyenne est portée par quelques pertes énormes — tenir un fade à travers un
vrai mouvement est ruineux. C'est aussi ce que disait la mesure horaire, où
les cellules spectaculaires (brut +40 bps à 6 σ) avaient une médiane de
2,5 bps et **51 % de gagnants** : une pièce de monnaie avec une queue épaisse,
pas un avantage.

## 4. Trois autres formes, fermées par mesure

**Structure de séance.** Les rendements se concentrent quand le marché
américain est fermé : +0,96 bps/h le week-end, +0,67 la nuit, **−0,68**
pendant la séance. Mais une fenêtre **témoin de même durée** prise en semaine
donne +54,1 bps contre +61,8 pour le week-end (t = 1,88, n = 28) : l'écart
n'est pas distinguable de la dérive de l'échantillon. Les actions ont monté,
la crypto a baissé, sur cette fenêtre. **Fermée par le témoin.**

Une propriété réelle subsiste sans être exploitable : l'écart-type d'une
fenêtre de week-end vaut **174 bps contre 393** pour la fenêtre témoin. Le
sous-jacent gelé réduit la variance de moitié — il ne crée pas de rendement.

**Couvrabilité.** 630 paires testées. **Aucune** à ρ > 0,95 ; une seule au-delà
de 0,90 (BZ/CL = Brent contre WTI, ρ = 0,946). Le meilleur résidu de
couverture vaut encore **32 % de la volatilité** de la jambe, et
SKHY/SKHYNIX — les deux noms qui semblaient être le même sous-jacent — laissent
**50 %/an** de résidu. La loi établie par l'audit tient dans cet univers :
*ce que rien n'arbitre n'est pas non plus couvrable.*

**Inter-venues OKX / Hyperliquid-xyz.** C'est une forme économique
**différente** de tout ce qui précède : la position est *couverte* — longue une
venue, courte l'autre, même sous-jacent — donc sans risque de prix. Ce qui est
encaissé est la variation de l'écart. Un aller-retour y compte **quatre**
exécutions : 19,0 bps en taker, 7,0 bps en borne maker.

*Carnets simultanés* (504 relevés, 18 paires, balayage médian 6,9 s, peg
converti) — écart **exécutable**, bid riche moins ask pauvre, jamais un prix
milieu :

| | écart médian | p99 | max | > 19 bps | > 7 bps |
|---|---|---|---|---|---|
| toutes observations | **1,28 bps** | 7,15 | 8,55 | **0,00 %** | 1,59 % |

*Historique* — 18 instruments, 17 jours, bougies 5 min des deux venues. Le
premier passage a rendu un brut de **20 bps** à 4 σ avec **t = 48** et cinq
cellules survivantes. **Ce chiffre était faux, et sa réfutation est le résultat
le plus utile de cette section.** Trois défauts le produisaient : seuil estimé
sur l'échantillon complet (la dispersion de demain décidait du seuil
d'aujourd'hui) ; entrée à la clôture de la barre du signal ; et surtout
**sélection sur le bruit d'observation** — une clôture de bougie est le
*dernier échange* de la barre, pas une cotation, et sur deux venues les deux
derniers échanges peuvent être distants de plusieurs minutes. Choisir les
barres où l'écart est le plus grand revient à choisir celles où ce décalage
est le plus grand, et la barre suivante « revient » d'exactement ce décalage.

Seuils rendus causaux et entrée repoussée d'une barre :

| seuil | tenue | décalage | n | /jour | brut | t | net taker |
|---|---|---|---|---|---|---|---|
| 2 σ | 12 | 0 | 1 675 | 96 | **+0,92** | 7,11 | −18,08 |
| 2 σ | 3 | 1 | 2 470 | 141 | +0,23 | 2,42 | −18,77 |
| 3 σ | 3 | 1 | 578 | 33 | +0,28 | 1,17 | −18,72 |
| 4 σ | 12 | 1 | 125 | 7 | +0,65 | 0,97 | −18,35 |

**0 / 27 cellules à net positif.** L'écart inter-venues *se referme
réellement* — t = 7,1 sur 1 675 observations — mais il vaut **0,9 bps** contre
19. C'est le rapport du projet dans sa forme la plus extrême : **1 pour 21.**

## 4 bis. Le compte des formes fermées dans cet univers

| forme | nature | ce qui a été mesuré | contre |
|---|---|---|---|
| perp / index composite | traversée | 0 à 6 bps, → 0 à 1 min | 11,4 bps |
| structure de séance | détention | dérive de l'échantillon | témoin apparié |
| couverture par paire | couverte | résidu ≥ 32 % de la vol | 630 paires |
| inter-venues OKX/HL | **couverte** | **0,9 bps**, t = 7,1 | **19 bps** |

Les quatre sont fermées par mesure, pas par opinion. La dernière est la plus
instructive parce qu'elle est *sans risque de prix* : c'est la forme que le
mandat cherche, et elle existe — elle vaut simplement un vingt-et-unième de
son coût.

## 5. Ce que cet univers ajoute à la conclusion du projet

L'audit avait conclu, sur la crypto : *le plancher qui bloque n'est pas mon
barème de frais, c'est le demi-spread lui-même, et il est de la même taille
que la sélection adverse parce que c'est ce qui le fixe.* Poussé à frais nuls,
BTC perdait encore 0,86 bps par remplissage.

**Dans cet univers, ce diagnostic est faux, et le vrai est plus net.** Le
demi-spread y vaut 0,05 à 0,5 bps — le marché facture l'immédiateté **dix à
cent fois moins cher que la venue ne facture l'accès**. Le plancher n'est plus
la sélection adverse : c'est **le frais, seul**.

    demi-spread mesuré        0,05 – 0,50 bps
    frais, aller-retour       10,0 bps (taker) / 4,0 bps (maker)
    tout écart mesurable      0 – 6 bps

Le barème public **exclut par construction toute la bande 0,5 – 10 bps**, et
*tout* ce qui est mesurable dans cet univers tombe dedans. Les paliers qui
descendent sous cette bande existent sur les deux venues, et commencent à
5 M$ de volume mensuel.

**Ce que je ne peux pas dire.** Que des frais nuls suffiraient. Ils ne
suffiraient pas ici : à résolution fine et sur fenêtre appariée, le brut de la
famille testée vaut **zéro**, pas « un peu moins que le coût ». Supprimer le
coût ne ressuscite pas un signal absent. Le frais est le blocage de
l'*univers* ; il n'est pas l'explication de l'échec de *cette famille*.

**Ce que la mesure établit.** Un écart dix fois plus grand ne donne pas un
avantage dix fois plus grand : **l'ampleur d'une dislocation et sa
prévisibilité ne sont pas la même variable.** C'était l'espoir précis que cet
univers portait, et il est chiffré, pas supposé.

---

## Reproduction

```
# collecte (endpoints publics uniquement, aucune clé)
python3 fetchmt.py 1H 60 8 ; python3 fetchmt.py 5m 250 8
python3 sampler.py 60          # demi-spreads réels
python3 xvenue.py 100000 30    # carnets simultanés OKX / Hyperliquid

# mesure
PRISM_NC_RAW=<dir> PRISM_NC_TICKS=<f> PRISM_NC_BAR=5m \
    python3 -m prism_v2.scans.nc_gate
PRISM_NC_XVENUE=<f> python3 -m prism_v2.scans.nc_xvenue
```

*Aucun seuil, aucune hypothèse de coût, aucun remplissage n'a été modifié pour
rendre un chiffre plus présentable. LIVE reste désactivé. Aucun secret, aucune
clé, aucun ordre réel. 931 tests passent.*
