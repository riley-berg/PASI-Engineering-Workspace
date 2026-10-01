#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

# When invoked as `python scripts/pasi_live_cdp_diagnostic.py`, Python places
# `scripts/` on sys.path rather than the repository root. Make the repository
# package tree importable without requiring PYTHONPATH or editable installation.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.computer_use.chatgpt import ChatGPTAdapter, UrllibBridgeTransport


EXPECTED_SERVICE = "pasi-engineering-workspace-chatgpt-bridge"
EXPECTED_SCHEMA = "pasi-native-chromium-v2"
EXPECTED_CONTROLLER = "cdp-worker-v1"
CHAT_URL_PREFIXES = ("https://chatgpt.com/c/", "https://www.chatgpt.com/c/")


class DiagnosticFailure(RuntimeError):
    def __init__(self, step: str, message: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.evidence = dict(evidence or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "message": self.message,
            "evidence": self.evidence,
        }


def request(transport: UrllibBridgeTransport, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return transport.request(method, path, payload)


def require(condition: bool, step: str, message: str, evidence: Mapping[str, Any] | None = None) -> None:
    if not condition:
        raise DiagnosticFailure(step, message, evidence)


def read_health(transport: UrllibBridgeTransport) -> dict[str, Any]:
    payload = dict(request(transport, "GET", "/health"))
    require(
        payload.get("status") == "ok" and payload.get("service") == EXPECTED_SERVICE,
        "bridge_health",
        "localhost endpoint is not the PASI Engineering Workspace ChatGPT bridge",
        payload,
    )
    return payload


def read_browser_diagnostics(transport: UrllibBridgeTransport) -> dict[str, Any]:
    status = dict(request(transport, "GET", "/status"))
    browser = dict(request(transport, "GET", "/browser/health"))
    observation = browser.get("observation")
    data = observation.get("data") if isinstance(observation, Mapping) else None
    require(isinstance(data, Mapping), "browser_health", "bridge returned no browser health data", browser)

    require(
        observation.get("schema_version") == EXPECTED_SCHEMA,
        "browser_health",
        f"expected browser schema {EXPECTED_SCHEMA!r}",
        {"schema_version": observation.get("schema_version"), "browser": browser},
    )
    require(
        data.get("controller_version") == EXPECTED_CONTROLLER,
        "browser_health",
        f"expected CDP controller {EXPECTED_CONTROLLER!r}",
        {"controller_version": data.get("controller_version"), "browser": browser},
    )
    require(
        data.get("native_controller") is True and data.get("network_authority") is True,
        "browser_health",
        "browser health does not confirm native CDP/network authority",
        {
            "native_controller": data.get("native_controller"),
            "network_authority": data.get("network_authority"),
            "browser": browser,
        },
    )

    chat_url = data.get("chat_url")
    require(
        isinstance(chat_url, str) and chat_url.startswith(CHAT_URL_PREFIXES),
        "browser_health",
        "browser health does not contain a current ChatGPT conversation URL",
        {"chat_url": chat_url},
    )

    return {
        "status": status,
        "observation": browser,
        "data": dict(data),
    }


def read_operation(transport: UrllibBridgeTransport, operation_id: str) -> dict[str, Any]:
    payload = dict(request(transport, "GET", f"/operation?operation_id={operation_id}"))
    operation = payload.get("operation")
    require(
        isinstance(operation, Mapping),
        "operation_read",
        "bridge did not return the requested operation",
        payload,
    )
    return dict(operation)


def read_response_diagnostic(transport: UrllibBridgeTransport, operation_id: str) -> dict[str, Any]:
    payload = dict(request(transport, "GET", "/browser/response"))
    observation = payload.get("observation")
    data = observation.get("data") if isinstance(observation, Mapping) else None
    require(
        isinstance(data, Mapping),
        "network_response_diagnostic",
        "bridge has no persisted browser response observation",
        payload,
    )
    require(
        data.get("active_operation_id") == operation_id,
        "network_response_diagnostic",
        "latest browser response observation belongs to a different operation",
        {
            "expected_operation_id": operation_id,
            "observed_operation_id": data.get("active_operation_id"),
        },
    )
    return dict(data)


def summarize_output(step_results: list[dict[str, Any]], final: Mapping[str, Any]) -> None:
    print("PASI LIVE CDP DIAGNOSTIC")
    print("=" * 28)
    for item in step_results:
        status = "PASS" if item.get("ok") else "FAIL"
        print(f"{status:4} {item['step']}: {item.get('summary', '')}")
    print()
    print("FINAL:", json.dumps(dict(final), indent=2, sort_keys=True))


def run_local_tests(root: Path) -> dict[str, Any]:
    commands = [
        ([sys.executable, "-m", "pytest", "-q"], "pytest"),
        (["node", "--test", "web/app.test.js"], "frontend"),
    ]
    results: dict[str, Any] = {}
    for command, name in commands:
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
            check=False,
        )
        output = completed.stdout[-6000:]
        results[name] = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "output_tail": output,
        }
        if completed.returncode != 0:
            raise DiagnosticFailure(
                f"local_tests.{name}",
                f"{name} failed",
                results[name],
            )
    return results


def make_probe_prompt(marker: str) -> str:
    return (
        "PASI LIVE CDP DIAGNOSTIC. "
        "Do not modify files, call tools, or perform repository work. "
        f"Reply with exactly this marker on its own line and nothing else: {marker}"
    )


def wait_until_status(
    transport: UrllibBridgeTransport,
    operation_id: str,
    wanted: set[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        operation = read_operation(transport, operation_id)
        if str(operation.get("status")) in wanted:
            return operation
        time.sleep(0.25)
    raise DiagnosticFailure(
        "operation_wait",
        f"operation {operation_id} did not reach {sorted(wanted)} within {timeout_seconds}s",
        {"operation": read_operation(transport, operation_id)},
    )


def acknowledge_processed(
    transport: UrllibBridgeTransport,
    operation: Mapping[str, Any],
) -> dict[str, Any]:
    controller_id = str(operation.get("controller_id") or "").strip()
    require(
        bool(controller_id),
        "response_processing_ack",
        "completed operation has no controller_id",
        {"operation_id": operation.get("operation_id")},
    )
    payload = dict(
        request(
            transport,
            "POST",
            "/chat/processed",
            {
                "operation_id": operation["operation_id"],
                "controller_id": controller_id,
            },
        )
    )
    processed = payload.get("operation")
    if not isinstance(processed, Mapping):
        processed = payload
    require(
        processed.get("response_processing_complete") is True,
        "response_processing_ack",
        "bridge did not mark response processing complete",
        payload,
    )
    return dict(processed)


def exercise_handoff(
    transport: UrllibBridgeTransport,
    first: Mapping[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    first_id = str(first["operation_id"])
    second_marker = f"PASI_LIVE_HANDOFF_{uuid.uuid4().hex[:10].upper()}"
    second_payload = dict(
        request(
            transport,
            "POST",
            "/queue",
            {
                "operation_type": "prompt",
                "prompt": make_probe_prompt(second_marker),
                "completion_markers": [second_marker],
                "idempotency_key": f"live-diagnostic-{uuid.uuid4().hex}",
            },
        )
    )
    second = second_payload.get("operation")
    require(
        isinstance(second, Mapping),
        "handoff.queue",
        "bridge did not return the second queued operation",
        second_payload,
    )
    second_id = str(second["operation_id"])

    time.sleep(0.5)
    pre_ack = read_operation(transport, second_id)
    require(
        pre_ack.get("status") == "queued",
        "handoff.barrier",
        "second prompt was claimed before response processing acknowledgement",
        {
            "first_operation_id": first_id,
            "second_operation": pre_ack,
        },
    )

    acknowledge_processed(transport, first)
    second_terminal = wait_until_status(
        transport,
        second_id,
        {"completed", "failed", "interrupted", "cancelled"},
        timeout_seconds,
    )
    require(
        second_terminal.get("status") == "completed",
        "handoff.second_completion",
        "second prompt did not complete successfully after processing acknowledgement",
        {"operation": second_terminal},
    )
    require(
        second_terminal.get("predecessor_operation_id") == first_id,
        "handoff.predecessor",
        "second prompt did not carry predecessor completion evidence",
        {"operation": second_terminal, "first_operation_id": first_id},
    )
    timing = second_terminal.get("timing")
    require(
        isinstance(timing, Mapping) and isinstance(timing.get("response_completed_to_prompt_injected_ms"), (int, float)),
        "handoff.timing",
        "second prompt has no response_completed_to_prompt_injected_ms diagnostic",
        {"timing": timing},
    )

    response_data = read_response_diagnostic(transport, second_id)
    require(
        response_data.get("network_source") == "cdp_fetch"
        and response_data.get("event_type") == "COMPLETED"
        and response_data.get("stream_complete") is True,
        "handoff.network_response",
        "second prompt was not captured as an authoritative completed CDP stream",
        response_data,
    )
    acknowledge_processed(transport, second_terminal)
    return second_terminal, response_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live PASI CDP diagnostic: exercise the real bridge/CDP path without browser-tab automation."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Engineering Workspace root.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument(
        "--no-handoff",
        action="store_true",
        help="Run a single prompt probe instead of the two-operation handoff exercise.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit one machine-readable JSON object.",
    )
    args = parser.parse_args()
    root = args.repo.expanduser().resolve()
    transport = UrllibBridgeTransport(timeout_seconds=10.0)
    step_results: list[dict[str, Any]] = []

    try:
        health = read_health(transport)
        step_results.append({"ok": True, "step": "bridge_health", "summary": "authenticated localhost bridge is the expected Engineering Workspace service"})

        browser = read_browser_diagnostics(transport)
        step_results.append({
            "ok": True,
            "step": "browser_health",
            "summary": "live ChatGPT session reports native controller, CDP controller v1, and network authority",
        })

        local_tests: dict[str, Any] | None = None
        if not args.skip_tests:
            local_tests = run_local_tests(root)
            step_results.append({"ok": True, "step": "local_tests", "summary": "pytest and frontend tests passed"})

        marker = f"PASI_LIVE_CDP_{uuid.uuid4().hex[:10].upper()}"
        adapter = ChatGPTAdapter(
            transport=transport,
            session_id=f"live-diagnostic-{uuid.uuid4().hex}",
            poll_interval_seconds=0.25,
            max_wait_seconds=args.timeout,
        )
        operation_id = adapter.submit_prompt(make_probe_prompt(marker), completion_markers=[marker])
        step_results.append({
            "ok": True,
            "step": "prompt_submission",
            "summary": f"queued live probe operation {operation_id}",
        })

        terminal = wait_until_status(
            transport,
            operation_id,
            {"completed", "failed", "interrupted", "cancelled"},
            args.timeout,
        )
        require(
            terminal.get("status") == "completed",
            "prompt_completion",
            "live probe did not complete successfully",
            {"operation": terminal},
        )
        require(
            isinstance(terminal.get("response_text"), str)
            and marker in str(terminal.get("response_text")),
            "response_text",
            "completed operation does not contain the diagnostic marker",
            {"operation_id": operation_id, "response_text_available": terminal.get("response_text_available")},
        )
        step_results.append({"ok": True, "step": "prompt_completion", "summary": "live operation completed with the expected response marker"})

        response_data = read_response_diagnostic(transport, operation_id)
        require(
            response_data.get("network_source") == "cdp_fetch",
            "network_authority",
            "live response was not sourced from cdp_fetch",
            response_data,
        )
        require(
            response_data.get("event_type") == "COMPLETED"
            and response_data.get("stream_complete") is True,
            "network_completion",
            "live network observation is not an authoritative completed stream",
            response_data,
        )
        telemetry = response_data.get("telemetry")
        require(
            isinstance(telemetry, Mapping)
            and telemetry.get("completionSignal")
            and telemetry.get("completionSignalSource"),
            "assistant_completion_signal",
            "CDP response lacks an assistant completion signal/source",
            {"telemetry": telemetry, "response": response_data},
        )
        require(
            terminal.get("network_response_authoritative") is True
            and terminal.get("response_source") == "cdp_fetch_stream",
            "operation_authority",
            "operation record does not retain authoritative CDP response provenance",
            {
                "network_response_authoritative": terminal.get("network_response_authoritative"),
                "response_source": terminal.get("response_source"),
            },
        )
        require(
            terminal.get("response_processing_required") is True
            and terminal.get("response_processing_complete") is False,
            "processing_barrier",
            "completed prompt did not expose the expected response-processing barrier before acknowledgement",
            {
                "response_processing_required": terminal.get("response_processing_required"),
                "response_processing_complete": terminal.get("response_processing_complete"),
            },
        )
        step_results.append({
            "ok": True,
            "step": "network_diagnostics",
            "summary": "CDP Fetch captured a completed authoritative stream with an assistant completion signal",
        })

        if args.no_handoff:
            processed = acknowledge_processed(transport, terminal)
            step_results.append({"ok": True, "step": "response_processing_ack", "summary": "processing acknowledgement released the handoff barrier"})
            final = {
                "ok": True,
                "mode": "single_prompt",
                "operation_id": operation_id,
                "chat_url": browser["data"].get("chat_url"),
                "completion_signal": telemetry.get("completionSignal"),
                "completion_signal_source": telemetry.get("completionSignalSource"),
                "response_processing_complete": processed.get("response_processing_complete"),
                "local_tests": local_tests,
            }
        else:
            second, second_response = exercise_handoff(transport, terminal, args.timeout)
            step_results.append({
                "ok": True,
                "step": "handoff",
                "summary": "second prompt stayed queued until processing acknowledgement, then completed through CDP",
            })
            final = {
                "ok": True,
                "mode": "two_operation_handoff",
                "first_operation_id": operation_id,
                "second_operation_id": second["operation_id"],
                "first_completion_signal": telemetry.get("completionSignal"),
                "first_completion_signal_source": telemetry.get("completionSignalSource"),
                "second_response_source": second.get("response_source"),
                "second_network_source": second_response.get("network_source"),
                "second_response_completed_to_prompt_injected_ms": second["timing"].get("response_completed_to_prompt_injected_ms") if isinstance(second.get("timing"), Mapping) else None,
                "local_tests": local_tests,
            }

        if args.as_json:
            print(json.dumps({"ok": True, "steps": step_results, "final": final}, indent=2, sort_keys=True))
        else:
            summarize_output(step_results, final)
        return 0

    except DiagnosticFailure as exc:
        step_results.append({"ok": False, "step": exc.step, "summary": exc.message, "evidence": exc.evidence})
        payload = {"ok": False, "steps": step_results, "failure": exc.as_dict()}
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            summarize_output(step_results, {"ok": False, "failure": exc.as_dict()})
        return 1
    except Exception as exc:
        failure = DiagnosticFailure(
            "unexpected_error",
            f"{type(exc).__name__}: {exc}",
        )
        step_results.append({"ok": False, "step": failure.step, "summary": failure.message})
        payload = {"ok": False, "steps": step_results, "failure": failure.as_dict()}
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            summarize_output(step_results, {"ok": False, "failure": failure.as_dict()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
