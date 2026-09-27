#!/usr/bin/env python3
"""Run the deterministic PASI 168-hour unattended Long-run acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
DEFAULT_DURATION_SECONDS = 604_800
DEFAULT_INTERVAL_SECONDS = 3_600
DEFAULT_OPERATION_COUNT = 168
DEFAULT_OPERATION_TIMEOUT_SECONDS = 900.0
POLL_SECONDS = 2.0
HEALTH_INTERVAL_SECONDS = 300
RESOURCE_INTERVAL_SECONDS = 60
MAX_RESPONSE_BYTES = 2_000_000
M1_CHECKPOINT_SCHEMA_VERSION = 1
M2_RECOVERY_SCHEMA_VERSION = 1
RUNTIME_TOKEN = ROOT / ".runtime" / "bridge-token"
ACCEPTANCE_DIR = ROOT / ".runtime" / "acceptance"
PARTIAL_EVIDENCE = ACCEPTANCE_DIR / "long-run-168h.partial.json"
PERSIST_LOCK = threading.Lock()
FINAL_EVIDENCE = ACCEPTANCE_DIR / "long-run-168h.json"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

PLANNED_RECOVERY_OFFSETS = (
    86_400,
    172_800,
    259_200,
    345_600,
    432_000,
    518_400,
)
RECOVERY_TOLERANCE_SECONDS = 900
MAX_RECOVERY_LATENCY_SECONDS = 300
MAX_UNPLANNED_RECOVERIES = 3
MAX_RECOVERY_RETRIES = 1

MAX_RSS_P95_MULTIPLIER = 2.0
MAX_RSS_PEAK_MULTIPLIER = 3.0
MAX_RSS_ABSOLUTE_BYTES = 2 * 1024**3
MAX_CPU_15M_AVERAGE_PERCENT = 95.0
MAX_CPU_15M_P95_PERCENT = 85.0
MAX_RUNTIME_DISK_GROWTH_BYTES = 1 * 1024**3
MIN_FREE_DISK_ABSOLUTE_BYTES = 10 * 1024**3
MIN_FREE_DISK_FRACTION = 0.10
MAX_FD_MULTIPLIER = 2.0
MIN_FD_PEAK = 512


class LongRunError(RuntimeError):
    """Raised when the 168-hour acceptance contract cannot be satisfied."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(evidence: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(evidence))
    integrity = copy.setdefault("integrity", {})
    integrity["canonical_sha256"] = ""
    payload = json.dumps(
        copy,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def commit_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise LongRunError(f"invalid git commit: {value!r}")
    return value


class BridgeClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is None
        ):
            raise ValueError(
                "bridge must use localhost HTTP, for example http://127.0.0.1:8765"
            )
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.token = os.environ.get("PASI_BRIDGE_TOKEN", "").strip()
        if not self.token:
            try:
                self.token = RUNTIME_TOKEN.read_text(encoding="utf-8").strip()
            except OSError:
                self.token = ""
        if not self.token:
            try:
                self.token = (
                    Path.home() / ".pasi" / "bridge-token"
                ).read_text(encoding="utf-8").strip()
            except OSError:
                self.token = ""
        if not self.token:
            raise LongRunError(
                "PASI bridge token is missing; provision .runtime/bridge-token first"
            )

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            detail = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
            raise LongRunError(f"bridge HTTP {exc.code}: {detail[:600]}") from exc
        except URLError as exc:
            raise LongRunError(f"bridge request failed: {exc.reason}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise LongRunError("bridge response exceeded configured bound")

        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LongRunError("bridge returned invalid JSON") from exc

        if not isinstance(result, dict):
            raise LongRunError("bridge JSON response must be an object")
        return result

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(
        " ".join(prompt.split()).strip().encode("utf-8")
    ).hexdigest()


def operation(client: BridgeClient, operation_id: str) -> dict[str, Any]:
    payload = client.get(
        f"/operation?operation_id={quote(operation_id, safe='')}"
    )
    value = payload.get("operation")
    if not isinstance(value, dict):
        raise LongRunError(f"operation {operation_id} missing from bridge")
    return value


def observation_data(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    data = value.get("data")
    return data if isinstance(data, dict) else value


def browser_health(client: BridgeClient) -> dict[str, Any]:
    payload = client.get("/browser/health")
    return observation_data(payload.get("observation"))


def browser_state(client: BridgeClient) -> dict[str, Any]:
    payload = client.get("/browser/state")
    return observation_data(payload.get("observation"))


def chat_limit_reason(health: dict[str, Any]) -> str:
    if (
        health.get("conversation_context_exhausted") is True
        or health.get("chat_exhausted") is True
    ):
        return "context_limit"
    if health.get("provider_usage_limited") is True:
        return "usage_limit"
    return ""


def wait_for_terminal(
    client: BridgeClient,
    operation_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = operation(client, operation_id)
        status = str(current.get("status", ""))
        if status in TERMINAL_STATUSES:
            return current
        time.sleep(POLL_SECONDS)
    raise LongRunError(
        f"operation {operation_id} did not reach terminal state within "
        f"{timeout_seconds:.1f}s"
    )


def queue_operation(
    client: BridgeClient,
    prompt: str,
    *,
    run_id: str,
    idempotency_key: str,
    chain_id: str,
    sequence_index: int,
    predecessor_operation_id: str | None,
    marker: str,
    recovery_probe: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operation_type": "prompt",
        "prompt": prompt,
        "idempotency_key": idempotency_key,
        "completion_markers": [marker],
        "chain_id": chain_id,
        "sequence_index": sequence_index,
        "m2_recovery_probe": recovery_probe,
    }
    if predecessor_operation_id is not None:
        body["predecessor_operation_id"] = predecessor_operation_id

    payload = client.post("/queue", body)
    value = payload.get("operation")
    if not isinstance(value, dict) or not value.get("operation_id"):
        raise LongRunError("bridge did not return the queued operation")

    if (
        value.get("chain_id") != chain_id
        or value.get("sequence_index") != sequence_index
        or value.get("predecessor_operation_id") != predecessor_operation_id
        or value.get("prompt_fingerprint") != prompt_fingerprint(prompt)
    ):
        raise LongRunError(
            f"operation {sequence_index} did not preserve authoritative chain metadata"
        )
    return value


def queue_auxiliary(
    client: BridgeClient,
    operation_type: str,
    *,
    run_id: str,
    suffix: str,
) -> dict[str, Any]:
    payload = client.post(
        "/queue",
        {
            "operation_type": operation_type,
            "prompt": "",
            "idempotency_key": f"{run_id}-{suffix}",
        },
    )
    value = payload.get("operation")
    if not isinstance(value, dict) or not value.get("operation_id"):
        raise LongRunError(f"{operation_type} did not return an operation")
    return value


def ensure_chat_ready(
    client: BridgeClient,
    *,
    run_id: str,
    thinking_timeout_seconds: float,
    evidence: dict[str, Any],
) -> None:
    health = browser_health(client)
    if health.get("native_controller") is not True:
        raise LongRunError("browser health does not identify the native controller")

    reason = chat_limit_reason(health)
    if reason:
        event_number = len(evidence["automation"]["fresh_chat_events"]) + 1
        thinking_before = health.get("thinking_enabled") is True
        if not thinking_before:
            select = queue_auxiliary(
                client,
                "select_reasoning",
                run_id=run_id,
                suffix=f"pre-fresh-thinking-{event_number}",
            )
            select_id = str(select["operation_id"])
            select_result = wait_for_terminal(
                client,
                select_id,
                thinking_timeout_seconds,
            )
            if select_result.get("status") != "completed":
                raise LongRunError(
                    "Thinking-selection operation failed before fresh-chat recovery"
                )
            deadline = time.monotonic() + thinking_timeout_seconds
            while time.monotonic() < deadline:
                health = browser_health(client)
                if health.get("thinking_enabled") is True:
                    thinking_before = True
                    break
                time.sleep(POLL_SECONDS)
        if not thinking_before:
            raise LongRunError(
                "Thinking was not verified before usage/context fresh-chat recovery"
            )

        fresh = queue_auxiliary(
            client,
            "new_chat",
            run_id=run_id,
            suffix=f"new-chat-{event_number}",
        )
        fresh_id = str(fresh["operation_id"])
        result = wait_for_terminal(
            client,
            fresh_id,
            thinking_timeout_seconds,
        )
        if result.get("status") != "completed":
            raise LongRunError(
                f"fresh-chat operation did not complete: {result.get('error')!r}"
            )

        event = {
            "timestamp": utc_now(),
            "reason": reason,
            "fresh_chat_operation_id": fresh_id,
            "thinking_verified_before": thinking_before,
            "thinking_verified_after": False,
        }

        health = browser_health(client)
        if health.get("thinking_enabled") is not True:
            select = queue_auxiliary(
                client,
                "select_reasoning",
                run_id=run_id,
                suffix=f"select-thinking-{len(evidence['automation']['fresh_chat_events']) + 1}",
            )
            select_id = str(select["operation_id"])
            select_result = wait_for_terminal(
                client,
                select_id,
                thinking_timeout_seconds,
            )
            if select_result.get("status") != "completed":
                raise LongRunError(
                    "Thinking-selection operation failed after fresh-chat recovery"
                )

        deadline = time.monotonic() + thinking_timeout_seconds
        while time.monotonic() < deadline:
            health = browser_health(client)
            if health.get("thinking_enabled") is True:
                event["thinking_verified_after"] = True
                break
            time.sleep(POLL_SECONDS)

        if event["thinking_verified_after"] is not True:
            raise LongRunError("Thinking was not re-verified after fresh-chat recovery")

        evidence["automation"]["fresh_chat_events"].append(event)
        evidence["automation"]["fresh_chat_creations"] += 1
        return

    if health.get("thinking_enabled") is True:
        return

    select = queue_auxiliary(
        client,
        "select_reasoning",
        run_id=run_id,
        suffix=f"select-thinking-{uuid.uuid4().hex}",
    )
    select_id = str(select["operation_id"])
    select_result = wait_for_terminal(
        client,
        select_id,
        thinking_timeout_seconds,
    )
    if select_result.get("status") != "completed":
        raise LongRunError(
            f"Thinking-selection operation failed: {select_result.get('error')!r}"
        )

    deadline = time.monotonic() + thinking_timeout_seconds
    while time.monotonic() < deadline:
        health = browser_health(client)
        if health.get("thinking_enabled") is True:
            return
        time.sleep(POLL_SECONDS)

    raise LongRunError("Thinking was not verified as enabled")


def parse_pid_stat(pid: int) -> tuple[int, int, int] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    closing = raw.rfind(")")
    if closing < 0:
        return None
    fields = raw[closing + 2 :].split()
    if len(fields) < 13:
        return None
    try:
        state = fields[0]
        ppid = int(fields[1])
        utime = int(fields[11])
        stime = int(fields[12])
    except (ValueError, IndexError):
        return None
    return ppid, utime, stime


def discover_process_tree(roots: set[int]) -> set[int]:
    children: dict[int, set[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        stat = parse_pid_stat(pid)
        if stat is None:
            continue
        children.setdefault(stat[0], set()).add(pid)

    result = set(roots)
    pending = list(roots)
    while pending:
        parent = pending.pop()
        for child in children.get(parent, set()):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def discover_bridge_pids() -> set[int]:
    result: set[int] = set()
    explicit = os.environ.get("PASI_BRIDGE_PID", "").strip()
    if explicit:
        try:
            result.add(int(explicit))
        except ValueError as exc:
            raise LongRunError("PASI_BRIDGE_PID must be an integer") from exc

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        cmdline_path = entry / "cmdline"
        try:
            raw = cmdline_path.read_bytes().replace(b"\x00", b" ")
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        if "scripts/run_bridge.py" in text:
            result.add(int(entry.name))
    return result


class ResourceSampler:
    """Linux /proc sampler for the runner plus the PASI bridge process tree."""

    def __init__(self, root_pids: set[int]) -> None:
        if sys.platform != "linux":
            raise LongRunError("the long-run resource sampler requires Linux/WSL /proc")
        self.root_pids = set(root_pids)
        self.clock_ticks = int(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        self.last_cpu_ticks: int | None = None
        self.last_monotonic: float | None = None

    def sample(self) -> dict[str, Any]:
        pids = discover_process_tree(self.root_pids)
        total_rss = 0
        total_cpu_ticks = 0
        total_fds = 0

        for pid in pids:
            try:
                rss_text = Path(f"/proc/{pid}/status").read_text(
                    encoding="utf-8"
                )
            except OSError:
                continue

            for line in rss_text.splitlines():
                if line.startswith("VmRSS:"):
                    try:
                        total_rss += int(line.split()[1]) * 1024
                    except (ValueError, IndexError):
                        pass
                    break

            stat = parse_pid_stat(pid)
            if stat is not None:
                total_cpu_ticks += stat[1] + stat[2]

            try:
                total_fds += len(list((Path(f"/proc/{pid}") / "fd").iterdir()))
            except OSError:
                pass

        now = time.monotonic()
        cpu_percent = 0.0
        if self.last_cpu_ticks is not None and self.last_monotonic is not None:
            cpu_delta = (
                total_cpu_ticks - self.last_cpu_ticks
            ) / self.clock_ticks
            wall_delta = max(0.001, now - self.last_monotonic)
            cpu_percent = max(0.0, cpu_delta / wall_delta * 100.0)

        self.last_cpu_ticks = total_cpu_ticks
        self.last_monotonic = now

        usage = os.statvfs(ROOT)
        free_disk = usage.f_bavail * usage.f_frsize

        runtime_disk = 0
        for path in (ROOT / ".runtime",):
            if not path.exists():
                continue
            for child in path.rglob("*"):
                try:
                    if child.is_file():
                        runtime_disk += child.stat().st_size
                except OSError:
                    continue

        return {
            "timestamp": utc_now(),
            "pid_count": len(pids),
            "tracked_pids": sorted(pids),
            "rss_bytes": total_rss,
            "cpu_percent": cpu_percent,
            "runtime_disk_bytes": runtime_disk,
            "free_disk_bytes": free_disk,
            "fd_count": total_fds,
        }


class TelemetrySampler:
    def __init__(
        self,
        client: BridgeClient,
        evidence: dict[str, Any],
        operation_id_getter,
        resource_sampler: ResourceSampler,
    ) -> None:
        self.client = client
        self.evidence = evidence
        self.operation_id_getter = operation_id_getter
        self.resource_sampler = resource_sampler
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="pasi-long-run-telemetry",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def capture(self) -> None:
        operation_id = self.operation_id_getter()
        try:
            health = browser_health(self.client)
            browser_status = (
                "healthy"
                if health.get("native_controller") is True
                and str(health.get("chat_url") or "").startswith(
                    "https://chatgpt.com/c/"
                )
                else "unhealthy"
            )
            heartbeat_age = health.get("heartbeat_age")
            if not isinstance(heartbeat_age, (int, float)):
                heartbeat_age = health.get("heartbeat_age_seconds")
            if not isinstance(heartbeat_age, (int, float)):
                heartbeat_age = 999999.0

            self.evidence["health"]["samples"].append(
                {
                    "timestamp": utc_now(),
                    "bridge_status": "ok",
                    "browser_status": browser_status,
                    "heartbeat_age_seconds": float(heartbeat_age),
                    "operation_id": operation_id,
                    "phase": str(health.get("phase") or ""),
                }
            )

            if browser_status != "healthy":
                self.evidence["health"]["failure_count"] += 1
        except Exception as exc:
            self.evidence["health"]["failure_count"] += 1
            self.evidence["health"]["samples"].append(
                {
                    "timestamp": utc_now(),
                    "bridge_status": "error",
                    "browser_status": "error",
                    "heartbeat_age_seconds": 999999.0,
                    "operation_id": operation_id,
                    "phase": "health_error",
                    "error": str(exc),
                }
            )

        try:
            self.evidence["resources"]["samples"].append(
                self.resource_sampler.sample()
            )
        except Exception as exc:
            self.evidence["resources"]["sampling_failures"] += 1
            self.evidence["resources"]["sampling_errors"].append(
                {"timestamp": utc_now(), "error": str(exc)}
            )

    def _run(self) -> None:
        next_health = time.monotonic()
        next_resource = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_health:
                self.capture_health()
                next_health = now + HEALTH_INTERVAL_SECONDS
            captured = False
            if now >= next_health:
                self.capture_health()
                next_health = now + HEALTH_INTERVAL_SECONDS
                captured = True
            if now >= next_resource:
                self.capture_resource()
                next_resource = now + RESOURCE_INTERVAL_SECONDS
                captured = True
            if captured:
                persist_partial(self.evidence)
            self.stop_event.wait(1.0)

    def capture_health(self) -> None:
        operation_id = self.operation_id_getter()
        try:
            health = browser_health(self.client)
            browser_status = (
                "healthy"
                if health.get("native_controller") is True
                and str(health.get("chat_url") or "").startswith(
                    "https://chatgpt.com/c/"
                )
                else "unhealthy"
            )
            heartbeat_age = health.get("heartbeat_age")
            if not isinstance(heartbeat_age, (int, float)):
                heartbeat_age = health.get("heartbeat_age_seconds")
            if not isinstance(heartbeat_age, (int, float)):
                heartbeat_age = 999999.0

            self.evidence["health"]["samples"].append(
                {
                    "timestamp": utc_now(),
                    "bridge_status": "ok",
                    "browser_status": browser_status,
                    "heartbeat_age_seconds": float(heartbeat_age),
                    "operation_id": operation_id,
                    "phase": str(health.get("phase") or ""),
                }
            )
            if browser_status != "healthy":
                self.evidence["health"]["failure_count"] += 1
        except Exception as exc:
            self.evidence["health"]["failure_count"] += 1
            self.evidence["health"]["samples"].append(
                {
                    "timestamp": utc_now(),
                    "bridge_status": "error",
                    "browser_status": "error",
                    "heartbeat_age_seconds": 999999.0,
                    "operation_id": operation_id,
                    "phase": "health_error",
                    "error": str(exc),
                }
            )

    def capture_resource(self) -> None:
        try:
            self.evidence["resources"]["samples"].append(
                self.resource_sampler.sample()
            )
        except Exception as exc:
            self.evidence["resources"]["sampling_failures"] += 1
            self.evidence["resources"]["sampling_errors"].append(
                {"timestamp": utc_now(), "error": str(exc)}
            )

    def capture(self) -> None:
        self.capture_health()
        self.capture_resource()


def recovery_phases(operation_state: dict[str, Any]) -> list[str]:
    events = operation_state.get("recovery_events")
    if not isinstance(events, list):
        return []
    return [
        str(event.get("phase", ""))
        for event in events
        if isinstance(event, dict)
    ]


def validate_recovery(
    operation_state: dict[str, Any],
    *,
    operation_id: str,
) -> dict[str, Any]:
    events = operation_state.get("recovery_events")
    if not isinstance(events, list):
        raise LongRunError(
            f"recovery operation {operation_id} has no recovery_events"
        )

    event_ids = {
        str(event.get("operation_id"))
        for event in events
        if isinstance(event, dict) and isinstance(event.get("operation_id"), str)
    }
    if event_ids and event_ids != {operation_id}:
        raise LongRunError(
            f"recovery operation {operation_id} changed operation identity"
        )

    phases = recovery_phases(operation_state)
    required = {
        "connection_lost",
        "ready_for_retry",
        "controlled_probe_resume",
        "retry_resumed",
    }
    missing = sorted(required.difference(phases))
    if missing:
        raise LongRunError(
            f"recovery operation {operation_id} is missing phases {missing!r}"
        )

    if phases.count("connection_lost") != 1:
        raise LongRunError(
            f"recovery operation {operation_id} has "
            f"{phases.count('connection_lost')} connection_lost events"
        )

    retry_count = int(operation_state.get("retry_count", 0) or 0)
    if retry_count != MAX_RECOVERY_RETRIES:
        raise LongRunError(
            f"recovery operation {operation_id} retry_count={retry_count}"
        )

    retry_counts = operation_state.get("retry_counts")
    if not isinstance(retry_counts, dict):
        raise LongRunError(
            f"recovery operation {operation_id} has no retry_counts"
        )

    normalized = {
        key: int(retry_counts.get(key, 0) or 0)
        for key in ("controller", "response", "context")
    }
    if normalized != {"controller": 1, "response": 0, "context": 0}:
        raise LongRunError(
            f"recovery operation {operation_id} has invalid retry_counts={normalized!r}"
        )

    for event in events:
        if not isinstance(event, dict):
            continue
        for key in ("error", "controller_error"):
            value = event.get(key)
            if isinstance(value, str) and value.startswith("CHAT_"):
                raise LongRunError(
                    f"recovery operation {operation_id} recorded terminal {value}"
                )

    return {
        "operation_id": operation_id,
        "same_operation_resumed": True,
        "checkpoint_preserved": True,
        "retry_count_delta": 1,
        "logical_execution_count": 1,
        "terminal_chat_errors": 0,
        "phases": phases,
        "event_count": len(events),
    }


def build_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "target_duration_seconds": DEFAULT_DURATION_SECONDS,
        "health_sample_interval_seconds": HEALTH_INTERVAL_SECONDS,
        "max_health_gap_seconds": 360,
        "max_heartbeat_age_seconds": 60,
        "expected_logical_operations": DEFAULT_OPERATION_COUNT,
        "max_duplicate_logical_operations": 0,
        "max_skipped_operations": 0,
        "max_terminal_chat_errors": 0,
        "max_premature_claims": 0,
        "max_premature_injections": 0,
        "planned_recovery_count": len(PLANNED_RECOVERY_OFFSETS),
        "recovery_schedule_seconds": list(PLANNED_RECOVERY_OFFSETS),
        "recovery_schedule_tolerance_seconds": RECOVERY_TOLERANCE_SECONDS,
        "max_recovery_latency_seconds": MAX_RECOVERY_LATENCY_SECONDS,
        "max_unplanned_recoveries": MAX_UNPLANNED_RECOVERIES,
        "max_recovery_retries_per_logical_operation": MAX_RECOVERY_RETRIES,
        "max_rss_multiplier_95th": MAX_RSS_P95_MULTIPLIER,
        "max_rss_multiplier_peak": MAX_RSS_PEAK_MULTIPLIER,
        "max_rss_absolute_peak_bytes": MAX_RSS_ABSOLUTE_BYTES,
        "max_cpu_15m_average_percent": MAX_CPU_15M_AVERAGE_PERCENT,
        "max_cpu_15m_p95_percent": MAX_CPU_15M_P95_PERCENT,
        "max_runtime_disk_growth_bytes": MAX_RUNTIME_DISK_GROWTH_BYTES,
        "min_free_disk_absolute_bytes": MIN_FREE_DISK_ABSOLUTE_BYTES,
        "min_free_disk_fraction_of_start": MIN_FREE_DISK_FRACTION,
        "max_fd_multiplier_peak": MAX_FD_MULTIPLIER,
        "min_fd_peak_floor": MIN_FD_PEAK,
        "fresh_chat_allowed_reasons": ["usage_limit", "context_limit"],
    }


def summarize_resources(
    evidence: dict[str, Any],
) -> None:
    samples = evidence["resources"]["samples"]
    baseline = evidence["resources"]["baseline"]
    if not samples:
        raise LongRunError("no resource samples collected")

    rss = [float(item["rss_bytes"]) for item in samples]
    fds = [int(item["fd_count"]) for item in samples]
    free_disk = [int(item["free_disk_bytes"]) for item in samples]
    runtime_disk = [int(item["runtime_disk_bytes"]) for item in samples]

    ordered_rss = sorted(rss)
    index = max(0, min(len(ordered_rss) - 1, int(len(ordered_rss) * 0.95) - 1))
    rss_p95 = ordered_rss[index]
    rss_peak = max(rss)
    fd_peak = max(fds)
    disk_growth = max(runtime_disk) - int(baseline["runtime_disk_bytes"])
    minimum_free = min(free_disk)

    cpu_windows: list[float] = []
    cpu_samples = [
        (
            datetime.fromisoformat(
                str(item["timestamp"]).replace("Z", "+00:00")
            ).timestamp(),
            float(item["cpu_percent"]),
        )
        for item in samples
    ]
    for end, _ in cpu_samples:
        values = [
            cpu
            for timestamp, cpu in cpu_samples
            if end - 900 <= timestamp <= end
        ]
        if values:
            cpu_windows.append(sum(values) / len(values))

    cpu_peak = max(cpu_windows) if cpu_windows else 0.0
    ordered_cpu = sorted(cpu_windows)
    cpu_index = max(
        0,
        min(len(ordered_cpu) - 1, int(len(ordered_cpu) * 0.95) - 1),
    )
    cpu_p95 = ordered_cpu[cpu_index]

    evidence["resources"]["peak_rss_bytes"] = int(rss_peak)
    evidence["resources"]["rss_p95_bytes"] = int(rss_p95)
    evidence["resources"]["peak_cpu_15m_average_percent"] = cpu_peak
    evidence["resources"]["cpu_15m_p95_percent"] = cpu_p95
    evidence["resources"]["runtime_disk_growth_bytes"] = disk_growth
    evidence["resources"]["minimum_free_disk_bytes"] = minimum_free
    evidence["resources"]["peak_fd_count"] = fd_peak


def persist_partial(evidence: dict[str, Any]) -> None:
    with PERSIST_LOCK:
        write_json_atomic(PARTIAL_EVIDENCE, evidence)


def persist_final(evidence: dict[str, Any], status: str, failures: list[str]) -> None:
    evidence["final"] = {
        "status": status,
        "verification_timestamp": utc_now(),
        "failures": failures,
    }
    evidence["integrity"] = {"canonical_sha256": ""}
    evidence["integrity"]["canonical_sha256"] = canonical_hash(evidence)
    write_json_atomic(FINAL_EVIDENCE, evidence)


def validate_finished_evidence(
    evidence: dict[str, Any],
    *,
    started: datetime,
    ended: datetime,
) -> None:
    duration = (ended - started).total_seconds()
    if duration < DEFAULT_DURATION_SECONDS:
        raise LongRunError("168-hour duration requirement not satisfied")

    if evidence["summary"]["logical_operations_completed"] != DEFAULT_OPERATION_COUNT:
        raise LongRunError("logical operation count is not exactly 168")

    if evidence["summary"]["duplicate_logical_operations"] != 0:
        raise LongRunError("duplicate logical execution detected")

    if evidence["summary"]["skipped_operations"] != 0:
        raise LongRunError("skipped operation detected")

    if evidence["summary"]["terminal_chat_errors"] != 0:
        raise LongRunError("terminal CHAT_* error detected")

    if (
        evidence["summary"]["successful_planned_recoveries"]
        != len(PLANNED_RECOVERY_OFFSETS)
    ):
        raise LongRunError("not all planned recoveries succeeded")

    if evidence["resources"]["sampling_failures"] != 0:
        raise LongRunError("resource sampling failures were recorded")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run PASI's deterministic 168-hour unattended acceptance."
    )
    parser.add_argument(
        "--bridge-url",
        default=os.environ.get("PASI_BRIDGE_URL", DEFAULT_BRIDGE_URL),
    )
    parser.add_argument(
        "--operation-timeout",
        type=float,
        default=DEFAULT_OPERATION_TIMEOUT_SECONDS,
    )
    args = parser.parse_args()

    if args.operation_timeout <= 0:
        parser.error("--operation-timeout must be positive")

    run_id = f"lr168-{uuid.uuid4().hex}"
    started_at = utc_now()
    started_dt = datetime.fromisoformat(started_at)
    chain_id = f"long-run-{uuid.uuid4().hex}"

    client = BridgeClient(args.bridge_url)
    current_operation_lock = threading.Lock()
    current_operation_id: str | None = None

    def get_current_operation_id() -> str | None:
        with current_operation_lock:
            return current_operation_id

    evidence: dict[str, Any] = {
        "schema_version": 1,
        "milestone": "LONG_RUN_168H",
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": None,
        "duration_seconds": 0,
        "git_commit": commit_sha(),
        "bridge_version": None,
        "controller_version": None,
        "policy": build_policy(),
        "health": {
            "sample_interval_seconds": HEALTH_INTERVAL_SECONDS,
            "samples": [],
            "failure_count": 0,
            "max_gap_seconds": 0,
            "max_heartbeat_age_seconds": 0,
        },
        "operations": [],
        "recoveries": [],
        "resources": {
            "baseline": None,
            "samples": [],
            "sampling_failures": 0,
            "sampling_errors": [],
        },
        "automation": {
            "fresh_chat_creations": 0,
            "fresh_chat_events": [],
            "thinking_state_violations": 0,
            "wrong_conversation_events": 0,
        },
        "summary": {
            "logical_operations_completed": 0,
            "duplicate_logical_operations": 0,
            "skipped_operations": 0,
            "terminal_chat_errors": 0,
            "premature_claims": 0,
            "premature_injections": 0,
            "planned_recoveries": len(PLANNED_RECOVERY_OFFSETS),
            "successful_planned_recoveries": 0,
            "unplanned_recoveries": 0,
        },
        "integrity": {"canonical_sha256": ""},
        "final": {
            "status": "RUNNING",
            "verification_timestamp": started_at,
            "failures": [],
        },
    }

    bridge_health = client.get("/health")
    if bridge_health.get("status") not in {"ok", "healthy"}:
        raise LongRunError(f"bridge health is not healthy: {bridge_health!r}")
    if bridge_health.get("m1_checkpoint_schema_version") != M1_CHECKPOINT_SCHEMA_VERSION:
        raise LongRunError("M1 checkpoint schema is stale")
    if bridge_health.get("m2_recovery_schema_version") != M2_RECOVERY_SCHEMA_VERSION:
        raise LongRunError("M2 recovery schema is stale")

    evidence["bridge_version"] = (
        bridge_health.get("version")
        or bridge_health.get("source_commit")
        or "unknown"
    )
    evidence["controller_version"] = (
        bridge_health.get("controller_version")
        or "unknown"
    )

    status = client.get("/status")
    if int(status.get("queue_size", 0) or 0) != 0:
        raise LongRunError(
            "168-hour run requires an empty bridge queue before starting"
        )

    runner_pid = os.getpid()
    bridge_pids = discover_bridge_pids()
    resource_sampler = ResourceSampler({runner_pid, *bridge_pids})
    baseline_resource = resource_sampler.sample()
    evidence["resources"]["baseline"] = {
        key: baseline_resource[key]
        for key in (
            "timestamp",
            "rss_bytes",
            "cpu_percent",
            "runtime_disk_bytes",
            "free_disk_bytes",
            "fd_count",
        )
    }
    evidence["resources"]["samples"].append(baseline_resource)

    telemetry = TelemetrySampler(
        client,
        evidence,
        get_current_operation_id,
        resource_sampler,
    )
    telemetry.capture()
    telemetry.start()
    persist_partial(evidence)

    previous_operation_id: str | None = None
    seen_operation_ids: set[str] = set()
    seen_idempotency: set[str] = set()
    seen_markers: set[str] = set()
    seen_fingerprints: set[str] = set()

    try:
        monotonic_start = time.monotonic()
        for sequence_index in range(1, DEFAULT_OPERATION_COUNT + 1):
            target_offset = (sequence_index - 1) * DEFAULT_INTERVAL_SECONDS
            wait_until = monotonic_start + target_offset
            while time.monotonic() < wait_until:
                time.sleep(min(5.0, max(0.1, wait_until - time.monotonic())))

            elapsed_before_queue = time.monotonic() - monotonic_start
            ensure_chat_ready(
                client,
                run_id=run_id,
                thinking_timeout_seconds=args.operation_timeout,
                evidence=evidence,
            )

            marker = f"PASI_LONGRUN_168H_{run_id[-10:]}_{sequence_index:03d}"
            prompt = (
                f"Reply with exactly this marker and no other text: {marker}. "
                "This is a PASI 168-hour unattended Long-run acceptance operation."
            )
            fingerprint = prompt_fingerprint(prompt)
            idempotency_key = f"{run_id}-{sequence_index:03d}"
            planned_recovery = any(
                abs(elapsed_before_queue - offset) <= RECOVERY_TOLERANCE_SECONDS
                for offset in PLANNED_RECOVERY_OFFSETS
            )

            if sequence_index > 1 and previous_operation_id is None:
                evidence["summary"]["skipped_operations"] += 1
                raise LongRunError("missing predecessor operation id")

            queued = queue_operation(
                client,
                prompt,
                run_id=run_id,
                idempotency_key=idempotency_key,
                chain_id=chain_id,
                sequence_index=sequence_index,
                predecessor_operation_id=previous_operation_id,
                marker=marker,
                recovery_probe=planned_recovery,
            )
            operation_id = str(queued["operation_id"])

            for value, seen, label in (
                (operation_id, seen_operation_ids, "operation_id"),
                (idempotency_key, seen_idempotency, "idempotency_key"),
                (marker, seen_markers, "marker"),
                (fingerprint, seen_fingerprints, "prompt_fingerprint"),
            ):
                if value in seen:
                    evidence["summary"]["duplicate_logical_operations"] += 1
                    raise LongRunError(
                        f"duplicate {label} at sequence {sequence_index}: {value}"
                    )
                seen.add(value)

            with current_operation_lock:
                current_operation_id = operation_id

            queued_at = utc_now()
            final = wait_for_terminal(
                client,
                operation_id,
                args.operation_timeout,
            )

            if final.get("operation_id") != operation_id:
                evidence["summary"]["skipped_operations"] += 1
                raise LongRunError(
                    f"operation identity changed at sequence {sequence_index}"
                )

            if final.get("status") != "completed":
                error = str(final.get("error") or "")
                if error.startswith("CHAT_"):
                    evidence["summary"]["terminal_chat_errors"] += 1
                raise LongRunError(
                    f"operation {sequence_index} did not complete: "
                    f"{final.get('status')!r} {error!r}"
                )

            response_text = str(final.get("response_text") or "").strip()
            if response_text != marker:
                raise LongRunError(
                    f"operation {sequence_index} response did not exactly match marker"
                )

            timing = final.get("timing")
            if not isinstance(timing, dict):
                raise LongRunError(
                    f"operation {sequence_index} is missing timing evidence"
                )

            if timing.get("user_messages_added") != 1:
                evidence["summary"]["duplicate_logical_operations"] += 1
                raise LongRunError(
                    f"operation {sequence_index} reported "
                    f"user_messages_added={timing.get('user_messages_added')!r}"
                )

            if timing.get("ack_verified") is not True:
                raise LongRunError(
                    f"operation {sequence_index} lacks verified submission acknowledgement"
                )

            if str(timing.get("submission_via") or "") == "sent_unverified":
                evidence["summary"]["premature_injections"] += 1
                raise LongRunError(
                    f"operation {sequence_index} used an unverified submission path"
                )

            replay = queue_operation(
                client,
                prompt,
                run_id=run_id,
                idempotency_key=idempotency_key,
                chain_id=chain_id,
                sequence_index=sequence_index,
                predecessor_operation_id=previous_operation_id,
                marker=marker,
                recovery_probe=planned_recovery,
            )
            if replay.get("operation_id") != operation_id:
                evidence["summary"]["duplicate_logical_operations"] += 1
                raise LongRunError(
                    f"idempotency replay created operation "
                    f"{replay.get('operation_id')!r} instead of {operation_id!r}"
                )

            recovery_record: dict[str, Any] | None = None
            recovery_events = final.get("recovery_events")
            has_recovery_events = isinstance(recovery_events, list) and bool(recovery_events)

            if planned_recovery or has_recovery_events:
                recovery_record = validate_recovery(final, operation_id=operation_id)
                recovery_number = len(evidence["recoveries"]) + 1
                recovery_record["recovery_id"] = f"{run_id}-recovery-{recovery_number:02d}"
                recovery_record["planned"] = planned_recovery
                recovery_record["scheduled_offset_seconds"] = round(elapsed_before_queue)

                if planned_recovery:
                    planned_seen = len(
                        [item for item in evidence["recoveries"] if item.get("planned") is True]
                    )
                    expected_offset = PLANNED_RECOVERY_OFFSETS[planned_seen]
                    if abs(
                        recovery_record["scheduled_offset_seconds"] - expected_offset
                    ) > RECOVERY_TOLERANCE_SECONDS:
                        raise LongRunError(
                            "planned recovery timing drifted beyond "
                            f"{RECOVERY_TOLERANCE_SECONDS}s"
                        )

                detected_at = None
                resumed_at = None
                completed_at = final.get("updated_at")
                if isinstance(recovery_events, list):
                    for event in recovery_events:
                        if not isinstance(event, dict):
                            continue
                        phase = event.get("phase")
                        captured = event.get("captured_at")
                        if phase == "connection_lost" and detected_at is None:
                            detected_at = captured
                        if phase == "retry_resumed" and resumed_at is None:
                            resumed_at = captured

                recovery_record["detected_at"] = detected_at or queued_at
                recovery_record["resumed_at"] = resumed_at or queued_at
                recovery_record["completed_at"] = (
                    completed_at if isinstance(completed_at, str) else utc_now()
                )

                try:
                    detected_dt = datetime.fromisoformat(
                        str(recovery_record["detected_at"]).replace("Z", "+00:00")
                    )
                    completed_dt = datetime.fromisoformat(
                        str(recovery_record["completed_at"]).replace("Z", "+00:00")
                    )
                    recovery_latency = max(
                        0.0,
                        (completed_dt - detected_dt).total_seconds(),
                    )
                except ValueError:
                    recovery_latency = float(MAX_RECOVERY_LATENCY_SECONDS + 1)

                recovery_record["recovery_latency_seconds"] = recovery_latency
                if recovery_latency > MAX_RECOVERY_LATENCY_SECONDS:
                    raise LongRunError(
                        f"recovery exceeded {MAX_RECOVERY_LATENCY_SECONDS}s"
                    )

                evidence["recoveries"].append(recovery_record)
                if planned_recovery:
                    evidence["summary"]["successful_planned_recoveries"] += 1
                else:
                    evidence["summary"]["unplanned_recoveries"] += 1
                    if evidence["summary"]["unplanned_recoveries"] > MAX_UNPLANNED_RECOVERIES:
                        raise LongRunError("unplanned recovery count exceeded acceptance limit")
            state = browser_state(client)
            signature = state.get("conversation_signature")
            if not isinstance(signature, str):
                signature = ""

            evidence["operations"].append(
                {
                    "operation_id": operation_id,
                    "sequence_index": sequence_index,
                    "marker": marker,
                    "prompt_fingerprint": fingerprint,
                    "idempotency_key": idempotency_key,
                    "chain_id": chain_id,
                    "predecessor_operation_id": previous_operation_id,
                    "status": "completed",
                    "logical_execution_count": 1,
                    "duplicate_execution_count": 0,
                    "terminal_chat_error": None,
                    "recovery_event_ids": (
                        [item["recovery_id"] for item in [recovery_record]]
                        if recovery_record is not None
                        else []
                    ),
                    "created_at": queued_at,
                    "completed_at": utc_now(),
                    "response_digest": hashlib.sha256(
                        response_text.encode("utf-8")
                    ).hexdigest(),
                    "timing": timing,
                    "conversation_signature_after": signature,
                }
            )

            evidence["summary"]["logical_operations_completed"] += 1
            previous_operation_id = operation_id
            with current_operation_lock:
                current_operation_id = None

            persist_partial(evidence)

        remaining = DEFAULT_DURATION_SECONDS - (
            time.monotonic() - monotonic_start
        )
        while remaining > 0:
            time.sleep(min(30.0, remaining))
            remaining = DEFAULT_DURATION_SECONDS - (
                time.monotonic() - monotonic_start
            )

        telemetry.stop()
        telemetry.capture()
        ended_at = utc_now()
        ended_dt = datetime.fromisoformat(ended_at)
        evidence["ended_at"] = ended_at
        evidence["duration_seconds"] = (
            ended_dt - started_dt
        ).total_seconds()
        summarize_resources(evidence)

        if evidence["health"]["samples"]:
            timestamps = [
                datetime.fromisoformat(
                    str(item["timestamp"]).replace("Z", "+00:00")
                ).timestamp()
                for item in evidence["health"]["samples"]
            ]
            gaps = [
                later - earlier
                for earlier, later in zip(timestamps, timestamps[1:])
            ]
            evidence["health"]["max_gap_seconds"] = max(gaps) if gaps else 0.0
            evidence["health"]["max_heartbeat_age_seconds"] = max(
                float(item["heartbeat_age_seconds"])
                for item in evidence["health"]["samples"]
            )

        evidence["summary"]["unplanned_recoveries"] = len(\n    [item for item in evidence["recoveries"] if item.get("planned") is False]\n)\n
        validate_finished_evidence(
            evidence,
            started=started_dt,
            ended=ended_dt,
        )
        persist_final(evidence, "PASS", [])

        verifier = ROOT / "scripts" / "verify_168h_long_run.py"
        verification = subprocess.run(
            [sys.executable, str(verifier), str(FINAL_EVIDENCE)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if verification.returncode != 0:
            raise LongRunError(
                "independent 168-hour verifier rejected the evidence: "
                + (verification.stdout.strip() or verification.stderr.strip())
            )

        PARTIAL_EVIDENCE.unlink(missing_ok=True)

        print(
            "LONG-RUN PASS: 168 hours elapsed; "
            f"{evidence['summary']['logical_operations_completed']} operations completed; "
            f"{evidence['summary']['successful_planned_recoveries']} planned recoveries succeeded"
        )
        print(f"Evidence: {FINAL_EVIDENCE}")
        return 0

    except Exception as exc:
        try:
            telemetry.stop()
        except Exception:
            pass

        evidence["ended_at"] = utc_now()
        try:
            ended_dt = datetime.fromisoformat(evidence["ended_at"])
            evidence["duration_seconds"] = (
                ended_dt - started_dt
            ).total_seconds()
        except ValueError:
            evidence["duration_seconds"] = 0

        failure = str(exc)
        evidence["final"]["status"] = "FAIL"
        evidence["final"]["failures"] = [failure]
        persist_partial(evidence)
        persist_final(evidence, "FAIL", [failure])

        print(f"LONG-RUN FAIL: {failure}", file=sys.stderr)
        print(f"Evidence: {FINAL_EVIDENCE}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
