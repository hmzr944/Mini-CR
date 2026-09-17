# Polymarket — le markout est le mauvais outil pour un contrat binaire

**Statut : `MEASUREMENT_INVALID` — l'instrument ne convient pas au domaine.**

Ce document ne rapporte pas de résultat économique. Il rapporte pourquoi celui
que la mesure produisait devait être rejeté.

---

## Ce que la mesure a produit

2,60 h · 12 marchés à frais actifs · 10 284 instantanés de carnet · **1 700
fills maker réels** (chaque trade du flux implique un maker de l'autre côté —
aucune hypothèse de file d'attente n'est nécessaire).

| horizon | demi-spread | markout | rebate | NET | t |
|---|---|---|---|---|---|
| 60 s | +0,00739 | −0,00004 | +0,00046 | **+0,00781** | 2,23 |
| 300 s | +0,00735 | −0,00008 | +0,00046 | +0,00774 | 2,22 |
| 1800 s | +0,00735 | +0,00030 | +0,00046 | +0,00811 | 2,33 |

Un net positif, 96,9 % de fills gagnants. **Ce chiffre ne doit pas être
publié**, et voici pourquoi.

---

## Pourquoi il est faux

**97,3 % des markouts à 60 s valent EXACTEMENT zéro.**

| tranche de prix | N | markout nul | demi-spread |
|---|---|---|---|
| p < 0,15 | 1 307 | **99,2 %** | +0,00050 |
| 0,35–0,65 | 347 | 89,3 % | +0,03873 |
| 0,65–0,85 | 10 | 70,0 % | +0,02482 |
| p > 0,85 | 36 | 88,9 % | **−0,05110** |

Le mid ne bouge pas à cette résolution. Le markout ne mesure donc **aucun
risque**, et le « net » n'est qu'un demi-spread encaissé sur un carnet gelé.

Un second signe que la mesure dérape : le demi-spread de la tranche p > 0,85
est **négatif** (−0,051). C'est impossible pour un maker véritable — il
signale une référence de carnet décalée par rapport au trade.

---

## Le défaut est conceptuel, pas numérique

Sur un **perpétuel**, une position peut être soldée au mid : le markout est la
bonne mesure, et c'est ce qui a permis de fermer proprement la famille maker
sur OKX (adverse selection −2,74 bps contre +1,40 bps de demi-spread).

Sur un **contrat binaire**, le risque réel n'est pas la dérive du mid à 60 s :
c'est le **règlement terminal à 0 ou 1 dollar**. Un markout court y est
structurellement aveugle. Le maker qui encaisse un demi-spread garde une
position qui vaudra 0 ou 1, et aucune mesure à 60 secondes ne le voit.

C'est exactement l'écart avec la littérature : l'étude Kalshi mesure les
makers **jusqu'à la résolution** et trouve **−10 %**. Ma mesure trouve
+0,8 ¢/part à 60 s. Les deux ne se contredisent pas — **elles ne mesurent pas
la même chose**, et c'est la seconde qui est hors sujet.

---

## Correctif apporté à l'outil

`markout_is_degenerate()` : au-delà de 60 % de markouts exactement nuls, la
mesure est déclarée dégénérée. Le pilote affiche alors les chiffres **en
diagnostic** et **annule** le verdict sur le rebate (`rebate_flips_the_sign:
null`, `void_reason: "markout degenere"`) au lieu de l'assortir d'un
avertissement.

La distinction compte : un avertissement se lit encore comme un résultat. Cinq
tests verrouillent la garde, dont un qui vérifie que le verdict est bien
**annulé** et pas seulement commenté.

---

## Ce que l'expérience établit malgré tout

**1. Le rebate n'était de toute façon pas la cause.** Section 3 : l'équation
était déjà positive **sans** rebate (+0,00735 contre +0,00781). Même si la
mesure avait été valide, elle n'aurait pas montré que le rebate change le
signe — elle aurait montré que ces marchés ont simplement des spreads larges.

**2. Le plafond structurel des récompenses est réel et mesuré.** Pool
quotidien observable sur les 12 marchés suivis : **2 500 $/jour**, dont
3 marchés seulement versent quelque chose. Même en captant 100 % — ce
qu'aucun maker ne fait — le revenu de récompense est borné à **912 500 $/an**
sur ce panier, et un opérateur plus gros ne l'augmente pas : il se dilue
lui-même.

Le rebate, lui, passe à l'échelle (proportionnel aux fills). Mais il est donc
proportionnel à l'adverse selection qui les accompagne — ce que cette mesure
ne sait pas voir sur un contrat binaire.

**3. Une famille fermée sans expérience.** Le « sniper » à 1–3 ¢ achète dans
la zone où l'étude Polymarket (588 M trades) mesure **−19,3 ¢ par dollar**.

---

## Ce qu'il faudrait pour trancher vraiment

Suivre des positions maker **jusqu'à la résolution**, pas à 60 secondes. C'est
ce que fait l'étude Kalshi, et sa réponse est −10 %.

Reproduire cela demanderait de collecter sur la durée de vie complète de
marchés — jours à semaines — et de reconstituer l'inventaire de makers
identifiables. Le champ `proxyWallet` du flux de trades le permet en principe.

**Je ne le recommande pas comme prochaine étape** : le meilleur résultat
possible est de retrouver le −10 % déjà publié, sur un échantillon plus petit.
Le coût est élevé et l'information attendue faible.

---

## Bilan des familles

| famille | statut | fermée par |
|---|---|---|
| taker microstructure OKX | **morte** | mesure — péage par aller-retour |
| carry inverse/linéaire | **dominée** | mesure — 191 bps/an, inconnue de marge |
| maker OKX | **morte** | mesure — adverse selection 2× le demi-spread |
| sniping longshot | **morte** | littérature — −19,3 ¢/$ |
| maker prédiction, moyenne | **présumée négative** | littérature — Kalshi, −10 % |
| maker Polymarket, markout court | **non mesurable** | ce document |

Trois de ces six ont été fermées par la recherche plutôt que par des runs.
C'est la consigne appliquée : l'échec d'un autre coûte moins cher que le mien.
