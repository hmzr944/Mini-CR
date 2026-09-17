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
