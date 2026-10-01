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

    second = bridge.queue_operation("prompt", "second task")
    claimed_second = bridge.wait_for_next_operation("controller-1", 0)
    assert claimed_second is not None
    assert claimed_second["operation_id"] == second.operation_id
    assert claimed_second["predecessor_operation_id"] == first.operation_id
    assert claimed_second["predecessor_completed_at_ms"] == completed_at_ms
