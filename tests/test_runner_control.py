import json
import subprocess

from automation.orchestrator import bridge


def test_supervised_168h_resolves_gh_token_and_injects_runner_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PASI_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.delenv("PASI_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    class FakeGhResult:
        stdout = "gh-test-token\n"

    def fake_run(*args, **kwargs):
        assert args[0] == ["gh", "auth", "token", "--hostname", "github.com"]
        return FakeGhResult()

    class FakeProcess:
        pid = 4242

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    monkeypatch.setattr(bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bridge, "RUNNER_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: False)

    result = bridge._start_runner("168h")

    assert result["accepted"] is True
    assert captured["environment"]["PASI_GITHUB_TOKEN"] == "gh-test-token"
    assert captured["environment"]["PASI_PUSH"] == "1"


def test_github_token_falls_back_to_git_credential(monkeypatch):
    monkeypatch.delenv("PASI_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    calls = []

    class FakeResult:
        stdout = "protocol=https\nhost=github.com\nusername=riley\npassword=git-test-token\n"

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        if args[0][0] == "gh":
            return type("GhResult", (), {"stdout": ""})()
        return FakeResult()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge._github_token() == "git-test-token"
    assert calls == [
        ["gh", "auth", "token", "--hostname", "github.com"],
        ["git", "credential", "fill"],
    ]


def test_github_token_accepts_projects_and_gh_names(monkeypatch):
    monkeypatch.delenv("PASI_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("PASI_PROJECTS_TOKEN", "projects-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")

    assert bridge._github_token() == "projects-token"


def test_explicit_start_never_becomes_stop(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "runner_process_info",
        lambda: {"pid": 4242, "profile": "m1", "cmdline": "pasi_m1_cdp_chain.py"},
    )

    result = bridge.request_runner_control("start", "m1")

    assert result["accepted"] is False
    assert result["action"] == "start"
    assert result["reason"] == "runner already running"


def test_load_runner_state_promotes_live_process_over_stale_terminal_state(monkeypatch, tmp_path):
    state_path = tmp_path / "runner" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"completed","runner_profile":"m1","execution_mode":"manual",'
        '"completed_operations":20,"completed_at":"2026-10-02T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(
        bridge,
        "runner_process_info",
        lambda: {"pid": 4242, "profile": "m1", "cmdline": "pasi_m1_cdp_chain.py"},
    )

    result = bridge.load_runner_state()

    assert result["status"] == "running"
    assert result["runner_profile"] == "m1"
    assert result["execution_mode"] == "supervised_m1"
    assert "error" not in result
    assert "completed_at" not in result


def test_load_runner_state_marks_dead_running_runner_failed(monkeypatch, tmp_path):
    state_path = tmp_path / "runner" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"running","runner_profile":"m1","execution_mode":"supervised_m1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(bridge, "runner_process_info", lambda: None)

    result = bridge.load_runner_state()

    assert result["status"] == "failed"
    assert result["error"] == "runner process is no longer alive"


def test_load_runner_state_exposes_live_process_without_claiming_ready(monkeypatch, tmp_path):
    state_path = tmp_path / "runner" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"completed","runner_profile":"m1","execution_mode":"manual",'
        '"completed_operations":1,"completed_at":"2026-10-02T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(
        bridge,
        "runner_process_info",
        lambda: {"pid": 4242, "profile": "m1", "cmdline": "pasi_m1_cdp_chain.py"},
    )

    result = bridge.load_runner_state()

    assert result["process_alive"] is True
    assert result["process_pid"] == 4242
    assert result["process_profile"] == "m1"
    assert result["ready"] is False
    assert result["status"] == "completed"


def test_stop_targets_live_runner_process_even_when_state_is_terminal(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    state_path = runtime / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"completed","runner_profile":"m1","execution_mode":"manual",'
        '"completed_operations":1}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_RUNTIME_DIR", runtime)
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(
        bridge,
        "runner_process_info",
        lambda: {"pid": 4242, "profile": "m1", "cmdline": "pasi_m1_cdp_chain.py"},
    )
    terminated = []
    monkeypatch.setattr(bridge, "_terminate_runner_process", lambda pid: terminated.append(pid))

    result = bridge.request_runner_control("stop", "m1")
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert result["accepted"] is True
    assert result["action"] == "stop"
    assert result["pid"] == 4242
    assert terminated == [4242]
    assert state["status"] == "paused"
    assert state["process_alive"] is False
    assert state["ready"] is False


def test_load_runner_state_preserves_status(monkeypatch, tmp_path):
    state_path = tmp_path / "runner" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"running","execution_mode":"supervised_m1","completed_operations":2}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: True)

    result = bridge.load_runner_state()

    assert result["available"] is True
    assert result["status"] == "running"
    assert result["execution_mode"] == "supervised_m1"


def test_load_runner_state_preserves_failure_details(monkeypatch, tmp_path):
    state_path = tmp_path / "runner" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"failed","error":"M1 startup failed","failed_at":"2026-10-02T00:00:00+00:00",'
        '"execution_mode":"manual"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)

    result = bridge.load_runner_state()

    assert result["status"] == "failed"
    assert result["error"] == "M1 startup failed"
    assert result["failed_at"].startswith("2026-10-02T00:00:00")


def test_runner_python_prefers_workspace_venv(monkeypatch, tmp_path):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    venv_python.chmod(0o755)
    monkeypatch.setattr(bridge, "CONFIG", type("Config", (), {"project_root": tmp_path})())

    assert bridge.runner_python() == str(venv_python)


def test_load_runner_state_marks_dead_starting_runner_failed(monkeypatch, tmp_path):
    state_path = tmp_path / "runner" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        '{"status":"starting","runner_profile":"m1","execution_mode":"supervised_m1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", state_path)
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: False)

    result = bridge.load_runner_state()

    assert result["status"] == "failed"
    assert result["error"] == "runner exited before initialization completed"


def test_runner_start_publishes_pid_and_starting_state(monkeypatch, tmp_path):
    script = tmp_path / "m1.py"
    script.write_text("print('stub')\n", encoding="utf-8")
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    monkeypatch.setattr(bridge, "RUNNER_STATE_PATH", tmp_path / "runtime" / "state.json")
    monkeypatch.setattr(bridge, "RUNNER_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(bridge, "RUNNER_PROFILES", {"m1": (str(executable), str(script))})
    monkeypatch.setattr(bridge, "runner_process_info", lambda: None)

    class FakeProcess:
        pid = 4242

    def fake_popen(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(bridge.subprocess, "Popen", fake_popen)

    result = bridge._start_runner("m1")
    state = json.loads((tmp_path / "runtime" / "state.json").read_text(encoding="utf-8"))
    pid = (tmp_path / "runtime" / "runner.pid").read_text(encoding="utf-8").strip()

    assert result["accepted"] is True
    assert state["runner_profile"] == "m1"
    assert state["runner_pid"] == 4242
    assert state["status"] == "starting"
    assert pid == "4242"


def test_stop_terminates_the_isolated_runner_process_group(monkeypatch):
    signals = []
    states = iter([True, False])

    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: next(states))
    monkeypatch.setattr(bridge.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        bridge.os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )

    bridge._terminate_runner_process(4242, grace_seconds=0.1)

    assert signals == [(4242, bridge.signal.SIGTERM)]


def test_stop_falls_back_to_pid_for_legacy_runner(monkeypatch):
    signals = []
    states = iter([True, False])

    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: next(states))
    monkeypatch.setattr(bridge.os, "getpgid", lambda pid: pid - 1)
    monkeypatch.setattr(
        bridge.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )

    bridge._terminate_runner_process(4242, grace_seconds=0.1)

    assert signals == [(4242, bridge.signal.SIGTERM)]
