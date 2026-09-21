# Le livre de carry, construit — et le plafond qu'il révèle

Produit par `python3 -m prism_v2.scans.book_build`, avec la règle de sélection
déclarée dans `prism_v2/carry_book.py` **avant** ce résultat. 21/09/2026.

---

## 1. Le livre

Univers : les **15** paires inverse/linéaire vivantes sur OKX — c'est tout ce
qui existe (467 perpétuels linéaires, mais seulement 15 inverses).

**Onze sur quinze sont rejetées, chacune par un critère nommé :**

| paire | rejet | valeur | seuil |
|---|---|---|---|
| ADA | médiane/moyenne | 0,00 | 0,30 |
| DOT, SUI, UNI | médiane/moyenne | 0,00 | 0,30 |
| BTC | t du différentiel | **1,96** | 3,00 |
| DOGE | dérive du résidu 14 j | 387 bps | 250 |
| FIL | dérive du résidu 14 j | **950 bps** | 250 |
| XRP | dérive du résidu 14 j | 670 bps | 250 |
| ETC | profondeur jambe inverse | **90 $** | 5 000 |
| LINK | profondeur jambe inverse | 375 $ | 5 000 |
| HYPE | heures de résidu | 1 844 | 2 000 |

Quatre survivent : **BCH, ETH, LTC, SOL.**

| | |
|---|---|
| flux capté `r` | **0,585 bps/jour** de notionnel |
| t | **9,96** (286 règlements) |
| levier retenu | **14,3×** — le minimum des leviers sûrs, borné par BCH |
| notionnel déployable | **12 408 USD** (25 % de la jambe la plus fine, ×4) |
| capital correspondant | **869 €** |

| détention | maker (8 bps A/R) | taker (~24 bps) |
|---|---|---|
| 14 j | +0,19 bps/j — **0,7 %/an** | −16,6 bps/j |
| 30 j | +4,54 bps/j — **18,0 %/an** | −3,3 bps/j |
| 60 j | +6,45 bps/j — **26,5 %/an** | +2,5 bps/j — 9,7 %/an |
| 90 j | +7,08 bps/j — **29,5 %/an** | +4,5 bps/j — 17,7 %/an |

La discipline a fait ce qu'elle devait : **le chiffre a baissé.** Mon estimation
de la veille (≈ 58 %/an) reposait sur ADA — rejetée ici pour médiane nulle — et
sur un levier de 23,6× supposé uniforme, alors que le barème réel le borne à
14,3× sur ce livre.

---

## 2. Le fait qui décide de tout

Le pourcentage annuel est trompeur, parce qu'il varie avec le levier alors que
**le PnL en euros, lui, ne bouge pas.**

| levier | capital immobilisé | rendement | **€/jour** | **€/an** |
|---|---|---|---|---|
| 14,3× | 868 € | 18,1 % | 0,39 | **144** |
| 10× | 1 241 € | 12,3 % | 0,39 | **144** |
| 7× | 1 773 € | 8,5 % | 0,39 | **144** |
| 5× | 2 482 € | 6,0 % | 0,39 | **144** |
| 3× | 4 136 € | 3,5 % | 0,39 | **144** |

Parce que `€/jour = (r − c/T)/10⁴ × notionnel`, et que le notionnel est plafonné
par **la profondeur du carnet**, pas par le capital. Le levier ne change que la
quantité de capital qu'il faut immobiliser pour atteindre le même euro.

> **Le plafond économique du carry sur OKX, tel que mesuré, vaut environ
> 144 € par an. Il n'est fonction ni du capital, ni du levier, ni de la durée
> de détention au-delà de ~60 jours. Il est fonction de la profondeur des
> carnets coin-margés, et ces carnets sont minuscules.**

Ajouter du capital ne l'augmente pas. Augmenter le levier ne l'augmente pas.
C'est une borne de **capacité**, et c'est la plus dure des trois (signal, coût,
capacité) parce qu'elle ne se négocie avec rien.

---

## 3. La seule chose qui pourrait le lever — et pourquoi elle n'est pas acquise

**BTC est la seule paire avec une vraie profondeur : 135 515 USD** sur la jambe
contraignante, soit onze fois tout le livre retenu. Son levier autorisé est 50×,
et son résidu est le plus sage de l'univers (60 bps de dérive sur 14 jours
contre 120 de coussin).

Mais son différentiel vaut **0,120 bps/jour avec t = 1,96** — sous le seuil de
3,0 déclaré. Et même en le supposant réel, l'arithmétique ne suit pas :

| détention | € / an sur BTC seul (si le différentiel était établi) |
|---|---|
| 30 j | **−181** |
| 60 j | −16 |
| 90 j | **+38** |

À 0,120 bps/jour contre 8 bps d'aller-retour, il faut **67 jours de détention
rien que pour couvrir le coût**. La profondeur est là ; le flux ne l'est pas.

C'est ce que la collecte forward tranchera : `t = 1,96` sur 95 jours n'est ni
une confirmation ni un rejet. Avec six mois de relevés supplémentaires, le t
doublerait si l'effet est réel.

---

## 4. Ce que cela dit de la direction

Le projet a successivement établi que, sur OKX avec des données publiques :

1. tout ce qui est capturable à haute fréquence vaut 1 à 6 bps, quand tout
   aller-retour en coûte 8 à 31 *(quatre familles indépendantes, tient à frais
   nuls)* ;
2. la seule famille qui survit est un carry à détention longue ;
3. **et ce carry est plafonné à ~144 €/an par la profondeur, pas par le capital.**

Les trois ensemble disent quelque chose de plus fort que chacun séparément :
**la contrainte n'est plus la stratégie, c'est la venue.**

La suite logique n'est donc pas une stratégie de plus sur OKX. C'est de savoir
si **le même mécanisme, sur un carnet coin-margé profond, porte le même flux.**
Binance COIN-M et Bybit inverse ont des carnets d'un ordre de grandeur
au-dessus. Le code de `carry_book.py` s'y applique sans modification : il ne
demande qu'un flux, deux barèmes de marge, deux profondeurs et un résidu.

**Je n'ai pas pu le tester depuis cet environnement** : `dapi.binance.com`
répond **HTTP 451** et `api.bybit.com` **HTTP 403** — restriction réseau de la
région d'exécution, pas une indisponibilité des API. Le test demande un
environnement avec une autre sortie réseau. C'est une limite de l'outil, et
elle est nommée plutôt que contournée.

---

## 5. État

| | |
|---|---|
| PnL net réalisé | **0,00 €** |
| Livre cible | BCH, ETH, LTC, SOL — 4 paires, 12 408 USD de notionnel |
| Plafond mesuré | **~144 €/an**, borné par la profondeur |
| Étape 1 (hors échantillon) | collecte lancée le 21/09, 8 553 relevés amorcés |
| Étape 2 (exécutable) | collecte lancée, verdict attendu sous ~8 jours |
| Étape 3 (réel) | bloquée par les étapes 1 et 2 |
| Question ouverte n° 1 | le différentiel BTC est-il réel ? *(t = 1,96)* |
| Question ouverte n° 2 | le mécanisme porte-t-il sur un carnet profond ailleurs ? |
