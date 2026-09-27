#!/usr/bin/env python3
"""Run the M1 live 20-task sequential handoff gate.

The bridge is given exactly one uniquely marked prompt operation at a time.
The next operation is not queued until the predecessor has reached verified
terminal completion and passed every M1 acceptance check.

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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
DEFAULT_COUNT = 20
POLL_SECONDS = 0.25
MAX_RESPONSE_BYTES = 2_000_000
RUNTIME_TOKEN = ROOT / ".runtime" / "bridge-token"
ACCEPTANCE_DIR = ROOT / ".runtime" / "acceptance"
M0_EVIDENCE = ACCEPTANCE_DIR / "m0-live.json"
M1_EVIDENCE = ACCEPTANCE_DIR / "m1-live.json"
DURABLE_CHAT_STATE = ACCEPTANCE_DIR / "durable-automation-chat.json"
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
        # The Engineering Workspace bridge is provisioned from the repository-local
        # .runtime/bridge-token. Keep the legacy ~/.pasi token only as a fallback
        # for older environments; it must not override the canonical workspace token.
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


def chat_limit_reason(health_data: dict[str, Any]) -> str:
    if (
        health_data.get("conversation_context_exhausted") is True
        or health_data.get("chat_exhausted") is True
    ):
        return "context_limit"
    if health_data.get("provider_usage_limited") is True:
        return "usage_limit"
    return ""


def durable_chat_url() -> str:
    """Return the persisted durable automation chat URL, newest evidence first."""
    candidates = [
        DURABLE_CHAT_STATE,
        M1_EVIDENCE,
        M0_EVIDENCE,
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if path == M1_EVIDENCE and payload.get("status") != "PASS":
            continue
        for key in ("durable_chat_url", "chat_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("https://chatgpt.com/c/"):
                return value
    return ""


def persist_durable_chat_url(chat_url: str, *, reason: str = "") -> None:
    if not chat_url.startswith("https://chatgpt.com/c/"):
        raise M1LiveError(f"cannot persist invalid durable ChatGPT URL: {chat_url!r}")
    ACCEPTANCE_DIR.mkdir(parents=True, exist_ok=True)
    DURABLE_CHAT_STATE.write_text(
        json.dumps(
            {
                "chat_url": chat_url,
                "updated_at": time.time(),
                "creation_reason": reason,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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


def wait_for_signature_delta(
    client: BridgeClient,
    *,
    expected_user: int,
    expected_assistant: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], tuple[int, int]]:
    """Wait for browser telemetry to publish the completed +1/+1 conversation delta."""
    deadline = time.monotonic() + timeout_seconds
    latest: tuple[int, int] | None = None
    while time.monotonic() < deadline:
        state = browser_state(client)
        counts = signature_counts(state.get("conversation_signature"))
        if counts is not None:
            latest = counts
            if (
                counts[0] == expected_user + 1
                and counts[1] == expected_assistant + 1
            ):
                return state, counts
        time.sleep(POLL_SECONDS)

    raise M1LiveError(
        "conversation signature did not reach exactly +1/+1 after the "
        f"operation completed; last_counts={latest!r}, "
        f"expected_next=({expected_user + 1}, {expected_assistant + 1})"
    )


def ensure_chat_ready(
    client: BridgeClient,
) -> tuple[str, tuple[int, int]]:
    """Use whichever ChatGPT conversation is active in the browser right now."""
    health = browser_health(client)
    health_url = str(health.get("chat_url") or "")
    if health.get("native_controller") is not True:
        raise M1LiveError("browser health does not identify the native controller")
    if not health_url.startswith("https://chatgpt.com/c/"):
        raise M1LiveError(
            f"browser health does not expose a ChatGPT conversation URL: {health_url!r}"
        )

    state = browser_state(client)
    counts = signature_counts(state.get("conversation_signature"))
    if counts is None:
        raise M1LiveError("browser state did not expose a parseable conversation_signature")
    return health_url, counts


def wait_for_thinking(
    client: BridgeClient,
    *,
    session_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Verify Thinking is enabled before the M1 chain can be queued."""
    health = browser_health(client)
    if health.get("thinking_enabled") is True:
        return health

    if chat_limit_reason(health):
        raise M1LiveError(
            "ChatGPT reported a usage/context limit while verifying Thinking; "
            "a fresh chat is required before starting the 20-operation chain"
        )

    reasoning = queue_operation(
        client,
        "select_reasoning",
        "",
        idempotency_key=f"{session_id}-select-thinking",
    )
    reasoning_id = str(reasoning.get("operation", {}).get("operation_id") or "")
    if not reasoning_id:
        raise M1LiveError("Thinking-selection operation did not return an operation id")

    result = wait_for_terminal(
        client,
        reasoning_id,
        timeout_seconds=timeout_seconds,
        later_operation_ids=[],
        violation_log=[],
    )
    if result.get("status") != "completed":
        raise M1LiveError(
            "Thinking-selection operation did not complete: "
            f"{result.get('status')!r} {result.get('error')!r}"
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        health = browser_health(client)
        if health.get("thinking_enabled") is True:
            return health
        if chat_limit_reason(health):
            raise M1LiveError(
                "ChatGPT reported a usage/context limit while verifying Thinking"
            )
        time.sleep(POLL_SECONDS)

    raise M1LiveError("Thinking was not verified as enabled before the M1 chain deadline")


def create_fresh_chat_after_limit(
    client: BridgeClient,
    *,
    session_id: str,
    reason: str,
    timeout_seconds: float,
) -> tuple[str, tuple[int, int], str]:
    if reason not in {"usage_limit", "context_limit"}:
        raise M1LiveError("fresh chat creation requires an explicit usage or context-limit report")

    print(
        "ChatGPT explicitly reported "
        f"{reason}; creating one replacement conversation before the M1 chain."
    )
    fresh = queue_operation(
        client,
        "new_chat",
        "",
        idempotency_key=f"{session_id}-new-chat-{reason}",
    )
    fresh_id = str(fresh.get("operation", {}).get("operation_id") or "")
    if not fresh_id:
        raise M1LiveError("fresh-chat operation did not return an operation id")

    fresh_result = wait_for_terminal(
        client,
        fresh_id,
        timeout_seconds=timeout_seconds,
        later_operation_ids=[],
        violation_log=[],
    )
    if fresh_result.get("status") != "completed":
        raise M1LiveError(
            "fresh-chat operation did not complete: "
            f"{fresh_result.get('status')!r} {fresh_result.get('error')!r}"
        )

    chat_url, counts = ensure_chat_ready(client)
    persist_durable_chat_url(chat_url, reason=reason)
    return chat_url, counts, fresh_id


def prepare_durable_chat(
    client: BridgeClient,
    *,
    session_id: str,
    timeout_seconds: float,
) -> tuple[str, tuple[int, int], bool, str, str, str]:
    """
    Use the conversation currently active in the browser.

    The previously persisted URL is evidence only; it never overrides the
    active browser conversation. A new conversation is created only after
    ChatGPT explicitly reports a usage/context limit.
    """
    stored_url = durable_chat_url()
    health = browser_health(client)
    reason = chat_limit_reason(health)

    if reason:
        chat_url, counts, fresh_id = create_fresh_chat_after_limit(
            client,
            session_id=session_id,
            reason=reason,
            timeout_seconds=timeout_seconds,
        )
        return chat_url, counts, True, reason, stored_url, fresh_id

    chat_url, counts = ensure_chat_ready(client)

    # The browser's active conversation is authoritative. Persist it even when
    # it differs from an older durable URL so subsequent runs reuse this chat.
    if stored_url != chat_url:
        persist_durable_chat_url(chat_url, reason="active_browser_chat")

    return chat_url, counts, False, "", stored_url, ""


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

    status = client.get("/status")
    queue_size = int(status.get("queue_size", 0) or 0)
    if queue_size != 0:
        raise M1LiveError(
            "M1 requires an empty bridge queue before starting; "
            f"found {queue_size} queued/active operation(s). "
            "Clear the abandoned M1 chain before retrying."
        )

    (
        chat_url,
        baseline_counts,
        fresh_chat_created,
        fresh_chat_reason,
        durable_chat_url_before,
        fresh_chat_operation_id,
    ) = prepare_durable_chat(
        client,
        session_id=session_id,
        timeout_seconds=args.timeout,
    )

    # Thinking must be verified before any of the 20 prompt operations are
    # allowed onto the bridge queue. If it is off, the native controller can
    # select Thinking through the typed select_reasoning operation.
    thinking_health = wait_for_thinking(
        client,
        session_id=session_id,
        timeout_seconds=args.timeout,
    )
    if thinking_health.get("thinking_enabled") is not True:
        raise M1LiveError("Thinking was not verified as enabled before queuing the M1 chain")

    expected_user, expected_assistant = baseline_counts

    operations: list[dict[str, Any]] = []
    generated_fingerprints: dict[str, int] = {}
    session_marker = uuid.uuid4().hex[:10]

    violations: list[str] = []
    duplicate_indices: list[int] = []
    skipped_indices: list[int] = []
    repeated_prompts: list[dict[str, Any]] = []
    terminal_chat_errors: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    failure_error = ""

    # Queue exactly one prompt at a time. A later operation does not exist in
    # the bridge queue until the predecessor has completed every acceptance
    # check. This makes the chain conditional: any failure stops the chain and
    # leaves no speculative future prompts queued.
    try:
        for index in range(1, DEFAULT_COUNT + 1):
            marker = f"PASI_M1_CHAIN_{session_marker}_{index:02d}"
            prompt = (
                f"Reply with exactly this marker and no other text: {marker}. "
                "This is a PASI M1 sequential handoff acceptance operation."
            )
            fingerprint = prompt_fingerprint(prompt)
            if fingerprint in generated_fingerprints:
                raise M1LiveError(
                    f"generated repeated prompt at index {index}; "
                    f"first seen at {generated_fingerprints[fingerprint]}"
                )
            generated_fingerprints[fingerprint] = index

            previous = results[-1] if results else None
            previous_completed_ms = (
                int(previous["completed_at_ms"]) if previous is not None else None
            )

            queued = queue_operation(
                client,
                "prompt",
                prompt,
                idempotency_key=f"{session_id}-{index:02d}",
                completion_markers=[marker],
            )
            operation_id = str(queued["operation_id"])
            expected = {
                "index": index,
                "operation_id": operation_id,
                "marker": marker,
                "prompt": prompt,
                "prompt_fingerprint": fingerprint,
            }
            operations.append(expected)

            result = wait_for_terminal(
                client,
                operation_id,
                timeout_seconds=args.timeout,
                later_operation_ids=[],
                violation_log=violations,
            )

            if result.get("operation_id") != operation_id:
                skipped_indices.append(index)
                raise M1LiveError(f"operation identity mismatch at index {index}")

            if result.get("status") != "completed":
                error = str(result.get("error") or "")
                if error.startswith("CHAT_"):
                    terminal_chat_errors.append(
                        {
                            "index": index,
                            "operation_id": operation_id,
                            "error": error,
                        }
                    )
                raise M1LiveError(
                    f"operation {index} did not complete: "
                    f"status={result.get('status')!r} error={error!r}"
                )

            response_text = str(result.get("response_text") or "")
            if str(expected["marker"]) not in response_text:
                raise M1LiveError(
                    f"operation {index} completed without its unique response marker"
                )

            timing = result.get("timing")
            if not isinstance(timing, dict):
                raise M1LiveError(
                    f"operation {index} completed without browser timing evidence"
                )

            if timing.get("user_messages_added") != 1:
                duplicate_indices.append(index)
                raise M1LiveError(
                    f"operation {index} reported user_messages_added="
                    f"{timing.get('user_messages_added')!r}; expected exactly 1"
                )

            if timing.get("ack_verified") is not True:
                raise M1LiveError(
                    f"operation {index} lacked verified prompt-submission acknowledgement"
                )

            submission_via = str(timing.get("submission_via") or "")
            if submission_via == "sent_unverified":
                raise M1LiveError(
                    f"operation {index} used an unverified submission path"
                )

            injected_at_ms = timing.get("injected_at_ms")
            if previous_completed_ms is not None:
                if not isinstance(injected_at_ms, (int, float)):
                    raise M1LiveError(
                        f"operation {index} lacks injected_at_ms timing for "
                        "predecessor-gated handoff"
                    )
                if injected_at_ms < previous_completed_ms:
                    violation = (
                        f"operation {index} injection occurred before predecessor "
                        f"completion: injected_at_ms={injected_at_ms}, "
                        f"previous_completed_at_ms={previous_completed_ms}"
                    )
                    violations.append(violation)
                    raise M1LiveError(violation)

                response_completed_to_injection = timing.get(
                    "response_completed_to_prompt_injected_ms"
                )
                if not isinstance(response_completed_to_injection, (int, float)):
                    raise M1LiveError(
                        f"operation {index} lacks "
                        "response_completed_to_prompt_injected_ms"
                    )
                if response_completed_to_injection < 0:
                    raise M1LiveError(
                        f"operation {index} reported a negative "
                        "completion-to-injection latency"
                    )

            state, counts = wait_for_signature_delta(
                client,
                expected_user=expected_user,
                expected_assistant=expected_assistant,
                timeout_seconds=30.0,
            )
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

            prior_index = next(
                (
                    item["index"]
                    for item in operations[:-1]
                    if item["prompt_fingerprint"] == fingerprint
                ),
                None,
            )
            if prior_index is not None:
                repeated_prompts.append(
                    {
                        "index": index,
                        "repeated_from_index": prior_index,
                        "fingerprint": fingerprint,
                    }
                )
                raise M1LiveError(
                    f"operation {index} reused the prompt fingerprint from "
                    f"operation {prior_index}"
                )

            claimed_at = result.get("claimed_at")
            completed_at_ms = timing.get("completed_at_ms")
            if not isinstance(completed_at_ms, (int, float)):
                raise M1LiveError(f"operation {index} lacks completed_at_ms timing")
            completed_at_epoch = float(completed_at_ms) / 1000.0

            observed_chat_url = str(
                result.get("chat_url") or state.get("chat_url") or ""
            )
            if observed_chat_url and observed_chat_url != chat_url:
                raise M1LiveError(
                    f"operation {index} moved away from the dedicated automation chat: "
                    f"{observed_chat_url!r}"
                )

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
                    "chat_url": observed_chat_url or chat_url,
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
    except M1LiveError as exc:
        failure_error = str(exc)

    payload = {
        "gate": "M1",
        "status": "PASS"
        if not failure_error
        and not violations
        and not duplicate_indices
        and not skipped_indices
        and not repeated_prompts
        and not terminal_chat_errors
        and len(results) == DEFAULT_COUNT
        else "FAIL",
        "count": DEFAULT_COUNT,
        "session_id": session_id,
        "chat_url": chat_url,
        "durable_chat_url": chat_url,
        "durable_chat_url_before_test": durable_chat_url_before,
        "fresh_chat_operation_id": fresh_chat_operation_id,
        "fresh_chat_created_after_limit": fresh_chat_created,
        "fresh_chat_creation_reason": fresh_chat_reason,
        "thinking_enabled_before_chain": thinking_health.get("thinking_enabled") is True,
        "baseline": {"user": baseline_counts[0], "assistant": baseline_counts[1]},
        "duplicate_indices": duplicate_indices,
        "skipped_indices": skipped_indices,
        "repeated_prompts": repeated_prompts,
        "premature_claim_or_injection_violations": violations,
        "terminal_chat_errors": terminal_chat_errors,
        "prompt_fingerprints": [item["prompt_fingerprint"] for item in operations],
        "results": results,
        "attempted_count": len(operations),
        "completed_count": len(results),
        "failure_error": failure_error,
        "completed_at": time.time(),
    }
    evidence_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if payload["status"] != "PASS":
        raise M1LiveError(
            f"M1 failed after {len(results)} completed operation(s); "
            f"evidence written to {evidence_path}: {failure_error or 'acceptance criteria not met'}"
        )

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