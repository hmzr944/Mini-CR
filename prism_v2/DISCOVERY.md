# Opportunity Discovery Engine

## Le changement

**Avant** — « aucune opportunité M2 → le système est bloqué »
**Après** — « aucune opportunité M2 → le moteur explore immédiatement les 8 autres familles »

Le moteur n'est plus le bot d'une stratégie. C'est une machine qui cherche en
continu des états de marché où une erreur de pricing pourrait devenir une
capture nette. Les stratégies sont des plugins.

## Le déblocage : trois modes d'évaluation

Chercher et exécuter n'exigent pas la même preuve.

| Mode | Question | UNKNOWN | Autorise |
|---|---|---|---|
| **DISCOVERY** | « cela *pourrait-il* être rentable ? » | **encadrés** par des bornes explicites | rien |
| **CAPTURE_VALIDATION** | « la simulation causale le confirme-t-elle ? » | exige `DERIVED`+ | PAPER |
| **EXECUTION** | « peut-on engager du capital ? » | exige `OBSERVED` | DEMO/LIVE |

En DISCOVERY, un coût inconnu n'est **jamais** remplacé par zéro : il est
**encadré**. Le résultat est un intervalle et un verdict :

- `DEAD_EVEN_AT_BEST` — même avec tous les inconnus à **zéro**, les coûts
  **connus** tuent la capture. Conclusion définitive : inutile de mesurer plus.
- `SURVIVES_ALL_BOUNDS` — survit même au pire cas. À valider causalement.
  Ce n'est **pas** une preuve de rentabilité.
- `NEEDS_MEASUREMENT` — la réponse dépend d'une grandeur non mesurée. Le
  résultat **nomme** laquelle et **de combien** elle doit rester en dessous.
  C'est ce qui transforme « je ne sais pas » en plan de mesure.

## Les 9 familles

| Famille | Raw edge mesuré (observable à la détection) |
|---|---|
| `CROSS_MARKET` | écart du basis à sa **médiane récente** (pas son niveau) |
| `CROSS_VENUE` | `fillable_bid` vs `fillable_ask` à taille donnée, frais des 2 venues déduits |
| `FUNDING_BASIS` | funding attendu sur l'horizon, en bps (pas un APR) |
| `FORCED_FLOW` | déplacement **déjà constaté** vs référence pré-événement |
| `BOOK_IMBALANCE` | déviation du microprix au mid |
| `DEPTH_WITHDRAWAL` | élargissement du spread après retrait de profondeur |
| `AGGRESSIVE_FLOW` | déplacement déjà provoqué par un flux déséquilibré |
| `SHORT_HORIZON_REVERSION` | écart du mid à sa moyenne de fenêtre |
| `SPREAD_DISLOCATION` | spread **moins** volatilité réalisée |

**Contrat commun** : le détecteur trouve une anomalie et mesure son amplitude.
Il ne décide **jamais** qu'elle est rentable — c'est le Capture Engine.

**Seuil d'émission** : économique, pas réglé. Une anomalie plus petite que le
spread observé ne peut pas être capturée, donc elle n'est pas émise.

## Priorité de recherche

Une famille stérile perd en priorité ; une famille qui survit monte. Un
**manque de données est neutre** : l'absence de mesure n'est pas l'absence
d'edge. La priorité pilote l'**effort de recherche**, jamais l'allocation —
celle-ci reste purement économique (Capital Router).

## Ce que le moteur refuse

- d'allouer quand aucune candidate ne le justifie (ne rien faire est une décision) ;
- de traiter des candidates du même sous-jacent comme des paris indépendants ;
- de consommer plus qu'une fraction de la profondeur affichée (elle n'est pas
  garantie à T0+latence) ;
- d'exécuter une mesure ex-post (information future) ;
- de comparer deux instruments libellés en devises différentes sans taux de change.

## Lancer

```bash
python3 -m prism_v2.edge_hunt --duration 180 --instruments 6 --json out.json
```
