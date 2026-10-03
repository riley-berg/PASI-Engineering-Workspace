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
    monkeypatch.setattr(bridge, "RUNNER_RUNTIME_DIR", runtime)
    monkeypatch.setattr(bridge, "RUNNER_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda profile=None: False)
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


def test_runner_diagnostics_endpoint_is_token_free(monkeypatch):
    captured = []

    class FakeServer:
        bridge_state = object()
        server_address = ("127.0.0.1", bridge.PORT)

    handler = object.__new__(bridge.BridgeRequestHandler)
    handler.path = "/runner/diagnostics"
    handler.server = FakeServer()
    handler._send_json = lambda payload, status=bridge.HTTPStatus.OK: captured.append((payload, status))

    monkeypatch.setattr(
        bridge,
        "runner_diagnostics_payload",
        lambda state: {"schema_version": "pasi-runner-diagnostics-v1"},
    )

    bridge.BridgeRequestHandler.do_GET(handler)

    assert captured == [
        (
            {"schema_version": "pasi-runner-diagnostics-v1"},
            bridge.HTTPStatus.OK,
        )
    ]


def test_runner_diagnostics_payload_contains_live_process_and_profile_state(monkeypatch):
    class FakeBridgeState:
        def get_browser_health(self):
            return {"observation": {"kind": "chatgpt_health"}}

    monkeypatch.setattr(
        bridge,
        "load_runner_state",
        lambda profile=None: {
            "active_profile": "m1",
            "bridge_process": {"pid": 100},
            "processes": [{"pid": 200, "profile": "m1", "recognized": True}],
            "profiles": {
                "m1": {"status": "running", "completed_operations": 1},
                "168h": {"status": "failed", "error": "acceptance worktree is not clean"},
            },
        },
    )

    payload = bridge.runner_diagnostics_payload(FakeBridgeState())

    assert payload["schema_version"] == "pasi-runner-diagnostics-v1"
    assert payload["active_profile"] == "m1"
    assert payload["processes"][0]["pid"] == 200
    assert payload["profiles"]["m1"]["completed_operations"] == 1
    assert payload["profiles"]["168h"]["error"] == "acceptance worktree is not clean"
    assert payload["browser_health"]["observation"]["kind"] == "chatgpt_health"


def test_load_runner_state_clears_stale_error_when_live_runner_is_running(monkeypatch, tmp_path):
    state_path = tmp_path / "runtime" / "m1" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "status": "running",
            "runner_profile": "m1",
            "execution_mode": "supervised_m1",
            "runner_pid": 4242,
            "error": "old unrelated failure",
            "failed_at": "2026-10-02T00:00:00+00:00",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "runner_state_path", lambda profile: state_path)
    monkeypatch.setattr(
        bridge,
        "runner_process_info",
        lambda profile=None: {
            "pid": 4242,
            "profile": "m1",
            "cmdline": "pasi_m1_cdp_chain.py",
            "workspace": True,
            "recognized": True,
        },
    )

    result = bridge.load_runner_state("m1")

    assert result["status"] == "running"
    assert result["ready"] is True
    assert result["error"] is None
    assert "failed_at" not in result
    assert result["evidence"]["live_process_present"] is True
    assert result["evidence"]["failure_confirmed"] is False


def test_load_runner_state_uses_process_loss_as_failure_evidence(monkeypatch, tmp_path):
    state_path = tmp_path / "runtime" / "m1" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "status": "running",
            "runner_profile": "m1",
            "execution_mode": "supervised_m1",
            "runner_pid": 4242,
            "error": "old unrelated failure",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "runner_state_path", lambda profile: state_path)
    monkeypatch.setattr(bridge, "runner_process_info", lambda profile=None: None)

    result = bridge.load_runner_state("m1")

    assert result["status"] == "failed"
    assert result["ready"] is False
    assert result["error"] == "runner process is no longer alive"
    assert result["evidence"]["live_process_present"] is False
    assert result["evidence"]["failure_confirmed"] is True
