# Protocole gelé — vitesse du momentum, données fraîches OKX

**Écrit avant toute exécution sur ces données.**

## Ce que le diagnostic impose

La décomposition du turnover (mesurée, 857 barres) :

```
signal + vol      turnover 0,1486 / barre
mu gele           turnover 0,0088 / barre    (6 %)
mu ET vol geles   turnover 0,0005 / barre    (0 %)
```

**Le moteur ne fabrique aucun turnover — 94 % vient du signal.** Le coût
vaut 0,97 bps/barre, soit 5,83 bps/jour à levier brut 1,0, soit **21 %/an de
frais**. Le problème n'est donc pas d'exécution mais de **vitesse de signal**.

C'est ce que ce test attaque, et rien d'autre : *un signal plus lent
conserve-t-il assez d'alpha en payant beaucoup moins de turnover ?*

## Données — fraîches, jamais utilisées

OKX, 200 perpétuels USDT linéaires, barres 4 h, jusqu'à 900 jours.
Venue différente, univers différent, période plus longue que le panneau
Hyperliquid (qui est **brûlé** et ne sera pas réutilisé).

Le funding OKX est plafonné à 92 jours par l'API : **aucun signal de carry
n'est testé ici**. Les tester sur 92 jours serait retomber exactement dans le
sous-dimensionnement qui a invalidé le test précédent.

## Signaux pré-enregistrés — quatre, une paramétrisation chacun

| id | μ | fenêtre | justification a priori |
|---|---|---:|---|
| **M1** | rendement passé moyen | 42 barres (7 j) | témoin : la vitesse déjà testée, qui perdait par les coûts |
| **M2** | rendement passé moyen | 180 barres (30 j) | ~4× moins de turnover. C'est l'hypothèse. |
| **M3** | rendement passé moyen | 540 barres (90 j) | plus lent encore ; borne de la plage |
| **R1** | −rendement passé | 6 barres (1 j) | témoin de retournement |

La plage 7 j → 90 j est choisie pour **couvrir** l'axe vitesse, pas pour
trouver le meilleur point. M1 et R1 sont des témoins dont je connais déjà le
comportement ailleurs : s'ils se comportent autrement ici, c'est le test qui
est suspect, pas eux.

## Moteur — corrigé, et identique pour les quatre

`γ = 1`, `gross_leverage = 1,0`, `max_weight = 0,10`,
**neutralité dollar ET bêta**, `band_multiple = 2,0`, `cost_bps = 6,54`,
volatilité et bêtas sur 180 barres, tous causaux.

La neutralité bêta est une **correction de justesse**, justifiée avant tout
résultat : un livre dollar-neutre portait 23,8 % de variance marché.

## Découpage et statistique

50 % / 25 % / 25 % chronologique. Benjamini-Hochberg q = 0,10 sur les
p-values de VALIDATION. Seuls les survivants passent au HOLDOUT, ouvert une
seule fois. Si aucun ne survit, le holdout n'est pas ouvert.

## Invalidation

1. net ≤ 0 en VALIDATION ;
2. net > 0 en VALIDATION et ≤ 0 en HOLDOUT ;
3. ne survit pas à BH ;
4. disparaît quand le coût double (13 bps).

## Ce que je rapporterai

Les quatre, sur les trois fenêtres, plus le **turnover et le ratio
alpha/turnover** de chacun — puisque c'est la grandeur que ce test existe
pour mesurer.

Aucun cinquième signal ne sera ajouté. Aucun paramètre ne bougera.
