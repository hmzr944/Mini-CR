# Protocole gelé — capture du flux de liquidation forcé

**Phase C.** Écrit avant que les données de test existent.

## Pourquoi cette famille, et pourquoi elle est d'une autre nature

Les cinq familles précédentes étaient des **prévisions de prix**. La matrice de
post-mortem montre qu'elles meurent toutes à la **stabilité du signe** —
c'est-à-dire dans la prévision elle-même.

Ici l'observable n'est pas une prévision : c'est un **ordre forcé**. Le moteur
de liquidation *doit* fermer la position quel que soit le prix. Ce n'est pas
une opinion sur la valeur, c'est une contrainte mécanique. Savoir que ce
vendeur vend ne demande aucun modèle.

Porte d'hypothèse franchie (`hypothesis_gate.py`) à **1,58× l'objectif**.

## Ce qui est déjà mesuré, et qui n'est pas en cause

Le surdépassement est **réel, grand et symétrique**, sur 1 528 minutes et
21 instruments :

| minute de flux | rendement pendant |
|---|---:|
| vente forcée dominante | **−45,5 bps** |
| achat forcé dominant | **+49,0 bps** |
| vente forcée ample | **−81,1 bps** |
| achat forcé ample | **+68,4 bps** |

Le flux forcé déplace le prix. Ce fait-là n'a pas besoin d'être retesté.

## Ce qui n'est PAS établi

Que ce déplacement soit **capturable**. Sur l'ensemble conditionnel complet,
le fade rapporte +12,47 bps à 30 min avec **t = 1,31** — non significatif.

Le sous-ensemble à flux ample donne 61,9 bps et t = 3,04, mais :
- il est choisi **après** avoir vu le résultat ;
- **12 combinaisons** seuil × horizon ont été regardées, donc le meilleur *t*
  est mécaniquement gonflé ;
- 24 h ne contiennent qu'un régime.

**Un défaut de la mesure exploratoire, nommé :** la normalisation d'ampleur
utilisait la médiane du flux sur *toute* la période, donc de l'information
future. La règle gelée ci-dessous emploie une médiane **glissante causale**.
Le chiffre de 61,9 bps est donc à considérer comme optimiste, et non comme le
point de départ.

## Règle gelée — aucun degré de liberté

- **Univers** : perpétuels USDT d'OKX disposant de données de liquidation,
  60 premiers par volume 24 h.
- **Barre** : 1 minute.
- **Flux forcé** de la minute `t` : `vente` = somme des notionnels des
  liquidations `side=sell` (le moteur vend, il ferme des longs) ; `achat` =
  idem pour `side=buy`.
- **Déséquilibre** : `imb = (vente − achat) / (vente + achat)`.
- **Ampleur** : `amp = (vente + achat) / médiane glissante du flux total`,
  la médiane portant sur les **1 440 minutes précédentes, strictement avant
  `t`**. Un instrument sans 1 440 minutes d'historique est ignoré, jamais
  doté d'une médiane par défaut.
- **Déclenchement** : `|imb| > 0,5` **et** `amp > 5`.
- **Entrée** : à la clôture de la minute `t`, donc après que le flux est
  observé. Jamais pendant.
- **Sens** : fade. Long si `imb > 0` (vente forcée), short si `imb < 0`.
- **Détention** : **30 minutes**, fixe. Aucune sortie discrétionnaire.
- **Coûts** : 10 bps d'aller-retour taker OKX + demi-spread mesuré par
  instrument, jamais supposé nul.
- **Taille** : bornée par la profondeur au touch, jamais par le capital.

## Lacune de spécification, résolue et signalée

Le protocole ne disait pas ce qui se passe quand la **médiane glissante vaut
zéro** — cas d'un instrument sans flux forcé habituel. L'ampleur y est
indéfinie (division par zéro).

Résolution retenue : **ne pas déclencher.** Ce choix ne peut que *retirer* des
déclenchements, jamais en fabriquer, donc il ne peut pas flatter le résultat.
L'alternative — un plancher de notionnel absolu — aurait introduit un
paramètre après le gel, ce qui est précisément ce que le gel interdit.

## Jeu de test — il n'existe pas encore

Les 24 h analysées sont **brûlées**. La règle sera testée sur des données
**collectées vers l'avant**, à partir de maintenant.

C'est le holdout le plus propre possible : il est impossible d'y avoir adapté
quoi que ce soit, puisqu'il n'existait pas au moment où la règle a été écrite.
Le commit de ce fichier en fait foi.

**Minimum avant tout verdict : 14 jours de collecte continue**, soit environ
20× l'échantillon exploratoire, et plusieurs régimes intra-période.

## Critères de fermeture

1. net ≤ 0 après coûts sur les données futures ;
2. `t < 2` sur l'échantillon accumulé ;
3. résultat porté par moins de 5 instruments ;
4. amplitude réelle inférieure au demi-spread + frais, c'est-à-dire
   déplacement visible mais non capturable — **c'est le barreau où je
   m'attends à mourir** ;
5. la profondeur au touch au moment du déclenchement ne permet pas de
   déployer 1 000 $.

## Ce que je ne ferai pas

Ajuster le seuil d'ampleur, l'horizon, ou l'univers après avoir vu les
données futures. Si la règle échoue, elle échoue.

---

# Amendement — trois défauts trouvés en implémentant la règle

Tous trois produisaient **le même résultat observable** : zéro déclenchement,
pour toujours, sur n'importe quelle donnée. La collecte aurait tourné quatorze
jours pour rendre « aucune opportunité » — un **faux négatif silencieux**, et
la pire issue possible puisqu'elle est indiscernable d'un vrai résultat.

## Défaut 1 — la référence dégénérait

La règle gelée disait : *« les minutes SANS flux comptent comme zéro dans la
médiane de référence »*. Or les liquidations sont éparses. Mesure sur les
données réelles :

| instrument | minutes actives / 1 440 | médiane TOUTES | médiane ACTIVES |
|---|---:|---:|---:|
| ARB-USDT-SWAP | 257 | **0,0** | 107,8 |
| AKE-USDT-SWAP | 167 | **0,0** | 19,7 |
| ADA-USDT-SWAP | 16 | **0,0** | 12,6 |

La médiane vaut **zéro partout**, l'amplitude devient indéfinie, et ma
résolution conservatrice (référence nulle → ne pas déclencher) désactivait
donc la règle entière.

Pire : cette rédaction **ne correspondait pas à la mesure exploratoire**, qui
utilisait la médiane des minutes *actives*. J'avais cru rendre la règle plus
rigoureuse en la figeant ; je l'avais rendue inopérante.

**Correction :** la référence porte sur les minutes actives. C'est la
définition réellement employée par la mesure qui a motivé l'hypothèse.

## Défaut 2 — la référence n'était jamais prête

`TrailingMedian` exigeait **1 440 valeurs** avant de publier une médiane. Une
fois qu'on ne pousse plus que les minutes actives, il n'y en a jamais 1 440.

**Correction :** la fenêtre borne ce qu'on *retient*, un minimum d'observations
borne ce à partir de quoi on *ose publier*. `MIN_ACTIVE_MINUTES = 30`, le
minimum conventionnel pour une statistique d'ordre stable. Paramètre déclaré,
non choisi sur un résultat.

## Défaut 3 — le feed perdait des minutes

Le feed ne lisait que la dernière minute complète, alors qu'un cycle dure plus
d'une minute (38 instruments × ~1,5 s). Il **sautait donc la plupart des
minutes**, et tout flux forcé qui y tombait disparaissait sans trace.

**Correction :** balayage de toutes les minutes écoulées depuis le passage
précédent, plafonné à 30 minutes de rattrapage — au-delà on a perdu le fil, et
rejouer davantage donnerait l'illusion d'une surveillance continue qui n'a pas
eu lieu. Vérifié en direct : 12 minutes balayées à l'amorçage, **48 au cycle
suivant**.

Le carnet n'est collecté que pour la minute **courante** : pour une minute
rattrapée il serait postérieur au choc et donnerait une profondeur qui
n'existait pas à cet instant.

## Ce que cet amendement coûte à la rigueur du gel

Les corrections 2 et 3 sont des défauts d'implémentation : la règle voulue
était claire, le code ne la réalisait pas. La correction 1 est plus gênante —
**la règle écrite différait de la mesure qui l'avait motivée**, et je m'en
aperçois après avoir vu les résultats exploratoires. Je ne peux donc pas
prétendre que cette définition est vierge de tout biais de sélection.

Ce qui reste intact : les **données futures**. Elles n'existaient pas quand la
règle a été écrite, ne l'ont pas influencée, et ne l'influenceront pas. Le
test confirmatoire garde toute sa valeur ; c'est la prétention à un gel parfait
qui n'en a plus.

## Garde ajoutée

`test_LA_REGLE_PEUT_SE_DECLENCHER_SUR_DONNEES_EPARSES` vérifie qu'une règle
**peut** se déclencher sur des données réalistes. Une règle qui ne peut pas se
déclencher n'est pas une hypothèse, et rien dans le dépôt ne le vérifiait.
