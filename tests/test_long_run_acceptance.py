from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_168h_long_run.py"
SCHEMA = ROOT / "schemas" / "long-run-168h-v1.json"


def load_verifier() -> Any:
    spec = importlib.util.spec_from_file_location("pasi_long_run_verifier", VERIFIER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iso(start: datetime, seconds: int) -> str:
    return (start + timedelta(seconds=seconds)).isoformat()


def _rehash(evidence):
    copy_for_hash = json.loads(json.dumps(evidence))
    copy_for_hash["integrity"]["canonical_sha256"] = ""
    evidence["integrity"]["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            copy_for_hash,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _build_evidence(verifier: Any = None) -> Any:
    verifier = verifier or load_verifier()
    verifier.POLICY = copy.deepcopy(verifier.POLICY)
    verifier.POLICY.update(
        {
            "target_duration_seconds": 600,
            "health_sample_interval_seconds": 60,
            "minimum_logical_operations": 3,
            "planned_recovery_count": 1,
            "recovery_schedule_seconds": [120],
            "recovery_schedule_tolerance_seconds": 30,
            "max_recovery_latency_seconds": 120,
        }
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=verifier.POLICY["target_duration_seconds"])

    operations = []
    for index in range(1, verifier.POLICY["minimum_logical_operations"] + 1):
        operations.append(
            {
                "operation_id": f"op-{index}",
                "sequence_index": index,
                "marker": f"MARKER-{index}",
                "prompt_fingerprint": hashlib.sha256(f"prompt-{index}".encode()).hexdigest(),
                "idempotency_key": f"key-{index}",
                "chain_id": "chain-1",
                "predecessor_operation_id": None if index == 1 else f"op-{index - 1}",
                "status": "completed",
                "logical_execution_count": 1,
                "duplicate_execution_count": 0,
                "terminal_chat_error": None,
                "recovery_event_ids": [],
                "created_at": _iso(start, (index - 1) * 3600),
                "completed_at": _iso(start, (index - 1) * 3600 + 60),
            }
        )

    planned_offsets = verifier.POLICY["recovery_schedule_seconds"]
    recoveries = []
    for idx, offset in enumerate(planned_offsets, start=1):
        op_index = 1 + offset // 3600
        recoveries.append(
            {
                "recovery_id": f"recovery-{idx}",
                "operation_id": f"op-{op_index}",
                "planned": True,
                "scheduled_offset_seconds": offset,
                "detected_at": _iso(start, offset),
                "resumed_at": _iso(start, offset + 30),
                "completed_at": _iso(start, offset + 60),
                "same_operation_resumed": True,
                "checkpoint_preserved": True,
                "retry_count_delta": 1,
                "logical_execution_count": 1,
                "terminal_chat_errors": 0,
                "controlled_probe": True,
                "recovery_latency_seconds": 60,
                "phases": [
                    "connection_lost",
                    "controlled_probe_resume",
                    "ready_for_retry",
                    "retry_resumed",
                ],
            }
        )

    health = []
    for offset in range(0, verifier.POLICY["target_duration_seconds"] + 1, 60):
        health.append(
            {
                "timestamp": _iso(start, offset),
                "bridge_status": "ok",
                "browser_status": "healthy",
                "heartbeat_age_seconds": 1,
                "operation_id": None,
                "phase": "healthy",
            }
        )

    resource_samples = []
    for offset in range(0, verifier.POLICY["target_duration_seconds"] + 1, 60):
        resource_samples.append(
            {
                "timestamp": _iso(start, offset),
                "rss_bytes": 100_000_000,
                "cpu_percent": 5.0,
                "runtime_disk_bytes": 100_000_000 + offset,
                "free_disk_bytes": 50 * 1024**3,
                "fd_count": 100,
            }
        )

    evidence = {
        "schema_version": 1,
        "milestone": "LONG_RUN_168H",
        "run_id": "run-1",
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": verifier.POLICY["target_duration_seconds"],
        "git_commit": "0" * 40,
        "bridge_version": "bridge",
        "controller_version": "controller",
        "policy": copy.deepcopy(verifier.POLICY),
        "health": {
            "sample_interval_seconds": verifier.POLICY["health_sample_interval_seconds"],
            "samples": health,
            "failure_count": 0,
            "max_gap_seconds": verifier.POLICY["health_sample_interval_seconds"],
            "max_heartbeat_age_seconds": 1,
        },
        "operations": operations,
        "recoveries": recoveries,
        "resources": {
            "baseline": {
                "timestamp": start.isoformat(),
                "rss_bytes": 100_000_000,
                "cpu_percent": 0,
                "runtime_disk_bytes": 100_000_000,
                "free_disk_bytes": 50 * 1024**3,
                "fd_count": 100,
            },
            "samples": resource_samples,
            "sampling_failures": 0,
            "sampling_errors": [],
            "peak_rss_bytes": 100_000_000,
            "rss_p95_bytes": 100_000_000,
            "peak_cpu_15m_average_percent": 5.0,
            "cpu_15m_p95_percent": 5.0,
            "runtime_disk_growth_bytes": verifier.POLICY["target_duration_seconds"],
            "minimum_free_disk_bytes": 50 * 1024**3,
            "peak_fd_count": 100,
        },
        "automation": {
            "fresh_chat_creations": 0,
            "fresh_chat_events": [],
            "thinking_state_violations": 0,
            "wrong_conversation_events": 0,
        },
        "summary": {
            "logical_operations_completed": len(operations),
            "duplicate_logical_operations": 0,
            "skipped_operations": 0,
            "terminal_chat_errors": 0,
            "premature_claims": 0,
            "premature_injections": 0,
            "planned_recoveries": verifier.POLICY["planned_recovery_count"],
            "successful_planned_recoveries": verifier.POLICY["planned_recovery_count"],
            "unplanned_recoveries": 0,
        },
        "integrity": {"canonical_sha256": ""},
        "final": {
            "status": "PASS",
            "verification_timestamp": end.isoformat(),
            "failures": [],
        },
    }

    copy_for_hash = json.loads(json.dumps(evidence))
    copy_for_hash["integrity"]["canonical_sha256"] = ""
    evidence["integrity"]["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            copy_for_hash,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return evidence


def test_schema_is_valid_json():
    payload = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert payload["$id"].endswith("long-run-168h-v1.json")


def test_clean_evidence_passes():
    verifier = load_verifier()
    verifier.verify_evidence(_build_evidence(verifier))


def test_duplicate_operation_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["operations"][1]["operation_id"] = evidence["operations"][0]["operation_id"]
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "duplicate operation_id" in str(exc)
    else:
        raise AssertionError("expected duplicate operation_id to fail")


def test_skipped_sequence_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["operations"][1]["sequence_index"] = 3
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "sequence index" in str(exc)
    else:
        raise AssertionError("expected skipped sequence to fail")


def test_recovery_identity_change_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["recoveries"][0]["same_operation_resumed"] = False
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "operation identity" in str(exc)
    else:
        raise AssertionError("expected recovery identity change to fail")


def test_second_recovery_retry_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["recoveries"][0]["retry_count_delta"] = 2
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "retry delta" in str(exc)
    else:
        raise AssertionError("expected second retry to fail")


def test_stale_heartbeat_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["health"]["samples"][0]["heartbeat_age_seconds"] = 61
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "heartbeat" in str(exc)
    else:
        raise AssertionError("expected stale heartbeat to fail")


def test_resource_leak_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    for sample in evidence["resources"]["samples"]:
        sample["rss_bytes"] = 250_000_000
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "RSS p95" in str(exc)
    else:
        raise AssertionError("expected RSS growth to fail")


def test_fresh_chat_requires_thinking_before_and_after():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["automation"]["fresh_chat_creations"] = 1
    evidence["automation"]["fresh_chat_events"] = [
        {
            "timestamp": "2026-01-01T00:05:00+00:00",
            "reason": "usage_limit",
            "thinking_verified_before": False,
            "thinking_verified_after": True,
        }
    ]
    _rehash(evidence)
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "before fresh chat" in str(exc)
    else:
        raise AssertionError("expected missing pre-fresh-chat Thinking verification to fail")


def test_tampered_evidence_fails():
    verifier = load_verifier()
    evidence = _build_evidence(verifier)
    evidence["summary"]["terminal_chat_errors"] = 1
    try:
        verifier.verify_evidence(evidence)
    except verifier.VerificationError as exc:
        assert "integrity hash mismatch" in str(exc)
    else:
        raise AssertionError("expected tampered evidence to fail integrity verification")
