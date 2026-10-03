import json
import time
from pathlib import Path

import pytest

from automation.pasi_agent_contracts import (
    SCHEMA_VERSION,
    TOOL_INPUT_SCHEMAS,
    TOOL_NAMES,
    TOOL_OUTPUT_SCHEMAS,
    PasiAgentObservationService,
    _redact_command,
)
from automation.orchestrator import bridge
from automation.orchestrator.state import StateManager


EXPECTED_TOOLS = {
    "pasi.control_runner",
    "pasi.run_browser_test",
    "pasi.get_runner_state",
    "pasi.get_process_state",
    "pasi.get_browser_state",
    "pasi.get_extension_state",
    "pasi.get_operation_state",
    "pasi.get_environment_state",
    "pasi.get_acceptance_evidence",
    "pasi.get_browser_screenshot",
    "pasi.get_browser_dom",
    "pasi.get_browser_console_errors",
    "pasi.get_browser_network",
}


def test_agent_tool_contracts_are_exactly_the_read_only_seven():
    assert set(TOOL_NAMES) == EXPECTED_TOOLS
    assert set(TOOL_INPUT_SCHEMAS) == EXPECTED_TOOLS
    assert set(TOOL_OUTPUT_SCHEMAS) == EXPECTED_TOOLS

    for name in EXPECTED_TOOLS:
        assert TOOL_INPUT_SCHEMAS[name]["type"] == "object"
        assert TOOL_INPUT_SCHEMAS[name]["additionalProperties"] is False
        assert TOOL_OUTPUT_SCHEMAS[name]["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_runner_observation_preserves_m1_and_168h_separation(monkeypatch):
    service = PasiAgentObservationService()

    monkeypatch.setattr(
        bridge,
        "load_runner_state",
        lambda profile=None: (
            {
                "active_profile": "m1",
                "profiles": {
                    "m1": {
                        "available": True,
                        "runner_profile": "m1",
                        "status": "running",
                        "execution_mode": "supervised_m1",
                        "ready": True,
                        "process_alive": True,
                        "run_id": "m1-run",
                        "runner_pid": 100,
                        "target_operations": 20,
                        "completed_operations": 1,
                        "current_operation_index": 2,
                        "current_operation_id": "op-2",
                        "current_operation_status": "queued",
                        "phase": "waiting_for_cdp_dispatch",
                        "last_updated_at": "2026-10-02T21:00:00+00:00",
                    },
                    "168h": {
                        "available": True,
                        "runner_profile": "168h",
                        "status": "failed",
                        "execution_mode": "manual",
                        "ready": False,
                        "process_alive": False,
                        "run_id": "168h-run",
                        "runner_pid": None,
                        "target_operations": 0,
                        "completed_operations": 0,
                        "phase": "failed",
                        "error": "RuntimeError: acceptance worktree is not clean",
                        "failed_at": "2026-10-02T20:00:00+00:00",
                    },
                },
            }
            if profile is None
            else (
                {
                    "available": True,
                    "runner_profile": profile,
                    "status": "running" if profile == "m1" else "failed",
                    "execution_mode": "supervised_" + profile if profile == "m1" else "manual",
                    "ready": profile == "m1",
                    "process_alive": profile == "m1",
                    "run_id": profile + "-run",
                    "runner_pid": 100 if profile == "m1" else None,
                    "target_operations": 20 if profile == "m1" else 0,
                    "completed_operations": 1 if profile == "m1" else 0,
                    "current_operation_index": 2 if profile == "m1" else None,
                    "current_operation_id": "op-2" if profile == "m1" else None,
                    "current_operation_status": "queued" if profile == "m1" else None,
                    "phase": "waiting_for_cdp_dispatch" if profile == "m1" else "failed",
                    "error": "RuntimeError: acceptance worktree is not clean" if profile == "168h" else None,
                    "last_updated_at": "2026-10-02T21:00:00+00:00",
                }
            )
        ),
    )

    result = service.observe("pasi.get_runner_state", {"_request_id": "test-1"})
    assert result["ok"] is True
    assert result["data"]["profiles"]["m1"]["progress"]["completed"] == 1
    assert result["data"]["profiles"]["168h"]["error"]["code"] == "ACCEPTANCE_WORKTREE_DIRTY"
    assert result["data"]["profiles"]["m1"]["error"] is None


def test_operation_observation_does_not_mutate_queue(tmp_path):
    manager = StateManager(tmp_path)
    operation = {
        "operation_id": "op-1",
        "status": "generating",
        "operation_type": "prompt",
        "prompt": "secret prompt",
        "controller_id": "controller",
    }
    manager.save_queue([operation])
    queue_path = manager.queue_path
    before = queue_path.stat().st_mtime_ns

    service = PasiAgentObservationService(state_manager=manager)
    result = service.observe(
        "pasi.get_operation_state",
        {"operation_id": "op-1", "_request_id": "test-2"},
    )

    assert result["ok"] is True
    assert result["data"]["operation"]["prompt"]["sha256"]
    assert result["data"]["operation"]["prompt"]["content_included"] is False
    assert queue_path.stat().st_mtime_ns == before


def test_missing_operation_returns_not_found():
    service = PasiAgentObservationService(state_manager=StateManager(Path("/tmp") / "pasi-agent-test-empty"))
    result = service.observe(
        "pasi.get_operation_state",
        {"operation_id": "does-not-exist", "_request_id": "test-3"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "PASI_NOT_FOUND"


def test_process_command_redaction():
    command = "python runner.py token=SECRET password=VALUE --flag"
    redacted = _redact_command(command)
    assert "SECRET" not in redacted
    assert "VALUE" not in redacted
    assert "<redacted>" in redacted


def test_derived_acceptance_evidence_is_available_without_registry(monkeypatch, tmp_path):
    manager = StateManager(tmp_path / "ai")
    service = PasiAgentObservationService(
        state_manager=manager,
        acceptance_evidence_path=tmp_path / "missing-evidence.json",
    )

    monkeypatch.setattr(
        bridge,
        "load_runner_state",
        lambda profile=None: {
            "available": True,
            "runner_profile": "m1",
            "status": "completed",
            "execution_mode": "manual",
            "ready": False,
            "process_alive": False,
            "run_id": "m1-pass",
            "runner_pid": 1234,
            "target_operations": 20,
            "completed_operations": 20,
            "last_operation_id": "op-20",
            "request_ids": 20,
            "chat_url": "https://chatgpt.com/c/test",
            "last_updated_at": "2026-10-02T21:00:00+00:00",
            "completed_at": "2026-10-02T21:00:00+00:00",
        },
    )

    result = service.observe(
        "pasi.get_acceptance_evidence",
        {"profile": "m1", "_request_id": "test-4"},
    )

    assert result["ok"] is True
    assert result["data"]["available"] is True
    assert result["data"]["acceptances"][0]["status"] == "passed"


@pytest.mark.skipif(
    __import__("importlib.util").util.find_spec("mcp") is None,
    reason="optional MCP SDK is not installed",
)
def test_mcp_server_advertises_exact_tool_schemas():
    from automation.pasi_agent_mcp import build_tools

    tools = {tool.name: tool for tool in build_tools()}
    assert set(tools) == EXPECTED_TOOLS
    for name in EXPECTED_TOOLS:
        assert tools[name].input_schema == TOOL_INPUT_SCHEMAS[name]
        assert tools[name].output_schema == TOOL_OUTPUT_SCHEMAS[name]


def test_runner_control_and_browser_interaction_tools_are_bounded():
    assert TOOL_INPUT_SCHEMAS["pasi.control_runner"]["additionalProperties"] is False
    assert TOOL_INPUT_SCHEMAS["pasi.control_runner"]["properties"]["wait_ms"]["maximum"] == 10000
    actions = TOOL_INPUT_SCHEMAS["pasi.run_browser_test"]["properties"]["action"]["enum"]
    assert "click" in actions
    assert "fill" in actions
    assert "press_key" in actions
    assert "scroll" in actions


def test_browser_tools_are_read_only_and_bounded():
    names = {
        "pasi.get_browser_screenshot",
        "pasi.get_browser_dom",
        "pasi.get_browser_console_errors",
        "pasi.get_browser_network",
    }
    assert names.issubset(set(TOOL_NAMES))
    assert TOOL_INPUT_SCHEMAS["pasi.get_browser_dom"]["additionalProperties"] is False
    assert TOOL_INPUT_SCHEMAS["pasi.get_browser_network"]["additionalProperties"] is False


def test_browser_dom_observation_delegates_to_the_cdp_boundary(monkeypatch):
    service = PasiAgentObservationService()
    monkeypatch.setattr(
        "automation.pasi_agent_browser_testing.BrowserTestingClient.request",
        lambda self, *args, **kwargs: {
            "tab_id": 31,
            "url": "https://chatgpt.com/c/test",
            "title": "PASI test",
            "selector": "#start",
            "matched_count": 1,
            "truncated": False,
            "elements": [{"tag": "button", "id": "start", "text": "Start", "visible": True}],
        },
    )
    result = service.observe(
        "pasi.get_browser_dom",
        {"selector": "#start", "max_elements": 1, "_request_id": "browser-dom"},
    )
    assert result["ok"] is True
    assert result["data"]["dom"]["elements"][0]["text"] == "Start"


def test_browser_screenshot_hashes_image_content(monkeypatch):
    service = PasiAgentObservationService()
    monkeypatch.setattr(
        "automation.pasi_agent_browser_testing.BrowserTestingClient.request",
        lambda self, *args, **kwargs: {
            "tab_id": 31,
            "mime_type": "image/png",
            "width": 1200,
            "height": 800,
            "byte_length": 4,
            "image_base64": "AAAA",
        },
    )
    result = service.observe(
        "pasi.get_browser_screenshot",
        {"_request_id": "browser-shot"},
    )
    assert result["ok"] is True
    assert result["data"]["screenshot"]["sha256"]
    assert result["data"]["screenshot"]["image_base64"] == "AAAA"
