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

---

# Résultat — aucun signal ne survit. Holdout NON ouvert.

200 actifs OKX, 5 400 barres 4 h, **900 jours**. Moteur corrigé
(neutralité dollar **et** bêta, bêtas résiduels mesurés entre 0,003 et 0,03).

## Les deux fenêtres, en bps/jour (levier brut 1,0)

| signal | turnover/barre | DISCOVERY 450 j | alpha/turn | VALIDATION 225 j | alpha/turn |
|---|---:|---:|---:|---:|---:|
| M1 MOM 7 j | 0,138 | −3,55 | +2,27 | +0,98 | +7,95 |
| **M2 MOM 30 j** | **0,043** | **+4,21** | **+20,64** | **−7,19** | **−21,35** |
| M3 MOM 90 j | 0,020 | −1,85 | −7,59 | +0,46 | +10,35 |
| R1 REV 1 j | 0,439 | −26,18 | −2,78 | +3,38 | +7,82 |

**Les quatre changent de signe.** M2, qui était l'hypothèse, passe de
+20,64 à −21,35 d'alpha par unité de turnover.

## Benjamini-Hochberg, q = 0,10

```
R1_REV_1j     p=0.3807  ->  rejete
M1_MOM_7j     p=0.4536  ->  rejete
M3_MOM_90j    p=0.4753  ->  rejete
M2_MOM_30j    p=0.8366  ->  rejete
```

Zéro survivant. **Le holdout n'est pas ouvert** : 225 jours de données OKX
restent propres et utilisables pour un test futur. C'est le seul actif
méthodologique que ce test produit, et il valait de ne pas le brûler.

## Ce que l'hypothèse valait — et ce qu'elle ne valait pas

Le diagnostic du turnover était **juste** : M2 tourne 2,8× moins que M1 et
conserve plus d'alpha brut. Le mécanisme est réel et mesuré.

Mais le mécanisme ne suffit pas. Réduire le coût ne sert à rien si le signal
n'a pas de signe stable — et il n'en a pas. Le ratio alpha/turnover de M2
s'inverse complètement d'une fenêtre à l'autre.

## Le constat qui revient pour la troisième fois

| test | données | résultat |
|---|---|---|
| Portefeuille carry | 45 j HL | holdout **négatif** |
| 4 signaux pré-enregistrés | 840 j HL | holdout **négatif** |
| 4 vitesses de momentum | 900 j OKX, moteur corrigé | **aucun survivant BH** |

Trois protocoles gelés, trois jeux de données, deux venues, un moteur
vérifié propre, une correction de justesse appliquée entre-temps. **Ce qui
brille en discovery s'inverse systématiquement ensuite.**

## Ce qui est démontré, et ce qui ne l'est pas

J'avais écrit ici « c'est la propriété du domaine ». **C'était une
surinterprétation, et je la retire.**

**Démontré :** les familles de signaux testées — carry, momentum de série
temporelle, retournement — sur perpétuels crypto, avec *ces* données, *ces*
horizons (1 à 90 jours), *ces* univers (161 actifs HL, 200 actifs OKX), *ces*
coûts (6,54 bps par unité de turnover) et *cette* construction de
portefeuille (transversale, neutre dollar et bêta), **n'ont pas démontré
d'edge stable hors échantillon.** C'est un résultat fort et il suffit à
fermer cette branche.

**Non démontré :** qu'aucun edge n'existe dans les perpétuels crypto. Je n'ai
exploré ni la microstructure fine, ni les options, ni les spreads
inter-échéances, ni l'événementiel, ni la dynamique de liquidation, ni les
contraintes de collatéral, ni la structure de base, ni le flux d'ordres
spécifique, ni l'information exogène, ni les mécaniques de settlement. La
distance entre « ces familles-là échouent » et « le domaine est vide » est
énorme, et rien dans mes mesures ne la franchit.

Arrêter cette branche est rationnel. Déclarer le domaine impossible serait
prématuré — et serait exactement le genre de conclusion trop large que ce
dépôt existe pour empêcher.
