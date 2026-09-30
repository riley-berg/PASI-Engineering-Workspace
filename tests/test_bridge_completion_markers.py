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


def test_cdp_network_response_becomes_authoritative_and_overrides_stale_dom_text(tmp_path):
    bridge = BridgeState(StateManager(tmp_path / "ai"))
    operation = bridge.queue_operation(
        "prompt",
        "expected",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )
    claimed = bridge.claim_operation(operation.operation_id, "controller-cdp")
    assert claimed is not None

    bridge.save_browser_observation(
        {
            "schema_version": "pasi-network-cdp-v1",
            "captured_at": "2026-09-30T00:00:00Z",
            "data": {
                "kind": "chatgpt_network_response",
                "network_source": "cdp_fetch",
                "active_operation_id": operation.operation_id,
                "controller_id": "controller-cdp",
                "request_id": "req-cdp-1",
                "event_type": "COMPLETED",
                "response_text": "NETWORK_PATCH_OK_2026",
                "response_text_available": True,
                "assistant_message_id": "assistant-current",
            },
        }
    )

    stored = bridge.get_operation(operation.operation_id, repair_response=False)
    assert stored is not None
    assert stored["network_response_authoritative"] is True
    assert stored["response_source"] == "cdp_fetch_stream"
    assert stored["network_request_id"] == "req-cdp-1"

    completed, _ = bridge.complete_operation_and_claim_next(
        operation.operation_id,
        response_text="That is stale DOM text.",
        response_text_available=True,
        controller_id="controller-cdp",
    )
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["response_text"] == "NETWORK_PATCH_OK_2026"
    assert completed["response_source"] == "cdp_fetch_stream"


def test_operation_state_status_stays_in_sync_with_durable_lifecycle(tmp_path):
    bridge = BridgeState(StateManager(tmp_path / "ai"))
    operation = bridge.queue_operation(
        "prompt",
        "expected",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )

    queued = bridge.get_operation(operation.operation_id, repair_response=False)
    assert queued is not None
    assert queued["status"] == "queued"
    assert queued["operation_state"]["status"] == "queued"

    claimed = bridge.claim_operation(operation.operation_id, "controller-cdp")
    assert claimed is not None
    assert claimed["status"] == "claimed"
    assert claimed["operation_state"]["status"] == "claimed"

    bridge.save_browser_observation({
        "schema_version": "pasi-network-cdp-v1",
        "captured_at": "2026-09-30T00:00:00Z",
        "data": {
            "kind": "chatgpt_network_response",
            "network_source": "cdp_fetch",
            "active_operation_id": operation.operation_id,
            "controller_id": "controller-cdp",
            "request_id": "req-state-sync",
            "event_type": "COMPLETED",
            "response_text": "NETWORK_PATCH_OK_2026",
            "response_text_available": True,
        },
    })

    completed = bridge.get_operation(operation.operation_id, repair_response=False)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["operation_state"]["status"] == "completed"


def test_cdp_started_event_moves_claimed_operation_to_generating(tmp_path):
    bridge = BridgeState(StateManager(tmp_path / "ai"))
    operation = bridge.queue_operation(
        "prompt",
        "expected",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )
    claimed = bridge.claim_operation(operation.operation_id, "controller-cdp")
    assert claimed is not None

    bridge.save_browser_observation(
        {
            "schema_version": "pasi-network-cdp-v1",
            "captured_at": "2026-09-30T00:00:00Z",
            "data": {
                "kind": "chatgpt_network_lifecycle",
                "network_source": "cdp_fetch",
                "active_operation_id": operation.operation_id,
                "controller_id": "controller-cdp",
                "request_id": "req-cdp-started",
                "event_type": "STARTED",
                "reason": None,
                "classification": None,
                "response_text": "",
                "response_text_available": False,
            },
        }
    )

    stored = bridge.get_operation(operation.operation_id, repair_response=False)
    assert stored is not None
    assert stored["status"] == "generating"
    assert stored["network_lifecycle_event"] == "STARTED"
    assert stored.get("network_terminal_event") is None


def test_cdp_completed_event_without_matching_marker_cannot_authorize_completion(tmp_path):
    bridge = BridgeState(StateManager(tmp_path / "ai"))
    operation = bridge.queue_operation(
        "prompt",
        "expected",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )
    claimed = bridge.claim_operation(operation.operation_id, "controller-cdp")
    assert claimed is not None

    bridge.save_browser_observation(
        {
            "schema_version": "pasi-network-cdp-v1",
            "captured_at": "2026-09-30T00:00:01Z",
            "data": {
                "kind": "chatgpt_network_response",
                "network_source": "cdp_fetch",
                "active_operation_id": operation.operation_id,
                "controller_id": "controller-cdp",
                "request_id": "req-cdp-stale",
                "event_type": "COMPLETED",
                "response_text": "STALE_RESPONSE",
                "response_text_available": True,
            },
        }
    )

    stored = bridge.get_operation(operation.operation_id, repair_response=False)
    assert stored is not None
    assert stored["network_terminal_event"] == "COMPLETED"
    assert stored.get("network_response_authoritative") is not True
    assert stored.get("response_text_available") is not True

    with pytest.raises(ValueError, match="completion markers"):
        bridge.complete_operation_and_claim_next(
            operation.operation_id,
            response_text="STALE_RESPONSE",
            response_text_available=True,
            controller_id="controller-cdp",
        )


def test_late_dom_response_cannot_replace_authoritative_cdp_response(tmp_path):
    bridge = BridgeState(StateManager(tmp_path / "ai"))
    operation = bridge.queue_operation(
        "prompt",
        "expected",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )
    claimed = bridge.claim_operation(operation.operation_id, "controller-cdp")
    assert claimed is not None

    bridge.save_browser_observation(
        {
            "schema_version": "pasi-network-cdp-v1",
            "captured_at": "2026-09-30T00:00:00Z",
            "data": {
                "kind": "chatgpt_network_response",
                "network_source": "cdp_fetch",
                "active_operation_id": operation.operation_id,
                "controller_id": "controller-cdp",
                "request_id": "req-cdp-2",
                "event_type": "COMPLETED",
                "response_text": "NETWORK_PATCH_OK_2026",
                "response_text_available": True,
            },
        }
    )
    bridge.save_browser_observation(
        {
            "schema_version": "pasi-native-chromium-v2",
            "captured_at": "2026-09-30T00:00:02Z",
            "data": {
                "kind": "chatgpt_response",
                "active_operation_id": operation.operation_id,
                "response_text": "STALE DOM RESPONSE",
                "response_text_available": True,
            },
        }
    )

    stored = bridge.get_operation(operation.operation_id, repair_response=False)
    assert stored is not None
    assert stored["response_text"] == "NETWORK_PATCH_OK_2026"
    assert stored["response_source"] == "cdp_fetch_stream"


def test_generic_cdp_network_failure_is_transient_and_late_authoritative_response_completes(tmp_path):
    bridge = BridgeState(StateManager(tmp_path / "ai"))
    operation = bridge.queue_operation(
        "prompt",
        "expected",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )
    claimed = bridge.claim_operation(operation.operation_id, "controller-cdp")
    assert claimed is not None

    bridge.save_browser_observation(
        {
            "schema_version": "pasi-network-cdp-v1",
            "captured_at": "2026-09-30T00:00:00Z",
            "data": {
                "kind": "chatgpt_network_response",
                "network_source": "cdp_fetch",
                "active_operation_id": operation.operation_id,
                "controller_id": "controller-cdp",
                "request_id": "req-cdp-late-success",
                "event_type": "COMPLETED",
                "reason": "RESPONSE_STREAM_FINISHED",
                "classification": "success",
                "response_text": "NETWORK_PATCH_OK_2026",
                "response_text_available": True,
            },
        }
    )

    completed = bridge.fail_operation(
        operation.operation_id,
        "PASI_CDP: network failure",
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["response_source"] == "cdp_fetch_stream"
    assert completed["network_response_authoritative"] is True
