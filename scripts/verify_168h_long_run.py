#!/usr/bin/env python3
"""Independently verify PASI's 168-hour Long-run evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POLICY: dict[str, Any] = {
    "schema_version": 1,
    "target_duration_seconds": 604_800,
    "health_sample_interval_seconds": 300,
    "max_health_gap_seconds": 360,
    "max_heartbeat_age_seconds": 60,
    "expected_logical_operations": 168,
    "max_duplicate_logical_operations": 0,
    "max_skipped_operations": 0,
    "max_terminal_chat_errors": 0,
    "max_premature_claims": 0,
    "max_premature_injections": 0,
    "planned_recovery_count": 6,
    "recovery_schedule_seconds": [86_400, 172_800, 259_200, 345_600, 432_000, 518_400],
    "recovery_schedule_tolerance_seconds": 900,
    "max_recovery_latency_seconds": 300,
    "max_unplanned_recoveries": 3,
    "max_recovery_retries_per_logical_operation": 1,
    "max_rss_multiplier_95th": 2.0,
    "max_rss_multiplier_peak": 3.0,
    "max_rss_absolute_peak_bytes": 2 * 1024**3,
    "max_cpu_15m_average_percent": 95.0,
    "max_cpu_15m_p95_percent": 85.0,
    "max_runtime_disk_growth_bytes": 1 * 1024**3,
    "min_free_disk_absolute_bytes": 10 * 1024**3,
    "min_free_disk_fraction_of_start": 0.10,
    "max_fd_multiplier_peak": 2.0,
    "min_fd_peak_floor": 512,
    "fresh_chat_allowed_reasons": ["usage_limit", "context_limit"],
}


class VerificationError(RuntimeError):
    pass


def as_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VerificationError(f"{field} must be an object")
    return value


def as_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise VerificationError(f"{field} must be an array")
    return value


def timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise VerificationError(f"{field} must be an ISO-8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError(f"{field} is invalid: {value!r}") from exc
    if result.tzinfo is None:
        raise VerificationError(f"{field} must include a timezone")
    return result.astimezone(timezone.utc)


def verify_hash(evidence: dict[str, Any]) -> None:
    integrity = as_dict(evidence.get("integrity"), "integrity")
    supplied = integrity.get("canonical_sha256")
    if not isinstance(supplied, str) or len(supplied) != 64:
        raise VerificationError("invalid canonical_sha256")

    copy = json.loads(json.dumps(evidence))
    as_dict(copy.setdefault("integrity", {}), "integrity")["canonical_sha256"] = ""
    actual = hashlib.sha256(
        json.dumps(
            copy,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if actual != supplied:
        raise VerificationError("evidence integrity hash mismatch")


def p95(values: list[float]) -> float:
    if not values:
        raise VerificationError("cannot calculate percentile from empty list")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


def verify_duration(evidence: dict[str, Any]) -> tuple[datetime, datetime]:
    start = timestamp(evidence.get("started_at"), "started_at")
    end = timestamp(evidence.get("ended_at"), "ended_at")
    actual = (end - start).total_seconds()
    if actual < POLICY["target_duration_seconds"]:
        raise VerificationError(
            f"duration {actual:.3f}s is below {POLICY['target_duration_seconds']}s"
        )
    reported = evidence.get("duration_seconds")
    if not isinstance(reported, (int, float)) or abs(float(reported) - actual) > 1:
        raise VerificationError("duration_seconds disagrees with timestamps")
    return start, end


def verify_health(evidence: dict[str, Any], start: datetime, end: datetime) -> None:
    health = as_dict(evidence.get("health"), "health")
    samples = as_list(health.get("samples"), "health.samples")
    expected_min = math.ceil(
        POLICY["target_duration_seconds"] / POLICY["health_sample_interval_seconds"]
    )
    if len(samples) < expected_min:
        raise VerificationError(
            f"health samples {len(samples)} below required {expected_min}"
        )

    parsed: list[datetime] = []
    for index, raw in enumerate(samples):
        item = as_dict(raw, f"health.samples[{index}]")
        captured = timestamp(item.get("timestamp"), f"health.samples[{index}].timestamp")
        if not start <= captured <= end:
            raise VerificationError(f"health sample {index} outside run window")
        if item.get("bridge_status") != "ok":
            raise VerificationError(f"health sample {index} bridge unhealthy")
        if item.get("browser_status") not in {"healthy", "ok"}:
            raise VerificationError(f"health sample {index} browser unhealthy")
        heartbeat = item.get("heartbeat_age_seconds")
        if not isinstance(heartbeat, (int, float)):
            raise VerificationError(f"health sample {index} missing heartbeat age")
        if heartbeat > POLICY["max_heartbeat_age_seconds"]:
            raise VerificationError(f"health sample {index} heartbeat too old")
        parsed.append(captured)

    parsed.sort()
    if abs((parsed[0] - start).total_seconds()) > 60:
        raise VerificationError("first health sample is more than 60s after run start")
    if abs((end - parsed[-1]).total_seconds()) > 60:
        raise VerificationError("last health sample is more than 60s before run end")
    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(parsed, parsed[1:])
    ]
    max_gap = max(gaps) if gaps else 0
    if max_gap > POLICY["max_health_gap_seconds"]:
        raise VerificationError(f"health gap {max_gap:.1f}s exceeds policy")
    if health.get("failure_count") != 0:
        raise VerificationError("health failure_count is nonzero")

    reported_gap = health.get("max_gap_seconds")
    if not isinstance(reported_gap, (int, float)) or abs(float(reported_gap) - max_gap) > 1:
        raise VerificationError("health.max_gap_seconds does not match samples")


def verify_operations(evidence: dict[str, Any]) -> None:
    operations = as_list(evidence.get("operations"), "operations")
    if len(operations) != POLICY["expected_logical_operations"]:
        raise VerificationError("operation count is not exactly 168")

    ids: set[str] = set()
    keys: set[str] = set()
    markers: set[str] = set()
    fingerprints: set[str] = set()

    for expected_index, raw in enumerate(operations, start=1):
        item = as_dict(raw, f"operations[{expected_index - 1}]")
        operation_id = item.get("operation_id")
        idempotency_key = item.get("idempotency_key")
        marker = item.get("marker")
        fingerprint = item.get("prompt_fingerprint")

        for name, value, seen in (
            ("operation_id", operation_id, ids),
            ("idempotency_key", idempotency_key, keys),
            ("marker", marker, markers),
            ("prompt_fingerprint", fingerprint, fingerprints),
        ):
            if not isinstance(value, str) or not value:
                raise VerificationError(f"operation {expected_index} missing {name}")
            if value in seen:
                raise VerificationError(f"duplicate {name}: {value}")
            seen.add(value)

        if item.get("sequence_index") != expected_index:
            raise VerificationError(
                f"sequence index {item.get('sequence_index')!r} at position {expected_index}"
            )
        if item.get("status") != "completed":
            raise VerificationError(f"operation {expected_index} is not completed")
        if item.get("logical_execution_count") != 1:
            raise VerificationError(f"operation {expected_index} executed more than once")
        if item.get("duplicate_execution_count") != 0:
            raise VerificationError(f"operation {expected_index} has duplicate execution")
        terminal_error = item.get("terminal_chat_error")
        if isinstance(terminal_error, str) and terminal_error.startswith("CHAT_"):
            raise VerificationError(
                f"operation {expected_index} has terminal {terminal_error}"
            )

    for index, item in enumerate(operations):
        expected_predecessor = None if index == 0 else operations[index - 1]["operation_id"]
        if item.get("predecessor_operation_id") != expected_predecessor:
            raise VerificationError(
                f"operation {index + 1} predecessor checkpoint is incorrect"
            )


def verify_recoveries(evidence: dict[str, Any]) -> None:
    recoveries = as_list(evidence.get("recoveries"), "recoveries")
    planned = [item for item in recoveries if isinstance(item, dict) and item.get("planned") is True]
    unplanned = [item for item in recoveries if isinstance(item, dict) and item.get("planned") is False]

    if len(planned) != POLICY["planned_recovery_count"]:
        raise VerificationError(
            f"planned recovery count {len(planned)} != {POLICY['planned_recovery_count']}"
        )
    if len(unplanned) > POLICY["max_unplanned_recoveries"]:
        raise VerificationError("too many unplanned recoveries")

    previous_offset = -1
    for index, raw in enumerate(planned):
        item = as_dict(raw, f"planned recovery {index}")
        offset = item.get("scheduled_offset_seconds")
        expected = POLICY["recovery_schedule_seconds"][index]
        if not isinstance(offset, int):
            raise VerificationError("recovery offset is not an integer")
        if abs(offset - expected) > POLICY["recovery_schedule_tolerance_seconds"]:
            raise VerificationError(
                f"recovery {index + 1} occurred outside its 15-minute window"
            )
        if offset <= previous_offset:
            raise VerificationError("recovery schedule is not ordered")
        previous_offset = offset

        if item.get("same_operation_resumed") is not True:
            raise VerificationError("recovery changed operation identity")
        if item.get("checkpoint_preserved") is not True:
            raise VerificationError("recovery lost the checkpoint")
        if item.get("retry_count_delta") != 1:
            raise VerificationError("recovery retry delta is not exactly one")
        if item.get("logical_execution_count") != 1:
            raise VerificationError("recovery caused duplicate logical execution")
        if item.get("terminal_chat_errors") != 0:
            raise VerificationError("recovery produced terminal CHAT_* errors")
        latency = item.get("recovery_latency_seconds")
        if not isinstance(latency, (int, float)) or latency > POLICY["max_recovery_latency_seconds"]:
            raise VerificationError("recovery exceeded the 300-second bound")

        phases = item.get("phases")
        if not isinstance(phases, list):
            raise VerificationError("recovery has no phase list")
        required = {
            "connection_lost",
            "ready_for_retry",
            "controlled_probe_resume",
            "retry_resumed",
        }
        if not required.issubset(set(phases)):
            raise VerificationError("recovery phase chain is incomplete")


def verify_resources(evidence: dict[str, Any]) -> None:
    resources = as_dict(evidence.get("resources"), "resources")
    baseline = as_dict(resources.get("baseline"), "resources.baseline")
    samples = as_list(resources.get("samples"), "resources.samples")
    if not samples:
        raise VerificationError("no resource samples")
    expected_resource_samples = math.ceil(
        POLICY["target_duration_seconds"] / 60
    ) + 1
    if len(samples) < expected_resource_samples:
        raise VerificationError(
            f"resource sample count {len(samples)} below required "
            f"{expected_resource_samples}"
        )

    baseline_rss = int(baseline["rss_bytes"])
    baseline_fds = int(baseline["fd_count"])
    baseline_free = int(baseline["free_disk_bytes"])
    baseline_disk = int(baseline["runtime_disk_bytes"])

    rss = [float(as_dict(item, "resource sample")["rss_bytes"]) for item in samples]
    fds = [int(as_dict(item, "resource sample")["fd_count"]) for item in samples]
    free = [int(as_dict(item, "resource sample")["free_disk_bytes"]) for item in samples]
    disk = [int(as_dict(item, "resource sample")["runtime_disk_bytes"]) for item in samples]
    resource_times = [
        timestamp(as_dict(item, "resource sample").get("timestamp"), "resource.timestamp").timestamp()
        for item in samples
    ]
    resource_gaps = [
        later - earlier
        for earlier, later in zip(resource_times, resource_times[1:])
    ]
    if resource_gaps and max(resource_gaps) > 90:
        raise VerificationError("resource sample gap exceeds 90 seconds")

    cpu_samples = [
        (
            timestamp(as_dict(item, "resource sample").get("timestamp"), "resource.timestamp").timestamp(),
            float(as_dict(item, "resource sample")["cpu_percent"]),
        )
        for item in samples
    ]

    rss_peak = max(rss)
    rss_p95 = p95(rss)
    fd_peak = max(fds)
    disk_growth = max(disk) - baseline_disk
    free_min = min(free)

    cpu_windows: list[float] = []
    for end, _ in cpu_samples:
        values = [
            cpu for stamp, cpu in cpu_samples
            if end - 900 <= stamp <= end
        ]
        if values:
            cpu_windows.append(sum(values) / len(values))
    cpu_peak = max(cpu_windows) if cpu_windows else 0.0
    cpu_p95 = p95(cpu_windows)

    if rss_p95 > baseline_rss * POLICY["max_rss_multiplier_95th"]:
        raise VerificationError("RSS p95 exceeds 2x baseline")
    if rss_peak > min(
        baseline_rss * POLICY["max_rss_multiplier_peak"],
        POLICY["max_rss_absolute_peak_bytes"],
    ):
        raise VerificationError("RSS peak exceeds policy")
    if cpu_peak > POLICY["max_cpu_15m_average_percent"]:
        raise VerificationError("15-minute CPU average peak exceeds policy")
    if cpu_p95 > POLICY["max_cpu_15m_p95_percent"]:
        raise VerificationError("15-minute CPU p95 exceeds policy")
    if disk_growth > POLICY["max_runtime_disk_growth_bytes"]:
        raise VerificationError("runtime disk growth exceeds policy")

    minimum_allowed_free = max(
        POLICY["min_free_disk_absolute_bytes"],
        int(baseline_free * POLICY["min_free_disk_fraction_of_start"]),
    )
    if free_min < minimum_allowed_free:
        raise VerificationError("free disk fell below policy")

    allowed_fd_peak = max(
        int(baseline_fds * POLICY["max_fd_multiplier_peak"]),
        POLICY["min_fd_peak_floor"],
    )
    if fd_peak > allowed_fd_peak:
        raise VerificationError("file descriptor peak exceeds policy")

    expected = {
        "peak_rss_bytes": int(rss_peak),
        "rss_p95_bytes": int(rss_p95),
        "peak_cpu_15m_average_percent": cpu_peak,
        "cpu_15m_p95_percent": cpu_p95,
        "runtime_disk_growth_bytes": disk_growth,
        "minimum_free_disk_bytes": free_min,
        "peak_fd_count": fd_peak,
    }
    for key, value in expected.items():
        reported = resources.get(key)
        if isinstance(value, float):
            if not isinstance(reported, (int, float)) or abs(float(reported) - value) > 0.01:
                raise VerificationError(f"resources.{key} does not match raw samples")
        elif reported != value:
            raise VerificationError(f"resources.{key} does not match raw samples")

    if resources.get("sampling_failures") != 0:
        raise VerificationError("resource sampling failures were recorded")


def verify_automation(evidence: dict[str, Any]) -> None:
    automation = as_dict(evidence.get("automation"), "automation")
    if automation.get("thinking_state_violations") != 0:
        raise VerificationError("Thinking-state violation recorded")
    if automation.get("wrong_conversation_events") != 0:
        raise VerificationError("wrong-conversation event recorded")
    events = as_list(automation.get("fresh_chat_events"), "fresh_chat_events")
    if automation.get("fresh_chat_creations") != len(events):
        raise VerificationError("fresh-chat count does not match events")

    for index, raw in enumerate(events):
        event = as_dict(raw, f"fresh_chat_events[{index}]")
        if event.get("reason") not in POLICY["fresh_chat_allowed_reasons"]:
            raise VerificationError("unauthorized fresh-chat reason")
        if event.get("thinking_verified_after") is not True:
            raise VerificationError("Thinking not verified after fresh chat")


def verify_summary(evidence: dict[str, Any]) -> None:
    summary = as_dict(evidence.get("summary"), "summary")
    exact_zeroes = (
        ("duplicate_logical_operations", 0),
        ("skipped_operations", 0),
        ("terminal_chat_errors", 0),
        ("premature_claims", 0),
        ("premature_injections", 0),
    )
    for key, value in exact_zeroes:
        if summary.get(key) != value:
            raise VerificationError(f"summary.{key}={summary.get(key)!r}; expected {value}")
    if summary.get("logical_operations_completed") != POLICY["expected_logical_operations"]:
        raise VerificationError("summary logical operation count is not 168")
    if summary.get("planned_recoveries") != POLICY["planned_recovery_count"]:
        raise VerificationError("summary planned recovery count is incorrect")
    if summary.get("successful_planned_recoveries") != POLICY["planned_recovery_count"]:
        raise VerificationError("not all planned recoveries succeeded")
    if not isinstance(summary.get("unplanned_recoveries"), int):
        raise VerificationError("summary.unplanned_recoveries is invalid")
    if summary["unplanned_recoveries"] > POLICY["max_unplanned_recoveries"]:
        raise VerificationError("too many unplanned recoveries")


def verify_evidence(evidence: dict[str, Any]) -> None:
    if evidence.get("schema_version") != POLICY["schema_version"]:
        raise VerificationError("unsupported schema version")
    if evidence.get("milestone") != "LONG_RUN_168H":
        raise VerificationError("wrong milestone")
    if evidence.get("policy") != POLICY:
        raise VerificationError("evidence policy does not match pinned policy")
    if as_dict(evidence.get("final"), "final").get("status") != "PASS":
        raise VerificationError("final evidence status is not PASS")

    start, end = verify_duration(evidence)
    verify_hash(evidence)
    verify_health(evidence, start, end)
    verify_operations(evidence)
    verify_recoveries(evidence)
    verify_resources(evidence)
    verify_automation(evidence)
    verify_summary(evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PASI 168-hour Long-run evidence.")
    parser.add_argument(
        "evidence",
        nargs="?",
        type=Path,
        default=Path(".runtime/acceptance/long-run-168h.json"),
    )
    args = parser.parse_args()

    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
        evidence = as_dict(payload, "evidence")
        verify_evidence(evidence)
    except (OSError, json.JSONDecodeError, VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"LONG-RUN FAIL: {exc}")
        return 1

    print(
        "LONG-RUN PASS: 168 hours elapsed; "
        "health, workload, recovery, automation, resource, and integrity checks passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
