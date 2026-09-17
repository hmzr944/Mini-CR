# Phase A — gel de l'acquis

État figé du projet après la première grande phase de recherche.

## 🟢 Ce qui est construit, testé, et conservé

| composant | fichier | rôle |
|---|---|---|
| Moteur de position continue | `portfolio.py` | état du marché → π* → bande → livre |
| Neutralité dollar **et** bêta | `portfolio.py` | corrigée ; bêta résiduel ~0,01 contre −0,307 |
| Signaux pré-enregistrés | `signals.py` | quatre μ, une paramétrisation chacun |
| Harnais de test long | `long_test.py` | BH, t-test, découpage, passage unique |
| Test de vitesse | `momentum_test.py` | alpha par unité de turnover |
| Post-mortem | `postmortem.py` | à quel barreau chaque famille meurt |
| Porte d'hypothèse | `hypothesis_gate.py` | grille à franchir avant tout code |
| Critère de capital | `capital_efficiency.py` | bps/jour, levier requis, benchmark HLP |
| Bot de criblage | `bot.py`, `funding_feed.py` | live, PAPIER, refus explicites |
| Coûts typés | `costs.py` | UNKNOWN jamais converti en zéro |
| **822 tests** | `tests/v2/` | dont les gardes d'architecture |

## 🟢 Données

| panneau | venue | couverture | statut |
|---|---|---|---|
| funding + prix 1 h | Hyperliquid | 45 j, 142 actifs | brûlé |
| funding + prix 4 h | Hyperliquid | 840 j, 161 actifs | brûlé |
| prix 4 h | OKX | 900 j, 200 actifs | discovery + validation brûlés |
| **holdout OKX** | OKX | **225 j** | **SCELLÉ** — jamais ouvert |

## 🔴 Ce qui n'est pas démontré

- **Aucun alpha crypto neutre.** Trois protocoles gelés, deux venues, cinq
  familles, tous négatifs hors échantillon.
- **Aucune stratégie prête pour le réel.** LIVE reste désactivé, aucune classe
  `RealExecutor`, aucune clé.
- Les barreaux **liquidité, capacité, capital immobilisé, PnL/€/jour** n'ont
  jamais été atteints : les familles mouraient avant. Ils restent `UNKNOWN`.

## La matrice de post-mortem

```
famille               venue   jours  STABILITE  ALPHA   COUT  LIQ  CAP   tuee a
carry (funding)       HL        840        NON     OK     OK    ?    ?   STABILITE
momentum 7 j          HL        840         OK     OK    NON    ?    ?   COUT
retournement 1 j      HL        840         OK    NON    NON    ?    ?   ALPHA
momentum 30 j         OKX       900        NON     OK     OK    ?    ?   STABILITE
momentum 90 j         OKX       900        NON    NON     OK    ?    ?   STABILITE
```

**Trois familles sur cinq franchissent le barreau du coût et meurent un cran
plus tôt.** Les rendre moins chères ou plus lentes ne les aurait pas sauvées —
ce à quoi j'ai pourtant consacré une part importante de l'effort. La
contrainte dominante est la **stabilité du signe**, pas le coût d'exécution.

## Ce que le gel préserve

Le moteur est devenu l'actif. Il permet de tester une mécanique économique
entièrement différente sans reconstruire l'infrastructure : changer de
famille, c'est écrire un `μ` et le déclarer. Tout le reste — risque, coûts,
turnover, neutralité, capacité, ledger, discipline de holdout — est en place
et vérifié.

**La prochaine étape n'est pas « signal n° 5 ». C'est un nouveau terrain, avec
le même moteur de falsification derrière, et la porte franchie avant toute
collecte.**
