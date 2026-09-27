from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "automation" / "chromium" / "pasi-chatgpt" / "content.js"
BACKGROUND = ROOT / "automation" / "chromium" / "pasi-chatgpt" / "background.js"
DETECTORS = ROOT / "automation" / "chromium" / "pasi-chatgpt" / "detectors.js"
POLICY = ROOT / "automation" / "chromium" / "pasi-chatgpt" / "timeout-policy.json"

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def test_native_controller_uses_bounded_idle_polling():
    source = _read(NATIVE)
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert policy["controller_poll_ms"] == 2000
    assert policy["dom_poll_ms"] == 100
    assert policy["click_settle_ms"] == 250
    assert policy["response_settle_ms"] <= 20
    assert "const POLL_MS = TIMEOUT_POLICY.pollMs || 2000;" in source
    assert "const DOM_POLL_MS = TIMEOUT_POLICY.domPollMs || 100;" in source
    assert "const ACTIVE_KEY = 'pasi:active-operation';" in source
    assert "localStorage.setItem(ACTIVE_KEY" in source
    assert "localStorage.removeItem(ACTIVE_KEY);" in source

def test_native_runtime_preserves_safety_boundaries():
    native = _read(NATIVE)
    background = _read(BACKGROUND)
    detectors = _read(DETECTORS)
    assert "chrome.runtime.sendMessage" in native
    assert "type: 'pasi-bridge-request'" in native
    assert "credentials: 'omit'" in background
    assert "allowedBridgeRequest(method, path)" in background
    assert "captcha" in detectors
    assert "session has expired" in detectors
    assert "CHAT_EXHAUSTED" in native
    assert "reportHealth" in native
