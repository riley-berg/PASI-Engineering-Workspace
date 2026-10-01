from __future__ import annotations

import threading
import time
from pathlib import Path

from automation.orchestrator.bridge import BridgeState
from automation.orchestrator.state import StateManager


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
