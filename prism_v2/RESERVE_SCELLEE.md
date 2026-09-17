# Réserve expérimentale scellée — 225 jours OKX

**Ne pas utiliser pour explorer. Ne pas regarder.**

## Ce que c'est

Le dernier quart chronologique du panneau OKX (200 perpétuels, barres 4 h) :

```
fenetre HOLDOUT  =  225 jours
fichier          =  okx_4h.json, dernier 25 % des barres
statut           =  JAMAIS OUVERT
```

Lors du test de vitesse du momentum, aucun signal n'a survécu à
Benjamini-Hochberg sur la validation. Le protocole imposait alors de **ne pas
ouvrir le holdout**, et il ne l'a pas été. Ces 225 jours sont donc intacts.

## Ce que ce n'est pas

Ce ne sont **pas** des « données inutilisées qu'on pourrait exploiter
maintenant ». C'est une réserve confirmatoire à usage unique.

## Règle d'usage

1. Les données **antérieures** (discovery, validation, panneau Hyperliquid)
   servent à explorer et à générer des hypothèses. Elles sont brûlées et on
   l'assume.
2. Le jour où une hypothèse est **complètement gelée** — paramètres, univers,
   coûts, construction, écrits et committés — elle est testée sur ces
   225 jours **sans aucune modification**.
3. Un seul passage. Si l'on ajuste quoi que ce soit après avoir regardé, la
   réserve est brûlée et le résultat n'a plus de valeur.
4. Une hypothèse qui n'a pas franchi la porte (`prism_v2/hypothesis_gate.py`)
   ne touche pas la réserve.

## Pourquoi cela vaut d'être protégé

Trois protocoles gelés ont produit trois résultats négatifs. Ce qui distingue
ces résultats d'une simple déception, c'est qu'ils sont **croyables** : les
fenêtres n'avaient pas été regardées. Cette crédibilité est le seul actif que
le projet ait accumulé sans jamais le dépenser.

Une donnée propre ne se régénère pas. Elle ne peut que se dépenser, une fois.
