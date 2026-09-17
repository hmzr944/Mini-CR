# Audit d'architecture — la faiblesse structurelle, nommée

Audit demandé sur l'architecture du projet. Voici ce qu'il trouve, y compris
ce que j'aurais dû voir il y a longtemps.

## La faiblesse : je paie 2 à 5× ce que paient mes concurrents

Vérification dans la documentation des venues :

| venue | frais négatifs (rabais) à partir de | accessible à un petit capital ? |
|---|---|---|
| OKX | VIP 7–9 (volume institutionnel) | **non** |
| OKX VIP 1 | 100 k$ d'actifs **ou** 5 M$ de volume/30 j | à la limite |
| Hyperliquid | **0,5 % du volume maker de toute la plateforme** | **non** |

**Toutes les incitations de frais récompensent le volume, donc la taille.**
Il n'existe aucun palier qui avantage un petit capital. Je trade à VIP 0 :
5 bps taker, ~10–11 bps l'aller-retour. Un acteur professionnel paie 2 à
2,4 bps, ou touche un rabais.

**Sur toute stratégie dont l'edge brut est de l'ordre de 10–30 bps, je pars
avec un handicap de 2 à 5× sur le même trade.**

## Ce que cela explique rétrospectivement

Les cinq familles de prévision mortes avaient toutes un edge brut du même
ordre de grandeur que mon désavantage de frais :

| famille | edge brut | mon coût | coût d'un pro |
|---|---:|---:|---:|
| momentum 7 j | ~5 bps/unité de turnover | 6,54 | ~2,4 |
| funding inter-venues | 12–27 bps | 39 (4 traversées) | ~14 |
| fade de liquidation | 12–36 bps | 11 | ~5 |

Je ne perdais pas seulement parce que le signal était faible. **Je perdais
sur des signaux qui auraient pu être rentables pour quelqu'un payant moins.**
C'est une contrainte d'accès, pas une contrainte de marché — et je ne l'avais
jamais formulée.

## Ce que la contrainte laisse ouvert

Une seule classe : **celles dont le mouvement brut écrase les frais.**

```
surdépassement de liquidation :  45 à 81 bps
mon coût d'aller-retour       :  10 à 11 bps
rapport                       :  4 à 7x
```

Ce n'est pas un hasard si c'est la famille où j'en suis. L'audit ne me dit
pas de pivoter : il dit que j'étais sur la seule piste compatible avec ma
position, et que j'y suis arrivé sans avoir formulé pourquoi.

## Les autres faiblesses, par ordre d'importance

1. **REST uniquement, aucun WebSocket.** Latence 300 ms – 1 s, résolution
   1–2 s. Toute microstructure fine est invisible et non compétitive. C'est
   une limite réelle, mais elle ne mord que sur des familles déjà fermées
   par les frais.
2. **Géo-blocages.** Binance et Bybit inaccessibles depuis cet
   environnement — mesuré, pas supposé. Cela ferme le lead/lag CEX et
   ampute l'univers.
3. **Aucune exécution réelle.** Tout est papier, donc slippage réel,
   position dans la file et taux de remplissage n'ont jamais été mesurés.
   Mes résultats sont des bornes supérieures de capture.
4. **Une seule classe d'actifs.** Perpétuels crypto. Options, spreads
   inter-échéances, settlement, événementiel : jamais explorés.

## Le seul levier où le capital aide vraiment

Passer VIP 1 (100 k$ d'actifs détenus) réduit les frais. C'est le seul
endroit de tout ce projet où « plus de ressources » change l'économie plutôt
que la vitesse de recherche. Tout le reste — calcul, temps, effort — ne
déplace pas la contrainte.

## Ce que l'audit ne résout pas

Il explique pourquoi les familles à faible marge échouaient **pour moi**. Il
ne démontre pas qu'une famille à forte marge existe. Le fade de liquidation
a le bon rapport mouvement/coût, mais son edge n'est pas établi : `t = 1,31`,
intervalle de confiance `[−6,2 ; +31,1]` bps. Un audit ne remplace pas une
mesure.
