from __future__ import annotations

from scripts.run_m0_acceptance import build_runtime_evidence, fresh_chat_required


def test_fresh_chat_is_not_created_for_a_usable_chat() -> None:
    assert fresh_chat_required(
        {
            "provider_usage_limited": False,
            "thinking_enabled": False,
        }
    ) is False


def test_fresh_chat_is_created_only_for_usage_limit() -> None:
    assert fresh_chat_required(
        {
            "provider_usage_limited": True,
            "thinking_enabled": False,
        }
    ) is True


def test_runtime_evidence_preserves_no_fresh_chat_and_thinking_state() -> None:
    evidence = build_runtime_evidence(
        health_data={"thinking_enabled": True},
        operation={
            "operation_id": "op-1",
            "recovery_events": [],
        },
        fresh_chat_created=False,
    )

    assert evidence["fresh_chat_created_after_usage"] is False
    assert evidence["fresh_chat_creation_reason"] == ""
    assert evidence["thinking_enabled"] is True
    assert evidence["connection_recovery"] is None


def test_runtime_evidence_detects_connection_recovery_on_same_operation() -> None:
    evidence = build_runtime_evidence(
        health_data={"thinking_enabled": True},
        operation={
            "operation_id": "op-1",
            "recovery_events": [
                {
                    "phase": "connection_lost",
                    "recovery_reason": "connection_error",
                    "operation_id": "op-1",
                },
                {
                    "phase": "ready_for_retry",
                    "operation_id": "op-1",
                },
            ],
        },
        fresh_chat_created=False,
    )

    recovery = evidence["connection_recovery"]
    assert recovery["connection_loss_detected"] is True
    assert recovery["response_stopped_on_loss"] is True
    assert recovery["checkpoint_preserved"] is True
    assert recovery["resumed_after_reconnect"] is True
    assert recovery["same_operation_resumed"] is True
    assert recovery["operation_id"] == "op-1"


def test_runner_keeps_recovery_probe_opt_in_for_targeted_testing() -> None:
    import inspect
    from scripts import run_m0_acceptance

    source = inspect.getsource(run_m0_acceptance.main)
    assert "--controlled-recovery-probe" in source
    assert "m0_recovery_probe=controlled_recovery_probe" in source
