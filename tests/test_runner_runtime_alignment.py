import json

from automation.orchestrator import bridge


def test_runner_start_publishes_shared_runtime_directory(monkeypatch, tmp_path):
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    script = tmp_path / "runner.py"
    script.write_text("print('stub')\n", encoding="utf-8")

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(
        bridge,
        "RUNNER_PROFILES",
        {"168h": (str(executable), str(script))},
    )
    monkeypatch.setattr(bridge, "RUNNER_RUNTIME_DIR", runtime)
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", runtime / "state.json")
    monkeypatch.setattr(bridge, "RUNNER_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: False)
    monkeypatch.setattr(bridge, "_github_token", lambda: "test-token")

    class FakeProcess:
        pid = 4242

    captured = {}

    def fake_popen(command, **kwargs):
        captured["environment"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(bridge.subprocess, "Popen", fake_popen)

    result = bridge._start_runner("168h")

    assert result["accepted"] is True
    assert captured["environment"]["PASI_RUNTIME_DIR"] == str(runtime / "168h")


def test_load_runner_state_preserves_m1_dispatch_progress(monkeypatch, tmp_path):
    state_path = tmp_path / "runtime" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "status": "running",
            "runner_profile": "m1",
            "execution_mode": "supervised_m1",
            "runner_pid": 4242,
            "target_operations": 20,
            "completed_operations": 0,
            "current_operation_id": "op-123",
            "current_operation_index": 1,
            "current_operation_status": "queued",
            "phase": "waiting_for_cdp_dispatch",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "runner_state_path", lambda profile: state_path)
    monkeypatch.setattr(
        bridge,
        "runner_process_info",
        lambda profile=None: {"pid": 4242, "profile": "m1", "cmdline": "pasi_m1_cdp_chain.py", "workspace": True, "recognized": True},
    )

    result = bridge.load_runner_state()

    assert result["status"] == "running"
    assert result["ready"] is True
    assert result["runner_profile"] == "m1"
    assert result["current_operation_id"] == "op-123"
    assert result["current_operation_index"] == 1
    assert result["current_operation_status"] == "queued"
    assert result["phase"] == "waiting_for_cdp_dispatch"


def test_runner_start_uses_profile_isolated_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(bridge, "RUNNER_RUNTIME_DIR", runtime)

    assert bridge.runner_runtime_dir("m1") == (runtime / "m1").resolve()
    assert bridge.runner_runtime_dir("168h") == (runtime / "168h").resolve()
    assert bridge.runner_state_path("m1") != bridge.runner_state_path("168h")
