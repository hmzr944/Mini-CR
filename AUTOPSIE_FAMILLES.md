# Autopsie des familles — où PRISM perd-il réellement les opportunités ?

21/09/2026. La question posée était la bonne, et elle n'avait jamais été posée :

> **PRISM rejette-t-il les edges parce qu'ils n'existent pas, ou parce que son
> détecteur est trop restrictif ?**

Les deux causes produisent le **même observable** — « aucune opportunité ».
C'est le pire mode d'échec possible pour un moteur de recherche.

**Réponse : sur neuf familles, deux ne pouvaient structurellement jamais émettre
un seul candidat.** Elles n'ont pas été réfutées. Elles n'ont jamais été testées.

---

## 1. BOOK_IMBALANCE — une porte mathématiquement infranchissable

```python
floor = state.spread_bps
if abs(dev) <= floor:            # dev = déviation du microprix au mid
    return DetectionOutcome.nothing(...)
```

Le microprix est une moyenne pondérée du bid et de l'ask :
`(bid·ask_sz + ask·bid_sz)/(bid_sz+ask_sz)`. Il vit donc **toujours dans
[bid, ask]**, et par conséquent :

> **|déviation| ≤ spread / 2, par construction.**

La porte exigeait `|déviation| > spread`. Elle ne pouvait **jamais** être
franchie, quelle que soit la donnée.

**Vérification** — 20 000 carnets aléatoires, prix de 1 à 100 000, spreads et
tailles balayés sur sept ordres de grandeur :

| | |
|---|---|
| rapport max observé `\|dev\|/spread` | **0,500000** |
| borne théorique | 0,500000 |
| exigé par la porte | > 1,000000 |
| **candidats émis** | **0** |

Ce n'est pas un résultat statistique. C'est une impossibilité, indépendante de
toute distribution de marché.

## 2. DEPTH_WITHDRAWAL — un historique qui n'existait pas

```python
def _past_spreads(self, state):
    out = []
    for ts, bid_usd, ask_usd in state.depth_history:
        if bid_usd > 0 and ask_usd > 0:
            out.append(state.spread_bps)   # borne basse: spread courant
    return out or [state.spread_bps]
```

`depth_history` contient `(ts, bid_usd, ask_usd)` — **aucun spread**. La méthode
ajoutait donc le spread **courant** à chaque observation passée. Alors :

```
tightest = min(past_spreads) = state.spread_bps
excess   = state.spread_bps - tightest = 0
porte    : excess <= tightest   →   0 <= spread   →   TOUJOURS VRAI
```

**Vérification** — 72 configurations, retrait de profondeur jusqu'à **99 %**,
spread jusqu'à **5000 bps** : **0 candidat émis.**

Le commentaire `# borne basse: spread courant` montre que la substitution était
consciente. Mais une borne basse égale à la valeur courante rend le test vide.

---

## 3. La garde qui aurait tout attrapé existait — et n'était appelée par personne

Le commit `5ef49cb` avait diagnostiqué **exactement** ce mode d'échec :

> « zéro déclenchement, pour toujours, sur n'importe quelle donnée. La collecte
> aurait tourné quatorze jours pour rendre "aucune opportunité", faux négatif
> indiscernable d'un vrai résultat. »

Et une garde avait été écrite pour lui : `sanity.check_firing_rate`, qui alerte
si un détecteur ne se déclenche jamais **ou** se déclenche trop souvent.

```
$ grep -rn 'check_firing_rate' --include=*.py . | grep -v sanity.py
$          # aucun résultat
```

**Elle n'était appelée nulle part.** Le diagnostic était juste, le remède écrit,
et jamais branché. `prism_v2/family_audit.py` le branche.

---

## 4. L'entonnoir, instrumenté

`prism_v2/family_audit.py` compte, famille par famille, où disparaissent les
opportunités — en séparant ce qui n'était jamais séparé : **donnée manquante**,
**seuil non franchi**, **erreur**, puis l'aval économique.

### Avant correctifs — 4 000 états générés

| famille | données KO | seuil KO | candidats | taux |
|---|---|---|---|---|
| SPREAD_DISLOCATION | 0 | 0 | 4 000 | **100 %** |
| AGGRESSIVE_FLOW | 0 | 933 | 3 067 | 76,7 % |
| SHORT_HORIZON_REVERSION | 0 | 1 199 | 2 801 | 70,0 % |
| **BOOK_IMBALANCE** | 0 | 4 000 | **0** | **0 %** |
| **DEPTH_WITHDRAWAL** | 0 | 4 000 | **0** | **0 %** |

**Cinq détecteurs sur cinq échouent à la garde du dépôt** : deux ne se
déclenchent jamais, trois se déclenchent 70 à 100 % du temps — un seuil qui ne
filtre rien n'est pas davantage une hypothèse.

Confirmé sur **carnets réels** (24 états reconstruits depuis
`data/touch_forward/`) : BOOK_IMBALANCE 0 %, DEPTH_WITHDRAWAL 0 %,
AGGRESSIVE_FLOW 60 %, SPREAD_DISLOCATION 45,8 %.

### Après correctifs

| famille | candidats | taux |
|---|---|---|
| BOOK_IMBALANCE | 3 726 | 93,2 % |
| DEPTH_WITHDRAWAL | 2 144 | 53,6 % |

Les deux familles peuvent enfin émettre. **Cela ne crée aucun edge** — cela rend
seulement la question posable.

---

## 5. Ce que les correctifs changent, et ce qu'ils ne changent pas

**`DEPTH_WITHDRAWAL`** : `MarketState` porte désormais `spread_history`, rempli
par `MarketStateTracker.on_book`. `_past_spreads` lit de vrais spreads passés,
hors instant courant. Et **quand l'historique manque, le détecteur répond
`INSUFFICIENT_DATA`, plus `nothing`** — c'est le correctif le plus important :
une donnée absente ne doit jamais se déguiser en résultat négatif.

**`BOOK_IMBALANCE`** : la porte devient un plancher de **significativité**
(25 % du demi-spread), pas un seuil **économique**. Votre diagnostic était
exact : un coût de traversée taker vivait dans un détecteur, alors que
l'architecture du dépôt énonce que *le détecteur ne doit jamais décider qu'une
opportunité est rentable*. C'est `economics.evaluate` qui trancherait, avec
l'hypothèse d'exécution explicite.

**Ce qui n'est pas démontré** : que ces familles contiennent quoi que ce soit.
Les taux de 53 à 100 % sont mesurés sur un générateur **uniforme sur un espace
extrême**, pas sur une distribution de marché réaliste. Ils disent que les
seuils ne sont pas calibrés ; ils ne disent rien sur l'existence d'un edge.

**L'asymétrie est essentielle** : les deux résultats à **0 %** sont
*indépendants de la distribution* — ce sont une impossibilité mathématique et un
défaut de code. Les taux élevés, eux, sont un artefact possible de mon
générateur et demandent des données réelles en volume.

---

## 6. Vos autres constats, vérifiés

| constat | vérifié ? | détail |
|---|---|---|
| `correlation_group = instrument.base` | **oui** | `router.py:54-57`. Toutes les opportunités BTC dans un seul bucket, quelles que soient la famille et la durée. Prudent comme garde-fou ; faux comme modèle de dépendance entre un carry de plusieurs jours et une réversion de quelques secondes. |
| `SizingLimits` : 100 $ / 1 000 $ / 10 % | **oui** | `sizing.py:46-52`. Confond bien trois questions distinctes : *l'edge existe-t-il*, *quelle capacité à 100 €*, *quelle capacité à 10 000 €*. |
| Le gate économique dans le détecteur | **oui** | `floor = state.spread_bps` dans 4 des 5 détecteurs de microstructure. |
| FORCED_FLOW mesure le déplacement déjà advenu | **oui** | Et le dépôt l'avait déjà réfuté causalement (`e05d90b`) : +14,55 bps **avant**, +2,97 bps après. |

---

## 7. Décision

**Arrêter la chasse à une dixième famille.** Vous aviez raison sur ce point, et
la preuve est maintenant chiffrée : deux familles sur neuf n'avaient jamais été
testées, et rien dans le dépôt ne pouvait le signaler.

Ordre des travaux :

1. **Fait** — corriger les deux détecteurs morts, brancher la garde, instrumenter
   l'entonnoir. 12 tests de non-régression ; 1 147 tests verts.
2. **Suivant** — calibrer les seuils sur des **données réelles en volume**, pas
   sur un générateur. La collecte `touch_forward` tourne 4×/jour ; il faut
   l'étendre aux instruments et à la durée nécessaires.
3. **Ensuite** — faire descendre les candidats dans l'aval économique et compter
   où ils meurent *vraiment* : coût connu, coût inconnu, causalité, capacité.
   L'entonnoir accepte déjà un `evaluate` pour cela.
4. **Seulement après** — conclure sur l'existence d'un edge dans ces familles.

**Aucun edge n'est démontré par ce travail.** Ce qui est démontré, c'est que
deux réponses négatives du moteur n'en étaient pas.
