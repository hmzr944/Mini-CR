# Audit total — carte CONFIRMÉ / INCERTAIN / FAUX / MANQUANT

Vérification contre le **code et les données réelles**, pas contre la
documentation. Chaque ligne a été rejouée ou inspectée.

---

## 🟢 CONFIRMÉ

| élément | vérification |
|---|---|
| **Mécanique des contrats** | Rejouée numériquement. INVERSE : notionnel USD = ctVal·sz·ctMult, **indépendant du prix** (100 $ à 50 000 comme à 90 000) ; PnL = 100·(1/50000−1/51000) = 3,9216e−5 BTC = 2,00 $ ; rendement coin 196,08 bps = (Px−Pe)/Px, rendement USD 200,00 bps. Conforme aux formules officielles OKX. |
| **Frais** | INVERSE 1,0e−6 BTC, LINEAR 0,25 USDT — **les deux valent exactement 5,0000 bps** du notionnel USD. Devises de règlement correctes. |
| **Données observatoire** | 1 173 717 snapshots **lus** contre 1 173 717 déclarés = **100,00 % de récupération**, span réel 6,50 h contre 6,49 déclaré, 15 instruments. Le lecteur tolérant multi-membres fonctionne. |
| **Markout maker** | Rejoué : demi-spread **+1,4020**, sélection adverse **−2,7432**, N = **8 465**, contrôle non conditionnel **0,0000** sur 23 092 échantillons (t = +0,00). Identique au rapport. |
| **Carry** | Rejoué : **191 bps/an**, 14 paires sur 15 de même signe. Le module énonce lui-même que la stratégie est **dominée** entièrement collatéralisée. |
| **Anti-look-ahead** | `book_at()` = `bisect_right(ts) − 1` : **ne peut pas** rendre un carnet futur. `is_resolvable()` refuse de mesurer sous l'intervalle de publication. |
| **Gardes d'architecture** | 27 gardes actives, 9 détecteurs enregistrés, aucun chemin vers un ordre réel, aucun endpoint authentifié. |
| **Délai WebSocket** | 1,231 s médian, p90 4,443 s. |

---

## 🟡 INCERTAIN

| élément | pourquoi |
|---|---|
| **Verdict Phase C** | Les 73 événements avaient de vrais horodatages mais étaient **sélectionnés parmi les liquidations publiées assez vite** pour tomber dans ma fenêtre REST (délai médian 2 434 s). Sélection sur la vitesse de publication, effets inconnus. |
| **Transfert inverse → linéaire** | Microstructure et markout ont été mesurés sur **15 contrats INVERSES** (`-USD-SWAP`). Tout mon travail récent porte sur des **linéaires**. Le transfert est plausible, **jamais testé**. |
| **Crible de mécanismes** | Mené à 6 s de résolution alors que des données milliseconde existent. Un effet sub-seconde serait invisible. |
| **Priorité de recherche EDGE_HUNT** | `FUNDING_BASIS 1,125`, `CROSS_MARKET 1,098` — ces scores n'ont jamais été confrontés à une mesure de capture. |

---

## 🔴 FAUX — affirmations que j'ai faites et qui ne tiennent pas

| affirmation | réalité |
|---|---|
| « REST uniquement, aucun WebSocket » (`AUDIT_ARCHITECTURE.md`) | **Faux.** `wsclient.py`, client RFC 6455 stdlib pur, 245 Mo d'événements collectés. Corrigé dans le document. |
| « Phase C : famille fermée » | **Trop fort.** À rouvrir comme non résolue — la sélection sur vitesse de publication n'est pas contrôlée. |
| « J'ai construit un crible de 5 événements candidats » | **Quatre existaient déjà** comme détecteurs testés : `BookImbalance`, `DepthWithdrawal`, `AggressiveFlow`, `SpreadDislocation`. |
| « Phase C est une famille nouvelle » | `ForcedFlowDetector` existait avant que je la reconstruise entièrement. |

---

## ⚫ MANQUANT

| manque | conséquence |
|---|---|
| **Slippage jamais mesuré** | `slippage_observed()` n'est **appelé nulle part**. Seul `slippage_excluded_for_paper_validation()` est utilisé. **Tous mes résultats de coût sont des bornes INFÉRIEURES** — le coût réel est supérieur, d'un montant inconnu. |
| **Souscriptions WS non appariées** | Carnets sur 23 instruments, liquidations sur 86 → **6 événements exploitables** sur 322. Les 245 Mo ne peuvent pas répondre à la question de Phase C. |
| **`CROSS_MARKET` jamais mesuré** | Dislocation du basis (écart à sa propre médiane récente) entre inverse/linéaire/spot. **Distinct du carry** que j'ai mesuré, qui est le *niveau* du basis. Aucune mesure de capture. |
| **Aucun inventaire de l'existant** | `edge_hunt.py` (746 lignes, 9 familles, routeur, mémoire) n'est **jamais exécuté** dans mes sessions. J'ai reconstruit `bot.py`, `flow_feed.py`, `forward_test.py`, `mechanism_screen.py` par-dessus des pièces existantes. |

---

## Les erreurs trouvées, leur impact, ce qu'elles invalident

### 1. Le WebSocket existe et n'était pas utilisé
**Impact** : facteur 2 000 sur le délai de publication. **Invalide** : la
conclusion de Phase C, qui reposait sur des données REST inadaptées.
**Correction** : affirmation retirée du document ; la prochaine collecte doit
utiliser le WS avec souscriptions appariées.

### 2. Duplication massive de l'existant
**Impact** : temps perdu, et surtout **risque d'incohérence** — deux
implémentations du même détecteur peuvent diverger silencieusement.
**Invalide** : rien directement, mais toute comparaison entre mes résultats
et ceux d'`edge_hunt` serait suspecte.

### 3. Le slippage n'est jamais mesuré
**Impact** : mon coût de 11 bps est un **plancher**. Le crible de mécanismes
conclut « 2,03 bps de capture contre 11 bps de coût » — l'écart réel est donc
**pire**, jamais meilleur. La conclusion négative est renforcée, pas affaiblie.

### 4. EDGE_HUNT refuse tout pour `UNTESTED_HYPOTHESIS`
50 candidates sur 9 familles, **toutes** refusées parce que « la capture brute
est le déplacement observé pris en entier ; la fraction réellement récupérée
n'est pas mesurée ». **Ce n'est pas un bug : c'est le bon refus.** Et c'est
exactement la mesure que mon crible a fournie — les deux pièces s'emboîtent.

---

## Ce qui reste fiable après l'audit

**Totalement fiable** : la mécanique financière, les frais, le typage des
coûts, l'anti-look-ahead, les 27 gardes, les données observatoire (100 % de
récupération), et les trois mesures rejouées à l'identique (markout maker,
carry, contrats).

**Fiable sous réserve** : les conclusions mesurées sur contrats inverses, dont
le transfert aux linéaires n'est pas testé.

**Non fiable** : le verdict de Phase C, rouvert.

**Systématiquement optimiste** : tout chiffre de coût, puisque le slippage
n'est jamais compté.

---

## La seule piste que l'audit désigne

Sur neuf familles, **`CROSS_MARKET` — la dislocation du basis — n'a jamais
été mesurée**. Elle est structurellement différente de tout ce que j'ai
fermé : ce n'est pas une prévision, ni un événement qui suit un mouvement,
mais un **écart observable entre trois instruments suivant le même actif**.

Le détecteur existe, il est testé, il n'a jamais été confronté à une mesure
de capture. C'est ce que l'étape 2 doit examiner en premier.
