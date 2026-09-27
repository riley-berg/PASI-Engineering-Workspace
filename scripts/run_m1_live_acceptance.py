#!/usr/bin/env python3
"""Run the M1 live 20-task sequential handoff gate.

The bridge queue is seeded with exactly 20 uniquely marked prompt operations.
The native ChatGPT controller must claim and inject them in order, and the
bridge must only claim the next operation after the previous operation has
reached verified terminal completion.

The gate records:
- duplicate submissions (conversation delta != +1 user / +1 assistant)
- skipped/out-of-order operations
- repeated prompt bodies
- premature next-operation claims
- terminal CHAT_* failures
- prompt-injection timing relative to the previous verified completion
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
DEFAULT_COUNT = 20
POLL_SECONDS = 0.25
MAX_RESPONSE_BYTES = 2_000_000
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"claimed", "running", "generating"}


class M1LiveError(RuntimeError):
    """Raised when the live M1 contract cannot be established."""


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
                self.token = (Path.home() / ".pasi" / "bridge-token").read_text(encoding="utf-8").strip()
            except OSError:
                self.token = ""

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
        except HTTPError as exc:
            detail = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
            raise M1LiveError(f"bridge HTTP {exc.code}: {detail[:600]}") from exc
        except URLError as exc:
            raise M1LiveError(f"bridge request failed: {exc.reason}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise M1LiveError("bridge response exceeded configured bound")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise M1LiveError("bridge returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise M1LiveError("bridge JSON response must be an object")
        return result

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)


def signature_counts(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d+):(\d+):", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def observation_data(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    data = value.get("data")
    return data if isinstance(data, dict) else value


def prompt_fingerprint(prompt: str) -> str:
    normalized = " ".join(prompt.split()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def operation(client: BridgeClient, operation_id: str) -> dict[str, Any]:
    payload = client.get(f"/operation?operation_id={quote(operation_id, safe='')}")
    value = payload.get("operation")
    if not isinstance(value, dict):
        raise M1LiveError(f"operation {operation_id} was missing from bridge response")
    return value


def browser_state(client: BridgeClient) -> dict[str, Any]:
    payload = client.get("/browser/state")
    return observation_data(payload.get("observation"))


def browser_health(client: BridgeClient) -> dict[str, Any]:
    payload = client.get("/browser/health")
    return observation_data(payload.get("observation"))


def queue_operation(
    client: BridgeClient,
    operation_type: str,
    prompt: str,
    *,
    idempotency_key: str,
    completion_markers: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operation_type": operation_type,
        "prompt": prompt,
        "idempotency_key": idempotency_key,
    }
    if completion_markers is not None:
        body["completion_markers"] = completion_markers
    payload = client.post("/queue", body)
    value = payload.get("operation")
    if not isinstance(value, dict) or not value.get("operation_id"):
        raise M1LiveError(f"bridge did not return an operation for {operation_type}")
    return value


def wait_for_terminal(
    client: BridgeClient,
    operation_id: str,
    *,
    timeout_seconds: float,
    later_operation_ids: list[str],
    violation_log: list[str],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        current = operation(client, operation_id)
        status = str(current.get("status", ""))

        if status in TERMINAL_STATUSES:
            return current

        if later_operation_ids:
            for later_id in later_operation_ids:
                later = operation(client, later_id)
                later_status = str(later.get("status", ""))
                if later_status != "queued":
                    violation = (
                        f"future operation {later_id} became {later_status!r} "
                        f"while predecessor {operation_id} was still {status!r}"
                    )
                    violation_log.append(violation)
                    raise M1LiveError(violation)

        time.sleep(POLL_SECONDS)

    raise M1LiveError(f"operation {operation_id} did not reach terminal state within {timeout_seconds}s")


def wait_for_next_claim(
    client: BridgeClient,
    operation_id: str,
    *,
    timeout_seconds: float,
    predecessor_completed_at: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = operation(client, operation_id)
        status = str(current.get("status", ""))
        if status in ACTIVE_STATUSES or status in TERMINAL_STATUSES:
            claimed_at = current.get("claimed_at")
            if isinstance(claimed_at, (int, float)) and claimed_at < predecessor_completed_at:
                raise M1LiveError(
                    f"operation {operation_id} was claimed at {claimed_at} before "
                    f"predecessor completion at {predecessor_completed_at}"
                )
            return current
        time.sleep(POLL_SECONDS)
    raise M1LiveError(f"next operation {operation_id} was not claimed within {timeout_seconds}s")


def ensure_chat_ready(client: BridgeClient) -> tuple[str, tuple[int, int]]:
    health = browser_health(client)
    health_url = str(health.get("chat_url") or "")
    if health.get("native_controller") is not True:
        raise M1LiveError("browser health does not identify the native controller")
    if not health_url.startswith("https://chatgpt.com/c/"):
        raise M1LiveError(f"browser health does not expose a ChatGPT conversation URL: {health_url!r}")

    state = browser_state(client)
    counts = signature_counts(state.get("conversation_signature"))
    if counts is None:
        raise M1LiveError("browser state did not expose a parseable conversation_signature")
    return health_url, counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the M1 20-task live sequential prompt-handoff gate."
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--bridge-url", default=os.environ.get("PASI_BRIDGE_URL", DEFAULT_BRIDGE_URL))
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()

    if args.count != DEFAULT_COUNT:
        parser.error("--count must be exactly 20")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    session_id = args.session_id.strip() or f"m1-{uuid.uuid4().hex}"
    evidence_dir = Path(".runtime/acceptance")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "m1-live.json"

    client = BridgeClient(args.bridge_url)

    # Confirm the browser/bridge boundary before touching the task queue.
    bridge_health = client.get("/health")
    if bridge_health.get("status") not in {"ok", "healthy", None}:
        raise M1LiveError(f"bridge health is not healthy: {bridge_health!r}")

    # Start from a dedicated fresh ChatGPT conversation.
    fresh = queue_operation(
        client,
        "new_chat",
        "",
        idempotency_key=f"{session_id}-new-chat",
    )
    fresh_id = str(fresh["operation_id"])
    fresh_result = wait_for_terminal(
        client,
        fresh_id,
        timeout_seconds=args.timeout,
        later_operation_ids=[],
        violation_log=[],
    )
    if fresh_result.get("status") != "completed":
        raise M1LiveError(f"fresh-chat operation did not complete: {fresh_result!r}")

    chat_url, baseline_counts = ensure_chat_ready(client)
    expected_user, expected_assistant = baseline_counts

    operations: list[dict[str, Any]] = []
    seen_fingerprints: dict[str, int] = {}
    session_marker = uuid.uuid4().hex[:10]

    # Queue all 20 operations before the browser controller consumes them. The
    # controller/bridge, not this harness, is responsible for claiming the next
    # operation. The gate proves the next claim cannot occur before completion of
    # the predecessor.
    for index in range(1, DEFAULT_COUNT + 1):
        marker = f"PASI_M1_CHAIN_{session_marker}_{index:02d}"
        prompt = (
            f"Reply with exactly this marker and no other text: {marker}. "
            "This is a PASI M1 sequential handoff acceptance operation."
        )
        fingerprint = prompt_fingerprint(prompt)
        if fingerprint in seen_fingerprints:
            raise M1LiveError(f"generated repeated prompt at index {index}")
        seen_fingerprints[fingerprint] = index

        queued = queue_operation(
            client,
            "prompt",
            prompt,
            idempotency_key=f"{session_id}-{index:02d}",
            completion_markers=[marker],
        )
        operation_id = str(queued["operation_id"])
        operations.append(
            {
                "index": index,
                "operation_id": operation_id,
                "marker": marker,
                "prompt": prompt,
                "prompt_fingerprint": fingerprint,
            }
        )

    violations: list[str] = []
    duplicate_indices: list[int] = []
    skipped_indices: list[int] = []
    repeated_prompts: list[dict[str, Any]] = []
    terminal_chat_errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for position, expected in enumerate(operations):
        index = int(expected["index"])
        operation_id = str(expected["operation_id"])

        if position > 0:
            previous = results[-1]
            previous_completed_at = float(previous["completed_at"])
            current_claim = wait_for_next_claim(
                client,
                operation_id,
                timeout_seconds=args.timeout,
                predecessor_completed_at=previous_completed_at,
            )
            if current_claim.get("operation_id") != operation_id:
                skipped_indices.append(index)
                raise M1LiveError(f"unexpected operation claimed at index {index}: {current_claim!r}")

        result = wait_for_terminal(
            client,
            operation_id,
            timeout_seconds=args.timeout,
            later_operation_ids=[
                str(item["operation_id"])
                for item in operations[position + 1 :]
            ],
            violation_log=violations,
        )

        if result.get("operation_id") != operation_id:
            skipped_indices.append(index)
            raise M1LiveError(f"operation identity mismatch at index {index}")

        if result.get("status") != "completed":
            error = str(result.get("error") or "")
            if error.startswith("CHAT_"):
                terminal_chat_errors.append({"index": index, "operation_id": operation_id, "error": error})
            raise M1LiveError(
                f"operation {index} did not complete: status={result.get('status')!r} error={error!r}"
            )

        response_text = str(result.get("response_text") or "")
        if str(expected["marker"]) not in response_text:
            raise M1LiveError(
                f"operation {index} completed without its unique response marker"
            )

        timing = result.get("timing")
        if not isinstance(timing, dict):
            raise M1LiveError(f"operation {index} completed without browser timing evidence")

        if timing.get("user_messages_added") != 1:
            duplicate_indices.append(index)

        if timing.get("ack_verified") is not True:
            raise M1LiveError(f"operation {index} lacked verified prompt-submission acknowledgement")

        submission_via = str(timing.get("submission_via") or "")
        if submission_via == "sent_unverified":
            raise M1LiveError(f"operation {index} used an unverified submission path")

        if position > 0:
            previous = results[-1]
            previous_completed_ms = int(previous["completed_at_ms"])
            injected_at_ms = timing.get("injected_at_ms")
            response_completed_to_injection = timing.get("response_completed_to_prompt_injected_ms")

            if not isinstance(injected_at_ms, (int, float)):
                raise M1LiveError(
                    f"operation {index} lacks injected_at_ms timing for predecessor-gated handoff"
                )
            if injected_at_ms < previous_completed_ms:
                violation = (
                    f"operation {index} injection occurred before predecessor "
                    f"completion: injected_at_ms={injected_at_ms}, "
                    f"previous_completed_at_ms={previous_completed_ms}"
                )
                violations.append(violation)
                raise M1LiveError(violation)
            if not isinstance(response_completed_to_injection, (int, float)):
                raise M1LiveError(
                    f"operation {index} lacks response_completed_to_prompt_injected_ms"
                )
            if response_completed_to_injection < 0:
                raise M1LiveError(
                    f"operation {index} reported a negative completion-to-injection latency"
                )

        state = browser_state(client)
        counts = signature_counts(state.get("conversation_signature"))
        if counts is None:
            raise M1LiveError(f"operation {index} lacked conversation_signature evidence")

        user_delta = counts[0] - expected_user
        assistant_delta = counts[1] - expected_assistant
        if user_delta != 1 or assistant_delta != 1:
            duplicate_indices.append(index)
            violation = (
                f"operation {index} conversation delta was +{user_delta}/+{assistant_delta}; "
                "expected exactly +1/+1"
            )
            violations.append(violation)
            raise M1LiveError(violation)
        expected_user, expected_assistant = counts

        fingerprint = str(expected["prompt_fingerprint"])
        prior_index = seen_fingerprints.get(fingerprint)
        if prior_index is not None and prior_index != index:
            repeated_prompts.append(
                {"index": index, "repeated_from_index": prior_index, "fingerprint": fingerprint}
            )
        seen_fingerprints[fingerprint] = index

        claimed_at = result.get("claimed_at")
        completed_at = result.get("updated_at")
        completed_at_epoch = float(completed_at) if isinstance(completed_at, (int, float)) else time.time()
        completed_at_ms = timing.get("completed_at_ms")
        if not isinstance(completed_at_ms, (int, float)):
            raise M1LiveError(f"operation {index} lacks completed_at_ms timing")

        results.append(
            {
                "index": index,
                "operation_id": operation_id,
                "marker": expected["marker"],
                "prompt_fingerprint": fingerprint,
                "status": result.get("status"),
                "claimed_at": claimed_at,
                "completed_at": completed_at_epoch,
                "completed_at_ms": completed_at_ms,
                "timing": timing,
                "user_delta": user_delta,
                "assistant_delta": assistant_delta,
                "chat_url": result.get("chat_url") or state.get("chat_url"),
                "retry_count": result.get("retry_count", 0),
                "recovery_event_count": len(result.get("recovery_events") or []),
            }
        )

        print(
            json.dumps(
                {
                    "index": index,
                    "operation_id": operation_id,
                    "status": result.get("status"),
                    "marker": expected["marker"],
                    "timing": timing,
                    "user_delta": user_delta,
                    "assistant_delta": assistant_delta,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    payload = {
        "gate": "M1",
        "status": "PASS"
        if not violations and not duplicate_indices and not skipped_indices and not repeated_prompts and not terminal_chat_errors
        else "FAIL",
        "count": DEFAULT_COUNT,
        "session_id": session_id,
        "chat_url": chat_url,
        "fresh_chat_operation_id": fresh_id,
        "baseline": {"user": baseline_counts[0], "assistant": baseline_counts[1]},
        "duplicate_indices": duplicate_indices,
        "skipped_indices": skipped_indices,
        "repeated_prompts": repeated_prompts,
        "premature_claim_or_injection_violations": violations,
        "terminal_chat_errors": terminal_chat_errors,
        "prompt_fingerprints": [item["prompt_fingerprint"] for item in operations],
        "results": results,
        "completed_at": time.time(),
    }
    evidence_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if payload["status"] != "PASS":
        raise M1LiveError(f"M1 failed; evidence written to {evidence_path}")

    print(
        "M1 PASS: 20 sequential tasks; "
        "zero duplicates, zero skipped tasks, zero repeated prompts, "
        "zero premature next-task claims/injections, zero terminal CHAT_* errors"
    )
    print(f"Evidence: {evidence_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except M1LiveError as exc:
        print(f"M1 FAIL: {exc}")
        raise SystemExit(1)
