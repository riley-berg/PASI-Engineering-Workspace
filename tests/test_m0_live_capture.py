from __future__ import annotations

import json
from pathlib import Path

from scripts.run_m0_live_capture import CaptureState


def complete_events() -> list[dict[str, object]]:
    return [
        {
            "type": "page_ready",
            "chat_url": "https://chatgpt.com/c/new",
            "authenticated_page": True,
            "thinking_enabled": True,
        },
        {
            "type": "fresh_chat",
            "fresh_chat_created_after_usage": True,
            "fresh_chat_creation_reason": "usage_limit",
            "fresh_chat_url": "https://chatgpt.com/c/new",
        },
        {
            "type": "operation_started",
            "operation_id": "op-1",
            "chat_url": "https://chatgpt.com/c/new",
            "thinking_enabled": True,
        },
        {
            "type": "checkpoint",
            "operation_id": "op-1",
            "resume_phase": "response_generation",
        },
        {
            "type": "connection_lost",
            "operation_id": "op-1",
            "response_stopped_on_loss": True,
            "checkpoint_preserved": True,
            "resume_phase": "response_generation",
        },
        {
            "type": "connection_restored",
            "operation_id": "op-1",
            "resumed_after_reconnect": True,
            "same_operation_resumed": True,
            "resume_phase": "response_generation",
        },
        {
            "type": "response_complete",
            "operation_id": "op-1",
            "chat_url": "https://chatgpt.com/c/new",
            "thinking_enabled": True,
            "fresh_chat_created_after_usage": True,
            "response_text": """PASI_TASK_ID: P0.1
PASI_RESULT_STATUS: complete
PASI_SUMMARY: Completed the M0 task.
PASI_EVIDENCE: Live evidence captured.
PASI_PATCH_START
diff --git a/acceptance/M0-LIVE-PROOF.txt b/acceptance/M0-LIVE-PROOF.txt
new file mode 100644
--- /dev/null
+++ b/acceptance/M0-LIVE-PROOF.txt
@@ -0,0 +1 @@
+PASI M0 LIVE PROOF
PASI_PATCH_END""",
        },
    ]


def test_independent_extension_manifest() -> None:
    root = Path(__file__).resolve().parents[1] / "extensions" / "pasi-chatgpt"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "src/background.js"
    assert manifest["content_scripts"][0]["js"] == [
        "src/protocol.js",
        "src/chatgpt.js",
        "src/content.js",
    ]
    assert "self-hosted" not in json.dumps(manifest).lower()
    assert "personal-ai-system" not in json.dumps(manifest).lower()


def test_extension_files_exist() -> None:
    root = Path(__file__).resolve().parents[1] / "extensions" / "pasi-chatgpt"
    for relative in (
        "manifest.json",
        "README.md",
        "src/background.js",
        "src/chatgpt.js",
        "src/content.js",
        "src/protocol.js",
    ):
        assert (root / relative).is_file(), relative


def test_capture_bridge_is_repo_local() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m0_live_capture.py"
    assert script.is_file()


def test_capture_bridge_refuses_incomplete_live_response() -> None:
    state = CaptureState()
    for event in complete_events()[:-1]:
        state.apply(event)
    assert state.ready() is False


def test_capture_bridge_materializes_real_runtime_evidence() -> None:
    state = CaptureState()
    for event in complete_events():
        state.apply(event)

    assert state.ready() is True
    payload = state.materialize()

    assert payload["provider"] == "chatgpt_browser"
    assert payload["authenticated"] is True
    assert payload["task_id"] == "P0.1"
    assert payload["status"] == "complete"
    assert payload["patch"].startswith("diff --git")
    assert payload["next_task_id"] == ""
    assert payload["next_prompt"] == ""
    assert payload["runtime_evidence"]["fresh_chat_creation_reason"] == "usage_limit"
    assert payload["runtime_evidence"]["thinking_enabled"] is True
    assert payload["runtime_evidence"]["connection_recovery"]["operation_id"] == "op-1"


def test_capture_bridge_does_not_fill_missing_response_markers() -> None:
    state = CaptureState()
    for event in complete_events():
        state.apply(event)
    state.response_text = state.response_text.replace("PASI_M0_NEXT_TASK_ID: P0.2\n", "")

    assert state.ready() is False


def test_capture_bridge_main_declares_and_returns_an_int() -> None:
    import ast

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_m0_live_capture.py"
    )
    tree = ast.parse(script.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")

    assert isinstance(main.returns, ast.Name)
    assert main.returns.id == "int"
    assert isinstance(main.body[-1], ast.Return)
    assert isinstance(main.body[-1].value, ast.Constant)
    assert main.body[-1].value.value == 0


def test_current_prompt_payload_is_canonical_and_durable(tmp_path, monkeypatch) -> None:
    import scripts.run_m0_live_capture as capture
    from pasi.core.task_progression import RoadmapTaskCatalog

    monkeypatch.setattr(capture, "PROGRESSION", tmp_path / "progression.json")
    monkeypatch.setattr(
        capture,
        "load_task_catalog",
        lambda roadmap, allow_fallback=False: RoadmapTaskCatalog.from_roadmap_file(roadmap),
    )

    payload = capture.current_prompt_payload()

    assert payload["task_id"] == "P0.1"
    assert "PASI_RESULT_STATUS: complete" in payload["prompt"]
    assert "Do not invent or precompute the next task prompt" in payload["prompt"]
    assert isinstance(payload["prompt_generation"], int)
    assert payload["prompt_generation"] == 1
    assert payload["advance_count"] == 0
