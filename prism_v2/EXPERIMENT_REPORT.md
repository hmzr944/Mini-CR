# Rapport expérimental final — DISCOVERY → PROOF → PAPER

> Ce rapport remplace `FINAL_REPORT.md` (audit de clôture, commit `4a8fea9`)
> sur tout ce qui concerne les résultats. L'audit d'architecture qu'il
> contient reste valide ; ses sections F (Open Discovery) et V (runs) sont
> **périmées** et remplacées ici.

**Verdict : `NO_VALIDATED_EDGE`** — 6/14 conditions du standard de preuve.

Donnée : 6,50 h continues, 15 perpétuels inverses OKX, 1 173 717 instantanés
de carnet (`hunt` session `2026-09-16T19:26:02Z`).

---

## A. Architecture finale

```
OBSERVATORY (6,5 h, 250 ms, 10 niveaux)   observatory.py
        ↓
SNAPSHOT SERIES (causale par construction) research/causal_lab.py
        ↓
PROTOCOLE 4 SEGMENTS (immuable, temporel)  research/protocol.py
        ↓
DISCOVERY → DEVELOPMENT → VALIDATION → FINAL HOLDOUT
        ↓
CAPTURE FRACTION · COÛTS · EXÉCUTION       experiment.py
        ↓
COMPTABILITÉ DES ESSAIS (DSR, PBO)         research/trials.py
        ↓
STANDARD DE PREUVE (14 conditions)         research/validation.py
        ↓
VERDICT
```

## B. Agents de recherche

Les six agents (`MarketObserver`, `Phenomenon`, `Relation`, `Mechanism`,
`Falsification`, `CaptureResearch`) et l'orchestrateur restent en place et
testés. **Ils n'ont pas produit le résultat de ce rapport** : l'expérience
finale court-circuite l'étage hypothèse/relation pour mesurer directement la
grandeur dont le défaut n°8 avait montré l'absence — la fraction de réversion.
Le dire autrement serait leur attribuer un travail qu'ils n'ont pas fait.

## C. Découverte d'opportunité

Espace balayé : 15 instruments × 3 lookbacks × 4 horizons × 6 seuils × 2 paris
= **2 160 configurations**, toutes comptées comme essais.

## D. Open Discovery — mesuré, plus déclaré

`open_discovery_test.audit()` retournait `STRUCTURED` **en dur**. Module
supprimé. `research/open_discovery.py` mesure trois choses :

| Mesure | Résultat |
|---|---|
| Taille de l'espace | 2 160, **énumérable avant le run** → `BOUNDED_HYPOTHESIS_ENGINE` |
| Sensibilité (effet planté 0,50) | retrouvé à **0,439** (N=348) → `engine_is_blind = False` |
| Spécificité (bruit pur) | +0,046 ± 0,050, t = +0,92 → `engine_hallucinates = False` |

Le verdict **découle** de `is_enumerable_by_hand` ; un test vérifie que rendre
l'espace non énumérable le changerait. Un audit sans les tests empiriques
retourne `UNDETERMINED`, jamais un verdict non gagné.

**Sans la mesure de sensibilité, « 0 survivant » était ininterprétable** : un
moteur aveugle produit exactement le même résultat qu'un marché sans
inefficience. C'est désormais tranché — le moteur voit.

## E. Registre d'hypothèses · F. Nombre total d'essais

**3 307 essais** comptés (2 160 DISCOVERY + 1 026 DEVELOPMENT + reste).
Tout essai compte, y compris abandonné et perdant.

## G. Correction pour tests multiples

| Grandeur | Valeur |
|---|---|
| Essais | 3 307 |
| Dispersion des Sharpe (essais ≥ 30 obs.) | 2,25 |
| PBO (CSCV, 922 configs, 70 partitions) | **0,000** |

**Garde d'interprétation, déclenchée dans le run** : la meilleure
configuration perd −9,222 bps. Un PBO bas ne signale ici qu'un **classement de
coûts stable**, pas un edge robuste. Le PBO mesure si la *sélection*
généralise, jamais si le sélectionné est rentable.

### Le piège des tests multiples, en chiffres

**3 configurations sur 2 160 sont nettes positives en échantillon :**

| net | N | configuration |
|---|---|---|
| **+11,189 bps** | **1** | `DOT-USD-SWAP\|lb2000\|h30000\|thr4.0\|REVERSION` |
| +3,180 bps | 4 | `DOT-USD-SWAP\|lb2000\|h30000\|thr2.0\|CONTINUATION` |
| +2,250 bps | 2 | `DOT-USD-SWAP\|lb5000\|h15000\|thr4.0\|CONTINUATION` |

Zéro avec au moins 30 événements. La « meilleure stratégie du balayage »
repose sur **un seul événement**. C'est exactement ce que l'appareil existe
pour attraper, et il l'a attrapé.

## H–K. Phénomènes, relations, hypothèses, falsification

| Étage | Compte |
|---|---|
| Événements déclenchés | 617 820 |
| Mesurables causalement | 605 200 (97,96 %) |
| Instants non évaluables (historique insuffisant) | 359 760 — *rien rejeté, rien testé* |
| Refus d'événements : impact UNKNOWN | 11 642 |
| Refus d'événements : déplacement nul | 978 |

## L. Résultat causal — la mesure centrale

**Fraction de réversion : −0,0511 ± 0,0194 (t = −2,63) sur 79 487 événements
uniques** (605 200 mesures avant dédoublonnage).

Mouvement récupérable brut : **−0,140 ± 0,011 bps**.

Lecture honnête : |t| = 2,63 sur **3 307 essais**. Le seuil de Bonferroni à
5 % exigerait |t| ≈ 4,1. Cette valeur n'est **pas** une découverte ; elle est
compatible avec zéro une fois la multiplicité prise en compte. Le signe
négatif indique une très légère **continuation**, pas une réversion.

### Les deux paris, testés tous les deux

| Pari | N | brut | net | part brut > 0 |
|---|---|---|---|---|
| RÉVERSION | 302 574 | −0,1788 bps | −12,12 bps | 35,3 % |
| CONTINUATION | 302 626 | **+0,1787 bps** | −11,83 bps | 39,7 % |

Exactement opposés, comme l'invariant l'impose. La continuation a un brut
positif de **+0,18 bps** — face à un péage de ~12 bps.

## M. Fraction de capture — caractérisation de l'instrument

Sur données synthétiques à vérité connue :

| Vérité plantée | Mesuré à l'événement | Mesuré sur grille |
|---|---|---|
| 0,00 | 0,007 | 0,007 |
| 0,25 | 0,289 | 0,171 |
| 0,50 | 0,447 | 0,350 |
| 0,80 | 0,727 | 0,572 |

Bruit pur : +0,007 ± 0,032, t = +0,24.

**L'estimateur est non biaisé à l'événement.** L'atténuation à ~0,70 sur
grille est un effet du maillage (ratio stable sur cinq niveaux de volatilité,
donc pas de la dilution). D'où la règle adoptée : déclencher **au
franchissement**, jamais sur grille.

## N. Latence

Délai de transport mesuré : **p50 116 ms, p90 127 ms, p99 176 ms**
(N = 1 173 717). C'est un délai exchange → local, borne **inférieure** de ce
qu'un ordre subirait. La latence aller-retour d'un ordre reste `UNKNOWN`.

| latence | brut | net |
|---|---|---|
| **0 ms** | +1,358 | **−10,345** |
| 250 ms | +1,264 | −10,525 |
| 2 000 ms | +1,501 | −10,282 |

**À latence nulle le net reste négatif.** La latence n'est pas la contrainte.

## O. Simulation d'exécution

Carnets d'entrée et de sortie **distincts** (T0+latence, +horizon). Impact non
mesurable (profondeur enregistrée épuisée) ⇒ événement **non résolu**, jamais
coût nul. 11 642 événements écartés à ce titre.

## P–R. Frais, slippage, impact

| frais/jambe | AR | net |
|---|---|---|
| **0,0** | 0,0 | **−3,118** |
| 2,0 | 4,0 | −7,118 |
| 5,0 (barème public Lv1) | 10,0 | −13,118 |

**Même à frais nuls, le net reste −3,12 bps.** Obtenir des frais `OBSERVED` —
le blocage principal du projet depuis le début — **ne changerait pas le
verdict de recherche**. Ce blocage porte sur l'exécution, pas sur l'existence
d'un edge.

Slippage d'exécution : `UNKNOWN`, exclu explicitement, jamais mis à zéro.

## S. Capacité

`SOL-USD-SWAP|lb15000|h15000|thr16.0|REVERSION`, brut +1,264 bps :

| notionnel | net |
|---|---|
| 10 $ | −9,995 |
| 100 $ | −10,134 |
| 1 000 $ | −11,664 |
| 10 000 $ | −14,826 |

L'impact mord à partir de ~250 $. **Aucune taille ne donne un net positif** —
le brut de +1,26 bps ne couvre jamais le péage.

## T–V. PnL PAPER, hors échantillon, holdout final

**Le FINAL HOLDOUT n'a pas été ouvert.** Aucune configuration n'étant nette
positive en DEVELOPMENT, il n'y avait rien à valider. Un holdout ouvert pour
rattraper un échec antérieur n'est plus un holdout.

`final_holdout: {"opened": false, "why": "aucune configuration selectionnee"}`

## W–X. Drawdown, risque

**Non mesurés.** Les événements de ce protocole ne forment pas une série de
positions consécutives : il n'y a pas de courbe d'équité à drawdowner. Les
deux conditions sont donc **non satisfaites**, et le rapport ne prétend pas le
contraire.

## Y. Attribution d'échec — où meurt l'edge

```
    617 820  100,00 %   événements déclenchés
    605 200   97,96 %   mesurables causalement
    226 828   36,71 %   mouvement récupérable > 0
      3 189    0,52 %   net de coûts > 0
          0    0,00 %   configurations nettes positives en DEVELOPMENT
          0    0,00 %   survit à VALIDATION
          0    0,00 %   positif sur FINAL HOLDOUT
```

**L'edge meurt entre 36,71 % et 0,52 %** — au passage des coûts. Pas à la
latence, pas à la taille, pas aux frais : au **spread traversé deux fois**.

## Z. Stabilité de régime

| découpage | bas | moyen | haut |
|---|---|---|---|
| par spread | −10,134 | −11,975 | **−17,611** |
| par amplitude | −10,361 | −12,270 | **−17,089** |

Ensemble : −13,240 ± 0,018 bps (N = 79 487, t = −731,6).

**Le net empire quand l'amplitude monte.** Les gros mouvements coïncident avec
les spreads larges : le coût de traversée croît plus vite que l'opportunité.
C'est la réponse à « l'edge existe-t-il aux grandes amplitudes ? » — non.

## AA. Décroissance

Pente **+0,272 bps par tranche** sur 6 tranches de 26 min. Une fenêtre de
6,5 h ne permet pas de distinguer une tendance d'une fluctuation ; ce chiffre
est rapporté, pas interprété.

## AB–AE. Corrélation, nombre de trades, turnover, capital

Anti-chevauchement : deux événements distants de moins d'un horizon partagent
leur issue — cooldown = horizon. Dédoublonnage par (instrument, instant) :
605 200 mesures → **79 487 événements uniques**.

Capital : aucun engagé. Aucun ordre réel, aucune clé.

## AF. Préparation DEMO — **BLOQUÉ**

5 pré-requis manquants : `observed_fees`, `account_state_channel`,
`order_state_channel`, `execution_reconciliation`, `api_permissions_checked`.

## AG. Blocages LIVE — **BLOQUÉ**

8 pré-requis manquants. `SystemMode.LIVE.is_implemented == False`.
`MODE_TRANSITIONS` interdit `DISCOVERY → LIVE`. Test :
`test_live_stays_refused_even_with_every_capability_declared`.
**Aucun garde n'a été affaibli.**

## AH. Défauts adversariaux — 8 antérieurs + 10 de cette phase

| # | Défaut | Conséquence |
|---|---|---|
| 9 | Collecteur mort en silence 2 h 30 ; `pgrep` matchait sa propre commande | 16 min de données au lieu de 6 h |
| 10 | `continue` dans le gestionnaire d'erreur sautait l'écriture d'instantanés | fichier muet indiscernable d'un marché calme |
| 11 | Flux gzip en écriture illisible ; `zlib.error` non attrapé | collecte inanalysable |
| 12 | **Ouverture en mode APPEND derrière un flux tronqué** | **6,5 h lues comme 0,29 h** |
| 13 | Dispersion des Sharpe polluée par des essais de 2 obs. | 399 → 2,56 |
| 14 | Même événement compté une fois par configuration | N 1 493 → 452 |
| 15 | Seuils ne testant que des cas structurellement perdants | 1–4 bps face à 10 bps de péage |
| 16 | **Seule la réversion testée** | supposait la réponse |
| 17 | **Seuil calculé sur la médiane de tout le segment** | **look-ahead + contamination du holdout** |
| 18 | `INSUFFICIENT_DATA` avec 22 993 événements mesurés | conclusion inverse de la vérité |
| 19 | `cadence_ms()` retriait 93 600 écarts à chaque appel | 440 s sur 450 de profil |
| 20 | PBO = 0,000 lisible comme un feu vert | garde d'interprétation ajoutée |

Les n°12, 16 et 17 sont les plus graves : le premier a failli invalider toute
la collecte, les deux autres rendaient la méthode indéfendable.

## AI. Tests

**563 tests verts**, dont 111 adversariaux et 47 sur le laboratoire causal.
Les 30 points de la checklist finale ont une couverture directe.

## AJ. Verdict final

### `NO_VALIDATED_EDGE`

| Condition | |
|---|---|
| causalité | ✅ |
| coûts observés ou bornés | ✅ |
| exécution réaliste (carnets distincts) | ✅ |
| **PnL net positif** | ❌ |
| **positif sur holdout** | ❌ (non ouvert) |
| **stabilité temporelle** | ❌ |
| **capacité non nulle** | ❌ |
| nombre de trades suffisant | ✅ |
| **risque mesuré** | ❌ |
| **drawdown mesuré** | ❌ |
| **survit aux tests multiples** | ❌ |
| aucune fuite temporelle | ✅ |
| aucun fill irréaliste | ✅ |
| **aucun UNKNOWN critique** | ❌ |

**6/14.**

---

## Réponse à la question du mandat

> *Existe-t-il, dans les données accessibles, une inefficience assez
> persistante et assez capturable après coûts, latence, impact et contraintes
> d'exécution pour produire un PnL net positif hors échantillon ?*

**Non, et le système démontre précisément pourquoi.**

Sur 6,5 h, 15 instruments, 617 820 événements et 2 160 configurations :

1. Le mouvement récupérable après un déplacement est **−0,140 ± 0,011 bps** —
   indiscernable de zéro une fois la multiplicité prise en compte. Le mid est
   une martingale à ces horizons.
2. Les deux paris possibles ont été testés. Le meilleur brut est **+0,18 bps**.
3. Traverser le spread deux fois coûte ~3 bps ; avec les frais publics, ~12 bps.
4. **Ni la latence, ni la taille, ni les frais ne sont la contrainte** — le net
   reste négatif à latence nulle, à 10 $ de notionnel, et à frais nuls.

**Il n'y a rien à capturer. Ce n'est pas un coût trop élevé à payer.**

### Ce qui est prouvé

- L'instrument ne fabrique pas d'edge à partir de bruit (t = +0,24) et n'est
  pas aveugle à un effet planté (0,50 → 0,447).
- Les trois contraintes candidates sont éliminées, chacune par une mesure.
- 3 configurations sur 2 160 paraissaient gagnantes ; elles reposaient sur
  1, 4 et 2 événements. L'appareil les a écartées.

### Ce qui n'est pas prouvé

- Qu'il n'existe aucune inefficience **ailleurs** : une seule venue, 15
  instruments inverses, 6,5 h, une seule famille de phénomène (déplacement de
  prix), un espace borné de 2 160 hypothèses.
- Que le résultat tient sur d'autres régimes : 6,5 h ne couvrent pas un
  week-end, une annonce macro, ni un épisode de stress.

### Ce qui reste inconnu

Slippage réel, probabilité de fill, position dans la file, adverse selection
réelle, latence aller-retour d'un ordre, tier de frais réel. Tous exigent un
compte authentifié ou des ordres réellement placés.

### La mesure unique qui réduirait le plus d'incertitude

**Non plus la fraction de réversion — elle est mesurée, et elle vaut zéro.**

Ce résultat déplace la question. Le spread traversé deux fois est le mur ; la
seule façon de ne pas le payer est de ne pas le traverser, c'est-à-dire
d'être **maker**. Or le mode MAKER est aujourd'hui structurellement
invalidable : l'adverse selection y est une composante `ESSENTIELLE` et
`UNKNOWN`, donc `total_bps()` retourne `None` et aucune stratégie maker ne
peut être évaluée.

**La mesure : la probabilité de fill et l'adverse selection d'un ordre passif,
par suivi de la file d'attente sur le canal `books` déjà collecté.** Elle est
faisable sans compte — en suivant la décroissance d'un niveau de prix on
distingue les exécutions des annulations — et elle est la seule qui ouvre une
famille entière aujourd'hui fermée par construction.

---

*Un moteur qui ne trouve rien et le démontre vaut mieux qu'un moteur qui
trouve quelque chose et se trompe. Mais « rien ici » n'est pas « rien nulle
part » : c'est « pas dans 6,5 heures de déplacements de prix sur quinze
perpétuels inverses d'une seule venue, en payant le spread ».*
