# Propagation OKX → Backpack EU — résultat de reconnaissance

**Statut : `PROPAGATION OBSERVÉE — ET NON EXPLOITABLE DANS LES DEUX SENS`**

Collecte de reconnaissance, 23 septembre 2026. Observation seule, aucun ordre.
Tous les seuils et exclusions étaient gelés avant lecture.

## Contrôles obligatoires — tous passés

| | |
|---|---|
| Backpack − local | 55 ms |
| OKX − local | 62 ms |
| **écart RELATIF des venues** | **−8 ms** |
| trous de flux | **0** |
| mises à jour du toucher | **~520 000** |
| univers | 20 familles, dérivées, **93 %** du volume perp Backpack |

L'écart de −8 ms est cent fois plus petit que le plus court délai testé. Un
décalage de 500 ms aurait déplacé tous les délais d'un délai entier.

## Échantillon

    liquidations collectees              10 584
    episodes (ecart 60 s)                   626
    dont DANS la fenetre de prix             59
    utilisables par delai             54 a 57

Les 567 écartés relèvent de la **backlog** OKX, antérieure à la collecte : les
mesurer supposerait un prix qu'on n'a pas.

## Résultat

Mouvement de l'**entrée** (après délai) à la sortie à 30 s, compté dans le sens
imposé par la liquidation, moins le témoin.

| délai | n | observé | témoin | **excès** | spread | **net** |
|---|---|---|---|---|---|---|
| 0,5 s | 57 | −8,07 | 0,00 | **−8,07** | 1,01 | **−19,07** |
| 1,0 s | 57 | −8,14 | −0,85 | **−7,29** | 1,00 | **−18,29** |
| 2,0 s | 56 | −6,70 | −0,55 | **−6,14** | 1,00 | **−17,14** |
| 5,0 s | 54 | −5,79 | 0,00 | **−5,79** | 1,48 | **−17,27** |

**Le témoin est à zéro sur trois délais sur quatre.** C'est le comportement
attendu d'un contrôle sain, et cela valide la construction : ce qu'on mesure
n'est pas la volatilité ordinaire.

## Ce que le signe dit, et il est inattendu

L'excès est **négatif** : après une liquidation, le prix sur Backpack part
**contre** le sens de la pression forcée. Une liquidation de position longue
est une vente forcée ; à l'instant où l'on pourrait entrer, le prix **remonte
déjà**.

L'effet **décroît avec le délai** — −8,07 à 0,5 s, −5,79 à 5 s — ce qui est
cohérent avec un rebond le plus vif immédiatement après l'événement.

Autrement dit : **la propagation existe, et l'information utile est déjà
consommée avant les 500 ms qui me séparent du carnet.** Ce qui reste visible
est le rebond, pas l'impact.

## Dispersion

| délai | q05 | q25 | médiane | q75 | q95 | part > 0 |
|---|---|---|---|---|---|---|
| 0,5 s | −35,6 | −18,8 | −9,2 | 1,7 | 16,4 | **30 %** |
| 1,0 s | −36,6 | −23,8 | −6,7 | 2,8 | 17,1 | 32 % |
| 2,0 s | −45,3 | −19,2 | −8,8 | 1,9 | 18,3 | 34 % |
| 5,0 s | −34,7 | −13,0 | −5,7 | 3,3 | 19,1 | 35 % |

Un tiers seulement des épisodes est positif, et la queue **gauche** (−35 à
−45 bps) est nettement plus lourde que la droite (+16 à +19). La distribution
penche du mauvais côté, pas seulement sa médiane.

## Le sens inverse ferme la famille aussi

Retourner le sens après lecture est la sélection a posteriori que le protocole
interdit. Le calcul n'est fait que pour vérifier que la famille est fermée
**des deux côtés** :

| délai | excès inverse | **net inverse** |
|---|---|---|
| 0,5 s | +8,07 | **−2,94** |
| 1,0 s | +7,29 | **−3,71** |
| 2,0 s | +6,14 | **−4,86** |
| 5,0 s | +5,79 | **−5,69** |

Même en prenant le sens que le résultat suggère après coup, les 10 bps de
frais et le spread traversé laissent un net négatif à tous les délais.

## Verdict selon les critères gelés

> **Propagation observée, mais trop faible après délai et coûts** — la
> deuxième des quatre issues préenregistrées.

Aucune justification pour passer à une stratégie d'exécution. Le seuil
économique du protocole complet (390 bps net) n'est pas approché : on en est à
**deux ordres de grandeur**.

## Ce que ce résultat n'établit pas

- **Une heure, un régime.** Aucune cascade majeure n'est tombée dans la
  fenêtre — or c'est là que la queue vit, et c'est la limite dominante.
- **n ≈ 57.** Suffisant pour un ordre de grandeur, pas pour une queue.
- **Un seul couple de venues**, un seul horizon de sortie (30 s), un seul mode
  d'exécution (taker des deux côtés).
- **Il ne ferme pas la famille `flux de liquidation`** : il ferme la voie
  **cross-venue OKX → Backpack EU** à des délais de 0,5 à 5 s. Le plafond
  `MEAN` existant (−8,03 bps/jour) reste valide pour ce qu'il mesure.

## Conséquence pour le protocole complet

Le protocole `PROTOCOLE_QUEUE_LIQUIDATION.md` reposait sur cette propagation.
Elle est mesurée, et elle va dans le mauvais sens avec un net négatif des deux
côtés. **Le lancer maintenant reviendrait à collecter sept heures pour
mesurer plus finement une grandeur déjà négative de deux ordres de grandeur.**

Réouverture conditionnée à un élément nouveau : un couple de venues où la
latence est structurellement plus faible, un flux d'événements sur la venue
tradable elle-même, ou un type d'événement à horodatage connu d'avance — où
la vitesse ne décide plus.
