# Le bot — criblage autonome, exécution PAPIER

```bash
python3 -m prism_v2.bot --cycles 1 --symbols 40 --books 10
python3 -m prism_v2.bot --cycles 0 --interval 300 --ledger data/ledger.jsonl
```

**PAS DE TRADING RÉEL.** Aucune classe `RealExecutor`, aucune clé, aucune
authentification, aucun endpoint d'ordre. Le bot lit des endpoints **publics**
(OKX, Hyperliquid) et écrit dans le Capture Ledger. `--cycles 0` boucle
indéfiniment.

## Ce qu'il est

Un **moteur de criblage**, pas un bot de stratégie. Le dépôt n'a trouvé
aucune stratégie dont l'économie justifie un déploiement : six familles
mesurées, toutes entre 0,01× et 0,35× de l'objectif, plafond de catégorie
établi à 16 %/an par HLP (187 M$, PnL réalisé). Un bot qui traderait quand
même mentirait sur la preuve.

Il surveille donc en continu des instruments réels, chiffre chaque candidate
contre ses coûts réels, et n'exécute — en papier — que ce qui franchit le
seuil économique.

## Le refus est le produit

Sortie typique sur données live :

```
  candidates detectees   : 12
  evaluees               : 8
    acceptees            : 0
    rejetees             : 8
  meilleure nette        : -3.82 bps  [CASHCAT (+100 %/an)]
    1x  net -9.0718 bps <= 0 (brut 20.9464 - couts 30.0182)
```

Le bot live **retrouve le résultat de la mesure historique** : tout est
rejeté, et de peu. C'est une validation croisée, pas un échec.

Un criblage qui accepterait souvent sur une famille mesurée négative
signalerait un bug dans les coûts, pas une opportunité.

## Le haircut — le cœur de son honnêteté

Le différentiel instantané **surestime massivement** ce qu'on encaisse. Le bot
n'utilise jamais le taux affiché : il applique une **surface mesurée**
(`MEASURED_SURFACE`, 142 actifs, 45 jours) donnant le différentiel réellement
réalisé par (force du signal, horizon).

Le fait économique que cette surface encode :

| signal observé | réalisé à 168 h | ratio |
|---:|---:|---:|
| 20 %/an | 7,3 %/an | 0,365 |
| 100 %/an | 13,7 %/an | 0,137 |
| 200 %/an | 11,6 %/an | 0,058 |
| 500 %/an | 5,5 %/an | 0,011 |

**Le réalisé sature vers 11–14 %/an et redescend sur les lectures extrêmes** :
celles-ci sont majoritairement du bruit sur un seul paiement.

Un premier jet appliquait un ratio unique (0,137) à tous les signaux. Sur
SOPH à +234 %/an il prédisait 61 bps là où la mesure en donne 21 — et le bot
**acceptait le trade**. Corrigé : la surface ne s'extrapole jamais vers le
haut et est plafonnée par le maximum jamais mesuré.

C'est l'erreur qui perd de l'argent en ayant l'air d'en trouver, et elle
frappe toujours les lectures les plus spectaculaires.

## Les coûts, un par un

Aucun n'est mis à zéro en silence. Les trois non issus du carnet sont
résolus explicitement, avec leur justification :

| composante | traitement | pourquoi |
|---|---|---|
| frais | ASSUMED, barèmes publics des deux venues, 4 traversées | OKX 5 bps, HL 4,5 bps |
| spread, impact | DERIVED du carnet **réel** | mesurés à la taille demandée |
| latence | NOT_APPLICABLE | le haircut mesuré intègre déjà une entrée à t+1h ; la compter en plus la facturerait deux fois |
| funding | NOT_APPLICABLE | c'est le **revenu** de cette famille, déjà dans `gross_capture_bps` ; le recompter serait un double comptage |
| slippage | **EXCLU**, pas mesuré à zéro | le résultat est une **borne inférieure du coût**. Interdit en mode `EXECUTION` |

Cette dernière ligne est structurelle : parce que le slippage est déclaré
exclu, `evaluate(mode=EXECUTION)` **refuse** ces candidates. Le bot ne peut
donc pas servir de feu vert de déploiement, par construction et non par
discipline.

## Ordre non contournable

```
QUALITÉ DES DONNÉES -> ÉCONOMIE -> CAPACITÉ -> RISQUE -> EXÉCUTION
```

Une donnée inutilisable produit `UNRESOLVED`, **jamais** un rejet économique.
Confondre « je ne peux pas mesurer » et « ce n'est pas rentable » fait
abandonner une piste vivante ou poursuivre une piste morte. C'est pourquoi
« carnet absent » apparaît comme motif distinct dans le rapport.

## Pourquoi surveiller une famille fermée

Fermée signifie « ne rapporte pas **aux niveaux observés sur 45 jours** », pas
« ne rapportera jamais ». Le détecteur existe pour déclencher le jour où la
condition change — panne de venue, cotation nouvelle, squeeze. Il porte le
seuil économique exact ; il n'a pas besoin qu'on le surveille.

## Étendre

Ajouter une famille = implémenter `Opportunity` et l'enregistrer. Le noyau
n'appelle que `name`, `requires`, `detect` : il ne connaît aucune
implémentation. Le reste de la chaîne — coûts, capacité, risque, routage,
papier, ledger — est déjà branché.

## Ce qu'il ne fait pas

Il ne gère pas de position ouverte dans le temps (chaque cycle est sans
mémoire), ne mesure pas le risque de jambe unique, et ne modélise ni la
liquidation, ni le risque de contrepartie Hyperliquid. Ces points sont à
traiter **avant** toute idée de déploiement, et aucun ne se règle par du code.
