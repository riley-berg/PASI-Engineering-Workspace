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
    state._save_queue([predecessor, blocked])

    assert state.claim_next_operation() is None
    assert state.get_operation("op-blocked")["status"] == "queued"


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
    assert state.get_operation(unrelated.operation_id)["status"] == "queued"
