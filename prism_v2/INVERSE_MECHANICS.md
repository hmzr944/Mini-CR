# Audit — mécanique des perpétuels INVERSE OKX (`-USD-SWAP`)

Verrouillage réalisé avant toute construction du Capture Engine.
Sources : documentation OKX + réponses réelles de `/api/v5/public/instruments`
(interrogée le 2026-09-16).

> **`BTC-USD-SWAP` ≠ `BTC-USDT-SWAP` ≠ `BTC-USDT` (spot).**
> Trois instruments distincts. Aucune conversion automatique n'existe dans V2.

---

## 1. Métadonnées réelles

`BTC-USD-SWAP` : `ctType=inverse`, `ctVal=100`, `ctValCcy=USD`, `ctMult=1`,
`settleCcy=BTC`, `lotSz=0.1`, `minSz=0.1`, `tickSz=0.1`, `lever=100`, `state=live`.

`BTC-USDT-SWAP` : `ctType=linear`, `ctVal=0.01`, `ctValCcy=**BTC**`, `ctMult=1`,
`settleCcy=USDT`.

La différence décisive est `ctValCcy` : **USD** pour l'inverse, **ccy de base**
pour le linéaire.

## 2. Comment `ctVal = 100 USD` intervient

Chaque contrat représente **100 USD de valeur faciale**, quel que soit le prix.

```
notionnel_USD = ctVal × sz × ctMult          ← INVERSE : le prix n'apparaît pas
notionnel_USD = ctVal × sz × ctMult × prix   ← LINEAR  : le prix multiplie
```

Conséquence : 100 contrats `BTC-USD-SWAP` valent 10 000 USD à 10 000 $ comme à
90 000 $. Appliquer la formule linéaire à un inverse à 100 000 $ donne
1 000 000 000 USD au lieu de 10 000 — **erreur d'un facteur 100 000**.

## 3. Quantité de contrats → exposition

```
exposition_USD  = ctVal × sz × ctMult
exposition_coin = ctVal × sz × ctMult / prix     ← porte le risque de marge
```

L'exposition **coin** décroît quand le prix monte : c'est la nature « inverse ».

## 4. PnL — formules officielles OKX

Libellé en **devise de règlement** (BTC pour `BTC-USD-SWAP`) :

```
INVERSE  long  = ctVal × |sz| × ctMult × (1/prix_ouverture − 1/prix_mark)
INVERSE  short = ctVal × |sz| × ctMult × (1/prix_mark − 1/prix_ouverture)

LINEAR   long  = ctVal × |sz| × ctMult × (prix_mark − prix_ouverture)   [USDT]
LINEAR   short = ctVal × |sz| × ctMult × (prix_ouverture − prix_mark)   [USDT]
```

**Le PnL inverse est affine en 1/prix, pas en prix.** Propriété vérifiée par
`test_inverse_pnl_is_linear_in_reciprocal_price`.

### Le piège silencieux

Le rendement d'un inverse, exprimé en coin, vaut :

```
(Px − Pe) / Px          ← dénominateur = prix de SORTIE
```

et non `(Px − Pe) / Pe` comme pour un linéaire.

| | Pe = 100 000 → Px = 101 000 (+1 %) |
|---|---|
| rendement coin inverse | **99.0099 bps** |
| hypothèse linéaire naïve | 100.0000 bps |
| **biais** | **0.9901 bps** |

Ce biais ne fait pas exploser un backtest — il le décale. À l'échelle d'un
edge de quelques bps, il est **du même ordre que l'edge**.

## 5. Frais

```
INVERSE : ctVal × sz × ctMult / prix × taux   → en COIN  (BTC)
LINEAR  : ctVal × sz × ctMult × prix × taux   → en USDT
```

Exemples chiffrés OKX, reproduits à l'identique par les tests :

| Cas | Calcul | Résultat |
|---|---|---|
| 100 contrats BTC/USD, ctVal 100, prix 10 000, taux 0.0005 | `100×100/10000×0.0005` | **0.0005 BTC** |
| 100 contrats BTC/USDT, ctVal 0.01, prix 10 000, taux 0.0005 | `0.01×100×10000×0.0005` | **5 USDT** |

Taux Lv1 par défaut : **maker 0.02 %**, **taker 0.05 %**.

**Propriété utile :** en bps du notionnel USD, les deux valent `taux`. Mais la
**devise diffère** — pour un inverse il faut détenir du BTC pour payer les frais
et la marge. En V2 ces taux restent `ASSUMED` : `/api/v5/account/trade-fee`
exige une authentification que V2 n'a pas.

## 6. Contraintes d'ordre

| Contrainte | Effet | Politique V2 |
|---|---|---|
| `lotSz` | pas de quantité | arrondi **vers le bas** — jamais plus de risque que demandé |
| `minSz` | taille minimale | ordre en-dessous **refusé**, jamais arrondi vers le haut |
| `tickSz` | pas de prix | arrondi **conservateur** selon le sens |

Notionnel minimum réel : `minSz × ctVal` = **10 USD** sur les 15 contrats
inverses (BTC : 0.1 × 100 ; autres : 1 × 10). Le résidu de quantification est
reporté (`residual_usd`), jamais dissimulé.

## 7. Coût aller-retour

```
frais_AR_bps = [frais(sz, prix_entrée) + frais(sz, prix_sortie)] / notionnel_entrée × 1e4
```

Chaque jambe est calculée **à son propre prix** : pour un inverse, la jambe de
sortie ne coûte pas le même montant en coin que celle d'entrée.

Convention de coût unifiée (démontrée dans `orderbook.py`) :

```
coût_bps = (prix_exécution − prix_référence) / prix_EXÉCUTION × 1e4
```

Dénominateur = prix d'**exécution**, pas le mid. Diviser par le mid est la
convention linéaire : l'écart est du second ordre (`C²/1e4` bps pour un coût de
`C` bps) mais systématique et asymétrique entre achat et vente.

## 8. Garanties structurelles

| Garantie | Mécanisme |
|---|---|
| Aucune fonction financière ne reçoit un symbole | `NotAnInstrumentSpec` levé ; balayage de toutes les fonctions publiques |
| Métadonnées incohérentes refusées | `InstrumentSpec.validate()` |
| `state != live` refusé | idem |
| Aucun univers codé en dur | test AST sur tout `prism_v2/` |
| Funding sur le **même** instId | `funding_rate(spec)` prend un spec, refuse le spot |
| Backtest / paper / live futur partagent la mécanique | tout passe par `contracts.py` |

## 9. Revue adversariale — « où pourrais-je encore traiter un inverse comme un linéaire ? »

Deux défauts **réels** trouvés dans mon propre code et corrigés :

1. **`orderbook.py` appelait `instrument.size_to_notional_usd()`**, méthode
   supprimée lors de la réécriture de `instruments.py` → le module était cassé.
   Corrigé : passe par `contracts.usd_notional(spec, …)`.
2. **Dénominateurs en bps au mid** — la convention linéaire. Corrigé par
   `cost_bps()`, dénominateur = prix d'exécution.

Points vérifiés et jugés corrects :

- taille des niveaux de carnet = **contrats** pour les swaps (confirmé : tous
  les `sz` observés sont des multiples de `lotSz`) ;
- funding interrogé sur l'instId exécuté, spot refusé explicitement ;
- frais calculés par jambe à leur prix propre.

Point **assumé et signalé** : le coût de spread aller-retour extrapole la jambe
de sortie depuis le carnet d'entrée. Le spread de sortie n'est pas observé —
c'est écrit dans la `note` de la composante.
