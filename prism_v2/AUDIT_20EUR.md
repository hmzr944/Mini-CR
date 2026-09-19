# Audit complet — objectif 20 € net/jour

## 1. Ce que l'audit a inspecté

| | |
|---|---|
| modules de production | 158 fichiers, **31 067 lignes** |
| tests | **978**, tous verts, dont 27 gardes d'architecture |
| documents | 30 |
| PnL réalisé | **0 €** — aucun ordre n'a jamais été émis |

**Graphe d'import reconstruit par AST** : 90 modules importés (23 085 lignes),
10 points d'entrée, 51 modules ni importés ni pourvus d'un `__main__`.

**Ces 51 modules ne sont pas du code mort à supprimer.** Ce sont les scripts de
`scans/`, exécutés par `python3 -m prism_v2.scans.X`, qui exécutent au niveau
module. La convention du dépôt — *« chaque nombre renvoie au script qui le
recalcule »* — en fait la piste d'audit du projet. Les effacer détruirait la
traçabilité sans rapporter un centime. **Conservés délibérément.**

## 2. Ce qui a été inspecté et jugé sain

- `costs.py` — modèle à composants typés, qualité épistémique explicite
  (`OBSERVED`/`DERIVED`/`ASSUMED`/`UNKNOWN`), inconnues jamais remplacées par
  une valeur optimiste. `slippage` reste `UNKNOWN` et bloque le passage en
  exécution. **Correct.**
- `fees.py` — barème public 5,0/2,0 bps marqué `ASSUMED`, `NoCredentialsFeeProvider`
  renvoie `UNKNOWN` par construction. **Correct.**
- `capital.py` — `bps_per_day_on_capital()` applique le levier effectif ;
  `position_capital(netting=False)` par défaut, parce que la compensation est
  une faveur de l'exchange et non un droit. **Correct.**
- `margin.py` — barème OKX réel par palier, échoue fermé au-delà du dernier
  palier. **Correct.**

Aucun `except:` nu. Les seules exceptions larges sont dans `wsclient.close()`,
où elles sont légitimes.

**Aucun bug économiquement conséquent trouvé dans le chemin de l'argent.** Le
code n'est pas ce qui bloque l'objectif.

## 3. Ce qui a été ajouté — la porte de capital

Le projet a changé deux fois d'objectif sans jamais écrire la conversion qui
les rend comparables. `capital_gate.py` la rend obligatoire.

> **Un objectif en euros n'est pas un objectif tant que le capital n'est pas dit.**
> 20 €/jour valent **200 bps/jour** sur 1 000 € et **1 bps/jour** sur 200 000 €.

| capital | taux requis | facteur manquant vs meilleur taux démontré |
|---|---|---|
| 1 000 € | **200,0 bps/j** | **182×** |
| 5 000 € | 40,0 | 36× |
| 10 000 € | 20,0 | 18× |
| 40 000 € | 5,0 | 4,5× |
| **182 000 €** | **1,1** | **1,0× — atteint** |

Meilleur taux réellement **démontré** du dépôt : **1,10 bps/jour** (prêt SOL
OKX, endpoint public). Les récompenses Polymarket (4 bps/jour médian) sont
enregistrées comme **non démontrées** — hors échantillon, sélectionner les
marchés les mieux payés donne −234 à −3 039 bps/jour.

### La décomposition qui laisse une chance à un petit capital

    bps/jour = edge net par aller-retour × rotations par jour

C'est le seul levier dont dispose 1 000 € : la **vitesse**. La contrainte
devient testable — non plus « trouver un edge » mais un **couple**.

Meilleur couple mesuré du dépôt (`nc_gate.py`, revérifié ce jour, reproduit à
l'identique) : perpétuels d'actions tokenisées, 5 min, seuil 2 σ, tenue 6
barres, exécution décalée d'une barre — **brut 2,63 bps × 141 rotations/jour
= 371 bps/jour brut**, t = 3,03, n = 10 754, 76 jours.

**Ce brut dépasse la cible de 200 bps/jour.** Ce qui manque est le coût :

| capital | plafond de coût par aller-retour |
|---|---|
| 1 000 € | **1,21 bps** |
| 10 000 € | 2,49 bps |

| coût réellement accessible (lu, non supposé) | |
|---|---|
| OKX taker ×2 | 10,00 bps |
| OKX maker ×2 | 4,00 bps |
| Hyperliquid taker ×2 | 9,00 bps |
| **Hyperliquid maker ×2** | **3,00 bps** |
| palier market-maker HL (exige 31 M$/jour de volume) | −0,20 bps |

**Le piège, encodé dans le module pour ne pas y retomber.** Rapprocher 2,63 bps
de brut et 3,00 bps de coût maker suggère qu'il ne manque qu'un facteur 1,15.
**C'est faux.** Ce brut a été mesuré en **traversant** : passer en maker ne
change pas seulement le tarif, il change la **règle de remplissage** — un ordre
passif n'est exécuté que lorsque le prix vient le chercher, donc lorsqu'il
bouge contre lui. `net_edge_bps()` **lève une exception** si on lui demande de
soustraire un coût maker d'un brut taker.

## 4. Ce qui bloque, par ordre d'importance

1. **L'arithmétique.** 20 €/jour sur 1 000 € exige 200 bps/jour. Le meilleur
   taux démontré vaut 1,10. Facteur 182.
2. **Le coût d'accès.** Le seul mécanisme dont le *brut* dépasse la cible exige
   un aller-retour sous 1,21 bps ; le moins cher accessible vaut 3,00 bps, et
   n'est pas comparable au brut mesuré. Le tarif qui renverserait le signe
   (−0,20 bps) exige 31 M$ de volume quotidien — 300× ce capital.
3. **Aucune voie d'exécution.** Le dépôt n'a ni classe d'exécution réelle, ni
   clé, ni compte. Même un mécanisme démontré produirait 0 € réalisé.

## 5. Prochaine action concrète

Le seul chemin vers un PnL **réalisé et vérifiable** qui ne dépende d'aucun edge
non démontré est le prêt : 1,10 bps/jour, contractuel, mesuré sur endpoint
public. Sur 1 000 € il rapporte **0,011 €/jour** — réel, vérifiable, et 1 800
fois trop petit pour l'objectif.

Il n'existe, dans ce dépôt, aucune mesure qui soutienne 20 €/jour sur 1 000 €.
Ce document ne propose pas d'expérience supplémentaire pour en chercher une :
les trois blocages ci-dessus sont chiffrés, et aucun ne se lève par du code.

---

*Aucun seuil, aucune hypothèse de coût, aucun tarif n'a été modifié pour rendre
un chiffre plus présentable. LIVE reste désactivé. 978 tests passent.*
