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
