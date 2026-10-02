from pathlib import Path

from automation.orchestrator.models import ChatOperation
from pasi.core.operation_state import OperationState


ROOT = Path(__file__).resolve().parents[1]


def test_orchestrator_models_keep_only_live_chat_operation_surface():
    from automation.orchestrator import models

    assert hasattr(models, "ChatOperation")
    assert not hasattr(models, "ProjectState")
    assert not hasattr(models, "CurrentTask")
    assert not hasattr(models, "FeatureStatus")


def test_legacy_orchestration_types_module_is_removed():
    assert not (ROOT / "automation" / "orchestrator" / "orchestration_types.py").exists()


def test_chat_operation_still_produces_canonical_operation_state():
    operation = ChatOperation(
        operation_id="op-live",
        operation_type="prompt",
        prompt="hello",
    )
    payload = operation.to_dict()
    assert payload["operation_id"] == "op-live"
    assert payload["operation_state"]["operation_id"] == "op-live"
    assert payload["operation_state"]["operation_type"] == "prompt"
    assert payload["operation_state"]["schema_version"] == 2
    assert payload["operation_state"]["status"] == "queued"
    assert not (ROOT / "automation" / "orchestrator" / "operation_state.py").exists()

def test_state_manager_exposes_only_live_bridge_state_api():
    from automation.orchestrator.state import StateManager

    manager = StateManager(ROOT / ".tmp-legacy-cleanup-state")
    assert hasattr(manager, "load_queue")
    assert hasattr(manager, "save_queue")
    assert hasattr(manager, "save_terminal_response")
    assert hasattr(manager, "load_terminal_response")
    assert hasattr(manager, "prune_terminal_responses")
    for live_browser_method in (
    ):
        assert hasattr(manager, live_browser_method), live_browser_method
    for retired in (
        "load_project_state",
        "save_project_state",
        "load_current_task",
        "save_current_task",
        "load_feature_status",
        "save_feature_status",
        "save_retry_state",
        "load_retry_state",
        "save_test_results",
        "save_context_package",
        "load_context_package",
        "save_research_state",
        "load_research_state",
        "save_execution_result",
        "load_execution_result",
        "save_handoff",
    ):
        assert not hasattr(manager, retired), retired

def test_orchestrator_config_contains_only_live_paths():
    from automation.orchestrator import config

    assert hasattr(config, "CONFIG")
    assert hasattr(config.CONFIG, "project_root")
    assert hasattr(config.CONFIG, "ai_dir")
    assert not hasattr(config, "RetryLimits")
    assert not hasattr(config, "FAILURES_DIR")
    assert not hasattr(config, "SCREENSHOTS_DIR")
    assert not hasattr(config, "TRACES_DIR")
    assert not hasattr(config, "LOGS_DIR")
    assert not hasattr(config, "ensure_runtime_directories")


def test_bridge_retry_policy_is_explicitly_distinct_from_canonical_lifecycle():
    from automation.orchestrator.operation_lifecycle import validate_transition
    canonical = OperationState(operation_id="op-live", operation_type="prompt", status="generating")
    assert canonical.can_transition_to("queued") is False
    validate_transition("generating", "queued")


def test_canonical_operation_state_supports_bridge_terminal_cancellation():
    state = OperationState(operation_id="op-cancelled", operation_type="prompt", status="claimed")
    cancelled = state.transition("cancelled")
    assert cancelled.status == "cancelled"
