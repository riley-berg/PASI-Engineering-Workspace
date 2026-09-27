from pasi.core.recovery import (
    RecoveryClassification,
    RecoveryDecision,
)
from pasi.core.repair_loop import BoundedRepairController


def test_repair_loop_stops_at_budget_without_success():
    calls = []

    result = BoundedRepairController().execute(
        RecoveryDecision(
            RecoveryClassification.RETRYABLE,
            "timeout",
            "retry_same_operation",
            2,
        ),
        action=lambda action, attempt: calls.append((action, attempt)) or False,
    )

    assert len(calls) == 2
    assert result.exhausted is True
    assert result.terminal is True


def test_repair_loop_stops_immediately_after_success():
    result = BoundedRepairController().execute(
        RecoveryDecision(
            RecoveryClassification.RECOVERABLE,
            "connection",
            "reconnect_same_operation",
            3,
        ),
        action=lambda action, attempt: attempt == 2,
    )

    assert [item.attempt for item in result.attempts] == [1, 2]
    assert result.attempts[-1].succeeded is True
    assert result.exhausted is False
