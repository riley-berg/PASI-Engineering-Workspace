from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pasi.core.recovery import (
    RecoveryClassification,
    RecoveryDecision,
)


class RepairLoopExhausted(RuntimeError):
    """Raised when a bounded repair budget has been consumed."""


@dataclass(frozen=True)
class RepairAttempt:
    attempt: int
    action: str
    succeeded: bool


@dataclass(frozen=True)
class RepairResult:
    classification: RecoveryClassification
    attempts: tuple[RepairAttempt, ...]
    terminal: bool
    exhausted: bool


class BoundedRepairController:
    """Execute at most the recovery classifier's explicit repair budget."""

    def execute(
        self,
        decision: RecoveryDecision,
        *,
        action: Callable[[str, int], bool],
        max_attempts_override: int | None = None,
    ) -> RepairResult:
        budget = (
            decision.max_attempts
            if max_attempts_override is None
            else max_attempts_override
        )
        if budget < 0:
            raise ValueError("repair budget must be non-negative")
        if budget == 0:
            return RepairResult(
                decision.classification,
                attempts=(),
                terminal=True,
                exhausted=decision.classification is RecoveryClassification.RETRYABLE,
            )

        attempts: list[RepairAttempt] = []
        for attempt_number in range(1, budget + 1):
            succeeded = bool(action(decision.action, attempt_number))
            attempts.append(
                RepairAttempt(
                    attempt=attempt_number,
                    action=decision.action,
                    succeeded=succeeded,
                )
            )
            if succeeded:
                return RepairResult(
                    decision.classification,
                    attempts=tuple(attempts),
                    terminal=False,
                    exhausted=False,
                )

        return RepairResult(
            decision.classification,
            attempts=tuple(attempts),
            terminal=True,
            exhausted=True,
        )
