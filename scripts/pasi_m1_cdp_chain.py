#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.computer_use.chatgpt import ChatGPTAdapter, UrllibBridgeTransport
from scripts.pasi_live_cdp_diagnostic import (
    acknowledge_processed,
    diagnostic_response_matches_marker,
    make_probe_prompt,
    read_browser_diagnostics,
    read_health,
    read_operation,
    wait_until_status,
)

TARGET_OPERATIONS = 20
DEFAULT_TIMEOUT_SECONDS = 300.0
EXECUTION_MODE = "supervised_m1"
STATE_DIR = Path(os.environ.get("PASI_M1_STATE_DIR", "~/.pasi/m1-cdp-chain")).expanduser()
BRIDGE_RUNTIME_DIR = Path(os.environ.get("PASI_RUNTIME_DIR", "~/.pasi/overnight")).expanduser().resolve()
LEGACY_PID_PATH = Path.home() / ".pasi" / "engineering-workspace-168h" / "runner.pid"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_runner_pid(pid: int) -> None:
    for path in (STATE_DIR / "runner.pid", BRIDGE_RUNTIME_DIR / "runner.pid", LEGACY_PID_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid}\n", encoding="utf-8")


def clear_runner_pid() -> None:
    for path in (STATE_DIR / "runner.pid", BRIDGE_RUNTIME_DIR / "runner.pid", LEGACY_PID_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def write_runner_state(payload: Mapping[str, Any]) -> None:
    state = dict(payload)
    state.setdefault("execution_mode", EXECUTION_MODE)
    atomic_write_json(STATE_DIR / "state.json", state)
    atomic_write_json(BRIDGE_RUNTIME_DIR / "state.json", state)


def fail_closed_if_active(transport: UrllibBridgeTransport) -> None:
    try:
        payload = dict(transport.request("GET", "/runner/state"))
    except Exception:
        return
    if payload.get("available") is True and payload.get("status") == "running":
        raise RuntimeError("a supervised PASI runner is already active; stop it before starting the M1 chain")


def validate_completed_operation(
    transport: UrllibBridgeTransport,
    operation: Mapping[str, Any],
    marker: str,
    previous_operation_id: str | None,
    initial_chat_url: str,
    request_ids: set[str],
    index: int,
) -> tuple[dict[str, Any], str]:
    operation_id = str(operation["operation_id"])

    if operation.get("status") != "completed":
        raise RuntimeError(f"M1 operation {index} did not complete: {operation.get('status')}")

    if not diagnostic_response_matches_marker(operation.get("response_text"), marker):
        raise RuntimeError(f"M1 operation {index} response did not exactly match its marker")

    controller_id = str(operation.get("controller_id") or "").strip()
    if not controller_id:
        raise RuntimeError(f"M1 operation {index} has no controller_id")

    request_id = str(operation.get("network_request_id") or "").strip()
    if not request_id:
        raise RuntimeError(f"M1 operation {index} has no CDP request id")
    if request_id in request_ids:
        raise RuntimeError(f"M1 operation {index} reused CDP request id {request_id}")
    request_ids.add(request_id)

    if operation.get("network_response_authoritative") is not True:
        raise RuntimeError(f"M1 operation {index} lacks authoritative CDP response provenance")
    if operation.get("response_source") != "cdp_fetch_stream":
        raise RuntimeError(f"M1 operation {index} did not use cdp_fetch_stream")

    predecessor = operation.get("predecessor_operation_id")
    if previous_operation_id is not None and predecessor != previous_operation_id:
        raise RuntimeError(
            f"M1 operation {index} predecessor mismatch: expected {previous_operation_id}, got {predecessor}"
        )
    if previous_operation_id is None and predecessor not in {None, ""}:
        raise RuntimeError("M1 first operation unexpectedly carried predecessor evidence")

    current_browser = read_browser_diagnostics(transport)
    current_chat_url = str(current_browser["data"].get("chat_url") or "").strip()
    if current_chat_url != initial_chat_url:
        raise RuntimeError(f"M1 operation {index} changed ChatGPT conversation URL")

    response_payload = dict(transport.request("GET", "/browser/response"))
    response_observation = response_payload.get("observation")
    data = response_observation.get("data") if isinstance(response_observation, Mapping) else None
    if not isinstance(data, Mapping):
        raise RuntimeError(f"M1 operation {index} has no browser response evidence")
    if data.get("active_operation_id") != operation_id:
        raise RuntimeError(f"M1 operation {index} response evidence has the wrong operation id")
    if data.get("network_source") != "cdp_fetch" or data.get("event_type") != "COMPLETED":
        raise RuntimeError(f"M1 operation {index} lacks completed CDP response evidence")
    if data.get("stream_complete") is not True:
        raise RuntimeError(f"M1 operation {index} CDP stream was not complete")

    processed = acknowledge_processed(transport, operation)
    if processed.get("response_processing_complete") is not True:
        raise RuntimeError(f"M1 operation {index} response processing was not acknowledged")

    return processed, request_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the finite P0.2 M1 CDP chain with one just-in-time operation at a time."
    )
    parser.add_argument("--operations", type=int, default=TARGET_OPERATIONS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    if args.operations != TARGET_OPERATIONS:
        parser.error(f"M1 acceptance is fixed to exactly {TARGET_OPERATIONS} operations")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    transport = UrllibBridgeTransport(timeout_seconds=10.0)
    fail_closed_if_active(transport)
    read_health(transport)
    browser = read_browser_diagnostics(transport)
    initial_chat_url = str(browser["data"].get("chat_url") or "").strip()
    if not initial_chat_url:
        raise RuntimeError("browser health did not provide a current ChatGPT conversation URL")

    run_id = f"m1-cdp-{uuid.uuid4().hex}"
    started_at = utcnow()

    write_runner_state({
        "run_id": run_id,
        "repo": "th3-st0v3/PASI-Engineering-Workspace",
        "started_at": started_at.isoformat(),
        "status": "running",
        "execution_mode": EXECUTION_MODE,
        "target_operations": args.operations,
        "completed_operations": 0,
        "chat_url": initial_chat_url,
    })
    write_runner_pid(os.getpid())
    atexit.register(clear_runner_pid)

    adapter = ChatGPTAdapter(
        transport=transport,
        session_id=run_id,
        poll_interval_seconds=0.25,
        max_wait_seconds=args.timeout,
    )

    request_ids: set[str] = set()
    previous_operation_id: str | None = None
    completed = 0

    try:
        for index in range(1, args.operations + 1):
            marker = f"PASI_M1_CDP_{index:02d}_{uuid.uuid4().hex[:10].upper()}"
            operation_id = adapter.submit_prompt(
                make_probe_prompt(marker),
                completion_markers=[marker],
            )
            print(
                f"M1 SUBMITTED {index:02d}/{args.operations}: {operation_id}",
                flush=True,
            )

            terminal = wait_until_status(
                transport,
                operation_id,
                {"completed", "failed", "interrupted", "cancelled"},
                args.timeout,
            )
            processed, request_id = validate_completed_operation(
                transport,
                terminal,
                marker,
                previous_operation_id,
                initial_chat_url,
                request_ids,
                index,
            )

            completed = index
            previous_operation_id = operation_id
            write_runner_state({
                "run_id": run_id,
                "repo": "th3-st0v3/PASI-Engineering-Workspace",
                "started_at": started_at.isoformat(),
                "status": "running",
                "execution_mode": EXECUTION_MODE,
                "target_operations": args.operations,
                "completed_operations": completed,
                "chat_url": initial_chat_url,
                "last_operation_id": operation_id,
                "last_request_id": request_id,
                "last_updated_at": utcnow().isoformat(),
            })
            print(
                f"M1 COMPLETED {completed:02d}/{args.operations}: {operation_id} "
                f"(response_processing_complete={processed.get('response_processing_complete')})",
                flush=True,
            )

        write_runner_state({
            "run_id": run_id,
            "repo": "th3-st0v3/PASI-Engineering-Workspace",
            "started_at": started_at.isoformat(),
            "status": "completed",
            "execution_mode": "manual",
            "target_operations": args.operations,
            "completed_operations": completed,
            "chat_url": initial_chat_url,
            "completed_at": utcnow().isoformat(),
            "last_operation_id": previous_operation_id,
            "request_ids": len(request_ids),
        })
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "m1_supervised_cdp_chain",
                    "operations": completed,
                    "chat_url": initial_chat_url,
                    "request_ids": len(request_ids),
                    "last_operation_id": previous_operation_id,
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
        return 0

    except KeyboardInterrupt:
        write_runner_state({
            "run_id": run_id,
            "repo": "th3-st0v3/PASI-Engineering-Workspace",
            "status": "cancelled",
            "execution_mode": "manual",
            "target_operations": args.operations,
            "completed_operations": completed,
            "cancelled_at": utcnow().isoformat(),
            "chat_url": initial_chat_url,
        })
        return 130
    except Exception as exc:
        write_runner_state({
            "run_id": run_id,
            "repo": "th3-st0v3/PASI-Engineering-Workspace",
            "status": "failed",
            "execution_mode": "manual",
            "target_operations": args.operations,
            "completed_operations": completed,
            "failed_at": utcnow().isoformat(),
            "error": str(exc)[:2000],
            "chat_url": initial_chat_url,
        })
        print(f"M1 FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
