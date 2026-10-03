#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


class SmokeVerificationError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()

    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    if evidence.get("milestone") != "LONG_RUN_168H_LIVE_SMOKE":
        raise SmokeVerificationError("wrong smoke milestone")

    integrity = evidence.get("integrity", {})
    supplied = integrity.get("canonical_sha256")
    copy = json.loads(json.dumps(evidence))
    copy["integrity"]["canonical_sha256"] = ""
    actual = hashlib.sha256(
        json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if actual != supplied:
        raise SmokeVerificationError("smoke evidence integrity hash mismatch")

    started = datetime.fromisoformat(str(evidence["started_at"]).replace("Z", "+00:00"))
    ended = datetime.fromisoformat(str(evidence["ended_at"]).replace("Z", "+00:00"))
    duration = (ended - started).total_seconds()
    if duration < float(evidence["policy"]["duration_seconds"]):
        raise SmokeVerificationError("smoke duration below policy")

    operations = evidence["operations"]
    if len(operations) < int(evidence["policy"]["minimum_operations"]):
        raise SmokeVerificationError("smoke operation count below policy")

    ids = set()
    keys = set()
    markers = set()
    for index, operation in enumerate(operations, start=1):
        if operation["sequence_index"] != index:
            raise SmokeVerificationError("sequence is not contiguous")
        if operation["operation_id"] in ids:
            raise SmokeVerificationError("duplicate operation id")
        if operation["idempotency_key"] in keys:
            raise SmokeVerificationError("duplicate idempotency key")
        if operation["marker"] in markers:
            raise SmokeVerificationError("duplicate marker")
        ids.add(operation["operation_id"])
        keys.add(operation["idempotency_key"])
        markers.add(operation["marker"])
        if operation["status"] != "completed":
            raise SmokeVerificationError("non-terminal successful operation")
        if operation["response_text"] != operation["marker"]:
            raise SmokeVerificationError("marker mismatch")
        if operation["logical_execution_count"] != 1:
            raise SmokeVerificationError("logical execution count mismatch")
        if operation["duplicate_execution_count"] != 0:
            raise SmokeVerificationError("duplicate execution count nonzero")
        if operation["idempotency_replay_operation_id"] != operation["operation_id"]:
            raise SmokeVerificationError("idempotency replay changed operation identity")
        if operation["timing"].get("ack_verified") is not True:
            raise SmokeVerificationError("unverified acknowledgement")
        if operation["timing"].get("user_messages_added") != 1:
            raise SmokeVerificationError("wrong logical user-message count")
        if str(operation["timing"].get("submission_via", "")) == "sent_unverified":
            raise SmokeVerificationError("unverified submission path")

        if index > 1:
            previous = datetime.fromisoformat(
                str(operations[index - 2]["completed_at"]).replace("Z", "+00:00")
            )
            current = datetime.fromisoformat(
                str(operation["queued_at"]).replace("Z", "+00:00")
            )
            gap = (current - previous).total_seconds()
            if gap > float(evidence["policy"]["max_submit_gap_seconds"]):
                raise SmokeVerificationError(
                    f"continuous-submit gap {gap:.2f}s exceeded policy"
                )

    summary = evidence["summary"]
    for key in ("duplicates", "skipped", "terminal_chat_errors"):
        if summary.get(key) != 0:
            raise SmokeVerificationError(f"{key} was nonzero")

    print(
        "LONG-RUN SMOKE VERIFIED: "
        f"duration={duration:.1f}s operations={len(operations)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeVerificationError as exc:
        raise SystemExit(f"FAIL: {exc}")
