# Protocole gelé — arbitrage de funding inter-venues

**Écrit et committé AVANT toute analyse.** Tout paramètre modifié après avoir
regardé un résultat invalide ce résultat.

## Hypothèse

Le différentiel de funding entre deux venues cotant le même actif perpétuel
(OKX linéaire et Hyperliquid) présente une queue épaisse concentrée sur des
actifs à faible capacité. Cette queue persiste assez longtemps après être
devenue observable pour être capturée nette de coûts.

## Pourquoi elle pourrait être réelle — et c'est la première fois que ce dépôt a une réponse

Toutes les familles précédentes butaient sur « pourquoi n'est-ce pas déjà
arbitré ? ». Ici la réponse est structurelle : **la contrainte de capacité**.
Un différentiel de 200 %/an sur un actif dont l'open interest vaut 500 k$
n'intéresse aucun acteur institutionnel — la taille déployable ne couvre pas
son coût d'opportunité. Un petit capital n'a pas ce problème.

**C'est la seule configuration rencontrée dans ce dépôt où un petit capital
est un avantage structurel et non un handicap.** Toutes les autres familles
pénalisaient la petite taille via les coûts fixes.

## Ce qui invaliderait l'hypothèse

Le différentiel est un signal *avancé*, pas une rente. S'il se referme en
moins de quelques heures, on encaisse quelques points de base de funding en
payant ~20 bps d'aller-retour. La famille est alors fermée.

## Règle gelée

- **Univers** : actifs cotés à la fois sur OKX (perp linéaire USDT) et
  Hyperliquid, hors actifs délistés.
- **Signal** à l'heure `t` : `d(t) = f_okx(t) − f_hl(t)`, exprimé par heure,
  construit **uniquement** à partir de paiements de funding déjà effectués à
  `t` ou avant. Aucun taux futur, aucun taux prédit.
- **Entrée** : si `|d(t)| ≥ T`, entrer à `t+1h` — une heure APRÈS le signal,
  jamais au même instant. Sens : long sur la venue qui paie, short sur
  l'autre.
- **Détention** : `H` heures, fixe. Pas de sortie discrétionnaire.
- **Revenu** : somme des paiements de funding réellement horodatés dans
  `]t+1, t+1+H]`, sur les deux venues, à leurs cadences respectives
  (Hyperliquid horaire, OKX toutes les 8 h). Un paiement OKX ne compte que
  si son horodatage tombe dans la fenêtre de détention.
- **Coûts** : 4 traversées (entrée 2 jambes, sortie 2 jambes). Taker OKX
  5,0 bps, taker Hyperliquid 4,5 bps, plus un demi-spread par traversée.
  Aucun coût inconnu n'est traité comme nul.
- **Capital** : `2 × notionnel` (1×, chaque jambe intégralement
  collatéralisée). La sensibilité au levier est rapportée séparément, avec
  le risque de liquidation nommé, jamais fondue dans le résultat principal.
- **Métrique** : bps nets par jour de capital immobilisé.

## Grilles

- `T ∈ {20, 50, 100, 200} %/an`
- `H ∈ {8, 24, 72, 168} heures`

16 configurations. Correction pour essais multiples obligatoire.

## Découpage temporel

`DISCOVERY` → `VALIDATION` → `HOLDOUT`, par ordre chronologique strict. Le
holdout n'est ouvert qu'une fois, après gel du couple `(T, H)` choisi sur la
validation.

## Critères de fermeture

La famille est **fermée** si l'un de ces faits est établi :

1. le net médian après coûts est ≤ 0 sur la validation ;
2. le net > 0 n'existe que sur des actifs dont la profondeur exécutable est
   inférieure à 1 000 $ ;
3. le différentiel se referme en moyenne avant d'avoir couvert les 20 bps
   d'aller-retour ;
4. le résultat ne survit pas au holdout.

## Ce que ce protocole ne teste pas

Le risque de liquidation d'une jambe, le risque de contrepartie Hyperliquid,
et la disponibilité réelle du collatéral. Ces points sont hors périmètre de
la mesure et devront être traités avant tout déploiement.

---

# Résultat — famille FERMÉE

Protocole exécuté sans modification. 142 actifs cotés sur les deux venues,
45 jours, découpage 20 j / 12 j / 12 j.

## Un bug trouvé en route, et ce qu'il enseigne

La première exécution codait `period_h = 8.0` pour OKX. **90 des 142
instruments paient toutes les 4 heures**, pas 8. Le taux horaire était donc
divisé par deux sur 63 % de l'univers, et le symptôme — un différentiel lu
à **+3879 %/an** sur SOPH — ressemblait davantage à une opportunité
spectaculaire qu'à une erreur. La cadence est désormais **déduite des
horodatages**, jamais supposée.

C'est la même classe d'erreur que `ctVal` sur les inverses : une constante
de venue supposée uniforme alors qu'elle est par instrument. Elle fabrique
toujours des opportunités, jamais des pertes — c'est à cela qu'on la
reconnaît.

## Le différentiel persiste vraiment — ce n'est pas le point de rupture

Différentiel réalisé moyen (%/an) après le signal, sur DISCOVERY :

| seuil | n | 1 h | 4 h | 8 h | 24 h | 72 h | 168 h |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20 % | 1 470 | 13,9 | 26,6 | 24,0 | 18,8 | 12,3 | 7,3 |
| 50 % | 524 | 19,5 | 47,2 | 41,3 | 30,7 | 19,1 | 10,7 |
| 100 % | 135 | −29,8 | 66,7 | 53,3 | 38,7 | 25,0 | 13,7 |
| 200 % | 29 | −256,4 | 70,0 | 23,9 | 18,8 | 18,6 | 11,6 |
| 500 % | 15 | −571,5 | 26,9 | −33,4 | −6,3 | 6,3 | 5,5 |

Deux lectures. La colonne **1 h est négative** aux seuils extrêmes : l'instant
qui suit une lecture extrême se retourne violemment — la lecture était en
grande partie du bruit sur un seul paiement. Mais à partir de 4 h le signal
est **réel et décroît proprement** : à 100 %/an de seuil, on capture encore
38,7 %/an sur 24 h.

**La famille n'échoue pas par absence de signal.** Elle échoue par
arithmétique.

## Pourquoi elle échoue quand même

Les 16 configurations sont négatives en DISCOVERY. La meilleure
(T = 200 %/an, H = 168 h) donne un brut de 26,8 bps contre 39 bps de coût.

**VALIDATION** (12 jours, jamais regardés) : T = 200 %/an, H = 168 h →
n = 19, net **+3,0 bps**, **0,21 bps/jour**, 47 % de positifs. Un tirage à
pile ou face.

Sensibilité, sur DISCOVERY à T = 100 %/an :

| Hypothèse de coût | coût AR | net (H = 168 h) | bps/jour |
|---|---:|---:|---:|
| taker + spread 5 bps (base) | 39,0 | −16,7 | −1,19 |
| taker seul, spread nul | 19,0 | +3,3 | +0,23 |
| maker/maker, spread nul 🟡 | 7,0 | +15,3 | +1,09 |

Et le levier nécessaire pour atteindre 63,28 bps/jour :

```
taker, spread nul   : même à 20x  ->  4,67 bps/j   (0,07x)
maker/maker 🟡      : même à 20x  -> 21,82 bps/j   (0,34x)
```

**Même sous des hypothèses indéfendables** — fills maker sur les deux jambes
d'actifs illiquides, spread nul, levier 20× sur une paire inter-venues qui
serait liquidée à la moindre divergence de marque — la famille plafonne à
**0,34×** l'objectif.

## Fermeture

Critère 3 du protocole : le différentiel ne couvre pas l'aller-retour avant
de s'être refermé. Critère 4 : le résultat ne survit pas hors échantillon
(47 % de positifs).

**Famille fermée.** Elle est réelle, mesurable, positive, et trop petite
d'un ordre de grandeur.
