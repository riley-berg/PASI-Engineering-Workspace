from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_m2_live_acceptance.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("pasi_m2_live_acceptance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m2_harness_compiles_and_exposes_contract():
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")
    module = load_harness()
    assert module.M2_CHECKPOINT_SCHEMA_VERSION == 1
    assert module.TERMINAL_STATUSES == {"completed", "failed", "cancelled"}


def test_m2_terminal_contract_accepts_one_same_operation_recovery():
    module = load_harness()
    state = {
        "operation_id": "op-m2",
        "status": "completed",
        "m2_recovery_probe": True,
        "retry_count": 1,
        "retry_counts": {"controller": 1, "response": 0, "context": 0},
        "recovery_events": [
            {"operation_id": "op-m2", "phase": "connection_lost"},
            {"operation_id": "op-m2", "phase": "controlled_probe_resume"},
            {"operation_id": "op-m2", "phase": "connection_restored"},
            {"operation_id": "op-m2", "phase": "ready_for_retry"},
            {"operation_id": "op-m2", "phase": "retry_waiting"},
            {"operation_id": "op-m2", "phase": "retry_resumed"},
        ],
        "response_text": "PASI_M2_RECOVERY_test_01",
        "response_text_available": True,
        "timing": {"user_messages_added": 1, "ack_verified": True},
    }
    result = module.validate_m2_terminal_contract(state, "PASI_M2_RECOVERY_test_01")
    assert result["same_operation_recovered"] is True
    assert result["duplicate_logical_operations"] == 0
    assert result["retry_count"] == 1


def test_m2_terminal_contract_rejects_second_recovery():
    module = load_harness()
    state = {
        "operation_id": "op-m2",
        "status": "completed",
        "m2_recovery_probe": True,
        "retry_count": 2,
        "retry_counts": {"controller": 2, "response": 0, "context": 0},
        "recovery_events": [
            {"operation_id": "op-m2", "phase": "connection_lost"},
            {"operation_id": "op-m2", "phase": "connection_lost"},
            {"operation_id": "op-m2", "phase": "controlled_probe_resume"},
            {"operation_id": "op-m2", "phase": "ready_for_retry"},
            {"operation_id": "op-m2", "phase": "retry_resumed"},
        ],
        "response_text": "PASI_M2_RECOVERY_test_02",
        "response_text_available": True,
        "timing": {"user_messages_added": 1, "ack_verified": True},
    }
    try:
        module.validate_m2_terminal_contract(state, "PASI_M2_RECOVERY_test_02")
    except module.M2LiveError:
        return
    raise AssertionError("expected a second recovery to fail the M2 contract")


def test_m2_harness_carries_same_operation_and_idempotency_requirements():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"same_operation_id_preserved": True' in source
    assert '"duplicate_logical_operations": 0' in source
    assert "idempotency replay created a different operation_id" in source
    assert "m2_recovery_probe" in source
    assert "controlled_probe_resume" in source
