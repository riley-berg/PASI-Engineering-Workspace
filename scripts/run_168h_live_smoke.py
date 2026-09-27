#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_168h_long_run import (
    BridgeClient,
    LongRunError,
    browser_health,
    browser_state,
    ensure_chat_ready,
    prompt_fingerprint,
    queue_operation,
    wait_for_terminal,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
DEFAULT_DURATION_SECONDS = 180.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 90.0
DEFAULT_MIN_OPERATIONS = 3
OUTPUT = ROOT / ".runtime" / "acceptance" / "long-run-168h-smoke.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def sha256_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def verify_smoke_evidence(evidence: dict[str, Any]) -> None:
    started = datetime.fromisoformat(
        str(evidence["started_at"]).replace("Z", "+00:00")
    )
    ended = datetime.fromisoformat(
        str(evidence["ended_at"]).replace("Z", "+00:00")
    )
    duration = (ended - started).total_seconds()
    if duration < float(evidence["policy"]["duration_seconds"]):
        raise LongRunError("smoke duration was shorter than requested")

    operations = evidence["operations"]
    minimum = int(evidence["policy"]["minimum_operations"])
    if len(operations) < minimum:
        raise LongRunError(
            f"smoke completed {len(operations)} operations; minimum is {minimum}"
        )

    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    seen_markers: set[str] = set()

    for index, operation in enumerate(operations, start=1):
        if operation["sequence_index"] != index:
            raise LongRunError(f"sequence gap at {index}")
        if operation["operation_id"] in seen_ids:
            raise LongRunError("duplicate operation id detected")
        if operation["idempotency_key"] in seen_keys:
            raise LongRunError("duplicate idempotency key detected")
        if operation["marker"] in seen_markers:
            raise LongRunError("duplicate marker detected")
        seen_ids.add(operation["operation_id"])
        seen_keys.add(operation["idempotency_key"])
        seen_markers.add(operation["marker"])

        if operation["status"] != "completed":
            raise LongRunError(f"operation {index} was not completed")
        if operation["response_text"] != operation["marker"]:
            raise LongRunError(f"operation {index} marker mismatch")
        if operation["logical_execution_count"] != 1:
            raise LongRunError(f"operation {index} logical execution count was not 1")
        if operation["duplicate_execution_count"] != 0:
            raise LongRunError(f"operation {index} duplicate execution was nonzero")

        timing = operation["timing"]
        if timing.get("ack_verified") is not True:
            raise LongRunError(f"operation {index} acknowledgement was not verified")
        if timing.get("user_messages_added") != 1:
            raise LongRunError(f"operation {index} did not add exactly one user message")
        if str(timing.get("submission_via", "")) == "sent_unverified":
            raise LongRunError(f"operation {index} used an unverified submission path")

        if index > 1:
            previous_completed = datetime.fromisoformat(
                str(operations[index - 2]["completed_at"]).replace("Z", "+00:00")
            )
            current_queued = datetime.fromisoformat(
                str(operation["queued_at"]).replace("Z", "+00:00")
            )
            submit_gap = (current_queued - previous_completed).total_seconds()
            if submit_gap > float(evidence["policy"]["max_submit_gap_seconds"]):
                raise LongRunError(
                    f"operation {index} waited {submit_gap:.2f}s after the prior "
                    "operation; continuous submission contract failed"
                )

        replay_id = operation["idempotency_replay_operation_id"]
        if replay_id != operation["operation_id"]:
            raise LongRunError(f"operation {index} idempotency replay changed identity")

    if evidence["summary"]["terminal_chat_errors"] != 0:
        raise LongRunError("terminal CHAT_* errors detected")
    if evidence["summary"]["duplicates"] != 0:
        raise LongRunError("duplicate execution detected")
    if evidence["summary"]["skipped"] != 0:
        raise LongRunError("skipped operation detected")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a short live smoke of the revised continuous 168-hour Long-run."
    )
    parser.add_argument(
        "--bridge-url",
        default=__import__("os").environ.get("PASI_BRIDGE_URL", DEFAULT_BRIDGE_URL),
    )
    parser.add_argument("--duration-seconds", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--minimum-operations",
        type=int,
        default=DEFAULT_MIN_OPERATIONS,
    )
    parser.add_argument(
        "--max-submit-gap-seconds",
        type=float,
        default=10.0,
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    if args.duration_seconds < 30:
        parser.error("--duration-seconds must be at least 30 seconds")
    if args.minimum_operations < 2:
        parser.error("--minimum-operations must be at least 2")
    if args.operation_timeout <= 0:
        parser.error("--operation-timeout must be positive")
    if args.max_submit_gap_seconds <= 0:
        parser.error("--max-submit-gap-seconds must be positive")

    client = BridgeClient(args.bridge_url)
    health = client.get("/health")
    if health.get("status") not in {"ok", "healthy"}:
        raise LongRunError(f"bridge health is not healthy: {health!r}")
    status = client.get("/status")
    if int(status.get("queue_size", 0) or 0) != 0:
        raise LongRunError("live smoke requires an empty bridge queue before starting")

    run_id = f"lr168-smoke-{uuid.uuid4().hex}"
    chain_id = f"long-run-smoke-{uuid.uuid4().hex}"
    started_at = utc_now()
    started_monotonic = time.monotonic()

    evidence: dict[str, Any] = {
        "schema_version": 1,
        "milestone": "LONG_RUN_168H_LIVE_SMOKE",
        "run_id": run_id,
        "git_commit": git_head(),
        "bridge_version": health.get("version") or health.get("source_commit") or "unknown",
        "controller_version": health.get("controller_version") or "unknown",
        "started_at": started_at,
        "ended_at": "",
        "duration_seconds": 0.0,
        "policy": {
            "duration_seconds": args.duration_seconds,
            "minimum_operations": args.minimum_operations,
            "max_submit_gap_seconds": args.max_submit_gap_seconds,
        },
        "operations": [],
        "health_samples": [],
        "summary": {
            "completed": 0,
            "duplicates": 0,
            "skipped": 0,
            "terminal_chat_errors": 0,
        },
        "integrity": {"canonical_sha256": ""},
    }

    previous_operation_id: str | None = None
    sequence = 1
    print(
        "LONG-RUN SMOKE START: "
        f"run_id={run_id} duration={args.duration_seconds:.0f}s "
        f"minimum_operations={args.minimum_operations} "
        f"commit={evidence['git_commit']} output={args.output}",
        flush=True,
    )

    try:
        last_health_sample = 0.0
        while time.monotonic() - started_monotonic < args.duration_seconds:
            now_mono = time.monotonic()
            if now_mono - last_health_sample >= 5.0:
                live_health = browser_health(client)
                evidence["health_samples"].append(
                    {
                        "captured_at": utc_now(),
                        "chat_url": live_health.get("chat_url", ""),
                        "native_controller": live_health.get("native_controller"),
                        "heartbeat_age_seconds": live_health.get("heartbeat_age_seconds"),
                        "status": live_health.get("status", ""),
                    }
                )
                last_health_sample = now_mono

            ensure_chat_ready(
                client,
                run_id=run_id,
                thinking_timeout_seconds=args.operation_timeout,
                evidence={
                    "automation": {
                        "fresh_chat_events": []
                    }
                },
            )

            marker = f"PASI_LONGRUN_168H_SMOKE_{run_id[-10:]}_{sequence:04d}"
            prompt = (
                f"Reply with exactly this marker and no other text: {marker}. "
                "This is a PASI revised continuous Long-run smoke operation."
            )
            fingerprint = prompt_fingerprint(prompt)
            idempotency_key = f"{run_id}-{sequence:04d}"

            queued_at = utc_now()
            queued = queue_operation(
                client,
                prompt,
                run_id=run_id,
                idempotency_key=idempotency_key,
                chain_id=chain_id,
                sequence_index=sequence,
                predecessor_operation_id=previous_operation_id,
                marker=marker,
                recovery_probe=False,
            )
            operation_id = str(queued["operation_id"])
            final = wait_for_terminal(client, operation_id, args.operation_timeout)
            completed_at = utc_now()

            error = str(final.get("error") or "")
            if error.startswith("CHAT_"):
                evidence["summary"]["terminal_chat_errors"] += 1
                raise LongRunError(
                    f"terminal CHAT_* error on smoke operation {sequence}: {error}"
                )
            if final.get("status") != "completed":
                raise LongRunError(
                    f"smoke operation {sequence} ended as {final.get('status')!r}"
                )

            response_text = str(final.get("response_text") or "").strip()
            if response_text != marker:
                raise LongRunError(f"smoke operation {sequence} marker mismatch")

            timing = final.get("timing")
            if not isinstance(timing, dict):
                raise LongRunError(f"smoke operation {sequence} missing timing")

            replay = queue_operation(
                client,
                prompt,
                run_id=run_id,
                idempotency_key=idempotency_key,
                chain_id=chain_id,
                sequence_index=sequence,
                predecessor_operation_id=previous_operation_id,
                marker=marker,
                recovery_probe=False,
            )

            operation_record = {
                "operation_id": operation_id,
                "sequence_index": sequence,
                "marker": marker,
                "prompt_fingerprint": fingerprint,
                "idempotency_key": idempotency_key,
                "predecessor_operation_id": previous_operation_id,
                "queued_at": queued_at,
                "completed_at": completed_at,
                "status": final.get("status"),
                "response_text": response_text,
                "logical_execution_count": 1,
                "duplicate_execution_count": 0,
                "timing": timing,
                "idempotency_replay_operation_id": replay.get("operation_id"),
            }
            evidence["operations"].append(operation_record)
            evidence["summary"]["completed"] += 1
            previous_operation_id = operation_id

            print(
                "LONG-RUN SMOKE COMPLETE: "
                f"sequence={sequence} operation_id={operation_id} "
                f"elapsed={time.monotonic() - started_monotonic:.1f}s",
                flush=True,
            )
            sequence += 1

        evidence["ended_at"] = utc_now()
        evidence["duration_seconds"] = (
            datetime.fromisoformat(evidence["ended_at"].replace("Z", "+00:00"))
            - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ).total_seconds()
        verify_smoke_evidence(evidence)
        canonical = json.loads(json.dumps(evidence))
        canonical["integrity"]["canonical_sha256"] = ""
        evidence["integrity"]["canonical_sha256"] = sha256_json(canonical)
        write_json(args.output, evidence)
        verifier = ROOT / "scripts" / "verify_168h_live_smoke.py"
        verification = subprocess.run(
            [sys.executable, str(verifier), str(args.output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if verification.returncode != 0:
            raise LongRunError(
                "independent smoke verifier rejected evidence: "
                + (verification.stdout.strip() or verification.stderr.strip())
            )

        print(
            "LONG-RUN SMOKE PASS: "
            f"{evidence['summary']['completed']} operations in "
            f"{evidence['duration_seconds']:.1f}s with no duplicate/skipped operations",
            flush=True,
        )
        print(f"Evidence: {args.output}", flush=True)
        return 0
    except Exception as exc:
        evidence["ended_at"] = utc_now()
        evidence["duration_seconds"] = (
            datetime.fromisoformat(evidence["ended_at"].replace("Z", "+00:00"))
            - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ).total_seconds()
        write_json(args.output, evidence)
        print(f"LONG-RUN SMOKE FAIL: {exc}", file=sys.stderr, flush=True)
        print(f"Evidence: {args.output}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
