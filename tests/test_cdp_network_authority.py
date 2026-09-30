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
    controller = (extension / "src" / "legacy" / "dom-controller.js").read_text(encoding="utf-8")

    assert cdp.is_file()
    assert node_tests.is_file()
    assert not (extension / "src" / "network-interceptor.js").exists()
    assert not (extension / "src" / "test_network_interceptor.cjs").exists()

    assert "debugger" in manifest["permissions"]
    assert all(
        "src/network-interceptor.js" not in entry.get("js", [])
        for entry in manifest["content_scripts"]
    )
    assert '"cdp-network-controller.js"' in background
    assert "chrome.debugger" in background
    assert "debuggerApi.onEvent.addListener(handlePaused)" in cdp.read_text(encoding="utf-8")
    assert "Fetch.takeResponseBodyAsStream" in cdp.read_text(encoding="utf-8")
    assert "Fetch.fulfillRequest" in cdp.read_text(encoding="utf-8")
    assert "requestContainsPrompt" in cdp.read_text(encoding="utf-8")
    assert "PASI_NETWORK_LIFECYCLE" not in controller
    assert "networkShadowByOperationId" not in controller
    assert "installNetworkLifecycleShadow" not in controller
    assert "bindCdpOperation(activeOperationId)" in controller
    assert "cdpOperationEvidence(operationId" in controller
    assert "pasi-cdp-submit-operation" in controller
    assert "submitPrompt(" not in controller
    assert "pasi-controller-ready" in background
    assert "pasi-dispatch-operation" in background
    assert "chrome.alarms.onAlarm" in background
    assert "function poll()" not in controller
    assert "setInterval(poll" not in controller
    assert "chrome.runtime.sendMessage" in controller
    assert "Accessibility.getFullAXTree" in cdp.read_text(encoding="utf-8")
    assert "DOM.focus" in cdp.read_text(encoding="utf-8")
    assert "Input.insertText" in cdp.read_text(encoding="utf-8")
    assert "Input.dispatchKeyEvent" in cdp.read_text(encoding="utf-8")
    assert "Runtime.evaluate" not in cdp.read_text(encoding="utf-8")
    assert "document.querySelector" not in cdp.read_text(encoding="utf-8")
    assert "form.requestSubmit" not in controller
    assert "nativeMouseActivate" not in controller
    assert "sendCandidatesForComposer" not in controller
    assert "dom_fallback" not in controller
    wait_start = controller.read_text(encoding="utf-8").index("async function waitForResponse(")
    wait_end = controller.read_text(encoding="utf-8").index("  function completionProgress", wait_start)
    wait_source = controller.read_text(encoding="utf-8")[wait_start:wait_end]
    assert "detectorState()" not in wait_source

    recovery_start = controller.read_text(encoding="utf-8").index("async function recoverInterruptedOperation()")
    recovery_end = controller.read_text(encoding="utf-8").index("  function recoveryOperationId", recovery_start)
    recovery_source = controller.read_text(encoding="utf-8")[recovery_start:recovery_end]
    assert "generating()" not in recovery_source

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
