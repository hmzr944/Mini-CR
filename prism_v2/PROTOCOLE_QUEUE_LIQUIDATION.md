# Protocole préenregistré — queue droite du flux de liquidation

**Écrit AVANT toute lecture de résultat. Aucune valeur ci-dessous ne sera
modifiée après observation.**

Date de gel : 23 septembre 2026. Statut : `TAIL_DISCOVERY`, non lancé.

---

## 0. Pourquoi cette hypothèse est légitime malgré un plafond existant

`flux de liquidation` figure au registre à **−8,03 bps/jour, n = 399**, motif
`MAGNITUDE_INSUFFISANTE`. `KillRegistry.revive` exige de dépasser un plafond
pour rouvrir une famille.

Ce plafond est une **moyenne**. Un mécanisme dont le gain serait concentré
dans cinq épisodes sur 399 produirait exactement ce chiffre. La question de la
**queue** n'a jamais été posée, et `tail.MEAN` / `tail.TAIL` rend les deux
mesures incomparables par construction — comme `CAPITAL` et `NOTIONNEL` le
sont déjà.

Ce n'est donc pas une ré-optimisation d'une piste morte.

---

## 1. Le problème de venue, qui décide du design

| | |
|---|---|
| flux de liquidations public | **OKX global** uniquement |
| Backpack EU | **aucun** endpoint de liquidations (404 vérifié) |
| venue tradable depuis la France | Backpack EU, OKX Europe |

**On détecte sur une venue qu'on ne peut pas trader, et on trade sur une venue
dont on ne voit pas les événements.**

Le protocole mesure donc le mouvement sur la venue **EXÉCUTABLE** (Backpack
EU), déclenché par un événement observé sur OKX. Mesurer le mouvement sur OKX
serait plus favorable et sans valeur : cet instrument n'est pas accessible.

Conséquence assumée : le test porte sur la **propagation** d'une dislocation
entre venues. Mon a priori est qu'elle est plus rapide que mes 420 ms. C'est
précisément ce que la mesure tranche.

---

## 2. Faisabilité, mesurée avant de geler

| | |
|---|---|
| historique de liquidations | **aucun** — `before=` ne remonte pas |
| débit observé | 300 liquidations / 261 min sur 3 familles |
| épisodes à écart 60 s | **25 en 4,35 h** sur 3 familles |
| pour 200 épisodes | ~35 h sur 3 familles, **~7 h sur 20 familles** |

**Contrainte d'environnement** : le conteneur est éphémère et fauche les
processus d'arrière-plan à l'inactivité. La collecte doit tourner au premier
plan, session active. C'est la limite pratique dominante de ce protocole.

---

## 3. Paramètres gelés

### Univers
Les **20 familles OKX SWAP** de plus fort volume 24 h, **dérivées de l'API**,
jamais écrites à la main. Une garde d'architecture du dépôt l'impose déjà.

### Définition d'un événement
Une liquidation dont la taille atteint le **95e centile de la distribution des
tailles du même instrument**, calculé sur une fenêtre **strictement
antérieure** à l'événement.

Pourquoi ce seuil et pas « toute liquidation » : les liquidations sont un
bruit de fond quasi continu — une toutes les 5 s sur BTC. Sans seuil, on
regroupe du bruit. Le seuil porte sur le **déclencheur**, jamais sur le
résultat : il ne peut donc pas sélectionner les issues favorables.

### Regroupement en épisodes
`gap_s = 60,0 s`, par instrument. Justification : à 60 s on obtient 12
liquidations par épisode, ce qui agrège une cascade sans fusionner deux
épisodes distincts. Une cascade non regroupée multiplierait un seul mouvement
par le nombre de ses déclencheurs.

### Latence et entrée
Entrée simulée à **`t_déclencheur + 1,0 s`**, soit 420 ms de latence mesurée
plus une marge de décision. Le gain est calculé depuis le **prix d'entrée**,
jamais depuis le prix au déclenchement — `tail.capturable_bps` l'impose et un
test le fige.

### Horizons de sortie
**60 s, 300 s, 1 800 s** (`MARKOUT_HORIZONS_S`, inchangé).

### Coûts
Aller-retour taker sur Backpack EU : **4 × 5 bps de frais + 2 demi-spreads
lus au carnet**. Barème Tier 1, source primaire.

### Quantiles rapportés
`REPORTED_QUANTILES` = (0,01 · 0,05 · 0,25 · 0,50 · 0,75 · 0,95 · 0,99),
figés dans le code avant toute donnée.

### Exclusions déclarées
- épisode dont le carnet Backpack n'est pas enregistré à l'entrée **ou** à
  l'horizon → `INCONNU`, jamais zéro ;
- instrument sans marché Backpack correspondant → hors univers ;
- épisode chevauchant la fin de la fenêtre de collecte → exclu.

---

## 4. Bornes de risque, chiffrées

Remplacent la garde ambiguë « si la queue gauche dépasse la droite ».

| | valeur | sur 1 000 EUR |
|---|---|---|
| perte max par épisode | **200 bps** | 20 EUR |
| perte cumulée max | **2 000 bps** | 200 EUR |
| quantile bas surveillé | **5 %** | — |

Trois contrôles indépendants (`RiskLimits.breaches`) :
1. le **quantile 5 %** — la perte d'un mauvais jour ordinaire ;
2. le **pire épisode** — la perte qui ferme un compte, même si elle est rare ;
3. la **perte cumulée** — l'érosion sans épisode spectaculaire.

Le troisième existe parce qu'une queue gauche plus *rare* que la droite peut
détruire le capital tout en paraissant « plus petite » à un quantile choisi.

---

## 5. Critères de décision, fixés d'avance

L'ordre est **taille → risque → économie** (`tail.evaluate_tail`). Le risque
précède l'économie parce que la queue gauche est précisément ce qu'on ne veut
pas découvrir en dernier.

| verdict | condition |
|---|---|
| **INCONNU** | n < 200 épisodes, ou niveau de mesure autre que `NET` |
| **REJETÉ (risque)** | une des trois bornes franchie |
| **REJETÉ (loterie)** | plus de **60 %** du gain vient des 5 % meilleurs épisodes |
| **REJETÉ (économie)** | q95 net < **390 bps** OU espérance nette ≤ 0 |
| **CANDIDAT** | aucune des conditions ci-dessus |

**Un q95 élevé ne suffit jamais.** Il faut aussi une espérance positive, des
pertes bornées et des gains non concentrés. Un seul critère franchi ne
conclut rien.

### D'où vient le seuil de 390 bps
Objectif +1 000 EUR sur 1 000 EUR en 60 jours, à ~3 épisodes tradables par
semaine, sans levier : 26 épisodes × 39 EUR. Ce seuil est **économique**, pas
statistique — il dit ce qu'il faudrait, pas ce qui est plausible.

---

## 6. Ce que ce protocole ne pourra pas établir

- **Un profit.** Au mieux un `CANDIDAT` en `TAIL_DISCOVERY`, à soumettre
  ensuite à `CAPTURE_VALIDATION` hors échantillon.
- **La robustesse temporelle.** 7 h de collecte, c'est un régime.
- **Le comportement en stress.** Les cascades les plus violentes sont rares et
  ne tomberont probablement pas dans la fenêtre — or c'est là que la queue
  gauche vit.
- **La capacité.** La profondeur au toucher pendant une cascade n'est pas la
  profondeur ordinaire, et elle n'est pas mesurée ici.

## 7. Ce qui fermerait la famille définitivement

q95 **net** sous 390 bps sur N ≥ 200 épisodes indépendants, **ou** une borne
de risque franchie. Dans les deux cas, le registre enregistrerait un plafond
de classe `TAIL` — distinct du plafond `MEAN` existant, qui reste valide pour
ce qu'il mesure.
