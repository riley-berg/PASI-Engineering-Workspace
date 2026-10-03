from automation.orchestrator import bridge


def test_runner_execution_authorized_requires_live_supervised_runner(monkeypatch):
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda profile=None: True)

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
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda profile=None: False)

    assert bridge.runner_execution_authorized({
        "status": "running",
        "execution_mode": "supervised_m1",
    }) is False


def test_runner_execution_authorized_accepts_registered_custom_profile(monkeypatch):
    monkeypatch.setattr(bridge, "all_runner_profiles", lambda: ("m1", "168h", "site-checker"))
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda profile=None: profile == "site-checker")

    assert bridge.runner_execution_authorized({
        "status": "running",
        "runner_profile": "site-checker",
        "execution_mode": "supervised_site-checker",
    }) is True
