import json
from pathlib import Path

import pytest

from automation.orchestrator.bridge import BridgeState, ControllerOwnershipConflict
from automation.orchestrator.state import StateManager


def _bridge(tmp_path):
    return BridgeState(StateManager(tmp_path / "ai"))


def test_operation_owner_is_recorded_and_cannot_be_stolen(tmp_path):
    bridge = _bridge(tmp_path)
    operation = bridge.queue_operation(
        "prompt",
        "test prompt",
        completion_markers=["NETWORK_PATCH_OK_2026"],
    )

    claimed = bridge.claim_operation(operation.operation_id, "controller-a")
    assert claimed is not None
    assert claimed["controller_id"] == "controller-a"

    with pytest.raises(ControllerOwnershipConflict):
        bridge.complete_operation_and_claim_next(
            operation.operation_id,
            response_text="NETWORK_PATCH_OK_2026",
            response_text_available=True,
            controller_id="controller-b",
        )

    current = bridge.get_operation(operation.operation_id, repair_response=False)
    assert current is not None
    assert current["status"] == "claimed"
    assert current["controller_id"] == "controller-a"


def test_operation_owner_can_complete_and_chain_next_operation(tmp_path):
    bridge = _bridge(tmp_path)
    first = bridge.queue_operation(
        "prompt",
        "first",
        completion_markers=["FIRST_OK"],
    )
    second = bridge.queue_operation(
        "prompt",
        "second",
        completion_markers=["SECOND_OK"],
    )

    claimed = bridge.claim_operation(first.operation_id, "controller-a")
    assert claimed is not None

    bridge.save_browser_observation({
        "schema_version": "pasi-network-cdp-v1",
        "captured_at": "2026-09-30T00:00:00Z",
        "data": {
            "kind": "chatgpt_network_response",
            "network_source": "cdp_fetch",
            "active_operation_id": first.operation_id,
            "controller_id": "controller-a",
            "request_id": "req-first",
            "event_type": "COMPLETED",
            "response_text": "FIRST_OK",
            "response_text_available": True,
        },
    })

    completed, chained = bridge.complete_operation_and_claim_next(
        first.operation_id,
        response_text="FIRST_OK",
        response_text_available=True,
        controller_id="controller-a",
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["controller_id"] == "controller-a"
    assert chained is not None
    assert chained["operation_id"] == second.operation_id
    assert chained["controller_id"] == "controller-a"


def test_next_operation_claim_records_controller_owner(tmp_path):
    bridge = _bridge(tmp_path)
    operation = bridge.queue_operation("prompt", "queued")

    claimed = bridge.claim_next_operation("controller-a")

    assert claimed is not None
    assert claimed["operation_id"] == operation.operation_id
    assert claimed["controller_id"] == "controller-a"


def test_legacy_dom_controller_isolated_from_active_entry_points():
    root = Path(__file__).resolve().parents[1]
    extension = root / "extensions" / "pasi-chatgpt"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    legacy = extension / "src" / "legacy" / "dom-controller.js"
    active_content = extension / "src" / "content.js"
    interceptor = extension / "src" / "network-interceptor.js"
    cdp = extension / "src" / "cdp-network-controller.js"
    background = (extension / "src" / "background.js").read_text(encoding="utf-8")

    assert not active_content.exists()
    assert not interceptor.exists()
    assert cdp.is_file()
    assert legacy.is_file()

    manifest_scripts = [
        script
        for entry in manifest["content_scripts"]
        for script in entry.get("js", [])
    ]
    assert "src/content.js" not in manifest_scripts
    assert "src/legacy/dom-controller.js" in manifest_scripts
    assert "src/legacy/dom-controller.js" in background

    legacy_text = legacy.read_text(encoding="utf-8")
    cdp_text = cdp.read_text(encoding="utf-8")

    assert "document.querySelector" in legacy_text
    assert "MutationObserver" in legacy_text
    assert "debuggerApi.onEvent.addListener(handlePaused)" in cdp_text
    assert "Fetch.takeResponseBodyAsStream" in cdp_text
    assert "MutationObserver" not in cdp_text
    assert "document.querySelector" not in cdp_text
