# PRISM — bilan de recherche au 22 septembre 2026

Document de décision unique. Aucune nouvelle collecte n'a été lancée pour le
produire.

**Règle de lecture, appliquée partout.** Chaque chiffre porte une étiquette :

| étiquette | sens |
|---|---|
| **REPRODUIT** | j'ai exécuté le code dans cette session et obtenu ce nombre |
| **CITÉ** | le nombre vient du dépôt ; les données qui l'ont produit n'existent plus ici |
| **SOURCE** | lu dans une documentation primaire d'une venue ou d'un régulateur |

Le dépôt tient déjà ce compte : `etat.py` affiche **« plafonds re-vérifiables
depuis ce dépôt : 3 sur 21 »**. Seuls `candles_1h.json`, `margin_tiers.json` et
`fwd_okx.json.gz` sont versionnés ; `panel.pkl`, `tape.pkl` et l'historique de
funding vivaient sous `/tmp` et ont disparu. **18 des 21 plafonds du registre
sont donc CITÉS, non recalculables.** Ce n'est pas un défaut découvert
aujourd'hui, c'est une propriété connue et affichée.

---

## 1. Objectif économique et critères d'acceptation

| | |
|---|---|
| capital | 1 000 EUR |
| cible | 2 000 EUR en ~60 jours |
| soit | **+116 bps/jour composés** |

**Écart à corriger dans le registre.** `etat.py` screene contre un seuil de
**271,87 bps/jour**, hérité d'un objectif antérieur (x5 en 60 j). L'objectif
courant est x2, soit 116 bps/jour. Le registre crible donc actuellement
contre une barre 2,3 fois trop haute. *Cela ne change aucune conclusion* — la
meilleure économie exécutable est 0,00 — mais la barre doit être corrigée
avant tout futur criblage, sans quoi un candidat entre 116 et 272 serait
rejeté à tort.

Critères d'acceptation d'un candidat, dans l'ordre :

1. **edge net** positif après frais, spread, sortie et sélection adverse ;
2. **exécution plausible** : fills démontrés, file modélisée, pas supposée ;
3. **fréquence et capacité** compatibles avec 1 000 EUR ;
4. **risque** borné, liquidation et contrepartie chiffrées ;
5. **robustesse** : hors échantillon, plusieurs régimes, seuils préenregistrés.

Aucune famille testée à ce jour ne franchit le critère 1.

---

## 2. Registre des familles

**REPRODUIT** — `python3 -m prism_v2.scans.etat`, exécuté aujourd'hui.

    ÉCONOMIE EXÉCUTABLE DÉMONTRÉE     0,00 bps/jour
    BORNE SUPÉRIEURE LA PLUS HAUTE   33,30 bps/jour  (mécanisme non établi)

21 familles au registre. Les cinq plus hautes :

| famille | plafond bps/j | classe de preuve | motif |
|---|---|---|---|
| flux couvert, durée optimale | 33,30 | mécanisme non établi | coût dominant |
| meilleure paire isolée (SKHYNIX/MU) | 19,60 | mécanisme non établi | coût dominant |
| liquidité à frais NULS | 13,60 | fill inconnu | magnitude insuffisante |
| non-crypto couvert | 12,84 | mécanisme non établi | non couvrable |
| machine complète | 10,36 | mécanisme non établi | coût dominant |

Onze familles ont un plafond **négatif**. Une seule est classée `EXECUTABLE`
avec un plafond non négatif : la politique adaptative conditionnelle, à
**0,00**.

Familles ajoutées ou fermées dans cette session :

| famille | statut | établi par |
|---|---|---|
| carry EEA (spot + short perp, Backpack EU) | **CANDIDAT** | REPRODUIT |
| cotation passive au toucher, BTC, barème 2 bps | **FERMÉE** | REPRODUIT |
| subvention market making (4 programmes, 3 venues) | **FERMÉE** | SOURCE |
| venues en amorçage / programmes de points | **NON RÉSOLU PAR CONSTRUCTION** | SOURCE |
| carry inverse/linéaire | **INACCESSIBLE** | SOURCE — aucun coin-margined sur entité EEA |

---

## 3. Expériences et versions de cette session

Toutes **REPRODUITES** sauf mention.

| expérience | protocole | résultat | limite |
|---|---|---|---|
| Accès juridique | API OKX publique, 486 SWAP | **0 X-Perp exposé** | OKX EU reste non mesurable |
| Latence | 7 aller-retours médians | **420 ms** vs 0,1 ms colocalisé | une mesure, un lieu |
| Intervalle de funding Backpack | horodatages, 3 symboles | **1,0 h**, non 8 h | — |
| Carry EEA | 43 perps à deux jambes, 60 j | **3 candidats, 7,4–10,1 %/an** | funding passé ≠ futur |
| Market making, fenêtre 75 min | 20 marchés, 16 360 instantanés | 2 mesures cohérentes, équilibre +0,12 / −0,16 | 1 créneau |
| Market making, fenêtre 7,4 min | BTC N=231 | équilibre **−3,57** | micro-régime |
| Market making, fenêtre 7,9 min | BTC N=351 | équilibre **+0,01** | micro-régime |
| Test placebo de dérive | dérive inconditionnelle à 60 s | **a tué mon seul résultat positif** | — |
| Frontière de levier | `conditions.rotations_required` | **IMPOSSIBLE** jusqu'à 100x | — |

---

## 4. Défauts de données et contamination

Six défauts trouvés dans cette session, tous par mes propres contrôles.

| # | défaut | effet | correctif |
|---|---|---|---|
| 1 | markout mesuré depuis le prix de fill | demi-spread compté **deux fois** | décomposition sur le mid, identité testée |
| 2 | demi-spread tiré de la bande | artefact de fraîcheur : 22,93 → 3,42 bps selon la fenêtre | `tape_mid_is_fit_for_level` |
| 3 | bande capturée après coup | BTC/ETH/SOL à « 0 échange » — **défaut de capture, pas marché mort** | `capture_tape` à la fin de la collecte |
| 4 | garde de couverture exigeant les deux bords | `NON` sur 6 marchés sains | resserrée au bord gauche |
| 5 | analyse à un seul côté | la hausse du marché lue comme un edge | `excess_markout_bps` |
| 6 | collectes d'arrière-plan fauchées | 7,4 min sur 35, puis 4,5 sur 40 | collecte au **premier plan** |

**Le défaut 5 mérite d'être retenu.** Il a produit un équilibre de +7,85 bps
sur NEAR, au-dessus du frais. Le test à blanc a montré une dérive
inconditionnelle de +1,36 bps/min quand le markout mesuré valait +1,18 : le
fill passif faisait **moins bien que ne rien faire**. Le marché était haussier
sur presque tout l'univers pendant la fenêtre.

**Contamination par ma propre garde, déclarée.** La garde de cohérence
(rapport demi-spread mesuré / demi-spread lu dans [0,5 ; 2,0]) écarte 4 lignes
sur 6 dans les dernières fenêtres, et elle a écarté ARB à +2,88 bps — le seul
chiffre au-dessus du frais. Une garde qui supprime systématiquement les
positifs doit être surveillée : les valeurs brutes et les motifs d'exclusion
sont conservés dans `backpack/FINDING.md` pour que ce soit vérifiable.

**Placebo : NON RÉALISÉ explicitement** sur les fenêtres BTC finales. Le rejeu
à deux côtés annule l'essentiel de la dérive par construction, mais ce n'est
**pas** un contrôle placebo démontré. À ne pas présenter comme tel.

---

## 5. Hypothèses encore ouvertes

Seulement celles qui ont une différence économique testable.

| hypothèse | statut | espérance |
|---|---|---|
| Cotation **à distance** du mid, corrigée de la dérive | non réfutée, **intestable** en 8 min (échantillon s'effondre dès 10 bps) | faible — loin du mid on n'est rempli que pendant les balayages, donc par le flux le plus informé |
| Stabilité du carry EEA sur plusieurs régimes | ouverte | le mécanisme est un flux de trésorerie publié, pas une prédiction |
| Marchés écartés par la garde de cohérence | ouverte | exige une cadence de sondage de 1 s, pas 5 s |
| ETH, SOL sous le seuil de 30 fills | **INCONNU**, seuil non déplacé | — |

Hypothèses **fermées par arithmétique**, non par mesure — aucune donnée
supplémentaire ne les rouvrira :

- le levier : `net = n·L·(e−c)` avec `(e−c) < 0`, et plafond retail à 2x ;
- la subvention MM : rebate 0,25–1,00 bps contre 2 160 $ de capital ;
- l'amorçage : payer au-dessus du marché exige un actif sans prix.

---

## 6. Règles de reprise

| famille | rouvrir si | fermer définitivement si |
|---|---|---|
| cotation passive au toucher | un barème maker **négatif** accessible à ce capital apparaît | — déjà fermée pour cette configuration |
| subvention MM | un programme à paiement **cash**, montant publié, seuil sous le capital | — |
| carry EEA | — | funding médian négatif sur 3 fenêtres indépendantes, ou perte d'accès |
| cotation à distance | une collecte de plusieurs **jours** à cadence 1 s | équilibre corrigé < 0 à toutes distances, N ≥ 30 |

Règle générale du dépôt, inchangée : une révision de plafond exige de
**dépasser** le plafond enregistré, avec preuve et échantillon
(`KillRegistry.revive`).

---

## 7. Prochaine expérience autorisée

**Aucune.**

Ce n'est pas une dérobade, c'est la conclusion du bilan. Le critère posé était
« une hypothèse qui change matériellement l'économie du système ». Je n'en ai
pas.

L'écart à combler est d'un **facteur 70** entre le seul mécanisme positif
mesuré (carry EEA, 12–17 EUR sur 60 jours) et l'objectif (+1 000 EUR). Les
trois leviers qui pourraient le combler sont tous fermés :

- **le rendement** — 21 familles, meilleure économie exécutable 0,00 bps/jour ;
- **le levier** — multiplie un signe négatif, et plafonné à 2x par l'ESMA ;
- **la fréquence** — les frais croissent avec elle, la simulation le montre.

Proposer une quatrième expérience de trading reviendrait à faire ce que ce
bilan existe pour empêcher : accumuler des données au lieu de décider.

**Ce qui change matériellement l'économie n'est pas une famille, c'est une
variable hors du système** : le capital, ou l'horizon.

    500 EUR/mois à 11 %/an  ->  54 500 EUR de capital
    1 000 EUR à 11 %/an     ->  1 110 EUR après un an

La prochaine décision appartient donc au titulaire du compte, pas au système
de mesure.

---

## Ce que ce bilan n'affirme pas

- **Pas** que PRISM ne peut pas trouver d'edge. Les configurations testées
  n'ont pas produit de preuve suffisante d'un edge net, capturable et robuste.
- **Pas** que le market making est perdant en général. Une configuration, un
  marché, un barème, des fenêtres courtes.
- **Pas** que le carry est déployable. Il est candidat, jamais exécuté, et sa
  faisabilité d'exécution n'a pas été vérifiée.

Ce qui a été produit, ce sont des **hypothèses éliminées et des contrôles de
mesure améliorés**. C'est une information utile à la recherche. Ce n'est pas
une voie démontrée vers l'objectif de rendement.
