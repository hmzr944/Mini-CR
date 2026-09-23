# Protocole — rejeu de tenue de marché sous contrainte d'inventaire

**Écrit AVANT toute implémentation et toute lecture. Soumis à relecture.**

Aucune nouvelle collecte. Aucun passage en réel. Données existantes seulement :
`mmws.jsonl`, 54,6 min, 808 204 carnets, 3 048 échanges, 20 marchés, 0 trou.

---

## 0. Pourquoi ce chantier passe avant tout le reste

L'hypothèse A a produit un positif — ARB, équilibre +6,33 bps, IC 95 %
[6,33 ; 17,24], survivant au contrôle de dérive. Et elle a révélé que ce
positif suppose une position nette de **94 000 $, soit 43,5× le pouvoir
d'achat du compte**.

Le banc ne mesurait pas une tenue de marché. Il mesurait *« si j'avais pris
chaque échange du côté opposé, sans jamais gérer mon bilan »*. **Ce défaut
affecte rétroactivement toutes les mesures de cotation passive du dépôt.**

Tant qu'il n'est pas réparé, aucun résultat positif de cette famille ne peut
être interprété — ni ceux déjà obtenus, ni ceux à venir.

---

## 1. Les invariants — propriétés testables, pas des intentions

Chacun doit être vérifié par un test unitaire, et chacun correspond à une
manière précise de fabriquer un faux résultat.

| # | invariant | faute qu'il interdit |
|---|---|---|
| **I1** | À tout instant, `|notionnel net| ≤ CAPITAL × LEVIER`. Jamais d'exception. | Le +6,33 bps d'ARB, qui suppose 43,5× le compte |
| **I2** | Un fill qui violerait I1 n'a **pas lieu** : la cotation de ce côté était retirée avant. | Poster un ordre qu'on ne peut pas financer |
| **I3** | Tout fill paie son frais maker. Aucun fill gratuit. | Un PnL brut lu comme un net |
| **I4** | Le PnL final = réalisé + latent **marqué au mid de clôture**, jamais au prix d'entrée. | Cacher une perte dans l'inventaire non soldé |
| **I5** | La position en fin de fenêtre est **soldée au carnet** et son coût compté. | Terminer long et appeler ça un profit |
| **I6** | Aucun ordre ne peut être rempli par un échange **antérieur** à son placement. | Se faire remplir par le passé |
| **I7** | La file devant l'ordre vient du **toucher enregistré**, jamais d'une hypothèse. | Se donner la première place |
| **I8** | Un instant sans carnet frais (> 200 ms) rend `INCONNU`, jamais une valeur reconduite. | Un markout nul fabriqué par un trou |

**I1 et I2 sont le cœur.** Les six autres existent déjà dans le dépôt sous une
forme ou une autre ; ceux-là sont neufs et ce sont eux qui manquaient.

---

## 2. Politique de cotation — explicite, car elle n'existait pas

L'ancien banc n'avait **aucune** politique : il prenait tout. Il en faut une,
et la plus simple défendable :

```
a chaque instant t :
    inv        = position nette en notionnel USD sur ce marche
    LIMITE     = CAPITAL x LEVIER / N_MARCHES_ACTIFS

    coter au BID   si   inv + TAILLE_ORDRE <=  LIMITE
    coter a l'ASK  si   inv - TAILLE_ORDRE >= -LIMITE
```

**Règle binaire, pas de biais de prix.** Un vrai teneur décale ses cotations
progressivement ; modéliser ce décalage exigerait de supposer comment le
marché y répond, et cette hypothèse n'est pas mesurable ici. La règle binaire
est plus grossière et **plus honnête** : elle ne suppose rien.

Conséquence assumée : elle **sous-estime** les fills d'un teneur habile, et
**surestime** ceux d'un teneur qui ne gérerait rien. Elle encadre.

### Paramètres, gelés

| | valeur | origine |
|---|---|---|
| capital | **1 080 $** | 1 000 EUR |
| levier | **2×** | plafond ESMA retail, mesuré au dossier |
| pouvoir d'achat | **2 160 $** | produit des deux |
| taille d'ordre | **200 $** | 1/10 du pouvoir d'achat — une position se construit en dix fills, pas en un |
| marchés actifs | **10** | ceux exploitables à la scission (§4) |
| limite par marché | **216 $** | pouvoir d'achat ÷ marchés actifs |
| frais maker | **2 bps** | barème Tier 1, source primaire |
| frais taker (solde final) | **5 bps** | idem |

---

## 3. Modèle de file, et son biais déclaré

Un ordre posé au toucher se place **derrière** la taille qui y dort
(`bookTicker` champs `B`/`A`). Il est servi quand les échanges suivants ont
consommé cette file. C'est `subsidy.tape.simulate`, qui attend exactement ce
`queue_ahead`.

**Le biais, mesuré et non supposé** : le flux ne publie **aucune annulation**
— seulement le toucher résultant. Ma position dans la file ne peut donc
avancer que par des **échanges**, jamais par des **retraits**. Or les retraits
sont fréquents.

→ **Le modèle sous-estime les fills.** Direction connue, magnitude inconnue.
Un résultat négatif sous ce modèle est donc **moins** décisif qu'il n'en a
l'air, et un résultat positif l'est **davantage**. C'est l'inverse de la
situation habituelle, et il faut le dire à la lecture.

---

## 4. Données, et scission calibration / évaluation

Scission **temporelle à la moitié** de la fenêtre. Elle est possible :

| marchés exploitables (≥ 30 échanges dans **chaque** moitié) | **10** |
|---|---|
| SOL · ETH · HYPE · UNI · BTC · XRP · NEAR · ARB · ZEC · SEI | |

Huit autres marchés existent mais ne passent pas le seuil dans une moitié :
ils sont **rapportés comme exclus avec leur N**, jamais silencieusement omis.

**La première moitié est la calibration. La seconde est l'évaluation.** Aucun
paramètre n'est touché après lecture de la première.

Avertissement à inscrire dans le résultat : **27 minutes par moitié, un seul
régime.** La scission contrôle le surajustement, pas la robustesse temporelle.

---

## 5. Mesures conservées — les échecs visibles

| mesure | pourquoi |
|---|---|
| PnL net, réalisé et latent séparés | un latent positif n'est pas un gain |
| drawdown maximal | une moyenne cache une ruine |
| temps passé à la **limite** d'inventaire | mesure la fréquence où l'on cesse de coter |
| inventaire net médian et maximal | vérifie I1 empiriquement |
| capital immobilisé, utilisation maximale | ce que la stratégie bloque vraiment |
| taux de remplissage | fills obtenus ÷ échanges atteignant le prix |
| markout après fill | l'adverse selection subie |
| coût de solde final | I5 |
| **par marché**, avec exclusions et N | interdit de ne montrer que ce qui marche |

---

## 6. Règle de décision, préenregistrée

Évaluée **sur la seconde moitié uniquement**, dans cet ordre.

| verdict | condition |
|---|---|
| **INCONNU** | moins de 5 marchés avec ≥ 30 fills en évaluation |
| **FERMÉ** | PnL net d'évaluation ≤ 0 sur le portefeuille |
| **MITIGÉ** | PnL net > 0 mais **drawdown > 10 %** du capital, ou moins de 3 marchés positifs |
| **CANDIDAT** | PnL net > 0, drawdown ≤ 10 %, ≥ 3 marchés positifs, **et** le signe du PnL identique en calibration et en évaluation |

La dernière condition est la vraie barrière : un signe qui s'inverse entre les
deux moitiés n'est pas un edge, c'est un régime.

**Aucun seuil ne sera déplacé après lecture.** Ils sont ici, datés, avant
l'écriture d'une seule ligne de simulation.

---

## 7. Ce que ce rejeu ne pourra pas établir

- **Le contrefactuel.** Si je cotais réellement, le carnet serait différent :
  ma présence déplacerait la file, et certains échanges n'auraient pas eu
  lieu au même prix. Aucune donnée d'observation ne corrige cela.
- **La profondeur** au-delà du toucher, absente du flux.
- **Les annulations**, donc la vraie dynamique de file (§3).
- **La robustesse** : 27 min par moitié, un régime.
- **Le passage en réel** : rejets, re-soumissions, latence d'ordre, coupures.

---

## 8. Mon a priori, écrit avant de mesurer

Je m'attends à ce que le **+6,33 bps d'ARB s'effondre**. Sa source probable
est l'accumulation de 47 fills nets dans un marché qui montait ; la contrainte
I1 interdit précisément de porter cette position.

Si ARB reste positif **sous contrainte**, ce sera un fait nouveau et
intéressant. Si tout devient négatif, la famille sera fermée sur un banc qui
mesure enfin ce qu'elle prétend mesurer.

**Les deux issues sont utiles. Aucune n'est celle que j'espère.**

---

## 9. Ce que je demande en relecture

1. La **règle binaire** de cotation (§2) est-elle acceptable, ou faut-il un
   décalage de prix malgré l'hypothèse qu'il introduit ?
2. La limite par marché (pouvoir d'achat ÷ 10) est-elle la bonne répartition,
   ou faut-il une limite **globale** partagée, qui laisserait un marché
   consommer tout le bilan ?
3. Le seuil de drawdown à **10 %** est-il le bon ? Il est choisi, pas mesuré.
4. Faut-il un **témoin** : la même simulation sur des instants aléatoires,
   pour vérifier que le PnL ne vient pas de la dérive du marché ?

Rien ne sera implémenté avant réponse.
