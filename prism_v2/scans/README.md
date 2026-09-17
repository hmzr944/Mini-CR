# Mesures sur les donnees deja collectees

Ces scripts ne collectent rien. Ils repondent a une seule question, posee aux
264 Mo d'evenements WebSocket et aux 1 173 717 instantanes de l'observatoire
qui dormaient dans `prism_v2/data/` :

> ou, dans ce que PRISM possede deja, existe-t-il un ecart assez grand pour
> payer son propre cout d'execution ?

## Ordre d'execution

```
export PRISM_SCAN_DIR=/tmp/prism_scans          # fichiers intermediaires

python -m prism_v2.scans.refresh_specs          # referentiel OKX (ctVal, ctType)
python -m prism_v2.scans.ws_full                # rejeu des carnets incrementiels
python -m prism_v2.scans.basis2                 # ecart spot <-> perp inverse
python -m prism_v2.scans.mm_scan                # demi-spread contre selection adverse
python -m prism_v2.scans.mm_bh                  # correction de multiplicite
python -m prism_v2.scans.panel                  # panneau synchrone 15 instruments
python -m prism_v2.scans.xsec                   # dislocations transversales
                                                #   + balayage du frais par traversee
python -m prism_v2.scans.mm_placebo             # placebo sur l'unique survivant
python -m prism_v2.scans.breakeven              # a quels frais le signe basculerait
```

## Recensement des flux (capture par duree, pas par traversee)

```
python -m prism_v2.scans.funding_pairs          # carry inverse/lineaire, 15 actifs
python -m prism_v2.scans.xvenue_funding         # OKX vs Hyperliquid, 138 actifs
python -m prism_v2.scans.xvenue_persistence     # le SIGNE persiste-t-il assez ?
python -m prism_v2.scans.carry_capital          # R(T) sur le capital reellement bloque
```

`carry_capital` exige `prism_v2/data/candles_1h.json` et
`prism_v2/data/margin_tiers.json`, tous deux versionnes dans le depot.

La cadence de funding est lue PERIODE PAR PERIODE, jamais par une mediane :
OKX la change pendant les episodes de stress (6 instruments sur 138 dans la
fenetre observee), et c'est precisement pendant ces episodes que les taux sont
extremes. Une mediane globale y surevalue le differentiel d'un facteur 4.

`ws_full` prend une dizaine de minutes : il rejoue 287 118 messages de carnet
incrementiel avec chainage de sequence. `panel` en prend une trentaine de
secondes de plus pour la lecture de l'observatoire.

## Ce qui est mesure, et comment

- **Prix executables, jamais le milieu.** Un ecart se prend en vendant au bid
  d'un cote et en achetant a l'ask de l'autre. Les demi-spreads des deux
  jambes sont donc payes a l'aller et au retour.
- **Carnet reconstruit, pas message brut.** Le canal `books` d'OKX est
  incrementiel : un message peut ne porter qu'une suppression a un niveau
  profond. Le lire comme un instantane produit des prix faux. Les mesures
  passent par `prism_v2.l2book`, avec chainage `prevSeqId -> seqId` et
  invalidation sur trou.
- **Causalite structurelle.** Toute valeur lue a l'instant `t` est la derniere
  connue *strictement avant* `t`. Dans `xsec`, le beta d'une paire est estime
  sur une fenetre qui se termine avant le debut de la fenetre de dislocation :
  la dislocation ne participe pas a l'estimation de son propre beta.
- **Multiplicite.** Chaque balayage teste plusieurs instruments et plusieurs
  horizons. Benjamini-Hochberg a q = 0,10 est applique sur l'ensemble des
  tests, pas sur le meilleur.
- **Placebo.** Un survivant unique n'est pas un resultat. `mm_placebo` melange
  les sens d'agresseur : si le resultat survit au melange, il ne doit rien a
  ce qu'il pretend mesurer.

## Resultats

Voir `SCAN_DONNEES.md` a la racine du depot.
