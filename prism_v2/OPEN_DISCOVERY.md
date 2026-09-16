# Statut d'ouverture du moteur de découverte

## Verdict : **B — STRUCTURED DISCOVERY ENGINE**

Ce n'est **pas** un Open Discovery Engine. Le nom n'a pas été changé pour
paraître plus impressionnant.

## Protocole (reproductible)

```bash
python3 -m prism_v2.edge_hunt --duration 300 --instruments 5
```

Le pipeline de recherche reçoit **uniquement** des données de marché. On lui
retient : le nom du phénomène, la famille, le sens, le seuil, l'horizon par
piste, le mécanisme, et toute règle de trading.

## Ce qui justifie « DISCOVERY »

| Preuve | Mécanisme |
|---|---|
| Aucune famille codée n'intervient | `research/` n'importe pas `detectors/` — vérifié par test |
| Seuils **mesurés**, pas posés | décile de l'échantillon observé |
| Sens **déduit**, pas fourni | signe de l'excès mesuré |
| Mécanisme proposé **après** la mesure | `MechanismAgent` intervient en 4ᵉ position |
| Balayage **mécanique** | 15 primitives × 4 horizons × 2 conditions, sans intuition |
| Falsification obligatoire | seul chemin vers `SURVIVED_FALSIFICATION` |

## Ce qui interdit « OPEN »

- `FEATURE_SPACE` est **écrit à la main** (15 primitives). Le système ne peut
  pas inventer une primitive absente de la liste.
- **Aucune composition** de primitives (pas de « A et B simultanément »).
- La grille d'horizons est **fixée**, non découverte.
- Les mécanismes viennent d'une **table de correspondance** écrite à la main ;
  hors table → `UNEXPLAINED`.
- Aucune relation **inter-instruments** (A précède B) par ce chemin.

## Ce qui interdit de le réduire à « FAMILY_BASED » (C)

Les 9 familles codées existent et restent utiles, mais **elles ne participent
pas à ce chemin**. Le pipeline produit des hypothèses qu'aucune famille n'avait
prévues, et les détruit sans qu'aucune famille n'intervienne.

## Pour prétendre à OPEN, il faudrait

1. inventer des primitives non prévues par l'auteur ;
2. composer des primitives sans schéma imposé ;
3. découvrir ses propres horizons ;
4. formuler des mécanismes hors table ;
5. généraliser à une classe d'actifs non anticipée sans modification de code.

Aucun de ces cinq points n'est atteint.
