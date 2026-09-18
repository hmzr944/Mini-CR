# PRISM V2 — RAPPORT FINAL

Contrat de sortie de la section XLIX du mandat. Chaque nombre renvoie au
script de `prism_v2/scans/` qui le recalcule. Rien n'est arrondi en ma
faveur, rien n'est extrapole.

---

## NO POSITIVE ECONOMIC IMPROVEMENT DEMONSTRATED.

C'est la ligne que le mandat exige lorsqu'aucun PnL positif n'a ete
demontre. Elle est exacte. Le reste de ce document explique ce qui a ete
mesure, ce qui a ete ferme, et pourquoi.

---

## 1. BASELINE

| | |
|---|---|
| PnL net realise au depart | **0 EUR** |
| Capital immobilise | 0 EUR |
| Mode | DISCOVERY — aucun ordre n'existe |

PRISM n'a jamais passe d'ordre. Il n'y avait pas de PnL a ameliorer : il y
avait un zero a quitter.

## 2. NEW SYSTEM — ce qui a ete construit

Cinq modules de mesure, tous en bibliotheque standard, tous testes.

| module | ce qu'il rend mesurable |
|---|---|
| `prism_v2/margin.py` | bareme de marge OKX REEL, par palier de taille, 482 familles. Echoue FERME au-dela du dernier palier au lieu d'extrapoler. |
| `prism_v2/capital.py` | coussin de survie MESURE : pire perte cumulee ATTEINTE dans la fenetre, quantile compatible avec un budget de ruine. Perte exacte par jambe, y compris la non-linearite des inverses. |
| `prism_v2/flow_census.py` | courbe de rendement par duree de detention. Ne CHOISIT deliberement pas la duree : il expose la courbe, declare un horizon d'avance, et ne fournit un maximum que comme BORNE d'elimination. |
| `prism_v2/dashboard.py` | tableau de bord economique. Une valeur absente ne peut pas se declarer MESUREE ; le PnL realise est DERIVE du mode, donc un PnL papier ne peut pas passer pour realise. |
| `prism_v2/kill_registry.py` | registre des plafonds. Refuse de comparer deux plafonds de denominateurs differents (CAPITAL vs NOTIONNEL) ou d'agregations differentes (PAIRE vs PORTEFEUILLE). |

Un defaut reel a ete corrige dans le code existant : `observatory.py`
agregeait le flux en `px * sz` sur des perpetuels INVERSES, ou `sz` est deja
libelle en USD. Le volume BTC en ressortait 760 fois trop grand. Apres
correction, les volumes tombent entre 0,37 et 1,37 fois les volumes publies
par OKX. Six tests de non-regression verrouillent la correction.

**1 024 tests passent. 27 gardes d'architecture passent. 0 secret. 0 classe
d'execution reelle. LIVE non implemente.**

## 3. REALIZED PNL

**0,00 EUR.** Aucun ordre reel n'a jamais ete emis, conformement au mandat.

## 4. EXECUTABLE PNL

**0,00 EUR.** Aucune strategie n'a atteint l'etat executable. Le blocage est
nomme et n'a jamais ete contourne par une hypothese : la probabilite de
remplissage passif est INCONNUE — PRISM n'a pas de modele de file d'attente.
Toute economie maker produite ici est donc une BORNE SUPERIEURE, file
supposee gagnee a chaque transaction.

## 5. ECONOMICALLY DEMONSTRATED PNL

**0,00 EUR.** Aucune famille testee ne franchit la porte economique.

Le meilleur plafond jamais mesure vaut **33,30 bps/jour de capital**
(3,33 EUR/jour sur 1 000 EUR) — et ce mecanisme est ABANDONNE : son
exposant de croissance du coussin vaut 0,493 [0,469 ; 0,517], c'est-a-dire
une marche aleatoire a la precision de la mesure. Le critere d'abandon
(alpha >= 0,45) avait ete declare AVANT le test.

## 6. NET PNL / CAPITAL / JOUR

| | |
|---|---|
| Realise | **0 bps/jour** |
| Meilleur plafond mesure (mecanisme abandonne) | 33,30 bps/jour |
| Seuil du mandat | **271,87 bps/jour** |

## 7. OOS

Aucune validation hors echantillon n'a eu lieu, pour une raison qui n'est
pas une negligence : **aucun candidat n'a survecu en echantillon**. Il n'y a
jamais eu de modele a valider hors echantillon.

La discipline statistique appliquee en echantillon a ete, a chaque famille :
correction de Benjamini-Hochberg sur l'ensemble des tests menes (et non sur
les seuls survivants), test de Student unilateral, et blocs DISJOINTS
lorsque les fenetres se recouvrent.

## 8. DRAWDOWN

**0 %** realise. Mesure sur l'historique disponible, le coussin de survie
requis vaut 20,03 % du notionnel a 14 jours pour la couverture par
correlation, 0,441 % pour la couverture meme sous-jacent.

## 9. COST

| poste | valeur | statut |
|---|---|---|
| Aller-retour median, deux jambes, taker | **21,8 bps** | MESURE |
| dont frais taker | 20,0 bps | bareme public |
| dont demi-spreads | 1,8 bps | carnets reels |
| Frais maker OKX | 2,0 bps/jambe | bareme public |
| Frais maker MEXC | **0,0 bps/jambe** | bareme public, verifie |
| Slippage reel | INCONNU | exige de vrais ordres |
| Latence reelle | INCONNU | jamais mesuree |

## 10. CAPITAL IMMOBILIZED

**0 EUR.** Taux d'utilisation 0 %.

Levier disponible mesure sur bareme reel : 5,37x sur une paire, 9,43x sur
16 paires — le gain de mutualisation du coussin vaut 2,21x, mesure et non
suppose.

## 11. TURNOVER

**0 x/jour.**

## 12. CAPACITY

La capacite est le poste le plus severement contraint et le moins souvent
regarde. Profondeur au touch sur les paires fines : **10 a 814 USD**. Sur
l'unique quasi-survivant du dernier test (DOT-USD-SWAP), le flux agressif
TOTAL de la journee vaut 4 580 USD : meme en le captant integralement, le
plafond est 1,36 USD/jour.

## 13. ADAPTATION BEHAVIOR

Le systeme n'adapte rien, parce qu'il n'a rien a adapter. Ce qui a ete
construit a la place est un mecanisme d'ARRET : le registre des plafonds
enregistre chaque famille morte avec son plafond, son denominateur, son
agregation, sa taille d'echantillon et son motif, et refuse qu'une famille
soit ressuscitee sans depasser le plafond enregistre, preuve et echantillon
a l'appui. C'est ce qui a empeche quatre resultats spectaculaires de
survivre.

## 14. DISTANCE TO 272 BPS

**Facteur 8,2 sur le meilleur plafond jamais mesure**, lui-meme abandonne.
Sur le PnL reellement demontre — 0 — la distance est infinie.

## 15. INCREMENTAL IMPROVEMENT

Sur le PnL : **aucune**. La ligne en tete de ce document dit exactement
cela.

Ce qui a change est la nature de la conclusion. Au depart, « ca ne marche
pas » etait une opinion. Il est maintenant une mesure, sur quatre formes
distinctes de gain de marche, chacune fermee par un chiffre :

| forme | ce qui a ete mesure | contre |
|---|---|---|
| TRAVERSEE | edge 1 a 6 bps, huit mesures independantes | cout 8 a 31 bps |
| FLUX DETENU | 33,3 bps/jour, cinq representations | seuil 272 |
| RISQUE | derive de 86 %/an requise pour g\* | inexistante |
| IMMEDIATETE | concession 0,00 a 0,26 bps, 29 038 rafales | selection adverse 1,54 a 1,88 |

Toutes les grandeurs favorables mesurees tombent entre 0,26 et 6,5 bps,
quand tout aller-retour en coute 8 a 31. Un rapport constant de 1 pour 5 a
1 pour 20, stable sur quatre familles sans rapport entre elles. Ce n'est pas
un defaut de mesure : c'est l'aspect d'une venue dense et concurrentielle
vue de l'exterieur avec des donnees publiques.

### Le dernier levier, et pourquoi il est ferme

Le mandat est explicit : si le brut existe et que le net est negatif, il
faut travailler le COUT, pas chercher une strategie de plus. Je l'ai fait.

Ma premisse de depart etait : « MEXC facture 2 bps contre 5 chez OKX, et
certains instruments y ont un demi-spread de 0,01 bps — soit un aller-retour
de 4,0 bps au lieu de 21,8 ». **Elle etait fausse, et je l'ecris avant tout
le reste.** Le bareme public MEXC (`api/v3/exchangeInfo`) renvoie
`takerCommission 0.0005`, soit 5,0 bps — identique a OKX — sur les quatre
symboles interroges. Et le demi-spread de 0,01 bps etait un artefact
d'instantane : sur dix releves espaces, la mediane de PONSUSDT vaut 5,390
bps. La table `FEES` de `univers_budget.py` portait donc MEXC a 2,0 bps a
tort ; elle est corrigee, et le classement d'univers qu'elle produisait
sous-estimait le cout MEXC.

Une seule chose a survecu a cette verification, et elle est reelle :
`makerCommission 0`. **Le frais maker nul existe.**

Cela permet un test que rien d'autre ne permettait — non pas « MEXC est-il
meilleur » (je ne transporte pas une microstructure d'une venue a l'autre),
mais une borne indifferente a la venue : **en annulant ENTIEREMENT le terme
de frais, le demi-spread encaisse depasse-t-il la selection adverse quelque
part ?** Zero est le plancher arithmetique du cout ; aucune venue ne peut
descendre plus bas. Si la reponse est non, le levier COUT est ferme
definitivement, pour toutes les venues a la fois.

`fee_floor.py`, sur la bande de 30 heures et 13 instruments, a d'abord
repondu oui : 7 instruments sur 13 positifs a l'horizon 300 s, dont
BCH-USDT-SWAP a +3,08 bps par remplissage avec t = 8,8.

Je ne l'ai pas cru, et j'ai eu raison. Ce t = 8,8 portait sur 1 348
remplissages ; mais la bande dure 30 heures, donc a l'horizon 300 s il
n'existe que 360 fenetres DISJOINTES, et tous les remplissages tombant dans
la meme fenetre voient la meme trajectoire de prix. Ce sont des copies d'une
observation, pas 1 348 observations.

`fee_floor_bh.py` refait la mesure par blocs disjoints — un bloc, une
observation — puis applique Benjamini-Hochberg sur les 17 tests menes :

> **0 / 17 survivent.**

Et le resultat decisif est ailleurs que dans le compte. L'unique instrument
dote d'une vraie capacite, BTC-USDT-SWAP, avec 55 743 remplissages repartis
sur 119 blocs, perd **0,86 bps par remplissage a FRAIS NULS**, avec
**t = -6,70**. La selection adverse mange le demi-spread entier, seule, sans
qu'aucun frais n'ait besoin d'intervenir.

Le quasi-survivant, DOT-USD-SWAP, merite d'etre nomme plutot qu'enterre en
silence : p = 0,0034 contre le seuil de 0,00294 exige au rang 1 par
Benjamini-Hochberg. Il echoue de peu. Je l'ai donc passe a la porte
economique malgre son echec statistique, avec toutes les generosites
possibles — file gagnee a chaque transaction, frais nuls, capture du flux
agressif ENTIER des deux cotes :

> **1,36 USD/jour, soit 13,6 bps/jour sur 1 000 EUR.**
> Le seuil du mandat est 272. Il manque un facteur 20 — sur un resultat
> qui, par ailleurs, ne survit pas.

Le cout n'etait donc pas le goulot. Pousse a son plancher arithmetique, il
ne suffit pas. Le goulot est la selection adverse, et elle ne se negocie
avec aucune venue.

## 16. FINAL BOT STATUS

| | |
|---|---|
| Mode | **DISCOVERY** |
| LIVE | non implemente |
| DEMO | non implemente |
| Classes d'execution reelle | **0** |
| Cles privees, secrets | **0** |
| Ordres reels emis | **0** |
| Tests | 1 024, tous verts |
| Gardes d'architecture | 27, tous verts |

PRISM V2 est un instrument de mesure economique, pas un bot de trading. Il
n'a pas atteint l'objectif de 272 bps/jour. Il a mesure, et non suppose,
pourquoi il ne l'atteint pas — et il refuse desormais structurellement de
laisser un resultat non demontre se presenter comme un PnL.

---

## Ce que je ne peux pas dire

Je ne peux pas dire que le marche est ferme. Je peux dire ceci, et pas plus
large : sur les instruments testes, avec les donnees publiques de ce compte,
sur les horizons testes, quatre formes de gain ont ete bornees par mesure et
le levier du cout a ete pousse a zero sans ouvrir aucune d'elles.

Les sources d'economie qui restent exigent ce que ce compte n'a pas —
latence, information privilegiee, ou une position du cote de la venue. Ce
n'est pas une limite de DONNEES : davantage d'historique de funding
validerait le 33,3 sans l'elever, et la concession de 0,26 bps est une
propriete structurelle de la densite des carnets, mesuree sur 29 038
rafales.

Le mandat interdit de fabriquer un resultat positif. Je n'en ai pas
fabrique.
