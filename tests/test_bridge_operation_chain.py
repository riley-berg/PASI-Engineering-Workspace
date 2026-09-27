import pytest

from pasi_bridge.bridge import BridgeState
from pasi_bridge.state import StateManager


def make_state(tmp_path):
    return BridgeState(StateManager(tmp_path))


def test_chain_sequence_requires_completed_immediate_predecessor(tmp_path):
    state = make_state(tmp_path)

    first = state.queue_operation(
        "prompt",
        "first",
        idempotency_key="chain-01",
        chain_id="chain-1",
        sequence_index=1,
    )

    with pytest.raises(ValueError, match="predecessor operation must be completed"):
        state.queue_operation(
            "prompt",
            "second",
            idempotency_key="chain-02",
            chain_id="chain-1",
            sequence_index=2,
            predecessor_operation_id=first.operation_id,
        )

    claimed = state.claim_operation(first.operation_id)
    assert claimed is not None
    assert claimed["status"] == "claimed"

    completed, chained = state.complete_operation_and_claim_next(
        first.operation_id,
        response_text="first",
        response_text_available=True,
    )
    assert completed is not None
    assert completed["status"] == "completed"
    assert chained is None

    second = state.queue_operation(
        "prompt",
        "second",
        idempotency_key="chain-02",
        chain_id="chain-1",
        sequence_index=2,
        predecessor_operation_id=first.operation_id,
    )
    assert second.chain_id == "chain-1"
    assert second.sequence_index == 2
    assert second.predecessor_operation_id == first.operation_id
    assert second.predecessor_completed_at_ms is None

    claimed_second = state.claim_operation(second.operation_id)
    assert claimed_second is not None
    assert claimed_second["status"] == "claimed"


def test_idempotency_key_remains_bound_after_terminal_completion(tmp_path):
    state = make_state(tmp_path)

    first = state.queue_operation(
        "prompt",
        "same prompt",
        idempotency_key="stable-key",
    )
    state.claim_operation(first.operation_id)
    completed = state.complete_operation(
        first.operation_id,
        response_text="done",
        response_text_available=True,
    )
    assert completed is not None
    assert completed["status"] == "completed"

    replay = state.queue_operation(
        "prompt",
        "same prompt",
        idempotency_key="stable-key",
    )
    assert replay.operation_id == first.operation_id
    assert replay.status == "completed"

    with pytest.raises(ValueError, match="already bound"):
        state.queue_operation(
            "prompt",
            "different prompt",
            idempotency_key="stable-key",
        )


def test_claim_next_skips_a_chain_item_whose_predecessor_is_not_complete(tmp_path):
    state = make_state(tmp_path)

    predecessor = {
        "operation_id": "op-predecessor",
        "status": "claimed",
        "chain_id": "chain-2",
        "sequence_index": 1,
    }
    blocked = {
        "operation_id": "op-blocked",
        "status": "queued",
        "chain_id": "chain-2",
        "sequence_index": 2,
        "predecessor_operation_id": "op-predecessor",
    }
    with state.lock:
        state._save_queue([predecessor, blocked])

    assert state.claim_next_operation() is None
    blocked_state = state.get_operation("op-blocked")
    assert blocked_state is not None
    assert blocked_state["status"] == "queued"


def test_complete_does_not_claim_an_unrelated_queued_operation(tmp_path):
    state = make_state(tmp_path)

    first = state.queue_operation(
        "prompt",
        "first",
        idempotency_key="first",
        chain_id="chain-3",
        sequence_index=1,
    )
    unrelated = state.queue_operation(
        "prompt",
        "unrelated",
        idempotency_key="unrelated",
    )

    state.claim_operation(first.operation_id)
    completed, chained = state.complete_operation_and_claim_next(
        first.operation_id,
        response_text="first",
        response_text_available=True,
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert chained is None
    unrelated_state = state.get_operation(unrelated.operation_id)
    assert unrelated_state is not None
    assert unrelated_state["status"] == "queued"


def test_chain_persists_predecessor_completion_timing_for_next_operation(tmp_path):
    state = make_state(tmp_path)

    first = state.queue_operation(
        "prompt",
        "first",
        idempotency_key="timed-first",
        chain_id="timed-chain",
        sequence_index=1,
    )
    state.claim_operation(first.operation_id)
    completed, _ = state.complete_operation_and_claim_next(
        first.operation_id,
        response_text="first",
        response_text_available=True,
        timing={
            "injected_at_ms": 1000,
            "ack_at_ms": 1100,
            "generation_start_ms": 1200,
            "completed_at_ms": 1500,
            "user_messages_added": 1,
            "ack_verified": True,
            "submission_via": "verified",
        },
    )
    assert completed is not None

    second = state.queue_operation(
        "prompt",
        "second",
        idempotency_key="timed-second",
        chain_id="timed-chain",
        sequence_index=2,
        predecessor_operation_id=first.operation_id,
    )
    assert second.predecessor_completed_at_ms == 1500

    queued = state.get_operation(second.operation_id)
    assert queued is not None
    assert queued["predecessor_completed_at_ms"] == 1500


def test_m2_recovery_probe_is_only_valid_for_prompt_operations(tmp_path):
    state = make_state(tmp_path)

    with pytest.raises(ValueError, match="m2_recovery_probe is only permitted"):
        state.queue_operation(
            "new_chat",
            "",
            idempotency_key="m2-new-chat",
            m2_recovery_probe=True,
        )

    operation = state.queue_operation(
        "prompt",
        "reply with marker",
        idempotency_key="m2-prompt",
        m2_recovery_probe=True,
    )
    assert operation.m2_recovery_probe is True
    persisted = state.get_operation(operation.operation_id)
    assert persisted is not None
    assert persisted["m2_recovery_probe"] is True


def test_m2_controlled_connection_loss_is_retryable():
    from pasi_bridge.bridge import BridgeState

    assert BridgeState._is_transient_browser_error(
        "PASI_NATIVE: controlled/observed connection loss interrupted generation; current chat preserved for bounded retry."
    )
