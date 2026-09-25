from pasi.core.operation_state import InvalidOperationState, OperationState, digest_text

def test_operation_state_round_trip_and_bounded_digests():
    state = OperationState(
        operation_id="op-1",
        operation_type="roadmap_task",
        task_id="P0.1",
        provider="ollama",
        prompt_digest=digest_text("hello"),
        response_digest=digest_text("world"),
    )
    assert OperationState.from_mapping(state.to_dict()) == state
    assert len(state.prompt_digest) == 64

def test_operation_state_rejects_unknown_status():
    try:
        OperationState(operation_id="op-1", operation_type="task", status="unknown")
    except InvalidOperationState:
        return
    raise AssertionError("unknown status was accepted")
