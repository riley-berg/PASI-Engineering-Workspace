import pytest

from automation.orchestrator.bridge import BridgeState, completion_markers_satisfied
from automation.orchestrator.state import StateManager

def test_completion_markers_accept_exact_line():
    assert completion_markers_satisfied(
        "NETWORK_CORRELATION_OK_2026",
        ["NETWORK_CORRELATION_OK_2026"],
    )


def test_completion_markers_accept_colon_suffix():
    assert completion_markers_satisfied(
        "NETWORK_CORRELATION_OK_2026: verified",
        ["NETWORK_CORRELATION_OK_2026"],
    )


def test_completion_markers_reject_stale_nonmatching_response():
    assert not completion_markers_satisfied(
        "Good — this is the previous assistant response.",
        ["NETWORK_CORRELATION_OK_2026"],
    )


def test_completion_markers_allow_unmarked_operations():
    assert completion_markers_satisfied(
        "A normal response",
        None,
    )


def _queued_marked_operation(tmp_path):
    state = StateManager(tmp_path / "ai")
    bridge = BridgeState(state)
    operation = bridge.queue_operation(
        "prompt",
        "test prompt",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )
    claimed = bridge.claim_operation(operation.operation_id)
    assert claimed is not None
    return bridge, state, operation.operation_id


def test_marked_browser_observation_rejects_stale_response(tmp_path):
    bridge, state, operation_id = _queued_marked_operation(tmp_path)
    bridge.save_browser_observation(
        {
            "schema_version": "pasi-native-chromium-v2",
            "captured_at": "2026-09-29T00:00:00Z",
            "data": {
                "kind": "chatgpt_response",
                "active_operation_id": operation_id,
                "response_text": "That is the previous assistant response.",
                "response_text_available": True,
            },
        }
    )
    operation = bridge.get_operation(operation_id)
    assert operation is not None
    assert operation["status"] == "claimed"
    assert operation.get("response_text_available") is not True


def test_marked_browser_observation_accepts_matching_response(tmp_path):
    bridge, state, operation_id = _queued_marked_operation(tmp_path)
    bridge.save_browser_observation(
        {
            "schema_version": "pasi-native-chromium-v2",
            "captured_at": "2026-09-29T00:00:00Z",
            "data": {
                "kind": "chatgpt_response",
                "active_operation_id": operation_id,
                "response_text": "NETWORK_PATCH_OK_2026",
                "response_text_available": True,
            },
        }
    )
    operation = bridge.get_operation(operation_id)
    assert operation is not None
    assert operation["response_text_available"] is True
    assert operation["response_text"] == "NETWORK_PATCH_OK_2026"


def test_marked_completion_rejects_nonmatching_response(tmp_path):
    bridge, state, operation_id = _queued_marked_operation(tmp_path)
    with pytest.raises(ValueError, match="completion markers"):
        bridge.complete_operation_and_claim_next(
            operation_id,
            response_text="stale response",
            response_text_available=True,
        )
    operation = bridge.get_operation(operation_id, repair_response=False)
    assert operation is not None
    assert operation["status"] == "claimed"
