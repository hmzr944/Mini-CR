# Ce que PRISM possède déjà, et ce que ça vaut

Question posée : *dans toutes les données que PRISM possède déjà, où
existe-t-il quelque chose qui pourrait réellement être transformé en argent ?*

Rien de neuf n'a été collecté. Trois mesures ont été faites sur ce qui
dormait dans `prism_v2/data/`. Les trois répondent non, et elles répondent non
**par le même écart**.

---

## 1. L'inventaire réel

| Jeu | Taille | Contenu | Utilisé jusqu'ici |
|---|---|---|---|
| `data/events` | 264 Mo | 21 instruments, carnets **incrémentiels 400 niveaux**, transactions, liquidations, horodatage exchange, 30 h | jamais rejoué |
| `data/observatory` | 27 Mo | 15 perpétuels inverses, **horloge commune 250 ms**, 10 niveaux, flux signé, 6,5 h | jamais ouvert |
| `data/funding` | 1,7 Mo | 92 jours d'historique OKX | oui |
| `data/polymarket` | 0,6 Mo | carnets | oui |

Le bien le plus précieux du dépôt n'est pas la taille : c'est que
l'observatoire échantillonne **quinze instruments sur la même horloge**.
Toutes les mesures antérieures du projet portaient sur un instrument à la
fois, ou sur des instruments lus à des instants différents — un écart entre
deux d'entre eux ne pouvait donc pas se distinguer d'un décalage
d'échantillonnage. Ces données-là le permettent. C'est ce qui a orienté la
mesure n° 3.

Les quinze sont tous des perpétuels inverses réglés en USD. **Aucun taux de
change n'est traversé** : un écart y est un écart réel, pas un mouvement de
peg.

---

## 2. Trois mesures

### Mesure 1 — écart exécutable entre instruments du même sous-jacent

Six paires `X-USD` (spot) contre `X-USD-SWAP` (perp inverse). Même devise de
cotation, donc aucun peg dans l'écart. 264 Mo rejoués à travers le moteur
de carnet incrémentiel : **287 118 messages, 0 trou de séquence**.

L'aller-retour complet s'écrit exactement, sans aucun prix milieu :

```
G_in  = (jambe_riche_bid − jambe_pauvre_ask) / mid      à l'entrée
G_out = l'écart exécutable dans le sens INVERSE          à la sortie
net   = G_in + G_out − frais
```

| paire | écart médian | max sur 30 h | > 20 bps | aller-retour brut | net maker |
|---|---|---|---|---|---|
| BTC | 4,98 | 6,92 | 0,00 % | **+0,10** | −7,90 |
| ADA | 5,21 | 10,50 | 0,00 % | −11,53 | −19,53 |
| DOGE | 6,32 | 10,14 | 0,00 % | −5,61 | −13,61 |
| DOT | 4,10 | 12,32 | 0,00 % | −17,71 | −25,71 |
| ETC | 0,00 | 19,64 | 0,00 % | −28,22 | −36,22 |
| BCH | 0,00 | 9,24 | 0,00 % | −11,96 | −19,96 |

Sur BTC l'écart est positif **100 % du temps**, à 4,98 bps, et ne s'inverse
jamais. Ce n'est pas une dislocation : c'est le basis porté par le funding,
c'est-à-dire le carry déjà mesuré à 0,52 bps/jour. L'entrée encaisse 4,91,
la sortie rend 4,89 : **+0,10 bps brut**, avant le moindre frais.

Profondeur au meilleur prix : **10 à 814 USD**. Même s'il y avait un écart,
il n'y aurait pas de place dedans.

### Mesure 2 — fournir la liquidité plutôt que la prendre

Le signe s'inverse : on encaisse le demi-spread au lieu de le payer. 13
instruments, 72 000 remplissages, en supposant **la file d'attente gagnée à
chaque transaction** — borne supérieure volontairement irréaliste.

| | demi-spread encaissé | sélection adverse à 1 s |
|---|---|---|
| BTC-USDT-SWAP | 0,01 | 1,21 |
| DOGE-USDT-SWAP | 0,65 | 1,27 |
| ADA-USDT-SWAP | 2,72 | 2,70 |
| DOT-USD-SWAP | 4,28 | 4,52 |
| ETC-USD-SWAP | 1,88 | 5,61 |
| … | … | … |

**À une seconde, la sélection adverse dépasse le demi-spread sur 12 des 13
instruments**, avant même les 2 bps de frais maker.

52 tests (13 × 4 horizons), Benjamini-Hochberg à q = 0,10 : **1 survivant**,
BCH-USDT-SWAP à 300 s, +1,08 bps par remplissage, t = 3,07. Le même
instrument perd à 1 s, 5 s et 30 s ; le signe ne bascule qu'à cinq minutes.

Placebo, sens d'agresseur mélangés : **+1,13 bps, t = 3,21 — 104 % du
résultat réel**. Le prétendu edge ne doit rien au côté qui a été frappé. Il
est mort.

### Mesure 3 — dislocations transversales sur le panneau synchrone

1 173 717 instantanés, 15 perpétuels inverses, horloge commune, 6,5 h. Pour
chaque alt, bêta contre BTC estimé sur une fenêtre **qui se termine avant**
la fenêtre de dislocation, puis mesure de la refermeture nette.

| paire | coût A/R | dislocation médiane | > coût | net 10 s | net 30 s | net 300 s |
|---|---|---|---|---|---|---|
| UNI | 27,9 | 6,0 | 3,9 % | −28,8 | −28,8 | −40,7 |
| DOT | 31,0 | 5,5 | 2,1 % | −31,8 | −31,1 | −25,4 |
| FIL | 26,3 | 4,2 | 0,8 % | −27,4 | −26,8 | −23,5 |
| XRP | 22,3 | 2,5 | 0,2 % | −21,0 | −17,3 | −4,5 |
| ETH | 20,1 | 1,3 | 0,1 % | — | — | — |

**28 tests, 0 moyenne positive.** Pas « ne survit pas à la correction » :
aucune n'est positive. Et les deux paires où la dislocation dépasse le plus
souvent son coût (UNI 3,9 %, DOT 2,1 %) sont exactement celles dont le net
est le pire : ce qui ressemble à une grande dislocation sur un instrument
fin est le bruit de son propre spread, et il ne revient pas.

---

## 3. Le fait qui revient trois fois

| mesure | amplitude trouvée | coût d'aller-retour | rapport |
|---|---|---|---|
| écart inter-instruments | 1,3 – 6,3 bps | 8 – 20 bps | ≈ 1 : 4 |
| demi-spread maker | 0,01 – 4,3 bps | 1,2 – 5,6 bps de sélection adverse | ≈ 1 : 1,3 |
| dislocation transversale | 1,3 – 6,0 bps | 20 – 31 bps | ≈ 1 : 5 |

Et, avec tout ce que le projet avait déjà mesuré : microstructure taker
2,03 bps, flux de liquidation 2,97 bps, crible de mécanismes 2,03 bps.

**Tout ce qui est mesurable dans l'univers accessible vaut 1 à 6 bps. Tout
aller-retour en coûte 8 à 31.** L'écart est d'un facteur quatre à cinq,
toujours le même, sur des mécanismes sans rapport entre eux.

Ce n'est pas un défaut de mesure qu'un meilleur signal corrigerait. La
mesure 2 dit pourquoi : le demi-spread et la sélection adverse sont **de la
même taille**, instrument par instrument. Ce n'est pas un hasard, c'est
l'équilibre — les teneurs de marché fixent le spread pour couvrir la
sélection adverse plus leurs coûts. Payant le tarif public et non le tarif
VIP, je suis structurellement **sous** cette ligne sur chaque instrument. Ce
n'est pas une observation empirique fragile, c'est une conséquence du barème
de frais.

---

## 3 bis. Est-ce mon barème de frais qui bloque ?

Puisque la contrainte qui mord est le plancher de coût, la question suivante
s'impose : **à quel niveau de frais la réponse changerait-elle ?** Les deux
mesures ont été inversées. Aucun barème n'est postulé — les seuils ci-dessous
sont une propriété des données.

**Fourniture de liquidité — frais maker de seuil, par jambe passive**

| instrument | demi-spread | sélection adverse 1 s | frais de seuil |
|---|---|---|---|
| ADA-USDT-SWAP | 2,72 | 2,70 | **+0,03** |
| DOT-USD-SWAP | 4,28 | 4,52 | −0,24 |
| BCH-USDT-SWAP | 2,32 | 2,69 | −0,37 |
| BTC-USDT-SWAP | 0,01 | 1,21 | −1,20 |
| ETC-USD-SWAP | 1,88 | 5,61 | −3,73 |

**Sur 12 des 13 instruments, la gratuité complète ne suffirait pas** : la
venue devrait me *payer* une remise de 0,24 à 3,73 bps par jambe pour
atteindre le simple point mort. Le seul seuil non négatif, ADA-USDT-SWAP,
vaut +0,03 bps — n'importe quel frais le tue, et c'est encore en supposant la
file d'attente gagnée à chaque transaction.

**Dislocations transversales — balayage du frais par traversée**

Baisser les frais abaisse aussi le seuil de déclenchement : on entre alors sur
des dislocations plus petites, qui se referment moins. Ce n'est donc pas le
même calcul décalé d'une constante, et cela se remesure.

| paire | 5 bps | 2 bps | 1 bps | **0 bps** |
|---|---|---|---|---|
| ETH | — | −5,5 | −3,8 | **−0,0** (t = −1,7) |
| SOL | — | −9,4 | −5,3 | **−1,1** |
| DOGE | −9,6 | −8,2 | −5,0 | **−1,0** |
| XRP | −17,3 | −9,9 | −6,8 | **−2,4** |
| UNI | −28,8 | −17,1 | −13,2 | **−9,1** |

**0 cellule positive sur 56, y compris à frais nuls.** Sur les paires les plus
liquides, les demi-spreads seuls (ETH 0,1 bps) sont pourtant *inférieurs* à la
dislocation médiane — j'ai cru un instant que le signe basculerait. La mesure
dit non : ce qu'on gagne en abaissant le coût, on le perd en entrant sur des
écarts qui ne reviennent pas.

Conclusion des deux : **ce n'est pas mon palier de frais qui bloque, c'est
tout palier de frais.** Changer de venue, monter en volume ou négocier un
tarif VIP ne renverse aucun de ces signes.

---

## 4. Deux corrections de code

**Défaut réel, corrigé.** `observatory.py` agrégeait le flux signé en
`px × sz`. Sur un perpétuel **inverse**, `sz` compte des contrats de `ctVal`
USD et le notionnel **ne dépend pas du prix**. Conséquence sur les données
déjà collectées : BTC-USD-SWAP ressortait à **9,57 milliards de dollars par
heure**, soit 760 fois le volume réel ; ADA était sous-évalué de 28 fois. Le
classement des instruments par volume était inversé entre pièces chères et
pièces bon marché.

Corrigé en passant par `contracts.usd_notional`, la fonction qui existait
déjà et que le carnet utilisait correctement. Contrôle indépendant : les
volumes reconstitués valent désormais **0,37 à 1,37 fois** le volume 24 h
publié par OKX (médiane 0,9) contre 760 fois avant. Six tests de régression
ajoutés, qui échouent sur l'ancien comportement. C'est la même classe
d'erreur que la profondeur de carnet lue en unités de base, qui avait déjà
coûté un facteur 100.

**Erreur attrapée dans ma propre mesure.** La première version de la mesure 2
portait le signe de la sélection adverse à l'envers. Elle affichait une
sélection adverse **négative** — le marché s'écartant en notre faveur après
chaque remplissage passif — et **+400 bps/jour**. Un résultat impossible :
c'est l'invraisemblance économique, pas la statistique, qui l'a arrêté. Le
signe est désormais fixé par la direction économique dans le code, avec le
raisonnement écrit à côté.

**Fausse alerte, signalée comme telle.** J'ai d'abord cru le fichier de
l'observatoire corrompu : `gzip` s'arrête à 52 766 lignes sur 1 173 717. Le
projet possède déjà `_load_gzip_members`, qui resynchronise sur chaque membre
gzip et récupère **la totalité** des enregistrements. Aucun défaut. La
donnée n'avait simplement jamais été ouverte.

---

## 5. Ce que les données disent de la direction

La contrainte qui mord n'est pas le signal. C'est le plancher de coût — il est
le même dans les trois mesures, et le signal échoue toujours du même facteur
contre lui.

Mais la section 3 bis ferme aussi la sortie évidente. **Le plancher de coût
n'est pas mon barème de frais.** À frais nuls, la dislocation transversale
reste négative sur 14 paires sur 14 ; la fourniture de liquidité exigerait une
remise payée par la venue sur 12 instruments sur 13. Le plancher qui bloque
est le demi-spread lui-même, et le demi-spread est de la même taille que la
sélection adverse parce que c'est ce qui le fixe.

Ce qui reste ouvert n'est donc ni « un signal plus grand » ni « des frais plus
bas » dans ce périmètre : les deux sont mesurés fermés. Ce qui reste ouvert est
un périmètre où le demi-spread n'est pas fixé par la même contrainte — et cela
se démontre par la mesure, pas par l'espoir.

Aucun résultat positif n'est retenu dans ce document. Aucun seuil, aucune
hypothèse de coût, aucun remplissage n'a été modifié pour rendre un chiffre
plus présentable.
