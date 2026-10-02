from __future__ import annotations

import threading
import time
from pathlib import Path

from automation.orchestrator.bridge import BridgeState
from automation.orchestrator.state import StateManager


def test_supervised_execution_authorization_requires_live_acceptance_runner(monkeypatch):
    from automation.orchestrator import bridge as bridge_module

    monkeypatch.setattr(bridge_module, "runner_process_is_alive", lambda: True)
    assert bridge_module.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_168h",
    }) is True

    assert bridge_module.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_m1",
    }) is True

    assert bridge_module._runner_profile("m1") == "m1"
    assert bridge_module._runner_profile("168h") == "168h"

    monkeypatch.setattr(bridge_module, "runner_process_is_alive", lambda: False)
    assert bridge_module.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_168h",
    }) is False

    assert bridge_module.runner_execution_authorized({
        "status": "running",
        "execution_mode": "manual",
    }) is False


def test_runner_start_reports_missing_script_instead_of_raising_server_error(tmp_path: Path, monkeypatch):
    from automation.orchestrator import bridge as bridge_module

    missing_script = tmp_path / "missing-runner.py"
    monkeypatch.setattr(bridge_module, "runner_process_is_alive", lambda: False)
    monkeypatch.setattr(bridge_module, "RUNNER_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(
        bridge_module,
        "RUNNER_PROFILES",
        {"m1": (bridge_module.sys.executable, str(missing_script))},
    )

    result = bridge_module.request_runner_control("start", "m1")

    assert result["accepted"] is False
    assert result["action"] == "start"
    assert result["profile"] == "m1"
    assert result["reason"] == "runner script is missing from the active PASI workspace"


def test_wait_for_next_operation_wakes_when_runner_queues_after_response_processing(tmp_path: Path):
    state = StateManager(tmp_path / "state")
    bridge = BridgeState(state)
    result: dict[str, object] = {}

    def waiter() -> None:
        result["operation"] = bridge.wait_for_next_operation("controller-1", 2_000)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)

    queued = bridge.queue_operation("prompt", "next task prompt")
    thread.join(timeout=2)

    assert not thread.is_alive()
    claimed = result["operation"]
    assert isinstance(claimed, dict)
    assert claimed["operation_id"] == queued.operation_id
    assert claimed["status"] == "claimed"
    assert claimed["controller_id"] == "controller-1"


def test_wait_for_next_operation_times_out_without_a_queued_task(tmp_path: Path):
    bridge = BridgeState(StateManager(tmp_path / "state"))
    started = time.monotonic()

    operation = bridge.wait_for_next_operation("controller-1", 20)

    elapsed = time.monotonic() - started
    assert operation is None
    assert elapsed < 0.5




def test_prequeued_next_operation_waits_for_runner_processing_ack(tmp_path: Path):
    state = StateManager(tmp_path / "state")
    bridge = BridgeState(state)

    first = bridge.queue_operation("prompt", "first response")
    second = bridge.queue_operation("prompt", "second task")
    claimed_first = bridge.wait_for_next_operation("controller-1", 0)
    assert claimed_first is not None
    assert claimed_first["operation_id"] == first.operation_id

    completed = bridge.complete_operation(
        first.operation_id,
        response_text="finished response",
        response_text_available=True,
        timing={"generation_start_ms": 120_000, "completed_at_ms": 123_456},
    )
    assert completed is not None
    assert completed["response_processing_required"] is True
    assert completed["response_processing_complete"] is False

    # The production completion path records CDP authority before acknowledging
    # completion. Mirror that durable evidence here so the processing-ack gate
    # is exercised rather than bypassed by the test fixture.
    with bridge.lock:
        queue = bridge._load_queue()
        for item in queue:
            if item.get("operation_id") == first.operation_id:
                item["network_response_authoritative"] = True
        bridge._save_queue(queue)

    blocked = bridge.wait_for_next_operation("controller-1", 20)
    assert blocked is None
    assert bridge.claim_operation(second.operation_id, "controller-1") is None

    processed = bridge.mark_response_processed(first.operation_id, "controller-1")
    assert processed is not None
    assert processed["response_processing_complete"] is True
    assert processed["timing"]["response_processed_at_ms"] >= 123_456

    claimed_second = bridge.wait_for_next_operation("controller-1", 0)
    assert claimed_second is not None
    assert claimed_second["operation_id"] == second.operation_id
    assert claimed_second["predecessor_operation_id"] == first.operation_id
    assert claimed_second["predecessor_completed_at_ms"] == 123_456


def test_cancel_operation_can_clean_up_an_interrupted_queued_diagnostic(tmp_path: Path):
    bridge = BridgeState(StateManager(tmp_path / "state"))
    operation = bridge.queue_operation("prompt", "interrupted diagnostic")

    cancelled = bridge.cancel_operation(
        operation.operation_id,
        reason="live diagnostic interrupted by operator",
    )

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["failure_reason"] == "cancelled"
    assert cancelled["error"] == "live diagnostic interrupted by operator"
    assert bridge.claim_next_operation("controller-1") is None


def test_cancel_operation_requires_matching_controller_for_claimed_work(tmp_path: Path):
    bridge = BridgeState(StateManager(tmp_path / "state"))
    operation = bridge.queue_operation("prompt", "claimed diagnostic")
    claimed = bridge.claim_next_operation("controller-1")

    assert claimed is not None
    assert claimed["operation_id"] == operation.operation_id

    try:
        bridge.cancel_operation(operation.operation_id, "controller-2", "stale controller")
    except Exception as exc:
        assert "different controller" in str(exc)
    else:
        raise AssertionError("controller fencing did not reject mismatched cancellation")

    cancelled = bridge.cancel_operation(
        operation.operation_id,
        "controller-1",
        "operator interrupted diagnostic",
    )
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"


def test_next_operation_carries_predecessor_completion_evidence(tmp_path: Path):
    state = StateManager(tmp_path / "state")
    bridge = BridgeState(state)

    first = bridge.queue_operation("prompt", "first response")
    claimed_first = bridge.wait_for_next_operation("controller-1", 0)
    assert claimed_first is not None
    assert claimed_first["operation_id"] == first.operation_id

    completed_at_ms = 123_456
    completed = bridge.complete_operation(
        first.operation_id,
        response_text="finished response",
        response_text_available=True,
        timing={
            "generation_start_ms": 120_000,
            "completed_at_ms": completed_at_ms,
        },
    )
    assert completed is not None
    assert completed["status"] == "completed"

    bridge.save_browser_observation({
        "schema_version": "pasi-network-cdp-v1",
        "captured_at": "2026-09-30T00:00:00Z",
        "data": {
            "kind": "chatgpt_network_response",
            "network_source": "cdp_fetch",
            "active_operation_id": first.operation_id,
            "controller_id": "controller-1",
            "event_type": "COMPLETED",
            "stream_complete": True,
            "response_text": "finished response",
            "response_text_available": True,
        },
    })
    processed = bridge.mark_response_processed(first.operation_id, "controller-1")
    assert processed is not None
    assert processed["response_processing_complete"] is True

    second = bridge.queue_operation("prompt", "second task")
    claimed_second = bridge.wait_for_next_operation("controller-1", 0)
    assert claimed_second is not None
    assert claimed_second["operation_id"] == second.operation_id
    assert claimed_second["predecessor_operation_id"] == first.operation_id
    assert claimed_second["predecessor_completed_at_ms"] == completed_at_ms
