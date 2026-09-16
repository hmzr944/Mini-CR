"""Detecteurs par famille d'inefficience. Isoles du noyau economique.

Aucun module du noyau n'importe ce paquet. Ajouter une famille ne modifie
aucun fichier de `prism_v2/` hors de ce dossier — verifie par test.

CONTRAT COMMUN A TOUS LES DETECTEURS
    1. Le `gross_capture_bps` d'une candidate est une grandeur ECONOMIQUE
       OBSERVABLE A L'INSTANT DE LA DETECTION. Jamais une prevision, jamais
       une amplitude calculee apres coup.
    2. Aucun indicateur technique.
    3. Le seuil d'emission est ECONOMIQUE : une anomalie plus petite que le
       spread observe ne peut pas etre capturee, donc elle n'est pas emise.
       Ce n'est pas un parametre regle sur des resultats.
    4. Le detecteur ne dit jamais qu'une candidate est rentable.
"""
from .cross_market import CrossMarketDetector
from .cross_venue import CrossVenueDetector
from .forced_flow import ForcedFlowDetector
from .funding_basis import FundingBasisDetector
from .microstructure import (
    AggressiveFlowDetector, BookImbalanceDetector, DepthWithdrawalDetector,
    ShortHorizonReversionDetector, SpreadDislocationDetector,
)

ALL_DETECTORS = (
    CrossMarketDetector, CrossVenueDetector, FundingBasisDetector,
    ForcedFlowDetector, BookImbalanceDetector, DepthWithdrawalDetector,
    AggressiveFlowDetector, ShortHorizonReversionDetector,
    SpreadDislocationDetector,
)
