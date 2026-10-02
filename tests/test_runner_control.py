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
