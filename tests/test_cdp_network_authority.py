from pathlib import Path
import json
import subprocess


def test_cdp_network_authority_contract():
    root = Path(__file__).resolve().parents[1]
    extension = root / "extensions" / "pasi-chatgpt"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    cdp = extension / "src" / "cdp-network-controller.js"
    node_tests = extension / "src" / "test_cdp_network_controller.cjs"
    background = (extension / "src" / "background.js").read_text(encoding="utf-8")

    assert cdp.is_file()
    assert node_tests.is_file()
    assert not (extension / "src" / "network-interceptor.js").exists()
    assert not (extension / "src" / "test_network_interceptor.cjs").exists()
    assert not (extension / "src" / "legacy").exists()
    assert not (extension / "src" / "recovery.js").exists()
    assert not (extension / "src" / "chatgpt.js").exists()
    assert not (extension / "src" / "detectors.js").exists()
    assert not (extension / "src" / "recovery_progress.js").exists()

    assert "debugger" in manifest["permissions"]
    manifest_scripts = [
        script
        for entry in manifest["content_scripts"]
        for script in entry.get("js", [])
    ]
    assert "src/network-interceptor.js" not in manifest_scripts
    assert "src/chatgpt.js" not in manifest_scripts
    assert "src/detectors.js" not in manifest_scripts
    assert "src/recovery_progress.js" not in manifest_scripts
    assert "src/legacy/dom-controller.js" not in manifest_scripts
    assert "src/recovery.js" not in manifest_scripts

    cdp_text = cdp.read_text(encoding="utf-8")
    assert '"cdp-network-controller.js"' in background
    assert "chrome.debugger" in background
    assert "debuggerApi.onEvent.addListener(handlePaused)" in cdp_text
    assert "Fetch.takeResponseBodyAsStream" in cdp_text
    assert "Fetch.fulfillRequest" in cdp_text
    assert "requestContainsPrompt" in cdp_text
    assert "ensureReasoningMode" in cdp_text
    assert "ensureGithubRepository" in cdp_text
    assert "MutationObserver" not in cdp_text
    assert "document.querySelector" not in cdp_text
    assert "Runtime.evaluate" not in cdp_text
    assert "pasi-controller-ready" not in background
    assert "pasi-dispatch-operation" not in background
    assert "pasi-network-bind-operation" not in background
    assert "pasi-cdp-submit-operation" not in background
    assert "pasi-cdp-interrupt-operation" not in background
    assert "injectExistingChatTabs" not in background
    assert "chrome.scripting.executeScript" not in background
    assert "executePromptOperation" in background
    assert "executeNewChatOperation" in background
    assert "executeAttachGithubOperation" in background
    assert "operation.operation_type === 'select_reasoning'" in background
    assert "claim_next: true" in background
    assert "chrome.tabs.onActivated" in background
    assert "chrome.tabs.onUpdated" in background
    assert "chrome.alarms.onAlarm" in background
    assert "BRIDGE_NEXT_OPERATION_RE" in background
    assert "&wait_ms=" in background

    for source in (cdp, extension / "src" / "background.js"):
        result = subprocess.run(
            ["node", "--check", str(source)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    tests = subprocess.run(
        ["node", "--test", str(node_tests)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr
