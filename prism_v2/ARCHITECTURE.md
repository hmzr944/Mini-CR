# Architecture PRISM V2 — état réel

Relevé sur le dépôt, pas de mémoire. **16 953 lignes de code**, **8 809 lignes
de tests**, **908 tests verts**, 24 documents, 273 Mo de données versionnées.

---

## 1. Les six couches

```
┌─────────────────────────────────────────────────────────────────────┐
│  COUCHE 6 — FALSIFICATION            la plus récente, la plus utile │
│  sanity · mechanism_screen · postmortem · hypothesis_gate           │
│  capital_efficiency                                                  │
│  « la quantité calculée correspond-elle à ce que son nom dit ? »    │
├─────────────────────────────────────────────────────────────────────┤
│  COUCHE 5 — RECHERCHE                                                │
│  portfolio · signals · long_test · momentum_test · forward_test      │
│  experiment · research · protocol · trials · validation              │
├─────────────────────────────────────────────────────────────────────┤
│  COUCHE 4 — OPPORTUNITÉS (enfichables)                               │
│  opp_funding · opp_liquidation · detectors                           │
│  le noyau n'en importe AUCUNE — garde d'architecture                 │
├─────────────────────────────────────────────────────────────────────┤
│  COUCHE 3 — MOTEUR                                                   │
│  opportunity · economics · costs · risk · router · sizing            │
│  execution · ledger · modes · capacity · reconciliation · replay     │
├─────────────────────────────────────────────────────────────────────┤
│  COUCHE 2 — PRIMITIVES DE MARCHÉ                                     │
│  instruments · orderbook · l2book · contracts · fees · quality       │
│  market_state · core_types                                           │
├─────────────────────────────────────────────────────────────────────┤
│  COUCHE 1 — COLLECTE                                                 │
│  wsclient (RFC 6455, stdlib pur) · ws_collector · observatory        │
│  market_data · venues · funding_feed · flow_feed · collector         │
└─────────────────────────────────────────────────────────────────────┘
```

### Ce qui fait tenir l'ensemble

`tests/v2/test_architecture.py` — **27 gardes** qui interdisent structurellement :

| garde | ce qu'elle empêche |
|---|---|
| `no_real_execution_path` | tout chemin vers un ordre réel |
| `no_private_endpoints_or_credentials` | toute clé, tout endpoint authentifié |
| `core_never_imports_a_concrete_detector` | le noyau qui connaîtrait une stratégie |
| `new_family_needs_zero_core_change` | une famille qui exigerait de modifier le noyau |
| `no_technical_indicators_in_detectors` | RSI, MACD, Bollinger — l'héritage V33 |
| `no_kelly_anywhere`, `no_score_based_sizing` | le dimensionnement par score inventé |
| `no_machine_learning`, `no_optimiser_in_v2` | la boîte noire et le sur-apprentissage |
| `no_financial_function_accepts_a_bare_symbol` | « BTC » au lieu d'un `InstrumentSpec` complet |
| `thresholds_are_named_and_documented_not_inline` | le seuil magique enfoui |

C'est la garde `no_signal_generation_identifiers` qui a rattrapé ma variable
`bb` (somme des bêtas au carré), lue comme *Bollinger Bands*. Elle avait
raison de sonner.

---

## 2. Le flux d'une décision

```
  collecte WS/REST
        │
        ▼
  InstrumentSpec ──────► OrderBook ──────► QualityReport
  (ctVal, ctType,        (bids/asks,       (STALE_BOOK, SEQUENCE_GAP,
   settleCcy, lotSz)      seq, ts)          CLOCK_ANOMALY)
        │                     │                    │
        └─────────────┬───────┴────────────────────┘
                      ▼
                MarketContext ──► OpportunityRegistry.detect_all()
                      │                    │
                      │                    ▼
                      │              Candidate(gross_capture_bps,
                      │                        capacity, provenance,
                      │                        causal_reference_ts)
                      ▼                    │
              CostBreakdown ───────────────┤
              fees/spread/impact/          │
              slippage/latency/            │
              funding/adverse              │
                      │                    │
                      ▼                    ▼
             ═══════ ORDRE NON CONTOURNABLE ═══════
             QUALITÉ → ÉCONOMIE → CAPACITÉ → RISQUE
             ═══════════════════════════════════════
                      │
                      ▼
              Evaluation(ACCEPTED | REJECTED | UNRESOLVED)
                      │
        ┌─────────────┴──────────────┐
        ▼                            ▼
   CapitalRouter               CaptureLedger
   (rang, groupe de            (append-only, 60 champs,
    corrélation, alloc)         cost_quality par composante)
        │
        ▼
   PaperExecutor ──► PaperFill ──► Reconciliation
   (JAMAIS de RealExecutor)
```

**Une donnée inutilisable produit `UNRESOLVED`, jamais un rejet économique.**
Confondre « je ne peux pas mesurer » et « ce n'est pas rentable » fait
abandonner une piste vivante ou poursuivre une piste morte.

---

## 3. Le typage des coûts — le cœur de l'honnêteté

Chaque composante porte une **qualité**, et `UNKNOWN` n'est jamais converti
en zéro :

```
OBSERVED   mesuré sur données réelles
DERIVED    calculé depuis une mesure
ASSUMED    barème public, non vérifié sur compte
UNKNOWN    non mesuré — bloque la conclusion
EXCLU      déclaré absent, résultat = BORNE INFÉRIEURE du coût
```

`total_bps()` rend `None` si une composante **essentielle** est `UNKNOWN`.
On ne somme jamais en ignorant un trou.

Le statut `EXCLU` est structurel : `evaluate(mode=EXECUTION)` **refuse** toute
candidate portant une composante exclue. Le bot ne peut donc pas servir de feu
vert de déploiement, par construction et non par discipline.

---

## 4. La couche de falsification (la plus récente)

Née de l'audit : **866 tests passaient et n'ont attrapé aucun de mes 14
défauts**, parce qu'ils vérifiaient ce que le code faisait, jamais si la
quantité correspondait à la réalité qu'elle nommait.

| vérification | défaut réel qu'elle attrape |
|---|---|
| `check_magnitude` | `ctVal` en dur, cadence funding 4 h/8 h, tailles en contrats |
| `check_firing_rate` | référence médiane nulle, fenêtre jamais prête, mélange S5 ≡ 0 |
| `check_causal_direction` | prendre un symptôme pour une cause (Phase C entière) |
| `check_invariant` | dollar-neutre ≠ market-neutre (β = +0,111) |
| `check_novelty` | retester une famille fermée sous un autre nom |
| `check_publication_lag` | dater un événement de sa découverte |

**Rejeu sur les défauts réels : 10 sur 10 attrapés.**

`mechanism_screen` applique la méthode : cribler l'espace des **causes** au
lieu de tester une famille de plus. Cinq événements candidats, verdict
symptôme/candidat, seuil économique à 11 bps.

---

## 5. Données versionnées

| jeu | taille | contenu |
|---|---|---|
| `data/events` | **245 Mo** | 335 806 événements WS, 6,1 h — 263 145 carnets, 66 775 trades, 322 liquidations |
| `data/observatory` | 27 Mo | snapshots 250 ms, 6,5 h |
| `data/funding` | 1,7 Mo | 142 actifs × 45 j (HL) |
| `data/liquidations` | 308 Ko | 57 instruments, 26 h (OKX REST) |
| `data/polymarket` | 596 Ko | carnets, question fermée |

**Réserve scellée** : 225 jours OKX, dernier quart du panneau momentum,
jamais ouvert. Usage unique, après gel complet.

---

## 6. Deux corrections à mes propres affirmations

### Le WebSocket existe, et mon audit disait le contraire

`AUDIT_ARCHITECTURE.md` affirmait « REST uniquement, aucun WebSocket ».
**C'est faux.** `wsclient.py` est un client RFC 6455 complet en stdlib pur,
traversant le proxy CONNECT, utilisé par `observatory` et `ws_collector`.

Conséquence mesurée : le délai de publication des liquidations vaut
**1,231 s en WebSocket** contre **2 434 s en REST** — un facteur 2 000. J'ai
mené tout le crible de mécanismes en REST à 6 secondes alors que l'outil
millisecondes était dans le dépôt.

### Le verdict de la Phase C est plus faible que je ne l'ai dit

Ma conclusion — « le prix cause la liquidation » — reposait sur 73 événements
dont les horodatages étaient réels, mais **sélectionnés parmi les liquidations
publiées assez vite pour tomber dans ma fenêtre REST**. C'est une sélection
sur la vitesse de publication, dont je ne connais pas les effets.

Les données WS lèveraient l'ambiguïté, mais **le collecteur souscrit aux
carnets sur 23 instruments alors que les liquidations arrivent sur 86** : 6
événements exploitables seulement. Les deux souscriptions ne sont pas
appariées.

**La Phase C doit donc être rouverte comme non résolue**, et non close. La
correction est simple et c'est la prochaine collecte : souscrire carnets,
trades et liquidations sur le **même** ensemble d'instruments, choisi pour sa
fréquence de liquidation.

---

## 7. Ce que l'architecture sait faire, et ce qu'elle ne sait pas

**Sait faire**
- collecter en temps réel, deux venues, avec double horodatage et suivi de séquence
- reconstruire un carnet, valider sa qualité, refuser une donnée périmée
- chiffrer un coût typé où l'inconnu reste inconnu
- rejouer causalement, sans look-ahead structurel
- évaluer, router, dimensionner, exécuter en papier, réconcilier, journaliser
- geler un protocole, corriger pour tests multiples, ouvrir un holdout une fois
- **attraper ses propres erreurs de correspondance**

**Ne sait pas faire**
- exécuter un ordre réel — et ne le saura pas : garde d'architecture
- mesurer le slippage réel, la position dans la file, le taux de remplissage
- accéder à Binance et Bybit — géo-bloqués, mesuré
- descendre sous ~300 ms de latence aller-retour
- sortir des perpétuels crypto : options, spreads inter-échéances,
  settlement, événementiel — jamais explorés

**Le chiffre qui résume la contrainte** : mon aller-retour coûte 10–11 bps ;
le plus grand effet de microstructure observable en vaut 2,03.
