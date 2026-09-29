from pathlib import Path
import json
import subprocess


def test_network_interceptor_phase1_contract():
    root = Path(__file__).resolve().parents[1]
    extension = root / "extensions" / "pasi-chatgpt"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    interceptor = extension / "src" / "network-interceptor.js"
    node_tests = extension / "src" / "test_network_interceptor.cjs"
    background = (extension / "src" / "background.js").read_text(encoding="utf-8")
    controller = (extension / "src" / "content.js").read_text(encoding="utf-8")

    assert interceptor.is_file()
    assert node_tests.is_file()

    main_scripts = [
        entry for entry in manifest["content_scripts"]
        if "src/network-interceptor.js" in entry.get("js", [])
    ]
    assert len(main_scripts) == 1
    assert main_scripts[0]["run_at"] == "document_start"
    assert main_scripts[0]["world"] == "MAIN"
    assert main_scripts[0]["js"] == ["src/network-interceptor.js"]

    assert "world: 'MAIN'" in background
    assert "vendor/typescript.js" not in background
    assert "files: ['src/network-interceptor.js']" in background
    assert "pasi-network-bind-operation" in background
    assert "world: 'MAIN'" in background
    assert "MutationObserver" not in interceptor.read_text(encoding="utf-8")
    assert "document.querySelector" not in interceptor.read_text(encoding="utf-8")
    assert "document.body" not in interceptor.read_text(encoding="utf-8")

    assert "installNetworkLifecycleShadow" in controller
    assert "PASI_NETWORK_LIFECYCLE" in controller
    assert "PASI_NETWORK_BIND_OPERATION" in controller
    assert "pasi-network-bind-operation" in controller
    assert "bindNetworkOperation(activeOperationId)" in controller
    assert "chatgpt_network_generation_started" in controller
    assert "chatgpt_network_generation_terminal" in controller
    assert "chatgpt_network_generation_stalled" in controller
    assert "chatgpt_network_dom_shadow" in controller
    assert "markNetworkDomCompletion" in controller
    assert "dom_completed" in controller
    assert "dom_response_available" in controller
    assert "network_shadow: true" in controller

    syntax = subprocess.run(
        ["node", "--check", str(interceptor)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    tests = subprocess.run(
        ["node", "--test", str(node_tests)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr
