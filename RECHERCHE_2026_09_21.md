# Recherche du 21/09/2026 — trois familles testées, trois réfutations

Mandat : trouver une opportunité capable de viser ~20 €/jour sur 1 000 €
(≈ 200 bps/jour de capital). Ce document rapporte les résultats **complets**,
positifs et négatifs.

---

## 1. Résumé exécutif, sans embellissement

**Trois familles testées, trois réfutations.** Aucune n'approche l'objectif, et
l'écart n'est pas marginal : il est d'un facteur 40 à 90.

| famille | statut | brut capté | coût A/R | meilleur net |
|---|---|---|---|---|
| Cash-and-carry OKX (97 instruments couvrables) | **RÉFUTÉE** | 2 – 5 bps/j | 26 bps | +2,16 bps/j |
| Funding absolu Hyperliquid, couvert spot OKX | **RÉFUTÉE** | 4 – 9 bps/j | 23 bps | +0,68 bps/j |
| Basis futures/perp OKX | **RÉFUTÉE** | — | 20 bps | **−0,4 à +0,1** |

Le résultat le plus important n'est pas statistique, il est **arithmétique** :

> Sur **716 perpétuels** des deux venues, le funding le plus élevé observé vaut
> **92,7 bps/jour** — sur un instrument à 524 k$ de volume. Sur les instruments
> réellement couvrables, le maximum est **34 bps/jour**. La couverture par spot
> impose **1× de levier**. L'objectif de 200 bps/jour est donc hors d'atteinte
> par cette famille **avant même de compter le moindre frais**.

---

## 2. État exact du dépôt et des données

HEAD `ab9eb36`, branche `claude/prism-v33-full-audit-p7dz9z`, 1 135 tests verts.

| donnée | source | volume | fraîcheur |
|---|---|---|---|
| funding OKX, 156 instruments | API, refetché | **67 729 relevés** | 95 j, 21/09 |
| funding Hyperliquid, 78 actifs | API, refetché | **38 803 relevés** | 42 j, 21/09 |
| funding forward versionné | `prism_v2/data/funding_forward/` | 8 553 relevés | collecte active |
| touch forward | `prism_v2/data/touch_forward/` | 1 fenêtre | collecte active |
| bougies 1 h, 24 instruments | dépôt | 375 j | — |

**Accès réseau** : OKX ✔, Hyperliquid ✔ *(ma conclusion précédente était
fausse — il manquait l'en-tête `Content-Type`)*, Deribit ✔, Binance ✘ (451),
Bybit ✘ (403).

---

## 3. Objectif et critères, déclarés avant mesure

`prism_v2/PROTOCOLE_CASHCARRY.md`, gelé avant toute mesure de rendement :
univers (volume > 1 M$ des deux côtés), coûts (26 bps OKX / 23 bps HL),
balayage 5 × 5 = 25 cellules, Benjamini-Hochberg sur les 25.

- **RÉFUTÉE** si le flux net ex ante en blocs disjoints ≤ 0.
- **NON CONCLUANTE** si positif mais < 20 bps/jour.
- **PROMETTEUSE** si ≥ 20 bps/jour, capacité ≥ 1 000 €, tous blocs positifs.

Aucun de ces seuils n'a été modifié après observation.

---

## 4. Pourquoi ces hypothèses, et pas d'autres

Le projet n'avait jamais examiné que **12 à 15 paires**. L'univers réel :
482 perpétuels OKX (233 avec spot), 234 Hyperliquid (145 avec spot OKX),
220 futures à échéance. Les trois hypothèses attaquent chacune une contrainte
différente identifiée par les audits précédents :

1. **Cash-and-carry OKX** — teste si l'univers élargi (97 au lieu de 12)
   contient un funding bien plus élevé.
2. **Funding absolu HL** — couvrir par du **spot** capture le taux **entier**,
   pas seulement l'écart entre deux perps (ce que testait `954ad9e`).
3. **Basis futures/perp** — seule structure où les **deux jambes sont des
   dérivés**, donc la seule susceptible de porter un vrai levier, contrairement
   au spot-perp bloqué à 1×.

---

## 5. Méthodes reproductibles

```
python3 -m prism_v2.scans.funding_capture okx     # cash-and-carry OKX
python3 -m prism_v2.scans.funding_capture hl      # funding absolu HL
```

Règle ex ante : à `t`, trier les actifs **disponibles** par le taux **déjà
publié** à `t`, retenir les `k` plus élevés, tenir `N` périodes, encaisser ce
qui est **réellement** payé. **Blocs disjoints.** Un actif n'est éligible que
s'il couvre toute la fenêtre.

**Cadences dérivées des horodatages**, jamais supposées : 68 instruments OKX
paient toutes les 8 h, **86 toutes les 4 h**. Coder la cadence en dur avait
déjà fabriqué un faux +3879 %/an dans ce dépôt.

---

## 6. Résultats complets

### 6.1 Hyperliquid — 64 actifs, 1 000 heures

**1 cellule nette positive sur 15**, à +0,68 bps/jour (2,5 %/an), 12 blocs.

### 6.2 OKX — 97 actifs, 650 règlements

**3 cellules nettes positives sur 25**, meilleure +2,16 bps/jour (8,2 %/an),
k = 3, 10 jours de détention, **8 blocs**. Benjamini-Hochberg sur 25 cellules
ne laisse rien survivre à ce compte.

### 6.3 Le résultat négatif le plus instructif

> **À k = 1, la capture OKX vaut −19 à −20 bps/jour, BRUT.**

Prendre l'instrument qui paie le **plus** fait **perdre**. Le funding extrême
est un signal de déséquilibre qui se corrige, pas une rente. Cela réfute
directement l'intuition « farmer le funding le plus élevé » et impose k ≥ 3 —
ce qui ramène aussitôt le brut à 2–5 bps/jour.

### 6.4 Le basis futures : arbitré, et on peut le montrer

Si le basis n'était que le funding attendu, `basis / jours restants` devrait
égaler le funding du perp. Mesuré :

| future | basis/j | funding perp/j | **écart = edge** |
|---|---|---|---|
| BTC-USD-261030 | +1,71 | +1,64 | **+0,07** |
| BTC-USD-261225 | +1,53 | +1,64 | **−0,11** |
| BTC-USD-270326 | +1,53 | +1,64 | **−0,11** |
| ETH-USD-261225 | +1,32 | +1,58 | **−0,26** |
| ETH-USD-270326 | +1,17 | +1,58 | **−0,41** |

**Le basis est le prix sans arbitrage du funding attendu, à ±0,4 bps/jour près.**
La seule échéance au basis élevé (4 jours, +2,20 d'écart) ne survit pas aux
20 bps d'aller-retour amortis sur 4 jours (5 bps/jour). Famille fermée
structurellement, pas statistiquement.

---

## 7. Brut → net, meilleure cellule

| poste | bps/jour |
|---|---|
| funding brut capté (OKX, k=3, 10 j) | **+4,76** |
| frais perp taker A/R (10 bps / 10 j) | −1,00 |
| frais spot taker A/R (16 bps / 10 j) | −1,60 |
| **net avant spread et slippage** | **+2,16** |
| demi-spreads réels | **non mesurés** |
| slippage, fills partiels, rejets | **non mesurés** |

Sur 1 000 € à 1× : **+0,22 €/jour = 79 €/an**, avant les coûts non mesurés.
Objectif : 20 €/jour. **Écart : facteur 92.**

---

## 8. Capital, capacité, risque

- **Levier 1×, structurel** : la couverture spot immobilise son notionnel
  entier. C'est la différence décisive avec le carry inverse/linéaire (14×).
- **Capacité non limitante** à 1 000 € : les 97 instruments font > 1 M$ des
  deux côtés. **Le goulot est le rendement, pas la taille.**
- **Risque** : le résidu est le basis spot-perp, faible. Le vrai risque est le
  retournement du funding, visible à k = 1 (−20 bps/jour).

---

## 9. Limites et biais

1. Spreads et slippage non mesurés : le +2,16 est une **borne supérieure**.
2. Les cellules positives reposent sur **8 à 12 blocs**. Trop peu.
3. Fenêtres de 42 j (HL) et 95 j (OKX) — **un seul régime de marché**.
4. Le compte unifié OKX est supposé accepter le spot comme collatéral ; non
   vérifié sur compte réel.
5. Trois familles testées en une session : le compte d'essais global du projet
   augmente, et chaque nouveau test rend les survivants marginaux plus
   suspects, pas moins.

---

## 10. Décision

**Les trois familles sont réfutées.** Je ne recommande pas de déploiement.

Ce qui reste structurellement non testé, avec son ordre de grandeur honnête :

| piste | mécanisme | potentiel estimé | obstacle |
|---|---|---|---|
| **Prime de risque de volatilité** (Deribit, joignable) | IV > RV de façon persistante ; mesuré aujourd'hui : **IV 39,9 % vs RV 33,6 %, soit +6,3 points** | ~2,3 %/an **du notionnel** avant coûts de couverture ; margé donc levier réel possible | **queue non bornée** — un gap de 20 % détruit un compte de 1 000 € ; coûts de delta-hedging non mesurés |
| Arbitrage inter-venues | dislocations entre CEX | inconnu | **Binance 451, Bybit 403** depuis cet environnement |
| Market making avec rebates | rebate maker positif | inconnu | exige un palier VIP hors de portée |

La prime de volatilité est la seule famille jamais touchée par ce dépôt et
mérite un protocole gelé. **Mais son ordre de grandeur, ~2 %/an de notionnel,
ne rapproche pas non plus de 200 bps/jour** — et son profil de risque est le
plus hostile possible à un petit compte.

---

## 11. Fichiers modifiés

| fichier | rôle |
|---|---|
| `prism_v2/PROTOCOLE_CASHCARRY.md` | protocole gelé **avant** mesure |
| `prism_v2/scans/funding_capture.py` | scan reproductible, deux venues |
| `RECHERCHE_FUNDING.md` | résultats détaillés des deux premières familles |
| `RECHERCHE_2026_09_21.md` | ce rapport |

---

## 12. Ce qui reste une hypothèse

1. Que le +2,16 bps/jour survive aux spreads et au slippage. **Non vérifié.**
2. Que le spot OKX serve de collatéral au perp en compte unifié. **Non vérifié.**
3. Que la prime de volatilité Deribit soit capturable nette de delta-hedging.
   **Non mesuré** — le +6,3 points est un instantané, pas une série.
4. Que les fenêtres de 42 et 95 jours représentent autre chose qu'un régime.
5. Que Binance et Bybit, inaccessibles ici, ne contiennent pas une structure
   absente d'OKX. **Non testable depuis cet environnement.**
