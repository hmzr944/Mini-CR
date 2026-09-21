# PRISM — objectif, et ce qui compte comme une réussite

Ce document **remplace le barème de `MISSION.md`**. `MISSION.md` reste au dépôt
comme archive : ses mesures sont bonnes, c'est son seuil qui ne l'était pas.

---

## 1. Ce qui a changé, et pourquoi

`MISSION.md` fixait **272 bps/jour de capital** — ×5 en 60 jours. Ce seuil a
fonctionné comme un filtre qui laisse tout passer dans la poubelle : des
mécanismes mesurés à 10, puis 33 bps/jour ont été déclarés morts parce qu'ils
n'atteignaient pas 272. Or 33 bps/jour de capital, si c'était réel, serait un
résultat remarquable.

> **Un critère d'arrêt calibré sur un objectif inatteignable ne distingue pas
> les mauvaises idées des bonnes : il les tue toutes. Et il ressemble à de la
> rigueur.**

C'est le biais le plus coûteux du projet, et il n'était visible nulle part
parce qu'il était dans le dénominateur.

Le second défaut est symétrique. V33 poursuivait le même objectif par le haut :
14 paramètres choisis en regardant le résultat, une whitelist sélectionnée après
coup, un outil nocturne qui promeut sur le PF **hors échantillon**. Mesuré
proprement — coupure au 22/07/2026, date du dernier commit V33, donc fixée par
git — cela donne **PF 1,58 en échantillon, 0,17 hors échantillon, 2 gagnants sur
29, p = 0,0015**. Détail dans `AUDIT_V33_INDEPENDANT.md`.

Les deux défauts ont la même racine : **une cible décidée à l'avance, et une
mesure qui plie pour l'atteindre.**

---

## 2. L'objectif, maintenant

**Faire passer le PnL net réalisé de 0 € à un nombre positif, vérifiable, et
reproductible sur des données que personne n'a regardées en le construisant.**

C'est tout. Pas de multiple, pas d'échéance.

Il n'y a pas de seuil de rendement dans cet objectif, **délibérément** : le
seuil est ce qui a détruit la capacité du projet à reconnaître un résultat.
Le premier euro net réellement gagné, sur une position réellement ouverte, vaut
plus que n'importe quel backtest à trois chiffres — parce qu'il est le seul
nombre que la méthode ne peut pas fabriquer.

### Ce qui compte comme une réussite, par ordre

| # | étape | critère, mesurable |
|---|---|---|
| 1 | **Hors échantillon** | Le différentiel de funding du livre reste positif avec t > 2 sur des relevés **collectés après le 21/09/2026**, donc sur des données qui n'existaient pas quand la règle a été écrite. |
| 2 | **Exécutable** | Probabilité de remplissage passif mesurée > 60 % à 4 h sur les jambes du livre, **et** sélection adverse ≤ demi-spread. Sans cela le coût rejoint le taker et le livre est négatif. |
| 3 | **Réel** | Une position ouverte en taille minimale, tenue jusqu'au terme, avec son PnL net réconcilié contre le relevé de l'exchange. |
| 4 | **Répété** | Le même livre, trois cycles de suite, sans intervention discrétionnaire. |

### Ce qui ne compte pas

- Un backtest, quel que soit son rendement.
- Un PnL papier.
- Un chiffre calculé sur les données qui ont servi à choisir la règle.
- Un résultat qui n'a pas de mécanisme causal nommable.

---

## 3. L'ordre de grandeur honnête

Les mesures de `AUDIT_V33_INDEPENDANT.md` donnent, pour la seule famille
ouverte — le carry inverse/linéaire même sous-jacent sur OKX — un rendement de
l'ordre de **quelques dizaines de pourcents par an**, sous trois conditions dont
aucune n'est acquise : exécution **maker**, persistance du différentiel hors des
95 jours mesurés, et absence de cascade pire que celle du 10/10/2025 pendant une
détention.

**Ce n'est pas ×5 en 60 jours. C'est ce que la mesure soutient.**

Et il y a une contrainte de forme, pas seulement de taille : cette famille se
tient sur **des semaines**, pas des minutes. Le projet cherchait de la fréquence
et de la rotation ; la mesure dit que sur cette venue, avec ces données
publiques, **la fréquence et le rendement sont en opposition directe** — tout ce
qui est capturable à haute fréquence vaut 1 à 6 bps, et tout aller-retour en
coûte 8 à 31. Ce rapport tient sur quatre familles sans rapport entre elles, et
il tient même à frais nuls.

---

## 4. Les règles de méthode, non négociables

Elles existent parce que chacune correspond à une erreur déjà commise dans ce
dépôt, avec son commit.

1. **La règle est écrite avant la mesure.** `prism_v2/carry_book.py` déclare ses
   sept critères en tête de module, avec le raisonnement de chaque seuil. Toute
   révision est datée et justifiée dans git. *(V33 : 14 paramètres fixés après
   coup.)*
2. **L'OOS ne sert jamais à choisir.** Aucun paramètre, aucun actif, aucun seuil
   ne se décide en regardant une performance hors échantillon.
   *(`tools/overnight_sweep.py:110` faisait exactement cela.)*
3. **Un rejet nomme son critère.** Un livre vide n'est jamais muet.
   *(Commit `5ef49cb` : « zéro déclenchement, pour toujours… faux négatif
   indiscernable d'un vrai résultat ».)*
4. **Jamais un instantané.** Profondeur, spread, volatilité : médiane de
   plusieurs relevés espacés. *(Un relevé unique donnait ETH à 513 k$ contre une
   médiane réelle de 18,6 k$ ; le dépôt s'était déjà fait piéger de même sur un
   demi-spread.)*
5. **Un coût non mesuré est supposé défavorable.** Le demi-spread encaissé est
   compté comme entièrement mangé par la sélection adverse tant que le markout
   n'est pas mesuré.
6. **Ce qui n'est pas versionné n'existe pas.** 264 Mo de carnets et 27 Mo
   d'observatoire ont été perdus avec un conteneur, et les mesures qui en
   dépendaient ne sont plus reproductibles.
7. **Aucun ordre réel sans les étapes 1 et 2 de la section 2.**

---

## 5. État au 21/09/2026

| | |
|---|---|
| PnL net réalisé | **0,00 €** |
| Ordres réels émis | **0** |
| V33 | **gelé** — invalidé hors échantillon, ne pas relancer |
| Famille ouverte | carry inverse/linéaire même sous-jacent, 15 paires |
| Étape en cours | **1 — hors échantillon** |
| Collecte forward | active, quotidienne, 8 553 relevés amorcés le 21/09 |
| Bloqueur nommé | l'API OKX ne rend que 95 jours ; l'OOS ne peut que se collecter |
| Inconnue suivante | probabilité de remplissage passif (étape 2) |

**Question tranchée depuis l'audit** : la compensation de marge entre jambe
inverse et jambe linéaire (« risk unit merge ») existe bien chez OKX, mais exige
le palier **VIP3**, hors de portée à ce capital. L'« inconnue décisive » de
`CARRY_FINDING.md` est donc résolue — **défavorablement**. Les deux marges
initiales s'additionnent, et le levier va de 14,3× (ADA) à 50× (BTC) selon la
paire, pas 23,6× partout.
