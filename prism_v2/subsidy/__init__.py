"""SUBVENTION — se faire payer pour coter, plutot que parier sur un prix.

POURQUOI CE MODULE EXISTE, ET POURQUOI IL NE RESSEMBLE A RIEN D'AUTRE ICI.

Tout le reste de PRISM cherche un EDGE : une information qui predit un
mouvement, capturee en traversant un carnet. Vingt et une familles ont ete
mesurees ainsi et l'arithmetique est close — `scans/conditions.py` l'ecrit :

    net = n * L * (e - c)

avec un cout `c` de 10,8 a 12,5 bps par aller-retour et la plus grande
magnitude brute jamais mesuree a 6,5 bps. Le terme (e - c) est NEGATIF sur
toutes les mesures du depot. Ce n'est pas un facteur a combler : c'est un
signe, et ni la rotation ni le levier ne retournent un signe.

CE MODULE NE CHERCHE PAS D'EDGE. Il n'y a rien a predire. Polymarket PUBLIE,
par marche, un montant quotidien en USDC verse aux ordres qui dorment dans
une bande autour du mid. C'est un contrat, pas une anticipation. Le revenu ne
depend d'aucune opinion sur l'issue.

TROIS FAITS STRUCTURELS QUI CHANGENT LE SIGNE, ET NON L'AMPLITUDE :

1. LE FRAIS MAKER EST NUL — ET LE FRAIS TAKER NE L'EST PAS. Sur OKX, 10 des
   10,8 bps de cout etaient des frais : 93 %. Ici la documentation est
   explicite, « makers are never charged fees », et la subvention s'encaisse
   donc sans peage. Mais NEUTRALISER un inventaire est une TRAVERSEE, et le
   taker paie 4 a 7 % selon la categorie — sauf sur les marches
   geopolitiques, ou le taux vaut zero. Le frais n'a donc pas disparu : il
   s'est deplace du revenu vers la PROTECTION, et il decide du choix des
   marches. `economics.TAKER_FEE_RATES` porte les taux publies.

2. LA PETITESSE DEVIENT UN AVANTAGE. La recompense se partage
   `Q_moi / somme(Q)`. Sur les carnets mesures, la liquidite qualifiante vaut
   200 a 3 000 $. Un compte de 1 000 EUR y pese 25 a 70 %. Partout ailleurs
   dans ce depot, la petitesse etait un handicap ; ici elle est la these.
   Un operateur a 1 M$ ne peut pas s'interesser a un pool de 30 $/jour : son
   cout operationnel est PAR MARCHE, pas par dollar.

3. L'INVENTAIRE SE NEUTRALISE EXACTEMENT. Sur un perpetuel, une position
   subie doit etre soldee au marche et le markout mesure sa derive. Sur un
   binaire, YES + NO = 1,00 $ PAR CONSTRUCTION : detenir autant de YES que de
   NO est sans risque, quelle que soit l'issue. Un remplissage subi se
   neutralise donc au prix du complement — un cout OBSERVABLE dans le carnet,
   et non une derive a estimer. C'est ce qui rend l'economie calculable sans
   engager un centime.

CE QUE CE MODULE NE PRETEND PAS. Il n'a fait gagner aucun euro. Aucun ordre
reel n'existe. Le statut de la famille au registre est EDGE BRUT OBSERVE, et
cinq inconnues peuvent en retourner le signe — elles sont nommees une par une
dans `economics.UNKNOWNS`. Tant qu'elles ne sont pas mesurees, toute sortie
de ce module est une BORNE SUPERIEURE et se declare comme telle.

Endpoints publics uniquement. Aucune cle. Aucun ordre.
"""
from prism_v2.subsidy.scoring import (MIDPOINT_BAND, SIDE_SCALING,
                                      QualifyingOrder, epoch_share,
                                      order_score, two_sided_score)

__all__ = ["MIDPOINT_BAND", "SIDE_SCALING", "QualifyingOrder", "epoch_share",
           "order_score", "two_sided_score"]
