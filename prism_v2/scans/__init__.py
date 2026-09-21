"""Scans de mesure. Chacun recalcule un plafond du registre.

LE REPERTOIRE DE TRAVAIL. Les scans forment une CHAINE : `ws_full` ecrit
`tape.pkl`, `panel` ecrit `panel.pkl`, et `policy_fit`, `fee_floor`,
`breakeven`, `xsec` les relisent. Ces artefacts intermediaires vivaient sous
`/tmp/prism_scans`, un repertoire que 24 des 42 scans ne creaient pas. Deux
consequences mesurees :

  1. un scan lance sur une machine neuve mourait d'un `FileNotFoundError` a la
     DERNIERE ligne, apres avoir fait tout son calcul — la mesure etait
     perdue ;
  2. `/tmp` disparait avec le conteneur, et rien de la chaine n'est versionne.
     Les plafonds du registre citent des scripts dont les entrees n'existent
     plus nulle part : ils ne sont donc pas re-verifiables ici, et le registre
     doit le dire au lieu de laisser croire le contraire.

`scan_dir()` corrige le point 1 — creation garantie, chemin unique. Le point 2
est une limite de reproductibilite, documentee dans le registre par la classe
de preuve `BORNE_NON_QUALIFIEE`, pas un defaut de code.
"""
from __future__ import annotations

import os

#: Emplacement par defaut des artefacts intermediaires. Surchargeable par
#: PRISM_SCAN_DIR pour pointer vers un disque persistant.
DEFAULT_SCAN_DIR = "/tmp/prism_scans"


def scan_dir() -> str:
    """Le repertoire de travail des scans, CREE s'il n'existe pas.

    Un scan qui calcule six heures puis echoue a `open(..., "w")` parce qu'un
    repertoire manque n'a pas produit de mesure. La creation appartient donc a
    la resolution du chemin, pas a l'appelant.
    """
    d = os.environ.get("PRISM_SCAN_DIR", DEFAULT_SCAN_DIR)
    os.makedirs(d, exist_ok=True)
    return d
