from automation.orchestrator import bridge


def test_runner_execution_authorized_requires_live_supervised_runner(monkeypatch):
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: True)

    assert bridge.runner_execution_authorized({
        "status": "starting",
        "execution_mode": "supervised_m1",
    }) is False
    assert bridge.runner_execution_authorized({
        "status": "paused",
        "execution_mode": "supervised_m1",
    }) is False
    assert bridge.runner_execution_authorized({
        "status": "running",
        "execution_mode": "manual",
    }) is False
    assert bridge.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_m1",
    }) is True
    assert bridge.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_168h",
    }) is True


def test_runner_execution_authorized_rejects_dead_process(monkeypatch):
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: False)

    assert bridge.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_m1",
    }) is False
