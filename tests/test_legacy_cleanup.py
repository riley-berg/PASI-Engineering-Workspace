from pathlib import Path

from automation.orchestrator.models import ChatOperation


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
