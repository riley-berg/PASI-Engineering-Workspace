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
    monkeypatch.setattr(bridge, "runner_process_is_alive", lambda: True)

    result = bridge.request_runner_control("start", "m1")

    assert result["accepted"] is False
    assert result["action"] == "start"
    assert result["reason"] == "runner already running"
