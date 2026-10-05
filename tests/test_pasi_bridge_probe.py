from pathlib import Path

from scripts import pasi_bridge_probe as probe


def test_probe_reports_authenticated_cdp_health(monkeypatch):
    def fake_get(path, token, timeout=5.0):
        if path == "/health":
            return {
                "status": "ok",
                "service": "pasi-engineering-workspace-chatgpt-bridge",
            }
        if path == "/status":
            return {"queue": [], "active_operation_id": None}
        if path == "/browser/health":
            return {
                "observation": {
                    "schema_version": "pasi-native-chromium-v2",
                    "data": {
                        "kind": "chatgpt_health",
                        "controller_version": "cdp-worker-v1",
                        "native_controller": True,
                        "network_authority": True,
                        "chat_url": "https://chatgpt.com/c/example",
                        "active_operation_id": None,
                        "page_visible": True,
                    }
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(probe, "get", fake_get)
    result = probe.probe("token")
    assert result["ok"] is True
    assert result["failures"] == []
    assert result["browser"]["controller_version"] == "cdp-worker-v1"


def test_probe_fails_closed_when_network_authority_is_false(monkeypatch):
    def fake_get(path, token, timeout=5.0):
        if path == "/health":
            return {
                "status": "ok",
                "service": "pasi-engineering-workspace-chatgpt-bridge",
            }
        if path == "/status":
            return {}
        if path == "/browser/health":
            return {
                "observation": {
                    "data": {
                        "schema_version": "pasi-native-chromium-v2",
                        "kind": "chatgpt_health",
                        "controller_version": "cdp-worker-v1",
                        "native_controller": True,
                        "network_authority": False,
                        "chat_url": "https://chatgpt.com/c/example",
                    }
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(probe, "get", fake_get)
    try:
        probe.probe("token")
    except RuntimeError as exc:
        assert "network_authority" in str(exc)
    else:
        raise AssertionError("probe unexpectedly accepted non-authoritative browser state")


def test_load_token_requires_owner_only_permissions(tmp_path: Path, monkeypatch):
    token_file = tmp_path / "bridge-token"
    token_file.write_text("secret\n", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("PASI_BRIDGE_TOKEN_FILE", str(token_file))
    assert probe.load_token() == "secret"
