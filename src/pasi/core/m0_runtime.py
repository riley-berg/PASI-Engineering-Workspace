from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


MAX_OPERATION_ID_CHARS = 256
MAX_PHASE_CHARS = 128
MAX_CHECKPOINT_CHARS = 512
MAX_CHAT_CREATION_REASON_CHARS = 64


class M0RuntimeError(ValueError):
    """Raised when M0 runtime evidence or recovery state is unsafe."""


def _text(value: object, *, name: str, limit: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise M0RuntimeError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise M0RuntimeError(f"{name} must not be empty")
    if len(value) > limit:
        raise M0RuntimeError(f"{name} exceeds {limit} characters")
    if any(ord(char) < 32 and char not in "\t" for char in value):
        raise M0RuntimeError(f"{name} contains control characters")
    return value


@dataclass(frozen=True)
class ConnectionRecoveryEvidence:
    connection_loss_detected: bool
    response_stopped_on_loss: bool
    checkpoint_preserved: bool
    resumed_after_reconnect: bool
    same_operation_resumed: bool
    operation_id: str
    resume_phase: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConnectionRecoveryEvidence":
        if not isinstance(value, Mapping):
            raise M0RuntimeError("connection_recovery must be an object")
        required = (
            "connection_loss_detected",
            "response_stopped_on_loss",
            "checkpoint_preserved",
            "resumed_after_reconnect",
            "same_operation_resumed",
            "operation_id",
            "resume_phase",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise M0RuntimeError(
                "connection_recovery missing fields: " + ", ".join(missing)
            )

        booleans = {
            key: value[key]
            for key in required
            if key not in {"operation_id", "resume_phase"}
        }
        for key, item in booleans.items():
            if item is not True:
                raise M0RuntimeError(f"M0 requires {key}=true")

        return cls(
            connection_loss_detected=True,
            response_stopped_on_loss=True,
            checkpoint_preserved=True,
            resumed_after_reconnect=True,
            same_operation_resumed=True,
            operation_id=_text(
                value["operation_id"],
                name="connection_recovery.operation_id",
                limit=MAX_OPERATION_ID_CHARS,
            ),
            resume_phase=_text(
                value["resume_phase"],
                name="connection_recovery.resume_phase",
                limit=MAX_PHASE_CHARS,
            ),
        )


@dataclass(frozen=True)
class M0RuntimeEvidence:
    fresh_chat_created_after_usage: bool
    fresh_chat_creation_reason: str
    thinking_enabled: bool
    connection_recovery: ConnectionRecoveryEvidence

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "M0RuntimeEvidence":
        if not isinstance(value, Mapping):
            raise M0RuntimeError("runtime_evidence must be an object")
        for key in (
            "fresh_chat_created_after_usage",
            "fresh_chat_creation_reason",
            "thinking_enabled",
            "connection_recovery",
        ):
            if key not in value:
                raise M0RuntimeError(f"runtime_evidence missing field: {key}")

        fresh_chat_created = value["fresh_chat_created_after_usage"] is True
        reason = _text(
            value["fresh_chat_creation_reason"],
            name="runtime_evidence.fresh_chat_creation_reason",
            limit=MAX_CHAT_CREATION_REASON_CHARS,
            required=False,
        )
        if fresh_chat_created and reason != "usage_limit":
            raise M0RuntimeError(
                "M0 forbids fresh-chat creation except as usage-limit recovery"
            )
        if not fresh_chat_created and reason:
            raise M0RuntimeError(
                "fresh_chat_creation_reason must be empty when no fresh chat was created"
            )
        if value["thinking_enabled"] is not True:
            raise M0RuntimeError("M0 requires Thinking to be enabled")

        return cls(
            fresh_chat_created_after_usage=fresh_chat_created,
            fresh_chat_creation_reason=reason,
            thinking_enabled=True,
            connection_recovery=ConnectionRecoveryEvidence.from_mapping(
                value["connection_recovery"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fresh_chat_created_after_usage": self.fresh_chat_created_after_usage,
            "fresh_chat_creation_reason": self.fresh_chat_creation_reason,
            "thinking_enabled": self.thinking_enabled,
            "connection_recovery": {
                "connection_loss_detected": self.connection_recovery.connection_loss_detected,
                "response_stopped_on_loss": self.connection_recovery.response_stopped_on_loss,
                "checkpoint_preserved": self.connection_recovery.checkpoint_preserved,
                "resumed_after_reconnect": self.connection_recovery.resumed_after_reconnect,
                "same_operation_resumed": self.connection_recovery.same_operation_resumed,
                "operation_id": self.connection_recovery.operation_id,
                "resume_phase": self.connection_recovery.resume_phase,
            },
        }


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    operation_id: str
    resume_phase: str


class ConnectionRecoveryController:
    """Bounded, exactly-once connection-loss stop/resume state machine."""

    def __init__(self, *, operation_id: str) -> None:
        self.operation_id = _text(
            operation_id,
            name="operation_id",
            limit=MAX_OPERATION_ID_CHARS,
        )
        self.connected = True
        self.generating = False
        self.interrupted = False
        self.resumed = False
        self.recovery_count = 0
        self.resume_phase = ""
        self.checkpoint = ""

    def start_generation(self, *, phase: str) -> None:
        if self.interrupted:
            raise M0RuntimeError("cannot start generation while interrupted")
        if self.resumed:
            raise M0RuntimeError("operation has already resumed")
        self.resume_phase = _text(phase, name="phase", limit=MAX_PHASE_CHARS)
        self.generating = True

    def checkpoint_progress(self, *, phase: str, checkpoint: str) -> None:
        self.resume_phase = _text(phase, name="phase", limit=MAX_PHASE_CHARS)
        self.checkpoint = _text(
            checkpoint,
            name="checkpoint",
            limit=MAX_CHECKPOINT_CHARS,
        )

    def checkpoint_record(self) -> dict[str, Any]:
        if not self.checkpoint:
            raise M0RuntimeError("cannot persist an empty checkpoint")
        return {
            "operation_id": self.operation_id,
            "resume_phase": self.resume_phase,
            "checkpoint": self.checkpoint,
            "connected": self.connected,
            "generating": self.generating,
            "interrupted": self.interrupted,
            "resumed": self.resumed,
            "recovery_count": self.recovery_count,
        }

    @classmethod
    def from_checkpoint(cls, value: Mapping[str, Any]) -> "ConnectionRecoveryController":
        if not isinstance(value, Mapping):
            raise M0RuntimeError("recovery checkpoint must be an object")
        controller = cls(operation_id=value.get("operation_id", ""))
        controller.resume_phase = _text(value.get("resume_phase", ""), name="resume_phase", limit=MAX_PHASE_CHARS)
        controller.checkpoint = _text(value.get("checkpoint", ""), name="checkpoint", limit=MAX_CHECKPOINT_CHARS)
        controller.connected = value.get("connected") is True
        controller.generating = value.get("generating") is True
        controller.interrupted = value.get("interrupted") is True
        controller.resumed = value.get("resumed") is True
        recovery_count = value.get("recovery_count", 0)
        if not isinstance(recovery_count, int) or recovery_count < 0:
            raise M0RuntimeError("recovery_count must be a non-negative integer")
        controller.recovery_count = recovery_count
        return controller

    def connection_lost(self) -> RecoveryDecision:
        if not self.generating:
            raise M0RuntimeError("connection loss requires an active generation")
        self.connected = False
        self.generating = False
        self.interrupted = True
        return RecoveryDecision(
            action="stop_generation",
            operation_id=self.operation_id,
            resume_phase=self.resume_phase,
        )

    def connection_restored(self) -> RecoveryDecision:
        if self.connected:
            raise M0RuntimeError("connection is already active")
        if not self.interrupted:
            raise M0RuntimeError("no interrupted operation is available to resume")
        if self.resumed:
            raise M0RuntimeError("operation has already resumed")

        self.connected = True
        self.interrupted = False
        self.resumed = True
        self.generating = True
        self.recovery_count += 1

        return RecoveryDecision(
            action="resume_generation",
            operation_id=self.operation_id,
            resume_phase=self.resume_phase,
        )

    def finish(self) -> None:
        if not self.connected:
            raise M0RuntimeError("cannot finish while disconnected")
        if self.generating:
            self.generating = False

    def evidence(self) -> ConnectionRecoveryEvidence:
        if self.recovery_count != 1 or not self.checkpoint:
            raise M0RuntimeError(
                "M0 recovery evidence requires exactly one resumed operation with a checkpoint"
            )
        return ConnectionRecoveryEvidence(
            connection_loss_detected=True,
            response_stopped_on_loss=True,
            checkpoint_preserved=True,
            resumed_after_reconnect=True,
            same_operation_resumed=True,
            operation_id=self.operation_id,
            resume_phase=self.resume_phase,
        )


__all__ = [
    "ConnectionRecoveryController",
    "ConnectionRecoveryEvidence",
    "M0RuntimeError",
    "M0RuntimeEvidence",
    "RecoveryDecision",
]
