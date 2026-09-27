"""Provider-independent computer-use control-plane package.

Only modules that are part of the canonical Engineering Workspace runtime are
imported here. Optional browser/research adapters from historical source
material are intentionally not imported as package side effects.
"""

from .adapters import AIAdapter
from .capability_gateway import CapabilityGateway
from .chatgpt import ChatGPTAdapter, ChatGPTAdapterError, UrllibBridgeTransport
from .completion import ChatGPTCompletionDetector
from .contracts import (
    ActionKind,
    ActionProposal,
    ActionRisk,
    AIResponse,
    CompletionState,
    ControlEvent,
    ControlPhase,
    ContextPackage,
    Observation,
    Session,
)
from .ide_state import VSCodeStateReader
from .local_access import CapabilitySpec, LocalAccessBroker, LocalAccessError
from .obstacles import Obstacle, ObstacleRegistry
from .preapproval import AcquisitionEngine, AcquisitionError
from .workspace_search import WorkspaceSearch

__all__ = [
    "AIAdapter",
    "CapabilityGateway",
    "ChatGPTAdapter",
    "ChatGPTAdapterError",
    "UrllibBridgeTransport",
    "ChatGPTCompletionDetector",
    "ActionKind",
    "ActionProposal",
    "ActionRisk",
    "AIResponse",
    "CompletionState",
    "ControlEvent",
    "ControlPhase",
    "ContextPackage",
    "Observation",
    "Session",
    "VSCodeStateReader",
    "CapabilitySpec",
    "LocalAccessBroker",
    "LocalAccessError",
    "Obstacle",
    "ObstacleRegistry",
    "AcquisitionEngine",
    "AcquisitionError",
    "WorkspaceSearch",
]
