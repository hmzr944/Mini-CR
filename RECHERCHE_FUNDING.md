# Capture du funding absolu, couverte par du spot — OKX et Hyperliquid

Recherche du 21/09/2026. Protocole gelé dans `prism_v2/PROTOCOLE_CASHCARRY.md`
**avant** toute mesure. **Verdict : hypothèse réfutée sur les deux venues.**

---

## 1. Pourquoi cette piste, et en quoi elle était neuve

Le projet n'avait jamais regardé que **12 à 15 paires** inverse/linéaire. La
réalité de l'univers accessible :

| | |
|---|---|
| perpétuels OKX vivants | **482** |
| dont ayant un spot OKX correspondant | **233** |
| dont spot **et** volume perp > 1 M$ | **156** |
| perpétuels Hyperliquid | **234** |
| dont ayant un spot OKX pour la couverture | **145** |
| dont liquides des deux côtés (> 1 M$) | **64** |

La structure testée n'est pas un **différentiel** entre deux perps — c'est la
capture du funding **absolu**, couverte par du **spot**. C'est la différence
avec le test inter-venues de `954ad9e` : couvrir un perp par un autre perp fait
payer le funding de la jambe de couverture, donc ne capture que l'écart. Couvrir
par du spot capture le taux entier — au prix d'un levier de **1×**, puisqu'il
faut détenir le spot.

Un fait structurel vérifié au passage : **Hyperliquid spot et Hyperliquid perp
n'ont aucun actif en commun** (0 sur 234). La couverture ne peut pas être locale.

---

## 2. Ce que le funding vaut réellement, en instantané

Hyperliquid, 234 perpétuels, relevé du 21/09 :

| | bps/**jour** |
|---|---|
| médiane | +3,0 |
| p90 | +13,9 |
| p99 | +53,4 |
| maximum (HMSTR) | **+92,7** |

Sur les **64** instruments couvrables *et* liquides des deux côtés, la médiane
tombe à **+3,8** et le maximum à **+34,0**. Les extrêmes vivent sur les
instruments minuscules : HMSTR à +87 bps/jour fait 524 k$ de volume perp et
108 k$ de volume spot.

> **Arithmétique décisive, avant tout coût.** L'objectif est 200 bps/jour à
> 1×. Le funding **maximum observé sur toute la venue**, sur l'instrument le
> plus extrême, vaut 92,7 bps/jour. Sur les instruments réellement couvrables,
> le maximum est 34. **La capture de funding ne peut pas atteindre 2 %/jour à
> 1× de levier, même en ignorant entièrement les frais.**

---

## 3. Le test ex ante — règle figée, blocs disjoints

À l'instant `t`, trier les actifs **disponibles** par le taux **déjà publié** à
`t`, retenir les `k` plus élevés, tenir `N` périodes, encaisser ce qui est
**réellement** payé. Blocs **disjoints**. Un actif n'est éligible que s'il
couvre toute la fenêtre — sinon la sélection serait conditionnée par la survie.

### Hyperliquid — 64 actifs, 1 000 heures, coût 23 bps

| k | N (h) | jours | blocs | brut bps/j | t | **net bps/j** |
|---|---|---|---|---|---|---|
| 1 | 6 | 0,2 | 165 | 9,53 | 9,63 | −82,47 |
| 1 | 24 | 1,0 | 40 | 9,07 | 5,35 | −13,93 |
| **1** | **72** | **3,0** | **12** | **8,35** | **4,01** | **+0,68** |
| 3 | 72 | 3,0 | 6 | 4,44 | 2,92 | −3,23 |
| 20 | 72 | 3,0 | 6 | 3,82 | 2,84 | −3,85 |

**1 cellule nette positive sur 15**, à +0,68 bps/jour — soit **2,5 %/an**.

### OKX — 97 actifs couvrables, 650 règlements, coût 26 bps

Cadences **dérivées des horodatages** : 68 instruments à 8 h, **86 à 4 h**.
Coder la cadence en dur avait déjà fabriqué un faux +3879 %/an dans ce dépôt.

| k | N | jours | blocs | brut bps/j | t | **net bps/j** |
|---|---|---|---|---|---|---|
| **1** | 3 | 0,5 | 216 | **−18,98** | −2,99 | −70,98 |
| **1** | 24 | 4,0 | 27 | **−20,33** | −1,36 | −26,83 |
| 3 | 24 | 4,0 | 21 | 3,70 | 3,90 | −2,80 |
| **3** | **60** | **10,0** | **8** | **4,76** | **3,52** | **+2,16** |
| 5 | 60 | 10,0 | 8 | 3,51 | 4,38 | +0,91 |
| 20 | 60 | 10,0 | 8 | 2,03 | 7,77 | −0,57 |

**3 cellules nettes positives sur 25**, la meilleure à +2,16 bps/jour
(**8,2 %/an**), sur **8 blocs**. Benjamini-Hochberg sur les 25 cellules ne
laisse rien survivre à ce compte de blocs.

### Le résultat négatif le plus instructif

> **À k = 1, la capture OKX est de −19 à −20 bps/jour, brut.**

Prendre l'instrument qui paie le **plus** fait **perdre** de l'argent. Le
funding extrême se retourne : c'est un signal de déséquilibre qui se corrige,
pas une rente. Cela réfute directement l'intuition « farmer le funding le plus
élevé », et c'est la raison pour laquelle il faut diversifier (k ≥ 3) — ce qui
réduit aussitôt le brut à 2–5 bps/jour.

---

## 4. Le fait qui revient, maintenant sur cinq familles sans rapport

| famille | brut capté | coût A/R | rapport |
|---|---|---|---|
| OKX inverse/linéaire (carry) | 0,6 – 1,6 bps/j | 8 – 26 bps | ≈ 1 : 10 |
| **HL funding absolu, couvert spot** | **4 – 9 bps/j** | **23 bps** | **≈ 1 : 3** |
| **OKX cash-and-carry, 97 instruments** | **2 – 5 bps/j** | **26 bps** | **≈ 1 : 6** |
| traversée / microstructure | 1 – 6 bps | 8 – 31 bps | ≈ 1 : 4 |
| liquidations forcées | 2,97 bps | 10 – 11 bps | ≈ 1 : 4 |

Cinq mécanismes sans rapport, deux venues, le même rapport. Ce n'est plus une
coïncidence : c'est le plancher de coût vu de l'extérieur, au tarif public.

---

## 5. Décomposition brut → net, meilleure cellule OKX

| poste | bps/jour |
|---|---|
| funding brut capté (k=3, 10 j) | **+4,76** |
| frais perp taker, aller-retour (10 bps / 10 j) | −1,00 |
| frais spot taker, aller-retour (16 bps / 10 j) | −1,60 |
| **net avant spread et slippage** | **+2,16** |
| demi-spreads réels des deux jambes | **non mesurés** |
| slippage, fills partiels, rejets | **non mesurés** |
| **net après ces postes** | **inconnu, ≤ +2,16** |

Sur 1 000 € à 1× de levier : **+0,22 €/jour, soit 79 €/an** — avant les coûts
non mesurés. L'objectif est 20 €/jour.

**Écart : facteur 92.**

---

## 6. Capital, capacité, risque

- **Levier 1×, structurel.** Il faut détenir le spot. Le carry inverse/linéaire
  portait 14× parce que les deux jambes étaient des dérivés margés ; ici la
  jambe spot immobilise son notionnel entier.
- **Capacité non limitante à 1 000 €** : les 97 instruments retenus font plus
  de 1 M$ de volume des deux côtés. La capacité n'est pas le goulot — le
  rendement l'est.
- **Risque** : la jambe spot et la jambe perp sont sur le même actif, donc le
  résidu est le basis spot-perp, faible. Le risque réel est le **retournement
  du funding** pendant la détention, visible à k=1 (−20 bps/jour).

---

## 7. Ce qui reste hypothèse

1. Les demi-spreads et le slippage ne sont pas mesurés ; le net de +2,16 est
   une **borne supérieure**.
2. Les blocs disjoints sont 8 à 12 sur les cellules positives. C'est trop peu.
3. La fenêtre HL couvre 42 jours, OKX 95 jours — **un seul régime de marché**.
4. La couverture suppose que le spot OKX se remplit au prix affiché.
5. Le compte unifié OKX est supposé accepter le spot comme collatéral de la
   jambe perp ; non vérifié sur compte réel.

---

## 8. Verdict

**HYPOTHÈSE RÉFUTÉE.** Sur les deux venues, la capture de funding absolu
couverte par du spot ne franchit pas le coût d'aller-retour de façon
significative. Les rares cellules positives sont marginales, reposent sur 8 à
12 blocs, et ne survivraient pas à une correction de multiplicité sur 25 et
15 cellules respectivement.

Le résultat structurel est plus fort que le verdict statistique :
**le funding maximum observé sur 716 perpétuels des deux venues vaut 92,7
bps/jour sur l'instrument le plus extrême, et 34 bps/jour sur le plus extrême
des instruments couvrables. L'objectif de 200 bps/jour à 1× de levier est donc
hors d'atteinte par cette famille, indépendamment des frais.**
