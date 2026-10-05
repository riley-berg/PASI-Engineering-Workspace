#!/usr/bin/env python3
"""Run the M2 controlled interruption/recovery acceptance gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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
DEFAULT_TIMEOUT_SECONDS = 180.0
POLL_SECONDS = 0.25
MAX_RESPONSE_BYTES = 2_000_000
M2_CHECKPOINT_SCHEMA_VERSION = 1
RUNTIME_TOKEN = ROOT / ".runtime" / "bridge-token"
ACCEPTANCE_DIR = ROOT / ".runtime" / "acceptance"
M2_EVIDENCE = ACCEPTANCE_DIR / "m2-live.json"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class M2LiveError(RuntimeError):
    """Raised when the live M2 contract cannot be established."""


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
            raise ValueError("bridge must use localhost HTTP, for example http://127.0.0.1:8765")
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
                self.token = (Path.home() / ".pasi" / "bridge-token").read_text(encoding="utf-8").strip()
            except OSError:
                self.token = ""

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        allow_http_error: bool = False,
    ) -> dict[str, Any]:
        body = None
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
                status_code = response.status
        except HTTPError as exc:
            if not allow_http_error:
                detail = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
                raise M2LiveError(f"bridge HTTP {exc.code}: {detail[:600]}") from exc
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            status_code = exc.code
        except URLError as exc:
            raise M2LiveError(f"bridge request failed: {exc.reason}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise M2LiveError("bridge response exceeded configured bound")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise M2LiveError("bridge returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise M2LiveError("bridge JSON response must be an object")
        if allow_http_error:
            result["_http_status"] = status_code
        return result

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any], *, allow_http_error: bool = False) -> dict[str, Any]:
        return self.request("POST", path, payload, allow_http_error=allow_http_error)


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(" ".join(prompt.split()).strip().encode("utf-8")).hexdigest()


def operation(client: BridgeClient, operation_id: str) -> dict[str, Any]:
    payload = client.get(f"/operation?operation_id={quote(operation_id, safe='')}")
    value = payload.get("operation")
    if not isinstance(value, dict):
        raise M2LiveError(f"operation {operation_id} was missing from bridge response")
    return value


def browser_health(client: BridgeClient) -> dict[str, Any]:
    payload = client.get("/browser/health")
    value = payload.get("observation")
    return value if isinstance(value, dict) else {}


def queue_m2_probe(
    client: BridgeClient,
    prompt: str,
    *,
    idempotency_key: str,
    marker: str,
) -> dict[str, Any]:
    payload = client.post(
        "/queue",
        {
            "operation_type": "prompt",
            "prompt": prompt,
            "idempotency_key": idempotency_key,
            "completion_markers": [marker],
            "m2_recovery_probe": True,
        },
    )
    value = payload.get("operation")
    if not isinstance(value, dict) or not value.get("operation_id"):
        raise M2LiveError("bridge did not return the M2 operation")
    return value


def recovery_events(operation_state: dict[str, Any]) -> list[dict[str, Any]]:
    value = operation_state.get("recovery_events")
    return [event for event in value if isinstance(event, dict)] if isinstance(value, list) else []


def validate_m2_terminal_contract(
    operation_state: dict[str, Any],
    marker: str,
) -> dict[str, Any]:
    operation_id = operation_state.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise M2LiveError("M2 operation has no durable operation_id")
    if operation_state.get("status") != "completed":
        raise M2LiveError(f"M2 operation did not complete: {operation_state.get('status')!r}")
    if operation_state.get("m2_recovery_probe") is not True:
        raise M2LiveError("M2 operation did not retain the controlled recovery probe flag")

    retry_count = int(operation_state.get("retry_count", 0) or 0)
    retry_counts = operation_state.get("retry_counts")
    if not isinstance(retry_counts, dict):
        raise M2LiveError("M2 operation is missing retry_counts")
    normalized_retry_counts = {
        key: int(retry_counts.get(key, 0) or 0)
        for key in ("controller", "response", "context")
    }
    if retry_count != 1 or normalized_retry_counts != {"controller": 1, "response": 0, "context": 0}:
        raise M2LiveError(
            "M2 requires exactly one controller recovery retry; "
            f"got retry_count={retry_count}, retry_counts={normalized_retry_counts}"
        )

    events = recovery_events(operation_state)
    phases = [str(event.get("phase", "")) for event in events]
    if phases.count("connection_lost") != 1:
        raise M2LiveError(f"M2 expected exactly one connection_lost event, got {phases.count('connection_lost')}")
    if phases.count("ready_for_retry") != 1:
        raise M2LiveError(f"M2 expected exactly one ready_for_retry event, got {phases.count('ready_for_retry')}")
    if phases.count("controlled_probe_resume") != 1:
        raise M2LiveError(
            f"M2 expected exactly one controlled_probe_resume event, got {phases.count('controlled_probe_resume')}"
        )
    if "retry_resumed" not in phases:
        raise M2LiveError("M2 never recorded retry_resumed")
    if "failed" in phases or "blocked_security_challenge" in phases:
        raise M2LiveError("M2 recovery entered a failure or security-boundary phase")

    event_operation_ids = {
        event.get("operation_id")
        for event in events
        if isinstance(event.get("operation_id"), str)
    }
    if event_operation_ids and event_operation_ids != {operation_id}:
        raise M2LiveError(
            f"M2 recovery events changed operation identity: {sorted(event_operation_ids, key=str)!r}"
        )

    chat_errors = []
    for event in events:
        for key in ("error", "controller_error"):
            value = event.get(key)
            if isinstance(value, str) and value.startswith("CHAT_"):
                chat_errors.append(value)
    operation_error = operation_state.get("error")
    if isinstance(operation_error, str) and operation_error.startswith("CHAT_"):
        chat_errors.append(operation_error)
    if chat_errors:
        raise M2LiveError(f"M2 recorded terminal CHAT_* errors: {chat_errors}")

    response_text = str(operation_state.get("response_text") or "").strip()
    if response_text != marker:
        raise M2LiveError(f"M2 terminal response did not exactly match marker: {response_text!r}")
    if operation_state.get("response_text_available") is not True:
        raise M2LiveError("M2 terminal response evidence is not marked available")

    timing = operation_state.get("timing")
    if not isinstance(timing, dict):
        raise M2LiveError("M2 terminal operation is missing timing evidence")
    if timing.get("user_messages_added") != 1:
        raise M2LiveError(
            "M2 expected one user message added on the final verified submission: "
            f"{timing.get('user_messages_added')!r}"
        )
    if timing.get("ack_verified") is not True:
        raise M2LiveError("M2 final submission acknowledgement was not verified")

    return {
        "operation_id": operation_id,
        "retry_count": retry_count,
        "retry_counts": normalized_retry_counts,
        "recovery_phases": phases,
        "recovery_events": events,
        "timing": timing,
        "response_text": response_text,
        "response_text_available": True,
        "duplicate_logical_operations": 0,
        "same_operation_recovered": True,
    }


def persist_evidence(payload: dict[str, Any]) -> None:
    ACCEPTANCE_DIR.mkdir(parents=True, exist_ok=True)
    M2_EVIDENCE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the live M2 interruption/recovery acceptance gate.")
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    started_at = datetime.now(timezone.utc).isoformat()
    run_id = uuid.uuid4().hex[:12]
    marker = f"PASI_M2_RECOVERY_{run_id}_01"
    prompt = (
        f"Reply with exactly this marker and no other text: {marker}. "
        "This is a PASI M2 controlled interruption/recovery acceptance operation. "
        "The same operation must resume after the bounded browser recovery."
    )
    idempotency_key = f"pasi-m2-{run_id}"
    prompt_hash = prompt_fingerprint(prompt)
    client = BridgeClient(args.bridge)

    evidence: dict[str, Any] = {
        "status": "FAIL",
        "milestone": "M2",
        "run_id": run_id,
        "started_at": started_at,
        "bridge_url": args.bridge,
        "marker": marker,
        "prompt_fingerprint": prompt_hash,
        "idempotency_key": idempotency_key,
        "contract": {
            "interrupt_during_generation": True,
            "same_operation_id_preserved": True,
            "exactly_one_recovery_retry": True,
            "duplicate_logical_operations": 0,
            "terminal_chat_errors": 0,
        },
        "status_history": [],
        "failures": [],
    }

    try:
        health = client.get("/health")
        if health.get("status") != "ok":
            raise M2LiveError(f"bridge health is not ok: {health}")
        if health.get("m1_checkpoint_schema_version") != M2_CHECKPOINT_SCHEMA_VERSION:
            raise M2LiveError(
                "bridge checkpoint schema mismatch; expected "
                f"{M2_CHECKPOINT_SCHEMA_VERSION}"
            )
        if health.get("m2_recovery_schema_version") != M2_CHECKPOINT_SCHEMA_VERSION:
            raise M2LiveError(
                "bridge recovery schema mismatch; expected "
                f"{M2_CHECKPOINT_SCHEMA_VERSION}"
            )
        evidence["bridge_health"] = health
        evidence["browser_health_before"] = browser_health(client)

        queued = queue_m2_probe(
            client,
            prompt,
            idempotency_key=idempotency_key,
            marker=marker,
        )
        if queued.get("completion_markers") != [marker]:
            raise M2LiveError(
                "M2 queued operation did not retain the exact completion marker: "
                f"{queued.get('completion_markers')!r}"
            )
        operation_id = str(queued["operation_id"])
        evidence["operation_id"] = operation_id
        evidence["queued_operation"] = queued
        evidence["queued_at"] = datetime.now(timezone.utc).isoformat()

        deadline = time.monotonic() + args.timeout
        last_status = None
        final = None
        while time.monotonic() < deadline:
            current = operation(client, operation_id)
            status = str(current.get("status", "unknown"))
            if status != last_status:
                evidence["status_history"].append({
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "status": status,
                    "retry_count": current.get("retry_count", 0),
                })
                last_status = status

            current_id = current.get("operation_id")
            if current_id != operation_id:
                raise M2LiveError(
                    f"M2 operation identity changed from {operation_id} to {current_id}"
                )

            if status in TERMINAL_STATUSES:
                final = current
                break
            time.sleep(POLL_SECONDS)

        if final is None:
            raise M2LiveError(f"M2 did not reach a terminal state within {args.timeout:.1f}s")

        evidence["final_operation"] = final
        validation = validate_m2_terminal_contract(final, marker)
        evidence.update(validation)

        replay = queue_m2_probe(
            client,
            prompt,
            idempotency_key=idempotency_key,
            marker=marker,
        )
        if replay.get("operation_id") != operation_id:
            raise M2LiveError(
                "idempotency replay created a different operation_id: "
                f"{replay.get('operation_id')!r}"
            )
        evidence["idempotency_replay"] = {
            "operation_id": replay.get("operation_id"),
            "status": replay.get("status"),
            "same_operation": True,
        }

        evidence["browser_health_after"] = browser_health(client)
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        evidence["status"] = "PASS"
        persist_evidence(evidence)

        print(
            "M2 PASS: one live operation interrupted during generation, "
            "recovered under the same operation_id exactly once, and completed "
            "without duplicate logical execution or terminal CHAT_* errors"
        )
        print(f"Operation: {operation_id}")
        print(f"Recovery events: {len(validation['recovery_events'])}")
        print(f"Evidence: {M2_EVIDENCE}")
        return 0
    except Exception as exc:
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        evidence["failures"].append(str(exc))
        try:
            if evidence.get("operation_id"):
                evidence["last_observed_operation"] = operation(client, str(evidence["operation_id"]))
        except Exception:
            pass
        persist_evidence(evidence)
        print(f"M2 FAIL: {exc}", file=sys.stderr)
        print(f"Evidence: {M2_EVIDENCE}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
