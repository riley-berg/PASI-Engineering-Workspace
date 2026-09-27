from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from pasi.core.approvals import ApprovalError, SQLiteApprovalStore
from pasi.core.computer_capabilities import CapabilityError, CapabilityDescriptor, SQLiteCapabilityRegistry
from pasi.core.event_store import SQLiteEventStore
from pasi.core.events import DurableEvent
from pasi.core.recovery import DeterministicRecoveryClassifier, RecoveryInput, RecoveryClassification


class ComputerControlError(ValueError):
    """Raised when a typed computer action violates policy."""


@dataclass(frozen=True)
class ComputerActionResult:
    operation_id: str
    capability_id: str
    target: str
    result: dict[str, object]
    approval_id: str = ""


class ComputerControlService:
    """Authorize and audit typed computer actions before invoking adapters."""

    def __init__(
        self,
        *,
        registry: SQLiteCapabilityRegistry,
        approvals: SQLiteApprovalStore,
        events: SQLiteEventStore,
    ) -> None:
        self.registry = registry
        self.approvals = approvals
        self.events = events

    def execute(
        self,
        *,
        operation_id: str,
        capability_id: str,
        target: str,
        action: Callable[[], dict[str, object]],
        approval_id: str = "",
        approval_token: str = "",
    ) -> ComputerActionResult:
        descriptor = self.registry.get(capability_id)
        if descriptor.side_effect != "read":
            if descriptor.authorization != "approval":
                raise ComputerControlError(
                    f"mutating capability {capability_id} lacks approval policy"
                )
            if not approval_id or not approval_token:
                raise ComputerControlError("mutating action requires approval")
            try:
                consumed = self.approvals.consume(
                    approval_id,
                    token=approval_token,
                    operation_id=operation_id,
                    capability_id=capability_id,
                    target=target,
                )
            except ApprovalError as exc:
                raise ComputerControlError(str(exc)) from exc
            approval_id = consumed.approval_id

        try:
            payload = action()
        except Exception as exc:
            self.events.append(
                DurableEvent(
                    event_id=f"computer-failed-{operation_id}-{capability_id}",
                    event_type="computer.action.failed",
                    source="computer_control",
                    operation_id=operation_id,
                    correlation_id=operation_id,
                    payload={
                        "capability_id": capability_id,
                        "target": target,
                        "error": str(exc)[:500],
                    },
                    evidence_refs=(f"computer://operation/{operation_id}",),
                )
            )
            raise

        self.events.append(
            DurableEvent(
                event_id=f"computer-completed-{operation_id}-{capability_id}",
                event_type="computer.action.completed",
                source="computer_control",
                operation_id=operation_id,
                correlation_id=operation_id,
                payload={
                    "capability_id": capability_id,
                    "target": target,
                    "result": payload,
                    "approval_id": approval_id,
                },
                evidence_refs=(
                    f"computer://operation/{operation_id}",
                    f"computer://capability/{capability_id}",
                ),
            )
        )
        return ComputerActionResult(
            operation_id=operation_id,
            capability_id=capability_id,
            target=target,
            result=payload,
            approval_id=approval_id,
        )


@dataclass(frozen=True)
class RecoveryAction:
    name: str
    action: Callable[[], bool]


@dataclass(frozen=True)
class RecoveryOutcome:
    operation_id: str
    classification: RecoveryClassification
    actions_attempted: tuple[str, ...]
    succeeded_action: str = ""
    exhausted: bool = False


class HostRecoveryController:
    """Finite recovery controller preserving the operation identity."""

    MAX_ACTIONS = 3

    def __init__(self, classifier: DeterministicRecoveryClassifier) -> None:
        self.classifier = classifier

    def recover(
        self,
        *,
        operation_id: str,
        failure_code: str,
        failure_family: str,
        retry_count: int,
        actions: Sequence[RecoveryAction],
    ) -> RecoveryOutcome:
        decision = self.classifier.classify(
            RecoveryInput(
                failure_code=failure_code,
                failure_family=failure_family,
                retry_count=retry_count,
                operation_identity_preserved=True,
            )
        )
        if decision.classification not in {
            RecoveryClassification.RETRYABLE,
            RecoveryClassification.RECOVERABLE,
        }:
            return RecoveryOutcome(
                operation_id=operation_id,
                classification=decision.classification,
                actions_attempted=(),
                exhausted=True,
            )

        bounded = tuple(actions[: self.MAX_ACTIONS])
        attempted: list[str] = []
        for candidate in bounded:
            attempted.append(candidate.name)
            if candidate.action():
                return RecoveryOutcome(
                    operation_id=operation_id,
                    classification=decision.classification,
                    actions_attempted=tuple(attempted),
                    succeeded_action=candidate.name,
                    exhausted=False,
                )

        return RecoveryOutcome(
            operation_id=operation_id,
            classification=decision.classification,
            actions_attempted=tuple(attempted),
            exhausted=True,
        )
