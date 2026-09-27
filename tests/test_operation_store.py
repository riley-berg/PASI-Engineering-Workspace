from pathlib import Path

import pytest

from pasi.core.operation_state import (
    InvalidOperationState,
    InvalidOperationTransition,
    OperationRevisionConflict,
    OperationState,
    digest_text,
)
from pasi.core.operation_store import (
    DuplicateOperationState,
    OperationStateNotFound,
    SQLiteOperationStateStore,
)


def make_state() -> OperationState:
    return OperationState(
        operation_id="op-1",
        operation_type="roadmap_task",
        task_id="P1.1",
        provider="ollama",
        prompt_digest=digest_text("prompt"),
    )


def test_operation_state_round_trip_and_revision():
    state = make_state()
    assert state.state_revision == 0
    claimed = state.transition("claimed", expected_revision=0, phase="claiming")
    generating = claimed.transition("generating", expected_revision=1, phase="generating")
    completed = generating.transition(
        "completed",
        expected_revision=2,
        phase="verified",
        verification_status="PASS",
        response_digest=digest_text("response"),
    )

    assert completed.state_revision == 3
    assert completed.status == "completed"
    assert completed.response_digest == digest_text("response")
    assert OperationState.from_mapping(completed.to_dict()) == completed


def test_operation_state_rejects_invalid_transition_and_stale_revision():
    state = make_state()

    with pytest.raises(InvalidOperationTransition):
        state.transition("completed")

    claimed = state.transition("claimed", expected_revision=0)

    with pytest.raises(OperationRevisionConflict):
        claimed.transition("generating", expected_revision=0)

    completed = claimed.transition("generating", expected_revision=1).transition(
        "completed",
        expected_revision=2,
        verification_status="PASS",
    )

    with pytest.raises(InvalidOperationTransition):
        completed.transition("claimed", expected_revision=3)


def test_failed_retry_increments_attempt_and_recovery_count():
    state = make_state().transition("claimed", expected_revision=0)
    failed = state.transition(
        "failed",
        expected_revision=1,
        failure_signature="provider-timeout",
    )
    retried = failed.transition("claimed", expected_revision=2)

    assert failed.attempt == 0
    assert failed.recovery_count == 0
    assert retried.attempt == 1
    assert retried.recovery_count == 1
    assert retried.state_revision == 3


def test_operation_state_rejects_unsupported_schema_and_bad_digest():
    with pytest.raises(InvalidOperationState):
        OperationState(
            operation_id="op-1",
            operation_type="task",
            schema_version=2,
        )

    with pytest.raises(InvalidOperationState):
        OperationState(
            operation_id="op-1",
            operation_type="task",
            prompt_digest="not-a-sha",
        )


def test_sqlite_store_survives_restart_and_enforces_revisions(tmp_path: Path):
    path = tmp_path / "operations.db"
    first_store = SQLiteOperationStateStore(path)
    state = first_store.create(make_state())

    claimed = first_store.transition(
        state.operation_id,
        "claimed",
        expected_revision=0,
        phase="claiming",
    )

    second_store = SQLiteOperationStateStore(path)
    recovered = second_store.get(state.operation_id)
    assert recovered == claimed

    generating = second_store.transition(
        state.operation_id,
        "generating",
        expected_revision=1,
        phase="generating",
    )
    assert second_store.get(state.operation_id).state_revision == 2

    with pytest.raises(OperationRevisionConflict):
        second_store.transition(
            state.operation_id,
            "completed",
            expected_revision=1,
        )

    completed = second_store.transition(
        state.operation_id,
        "completed",
        expected_revision=2,
        verification_status="PASS",
    )
    assert completed.status == "completed"
    assert completed.state_revision == 3
    assert generating.state_revision == 2


def test_sqlite_store_rejects_duplicates_and_missing_operations(tmp_path: Path):
    store = SQLiteOperationStateStore(tmp_path / "operations.db")
    state = make_state()
    store.create(state)

    with pytest.raises(DuplicateOperationState):
        store.create(state)

    with pytest.raises(OperationStateNotFound):
        store.get("missing")

    with pytest.raises(OperationStateNotFound):
        store.transition(
            "missing",
            "claimed",
            expected_revision=0,
        )
