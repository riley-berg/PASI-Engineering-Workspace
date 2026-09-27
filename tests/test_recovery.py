import pytest

from pasi.core.recovery import (
    DeterministicRecoveryClassifier,
    RecoveryClassification,
    RecoveryInput,
)


def test_recovery_classifier_is_deterministic_and_bounded():
    classifier = DeterministicRecoveryClassifier()

    transient = classifier.classify(
        RecoveryInput(
            failure_code="timeout",
            failure_family="transient_provider",
            retry_count=0,
        )
    )
    assert transient.classification is RecoveryClassification.RETRYABLE
    assert transient.max_attempts == 2
    assert transient.action == "retry_same_operation"

    exhausted = classifier.classify(
        RecoveryInput(
            failure_code="timeout",
            failure_family="transient_provider",
            retry_count=2,
        )
    )
    assert exhausted.classification is RecoveryClassification.TERMINAL
    assert exhausted.max_attempts == 0


@pytest.mark.parametrize(
    ("code", "family", "classification", "action"),
    [
        ("connection_lost", "connection", RecoveryClassification.RECOVERABLE, "reconnect_same_operation"),
        ("usage_limit", "context", RecoveryClassification.RECOVERABLE, "fresh_chat_and_rebind"),
        ("invalid_request", "request", RecoveryClassification.BLOCKED, "repair_input_before_retry"),
        ("permission_denied", "permission", RecoveryClassification.HUMAN_REQUIRED, "pause_for_authorization"),
        ("corrupt", "integrity", RecoveryClassification.TERMINAL, "stop_and_preserve_evidence"),
    ],
)
def test_recovery_classifier_covers_contract(code, family, classification, action):
    decision = DeterministicRecoveryClassifier().classify(
        RecoveryInput(failure_code=code, failure_family=family)
    )
    assert decision.classification is classification
    assert decision.action == action


def test_unknown_failure_requires_human_review():
    decision = DeterministicRecoveryClassifier().classify(
        RecoveryInput(failure_code="mystery", failure_family="unknown")
    )
    assert decision.classification is RecoveryClassification.HUMAN_REQUIRED
    assert decision.requires_human is True
