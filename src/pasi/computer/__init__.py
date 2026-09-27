from .registry import CapabilityRegistryError, CapabilitySpec, SQLiteCapabilityRegistry
from .terminal import TerminalCommand, TerminalExecutionError, TerminalResult, TypedTerminalCapability
from .vscode import VSCodeSemanticAdapter, VSCodeActionError
from .browser import BrowserSemanticAdapter, BrowserActionError, SemanticTarget
from .adapters import ApplicationLauncher, FileAdapterError, GitAdapter, WorkspaceFileAdapter
from .approval import ApprovalError, ApprovalRecord, SQLiteApprovalStore
from .recovery import ComputerRecoveryController, RecoveryActionError, RecoveryRecord
from .resources import HostResourceObserver, ResourceObservationError, ResourceSnapshot

__all__ = [
    "CapabilityRegistryError",
    "CapabilitySpec",
    "SQLiteCapabilityRegistry",
    "TerminalCommand",
    "TerminalExecutionError",
    "TerminalResult",
    "TypedTerminalCapability",
    "VSCodeSemanticAdapter",
    "VSCodeActionError",
    "BrowserSemanticAdapter",
    "BrowserActionError",
    "SemanticTarget",
    "ApplicationLauncher",
    "FileAdapterError",
    "GitAdapter",
    "WorkspaceFileAdapter",
    "ApprovalError",
    "ApprovalRecord",
    "SQLiteApprovalStore",
    "ComputerRecoveryController",
    "RecoveryActionError",
    "RecoveryRecord",
    "HostResourceObserver",
    "ResourceObservationError",
    "ResourceSnapshot",
]
