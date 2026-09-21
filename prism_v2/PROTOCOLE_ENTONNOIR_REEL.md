# Protocole gelé — entonnoir des familles jusqu'au PnL net, sur données réelles

Écrit le 21/09/2026 **avant** d'avoir regardé le moindre résultat de capture.
Aucun seuil ci-dessous n'a été choisi en observant une sortie.

## Question
Les deux familles ressuscitées (BOOK_IMBALANCE, DEPTH_WITHDRAWAL) et les trois
autres de microstructure produisent-elles un **PnL net positif par euro
immobilisé et par jour**, une fois la capture mesurée causalement ?

Un taux de déclenchement n'est pas un PnL. Cette mesure-ci en est un.

## Données
Collecte L1 (5 niveaux) + tape agresseur, ~1 s de résolution, 12 instruments
OKX USDT-SWAP liquides, 14 minutes. Chaque détection dispose donc d'un
**avenir réel** dans la même série.

## Mesure de la capture — causale par construction
`prism_v2.replay.causal_capture`, déjà présent au dépôt :
- entrée à `T0 + latence`, jamais au prix de l'événement ;
- prix d'entrée = **VWAP du carnet réellement disponible** à cet instant
  (traversée du spread et impact inclus) ;
- sortie à `T0 + latence + détention`, au carnet de cet instant ;
- si la fenêtre ne couvre pas entrée **et** sortie, la capture est
  **NON RÉSOLUE** — elle n'est jamais comptée comme nulle ;
- si la profondeur ne suffit pas pour le notionnel, **NON RÉSOLUE** aussi.

## Coûts, comptés en entier
| poste | traitement |
|---|---|
| traversée du spread | **incluse** dans le VWAP d'entrée/sortie |
| impact de marché | **inclus** (marche dans le carnet) |
| frais taker OKX | **10 bps** ajoutés explicitement (5 bps × 2) |
| latence | balayée, pas supposée |
| remplissage partiel | traité comme NON RÉSOLU, jamais comme un fill |
| adverse selection | **implicite** dans le prix de sortie mesuré |
| funding | nul à ces horizons (secondes) |

## Balayage déclaré
- notionnel : **100, 1 000, 10 000 USD** — pour séparer *l'edge existe-t-il*
  de *quelle capacité*, qui sont deux questions différentes ;
- latence : **200, 1 000, 3 000 ms** ;
- détention : **5, 30, 120 s**.

5 familles × 3 × 3 × 3 = **135 cellules**. Benjamini-Hochberg sur les 135,
pas sur les survivantes.

## Critères de réussite, déclarés maintenant
- **RÉFUTÉE** : PnL net moyen par trade ≤ 0.
- **NON CONCLUANTE** : net > 0 mais `t` < 3, ou moins de 30 captures résolues.
- **PROMETTEUSE** : net > 0, `t` ≥ 3, ≥ 30 captures résolues, **et** survie à
  Benjamini-Hochberg sur les 135 cellules.
- Aucun de ces statuts n'autorise d'allouer du capital. Le statut supérieur
  exige une exécution réelle documentée, qui n'existe pas.

## Ce que cette mesure ne peut PAS établir
1. Une fenêtre de 14 minutes = **un seul régime de marché**. Rien ici ne vaut
   hors échantillon.
2. Les captures se chevauchent : elles ne sont pas indépendantes. Le `t` rendu
   est une **borne supérieure** de la significativité.
3. L'exécution est **taker** uniquement. La question maker reste ouverte.
4. Aucun ordre réel n'est passé. Tout ceci reste de la simulation.

---

## AMENDEMENT 1 — 21/09/2026, avant toute mesure de capture

La collecte atteint **~6,7 s de résolution** (les appels REST dominent), pas
la seconde espérée. Conséquence : une détention de **5 s n'est pas mesurable**
— l'entrée et la sortie tomberaient sur le même carnet et rendraient un gross
identiquement nul, ce qui serait un artefact d'échantillonnage, pas un résultat.

La grille de détention devient **30, 120, 300 s**. La latence reste
200 / 1 000 / 3 000 ms.

**Ce qui motive cet amendement est une contrainte de DONNÉES, connue avant
d'avoir regardé une seule capture, et non un résultat observé.** La distinction
est celle qui a perdu V33 ; elle est consignée ici pour être vérifiable.

Le nombre de cellules reste 5 × 3 × 3 × 3 = **135**, et Benjamini-Hochberg
s'applique sur les 135.
