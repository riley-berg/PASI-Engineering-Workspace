from __future__ import annotations

import pytest

from pasi.core.m0_runtime import (
    ConnectionRecoveryController,
    M0RuntimeError,
    M0RuntimeEvidence,
)


def runtime_mapping() -> dict:
    return {
        "fresh_chat_created_after_usage": False,
        "fresh_chat_creation_reason": "",
        "thinking_enabled": True,
        "connection_recovery": {
            "connection_loss_detected": True,
            "response_stopped_on_loss": True,
            "checkpoint_preserved": True,
            "resumed_after_reconnect": True,
            "same_operation_resumed": True,
            "operation_id": "op-1",
            "resume_phase": "contract_parsing",
        },
    }


def test_m0_runtime_evidence_requires_all_live_runtime_guards() -> None:
    evidence = M0RuntimeEvidence.from_mapping(runtime_mapping())
    assert evidence.fresh_chat_created_after_usage is False
    assert evidence.fresh_chat_creation_reason == ""
    assert evidence.thinking_enabled is True
    assert evidence.connection_recovery.operation_id == "op-1"


@pytest.mark.parametrize(
    "field, value",
    [
        ("fresh_chat_creation_reason", "usage_limit"),
        ("thinking_enabled", False),
        ("connection_recovery", {}),
    ],
)
def test_m0_runtime_evidence_rejects_missing_live_guards(field: str, value: object) -> None:
    payload = runtime_mapping()
    payload[field] = value
    with pytest.raises(M0RuntimeError):
        M0RuntimeEvidence.from_mapping(payload)


def test_recovery_checkpoint_can_be_restored_before_resume() -> None:
    controller = ConnectionRecoveryController(operation_id="op-2")
    controller.start_generation(phase="response_generation")
    controller.checkpoint_progress(phase="patch_application", checkpoint="proof artifact verified")
    controller.connection_lost()

    restored = ConnectionRecoveryController.from_checkpoint(controller.checkpoint_record())
    decision = restored.connection_restored()

    assert decision.operation_id == "op-2"
    assert decision.resume_phase == "patch_application"
    assert restored.checkpoint_record()["checkpoint"] == "proof artifact verified"


def test_connection_loss_stops_generation_and_resumes_same_operation_once() -> None:
    controller = ConnectionRecoveryController(operation_id="op-1")
    controller.start_generation(phase="contract_parsing")
    controller.checkpoint_progress(phase="patch_application", checkpoint="proof-file patch staged")

    stopped = controller.connection_lost()
    assert stopped.action == "stop_generation"
    assert stopped.operation_id == "op-1"
    assert stopped.resume_phase == "patch_application"

    resumed = controller.connection_restored()
    assert resumed.action == "resume_generation"
    assert resumed.operation_id == "op-1"
    assert resumed.resume_phase == "patch_application"

    controller.finish()
    evidence = controller.evidence()
    assert evidence.same_operation_resumed is True

    with pytest.raises(M0RuntimeError):
        controller.connection_restored()


def test_connection_loss_cannot_resume_without_checkpoint() -> None:
    controller = ConnectionRecoveryController(operation_id="op-1")
    controller.start_generation(phase="response_generation")
    controller.connection_lost()
    controller.connection_restored()

    with pytest.raises(M0RuntimeError):
        controller.evidence()


def test_fresh_chat_is_valid_only_when_usage_limit_is_the_reason() -> None:
    payload = runtime_mapping()
    payload["fresh_chat_created_after_usage"] = True
    payload["fresh_chat_creation_reason"] = "usage_limit"

    evidence = M0RuntimeEvidence.from_mapping(payload)

    assert evidence.fresh_chat_created_after_usage is True
    assert evidence.fresh_chat_creation_reason == "usage_limit"


def test_fresh_chat_is_rejected_for_non_usage_reasons() -> None:
    payload = runtime_mapping()
    payload["fresh_chat_created_after_usage"] = True
    payload["fresh_chat_creation_reason"] = "task_completed"

    with pytest.raises(M0RuntimeError):
        M0RuntimeEvidence.from_mapping(payload)
