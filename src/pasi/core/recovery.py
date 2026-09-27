from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecoveryClassification(StrEnum):
    RETRYABLE = "retryable"
    RECOVERABLE = "recoverable"
    BLOCKED = "blocked"
    TERMINAL = "terminal"
    HUMAN_REQUIRED = "human_required"


@dataclass(frozen=True)
class RecoveryInput:
    failure_code: str
    failure_family: str
    retry_count: int = 0
    checkpoint_preserved: bool = True
    operation_identity_preserved: bool = True


@dataclass(frozen=True)
class RecoveryDecision:
    classification: RecoveryClassification
    reason_code: str
    action: str
    max_attempts: int
    requires_human: bool = False
    preserve_operation_identity: bool = True


class DeterministicRecoveryClassifier:
    """Classify recovery paths from explicit runtime facts, never model output."""

    MAX_PROVIDER_RETRIES = 2
    MAX_RECOVERY_RETRIES = 1

    def classify(self, request: RecoveryInput) -> RecoveryDecision:
        if request.retry_count < 0:
            raise ValueError("retry_count must be non-negative")

        code = request.failure_code.strip().lower()
        family = request.failure_family.strip().lower()

        if not request.operation_identity_preserved:
            return RecoveryDecision(
                RecoveryClassification.TERMINAL,
                "operation_identity_lost",
                "stop_and_preserve_evidence",
                0,
                preserve_operation_identity=False,
            )

        if family in {"corruption", "integrity"}:
            return RecoveryDecision(
                RecoveryClassification.TERMINAL,
                "integrity_failure",
                "stop_and_preserve_evidence",
                0,
            )

        if family in {"auth", "authorization", "permission"}:
            return RecoveryDecision(
                RecoveryClassification.HUMAN_REQUIRED,
                "authorization_required",
                "pause_for_authorization",
                0,
                requires_human=True,
            )

        if code in {"invalid_request", "schema_invalid", "unsafe_command"}:
            return RecoveryDecision(
                RecoveryClassification.BLOCKED,
                "input_contract_invalid",
                "repair_input_before_retry",
                0,
            )

        if code in {"usage_limit", "context_exhausted"} or family == "context":
            return RecoveryDecision(
                RecoveryClassification.RECOVERABLE,
                "provider_context_limit",
                "fresh_chat_and_rebind",
                self.MAX_RECOVERY_RETRIES,
            )

        if code in {"connection_lost", "controller_lost"} or family == "connection":
            return RecoveryDecision(
                RecoveryClassification.RECOVERABLE,
                "controller_connection_lost",
                "reconnect_same_operation",
                self.MAX_RECOVERY_RETRIES,
            )

        if family in {"transient_provider", "timeout", "overloaded"}:
            if request.retry_count < self.MAX_PROVIDER_RETRIES:
                return RecoveryDecision(
                    RecoveryClassification.RETRYABLE,
                    "transient_provider_failure",
                    "retry_same_operation",
                    self.MAX_PROVIDER_RETRIES,
                )
            return RecoveryDecision(
                RecoveryClassification.TERMINAL,
                "provider_retry_budget_exhausted",
                "stop_and_preserve_evidence",
                self.MAX_PROVIDER_RETRIES,
            )

        return RecoveryDecision(
            RecoveryClassification.HUMAN_REQUIRED,
            "unknown_failure",
            "pause_for_review",
            0,
            requires_human=True,
        )


def validate_recovery_decision(decision: RecoveryDecision) -> None:
    if decision.max_attempts < 0:
        raise ValueError("max_attempts must be non-negative")
    if decision.classification is RecoveryClassification.RETRYABLE:
        if decision.max_attempts <= 0:
            raise ValueError("retryable recovery requires a positive retry budget")
    if decision.classification in {
        RecoveryClassification.BLOCKED,
        RecoveryClassification.TERMINAL,
        RecoveryClassification.HUMAN_REQUIRED,
    } and decision.max_attempts != 0:
        raise ValueError("non-retryable recovery cannot have a retry budget")
