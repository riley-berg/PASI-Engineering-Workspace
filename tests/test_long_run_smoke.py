import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.run_168h_live_smoke import verify_smoke_evidence
from scripts.run_168h_live_smoke import sha256_json


def evidence(operations):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=180)
    payload = {
        "schema_version": 1,
        "milestone": "LONG_RUN_168H_LIVE_SMOKE",
        "run_id": "smoke-test",
        "git_commit": "1" * 40,
        "bridge_version": "bridge",
        "controller_version": "controller",
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "duration_seconds": 180.0,
        "policy": {
            "duration_seconds": 180.0,
            "minimum_operations": 2,
            "max_submit_gap_seconds": 10.0,
        },
        "operations": operations,
        "health_samples": [],
        "summary": {
            "completed": len(operations),
            "duplicates": 0,
            "skipped": 0,
            "terminal_chat_errors": 0,
        },
        "integrity": {"canonical_sha256": ""},
    }
    payload["integrity"]["canonical_sha256"] = sha256_json(payload)
    return payload


def op(index, completed, queued):
    marker = f"M{index}"
    return {
        "operation_id": f"op-{index}",
        "sequence_index": index,
        "marker": marker,
        "prompt_fingerprint": f"{index:064x}",
        "idempotency_key": f"key-{index}",
        "predecessor_operation_id": None if index == 1 else f"op-{index-1}",
        "queued_at": queued,
        "completed_at": completed,
        "status": "completed",
        "response_text": marker,
        "logical_execution_count": 1,
        "duplicate_execution_count": 0,
        "timing": {
            "ack_verified": True,
            "user_messages_added": 1,
            "submission_via": "native_controller",
        },
        "idempotency_replay_operation_id": f"op-{index}",
    }


def test_smoke_verifier_accepts_immediate_contiguous_work():
    start = "2026-01-01T00:00:00+00:00"
    first_done = "2026-01-01T00:00:20+00:00"
    second_queue = "2026-01-01T00:00:21+00:00"
    second_done = "2026-01-01T00:00:40+00:00"
    verify_smoke_evidence(
        evidence(
            [
                op(1, first_done, start),
                op(2, second_done, second_queue),
            ]
        )
    )


def test_smoke_verifier_rejects_hourly_gap():
    start = "2026-01-01T00:00:00+00:00"
    first_done = "2026-01-01T00:00:20+00:00"
    second_queue = "2026-01-01T01:00:21+00:00"
    second_done = "2026-01-01T01:00:40+00:00"
    with pytest.raises(Exception, match="continuous submission"):
        verify_smoke_evidence(
            evidence(
                [
                    op(1, first_done, start),
                    op(2, second_done, second_queue),
                ]
            )
        )


def test_smoke_verifier_rejects_duplicate_operation():
    start = "2026-01-01T00:00:00+00:00"
    completed = "2026-01-01T00:00:20+00:00"
    first = op(1, completed, start)
    second = op(2, "2026-01-01T00:00:40+00:00", "2026-01-01T00:00:21+00:00")
    second["operation_id"] = first["operation_id"]
    second["idempotency_replay_operation_id"] = first["operation_id"]
    with pytest.raises(Exception, match="duplicate operation id"):
        verify_smoke_evidence(evidence([first, second]))
