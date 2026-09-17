# Protocole gelé — portefeuille à position continue

**Écrit avant d'ouvrir la fenêtre de validation.**

## Ce que DISCOVERY a montré

Architecture : `état du marché → μ → π* = μ/(γσ²) → bande de non-négociation
→ livre`. Aucun motif, aucun déclencheur.

Résultat du carry pur (`signal_sign = +1`), 13 jours, 140 actifs :

```
pnl funding  +98,34 bps     le carry EST encaissé, il est réel
pnl prix    -416,74 bps     et il est plus que dévoré
coûts        -65,59 bps
net         -383,99 bps     =  -29,5 bps/jour
```

Régression transversale, n = 43 680 : le signal de carry prédit le rendement
suivant avec **β = −5,5 (t = −2,24)**. Un actif offrant +10,7 %/an de carry
dérive de **−59,1 %/an** en prix. Deux mesures indépendantes concordent.

**Conclusion : le carry en perpétuels n'est pas une prime, c'est une
compensation insuffisante.** Le funding négatif signale que le marché veut
être short — et il veut être short parce que le prix baisse.

## Hypothèse gelée pour la validation

Le funding est un indicateur de **positionnement**, pas une rente. Le
rendement total d'un long vaut `carry × (1 + β)`, négatif pour β = −5,5. On
inverse donc le signe : `signal_sign = −1`.

Tout le reste est inchangé — même μ, même σ, même bande, mêmes coûts, mêmes
bornes. **Un seul paramètre bascule, et c'est un signe.** C'est la plus
petite surface de surapprentissage possible, mais c'en est une.

## Ce qui invaliderait l'hypothèse

- net ≤ 0 en VALIDATION ;
- net > 0 en VALIDATION mais ≤ 0 en HOLDOUT ;
- résultat porté par moins de 5 actifs, ou par une seule journée ;
- résultat qui disparaît quand le coût passe de 4,5 à 9 bps.

## Discipline

Le signe a été choisi **après** avoir vu DISCOVERY. C'est une sélection. Elle
n'a de valeur que confirmée sur des données jamais regardées.

- VALIDATION (12 j) : ouverte une fois, pour confirmer ou infirmer le signe.
- HOLDOUT (12 j) : ouvert une seule fois, après gel définitif. Aucun
  paramètre ne bouge entre les deux.

Aucun autre paramètre ne sera ajusté. Si le résultat exige un réglage, c'est
qu'il n'existe pas.

---

# Résultat — hypothèse REJETÉE par le holdout

Coût **mesuré**, non réglé : frais Hyperliquid 4,5 bps + demi-spread p75
mesuré 2,04 bps sur les 140 actifs = **6,54 bps** par unité de turnover.
(demi-spread médian réel : 1,14 bps ; 89 % des actifs sous 4,5 bps.)

| fenêtre | jours | net bps | prix | funding | coûts | bps/jour | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| DISCOVERY | 13,0 | +229,5 | +415,7 | −97,8 | 88,4 | **+17,66** | 5,32 |
| VALIDATION | 5,0 | +27,6 | +85,2 | −37,2 | 20,4 | **+5,52** | 1,41 |
| **HOLDOUT** | 7,0 | **−182,6** | −117,2 | −29,4 | 36,0 | **−26,24** | **−7,57** |

**Critère d'invalidation n° 2 déclenché** : net > 0 en validation, ≤ 0 en
holdout. L'hypothèse est rejetée. Le signe inversé ne se généralise pas.

## Ce que cela dit

Le signal est un **momentum transversal de court terme** déguisé en carry. Il
gagne en régime de tendance et perd en retournement. DISCOVERY et VALIDATION
tombaient dans le même régime ; le HOLDOUT non. C'est le mode d'échec typique
d'un momentum court, et 45 jours ne contiennent pas assez de régimes
indépendants pour le distinguer du bruit.

## La faiblesse de conception, nommée

L'échauffement consomme 168 h (7 jours) par fenêtre. Sur 45 jours au total,
il reste **13 / 5 / 7 jours effectifs**. Une validation de 5 jours n'a aucune
puissance statistique. **L'expérience était sous-dimensionnée dès le départ**,
et c'est la vraie leçon : le résultat de DISCOVERY (Sharpe 5,32) était trop
beau pour un signal réel, et il l'était en effet.

## Ce qui n'a PAS été fait, délibérément

Aucun paramètre n'a été ajusté après le holdout. Aucune fenêtre n'a été
redécoupée, aucun actif exclu, aucun `lookback` re-testé. Les trois fenêtres
sont maintenant **brûlées** : toute recherche supplémentaire sur ces 45 jours
serait contaminée, et son résultat sans valeur.

Le honnête prochain pas n'est pas un réglage : c'est **davantage de données**
(un an minimum, plusieurs régimes), puis un nouveau gel.

## Ce qui survit

L'architecture. Le carry pur est mesuré **perdant et décisivement** :
β = −5,5 (t = −2,24, n = 43 680) — un actif offrant +10,7 %/an de carry dérive
de −59,1 %/an. Ce résultat-là est cohérent sur les trois fenêtres et ne
dépend d'aucun réglage.
