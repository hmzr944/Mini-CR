"""Couche de RECHERCHE — au-dessus du noyau de capture, jamais a sa place.

    OBSERVATION -> PHENOMENE -> RELATION -> HYPOTHESE
                -> FALSIFICATION -> CAPTURE ECONOMIQUE

Distinction fondatrice, jamais relachee :

    DECOUVRIR  = observer le marche et chercher si une observation
                 correspond a une relation exploitable.
    GENERER UN SIGNAL = decider d'acheter ou de vendre.

Ce paquet fait le premier. Il ne fait JAMAIS le second.

Aucun agent de ce paquet ne peut contourner economics, risk, execution ou la
barriere LIVE : ils produisent des objets de recherche, et c'est le noyau
(hors de ce paquet) qui decide s'il y a capture. Verifie par test.
"""
from .observation import (
    FEATURE_SPACE, Observation, ObservationLog, observe_state,
)
from .hypothesis import (
    EconomicMechanism, Hypothesis, HypothesisRegistry, HypothesisStatus,
    MultipleTestingAccount, Phenomenon, Relation,
)
from .falsification import FalsificationAgent, FalsificationVerdict, RejectionReason
from .agents import (
    CaptureResearchAgent, MarketObserverAgent, MechanismAgent,
    PhenomenonAgent, RelationAgent,
)
from .orchestrator import ResearchBudget, ResearchOrchestrator
from .discovery_ledger import DiscoveryLedger, DiscoveryRecord
