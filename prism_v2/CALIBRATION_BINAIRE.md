# Le biais favori/outsider : mesuré des deux côtés, fermé des deux côtés

**Mécanique testée**, et pourquoi ce n'est pas une n-ième famille de signaux :
on ne prédit rien. On achète un contrat à `p`, on encaisse 1 si l'événement se
produit. La seule question est de **calibration** — la part des marchés résolus
OUI dans un seau de prix égale-t-elle le prix de ce seau ?

| | |
|---|---|
| **Qui paie ?** | l'acheteur d'outsider, qui paie un billet de loterie au-dessus de sa valeur |
| **Quelle contrainte ?** | un goût documenté pour les gains rares et élevés |
| **Pourquoi accessible à 1 000 € ?** | profondeur au touch mesurée : **685 à 4 950 $** par marché |
| **Quel risque ?** | binaire, non couvrable : perte de 100 % avec probabilité 1 − w |
| **Prédiction directionnelle ?** | non — calibration d'une population, pas anticipation d'un prix |

**Pourquoi rouvrir cette venue.** L'audit précédent avait fermé la moitié
**outsider** (−19,3 ¢/$ à 1–3 ¢) sans jamais tester l'autre. Si les outsiders
sont surcotés, les favoris sont mécaniquement sous-cotés : c'est le même biais
vu de l'autre côté. C'est la différence structurelle qui justifiait la
réouverture.

**Échantillon** : 13 075 marchés résolus, **6 578 événements**, **621 jours**,
volume ≥ 10 000 $.

---

## 1. Trois défauts de méthode corrigés avant tout résultat

**(a) L'ancrage temporel.** La v1 datait les prix par rapport au dernier point
de l'historique. Celui-ci marque l'**arrêt des échanges**, pas la résolution :
l'écart mesuré allait de **2 593 à 11 935 heures**. « Six heures avant » pouvait
être des mois avant, sur des marchés délaissés. Un tiers de l'échantillon
tombait ainsi à 0,44 pour résoudre NON dans 95 % des cas — ce n'était pas un
biais de marché, c'était ma fenêtre. Corrigé par `closedTime`.

**(b) L'ordre du regroupement.** Regrouper par événement **avant** de répartir
en seaux moyenne le prix du groupe entier : un « quel candidat gagne » coté
0,90 / 0,05 / 0,02 ressort à 0,32, et **la zone favorite se vide**. Tous les
seaux au-dessus de 0,75 étaient inexistants. On répartit d'abord par prix, on
regroupe ensuite à l'intérieur du seau.

**(c) L'écart-type sous H0.** L'écart-type de la proportion *observée* vaut 0
quand un seau résout unanimement, et le `t` part à l'infini — la v1 affichait
**t = 38 000**. Le test correct prend la variance sous l'hypothèse nulle,
`sqrt(p(1−p)/n)`. Seize marchés sur seize à 0,98 ne sont pas remarquables : la
probabilité vaut 0,75.

**(d) Le déblocage des données.** `prices-history?interval=max` ne rend **rien**
pour un marché résolu il y a plus d'un mois — la première collecte n'en
retenait que 651, tous des trente derniers jours. Avec `startTs`/`endTs`
explicites, le même marché résolu en novembre 2024 rend son historique complet.
C'est ce qui a porté l'échantillon de 651 à 13 075.

## 2. Le résultat

**0 / 32 cellules survivent à Benjamini-Hochberg avec un écart positif.**

Zone favorite, horizon 48 h, coûts mesurés (demi-spread 0,5 ¢, frais par marché) :

| zone | n év. | prix | % OUI | écart | IC 95 % | n requis | bps/jour |
|---|---|---|---|---|---|---|---|
| [0,90 ; 1,00) | 542 | 0,9674 | 0,9637 | −0,36 ¢ | [−1,86 ; +1,13] | 474 | −51 |
| [0,96 ; 1,00) | 364 | 0,9872 | 0,9890 | +0,18 ¢ | [−0,98 ; +1,33] | 1 237 | −18 |
| [0,97 ; 0,99) | 135 | 0,9816 | 0,9926 | +1,10 ¢ | **[−1,17 ; +3,37]** | 854 | +27 |

**Tous les intervalles de confiance contiennent zéro**, et l'échantillon est
2 à 3 fois trop petit partout. Le seul motif cohérent en signe — [0,97 ; 0,99)
positif aux quatre horizons — ne dépasse jamais `t = 1,40`, et la coupe
temporelle le montre porté par la seconde moitié seule (+0,05 ¢ puis +1,78 ¢
à 12 h ; +0,18 ¢ puis +1,81 ¢ à 48 h).

## 3. La contrainte structurelle, qui est le vrai résultat

Le gain maximal d'un favori acheté à `p` vaut `(1 − p)` et s'annule quand
`p → 1`. Le bruit, lui, ne décroît qu'en `sqrt(p(1−p))`. Si l'écart réel vaut
une fraction `f` du maximum, le nombre d'observations requis est

    n = t² · p / ( f² · (1 − p) )

| prix | gain max | n requis (t = 2, f = ½) |
|---|---|---|
| 0,85 | 17,6 % | 91 |
| 0,90 | 11,1 % | 144 |
| 0,95 | 5,3 % | 304 |
| 0,97 | 3,1 % | 517 |
| 0,99 | 1,0 % | **1 584** |

**La mécanique devient invérifiable exactement là où elle paraît la plus
sûre.** Ce n'est pas un défaut de collecte qu'un an de plus corrigerait : une
année entière de Polymarket au-dessus de 10 000 $ de volume ne fournit que 135
événements dans [0,97 ; 0,99) à 48 heures, contre 854 requis. Toute conclusion
tirée d'un seau à faible `n` affichant `w = 1,000` est une illusion
d'échantillon.

## 4. Le côté où la mesure est solide — et pourquoi il ne paie pas non plus

Le seul effet significatif se trouve au **milieu**, où l'échantillon est grand :
les contrats cotés entre 0,50 et 0,75 résolvent OUI **moins** souvent que leur
prix ne l'annonce (−2,4 à −4,4 ¢ ; `t` = −2,0 à −2,4 sur 1 717 événements).
Acheter y perd ; acheter le NON devrait donc y gagner.

**Le frais annule exactement l'écart.** Polymarket facture au preneur
`taux × min(p, 1−p)`, avec un taux lu par marché (0,0175 à 0,25 ; aucun marché
résolu à taux nul). **Cette assiette est maximale à mi-prix** : 5 % sur un
contrat à 0,455 coûte 2,28 ¢, pour un écart mesuré de 2,6 ¢.

Achat du NON quand le OUI cote dans [0,50 ; 0,75), frais appliqué par marché :

| horizon | taux max | n év. | EV net | t |
|---|---|---|---|---|
| 12 h | ≤ 0,03 | 535 | +1,91 % | 0,38 |
| 48 h | ≤ 0,03 | 532 | +3,39 % | 0,67 |
| 48 h | tous | 892 | +1,15 % | 0,30 |
| 168 h | tous | 686 | −5,23 % | −1,21 |

**0 / 9 cellules survivent à Benjamini-Hochberg.** Le signe s'inverse avec
l'horizon, et les marchés à faible taux n'existent que dans la seconde moitié
de l'échantillon — il n'y a donc même pas de première moitié contre laquelle
valider.

**Le fait structurel** : le barème de la venue est anti-corrélé avec la zone où
un écart est mesurable. Le frais est maximal à `p = 0,5`, c'est-à-dire
exactement là où la puissance statistique est la plus forte et où le
mispricing est mesuré. Là où le frais est négligeable — les favoris — l'écart
est trop petit pour être établi.

## 5. Portée exacte

Fermé : Polymarket, 621 jours, marchés au-dessus de 10 000 $ de volume,
horizons de 2 à 168 heures, achat au preneur. Les trois régions de prix sont
traitées : outsiders (audit précédent), milieu (ci-dessus), favoris (ci-dessus).

Non testé, et donc non conclu : la cotation passive sur ces mêmes marchés (le
maker n'y paie aucun frais — voir `REVENU_CONTRACTUEL.md`, fermé pour une autre
raison), les autres venues de prédiction, et les marchés sous 10 000 $ de
volume.

---

*Aucun seuil, aucune hypothèse de coût, aucun frais n'a été modifié pour rendre
un chiffre plus présentable. PnL réalisé : 0 €. 964 tests passent.*
