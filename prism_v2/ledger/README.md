# Capture Ledger

`captures.jsonl` — memoire **append-only** et **versionnee** du systeme.
Une ligne = une opportunite observee.

## Regle fondatrice

**Chaque opportunite produit un enregistrement, meme rejetee, meme non
resolue.** Un rejet est une information, pas un non-evenement. C'est
exactement ce dont l'absence a fait perdre l'historique T1-T45 : 45 taches
revendiquees, zero trace, aucun resultat reproductible.

## Lecture des couts

| Champ | Signification |
|---|---|
| `fees_bps` etc. a `null` | composante **UNKNOWN** — non mesuree. **Jamais** convertie en 0. |
| `cost_quality.<composante>` | `OBSERVED` / `DERIVED` / `ASSUMED` / `UNKNOWN` |
| `weakest_quality` | qualite la plus faible de la chaine — plafonne ce qu'on peut conclure |
| `status` | `ACCEPTED` / `REJECTED` / `UNRESOLVED` |

`ACCEPTED` ne signifie **pas** "rentable" : seulement qu'apres *ces* couts-la,
sur *cette* observation, il reste quelque chose.

## Types d'opportunite

- `M2_LIQUIDATION_DISLOCATION` — mesure d'amplitude ex-post. Les metadonnees
  portent `backward_looking: true` et `upper_bound: true` : c'est une **borne
  superieure avec information future**, pas un edge.
- `EXECUTION_CONTROL_PAPER` — controle, pas une opportunite
  (`gross_capture_bps = 0` par construction). Mesure le plancher de cout
  aller-retour sur carnet reel.

## Securite

Aucun secret. Le ledger ne contient que des donnees de marche publiques, des
mesures derivees et des resultats `PAPER`. Une garde (`_assert_no_secrets`)
refuse l'ecriture de tout champ dont le nom evoque une cle.
