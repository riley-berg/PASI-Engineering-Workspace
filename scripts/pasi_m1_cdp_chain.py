#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
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
STATE_DIR = Path(
    os.environ.get(
        "PASI_M1_STATE_DIR",
        "~/.pasi/m1-cdp-chain",
    )
).expanduser()
BRIDGE_RUNTIME_DIR = Path(
    os.environ.get(
        "PASI_RUNTIME_DIR",
        "~/.pasi/overnight",
    )
).expanduser().resolve()
LEGACY_PID_PATH = Path.home() / ".pasi" / "engineering-workspace-168h" / "runner.pid"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_runner_pid(pid: int) -> None:
    for path in (
        STATE_DIR / "runner.pid",
        BRIDGE_RUNTIME_DIR / "runner.pid",
        LEGACY_PID_PATH,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid}\n", encoding="utf-8")


def clear_runner_pid() -> None:
    for path in (
        STATE_DIR / "runner.pid",
        BRIDGE_RUNTIME_DIR / "runner.pid",
        LEGACY_PID_PATH,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def write_runner_state(payload: Mapping[str, Any]) -> None:
    state = dict(payload)
    state.setdefault("execution_mode", EXECUTION_MODE)
    atomic_write_json(STATE_DIR / "state.json", state)
    runtime_state = dict(state)
    atomic_write_json(BRIDGE_RUNTIME_DIR / "state.json", runtime_state)


def fail_closed_if_active(transport: UrllibBridgeTransport) -> None:
    try:
        payload = dict(transport.request("GET", "/runner/state"))
    except Exception:
        return
    if payload.get("available") is True and payload.get("status") == "running":
        raise RuntimeError(
            "a supervised PASI runner is already active; stop it before starting the M1 chain"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the finite P0.2 M1 CDP acceptance chain using the same supervised handoff path as the 168-hour runner."
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
    deadline_at = started_at + timedelta(seconds=args.operations * args.timeout)

    write_runner_state({
        "run_id": run_id,
        "repo": "th3-st0v3/PASI-Engineering-Workspace",
        "started_at": started_at.isoformat(),
        "deadline_at": deadline_at.isoformat(),
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

    operations: list[dict[str, Any]] = []
    markers: dict[str, str] = {}
    previous_operation_id: str | None = None
    request_ids: set[str] = set()

    try:
        for index in range(1, args.operations + 1):
            marker = f"PASI_M1_CDP_{index:02d}_{uuid.uuid4().hex[:10].upper()}"
            operation_id = adapter.submit_prompt(
                make_probe_prompt(marker),
                completion_markers=[marker],
            )
            markers[operation_id] = marker
            operations.append({
                "index": index,
                "operation_id": operation_id,
                "status": "queued",
            })
            print(
                f"M1 QUEUED {index:02d}/{args.operations}: {operation_id}",
                flush=True,
            )

        for index, record in enumerate(operations, start=1):
            operation_id = str(record["operation_id"])
            terminal = wait_until_status(
                transport,
                operation_id,
                {"completed", "failed", "interrupted", "cancelled"},
                args.timeout,
            )
            if terminal.get("status") != "completed":
                raise RuntimeError(
                    f"M1 operation {index} did not complete: {terminal.get('status')}"
                )

            marker = markers[operation_id]
            if not diagnostic_response_matches_marker(
                terminal.get("response_text"),
                marker,
            ):
                raise RuntimeError(
                    f"M1 operation {index} response did not exactly match its marker"
                )

            controller_id = str(terminal.get("controller_id") or "").strip()
            if not controller_id:
                raise RuntimeError(f"M1 operation {index} has no controller_id")

            request_id = str(terminal.get("network_request_id") or "").strip()
            if not request_id:
                raise RuntimeError(f"M1 operation {index} has no CDP request id")
            if request_id in request_ids:
                raise RuntimeError(f"M1 operation {index} reused CDP request id {request_id}")
            request_ids.add(request_id)

            if terminal.get("network_response_authoritative") is not True:
                raise RuntimeError(f"M1 operation {index} lacks authoritative CDP response provenance")
            if terminal.get("response_source") != "cdp_fetch_stream":
                raise RuntimeError(f"M1 operation {index} did not use cdp_fetch_stream")

            predecessor = terminal.get("predecessor_operation_id")
            if previous_operation_id is not None and predecessor != previous_operation_id:
                raise RuntimeError(
                    f"M1 operation {index} predecessor mismatch: "
                    f"expected {previous_operation_id}, got {predecessor}"
                )
            if previous_operation_id is None and predecessor not in {None, ""}:
                raise RuntimeError("M1 first operation unexpectedly carried predecessor evidence")

            current_browser = read_browser_diagnostics(transport)
            current_chat_url = str(current_browser["data"].get("chat_url") or "").strip()
            if current_chat_url != initial_chat_url:
                raise RuntimeError(
                    f"M1 operation {index} changed ChatGPT conversation URL"
                )

            response_observation = dict(
                transport.request("GET", "/browser/response")
            ).get("observation")
            data = response_observation.get("data") if isinstance(response_observation, Mapping) else None
            if not isinstance(data, Mapping):
                raise RuntimeError(f"M1 operation {index} has no browser response evidence")
            if data.get("active_operation_id") != operation_id:
                raise RuntimeError(f"M1 operation {index} response evidence has the wrong operation id")
            if data.get("network_source") != "cdp_fetch" or data.get("event_type") != "COMPLETED":
                raise RuntimeError(f"M1 operation {index} lacks completed CDP response evidence")
            if data.get("stream_complete") is not True:
                raise RuntimeError(f"M1 operation {index} CDP stream was not complete")

            processed = acknowledge_processed(transport, terminal)
            if processed.get("response_processing_complete") is not True:
                raise RuntimeError(f"M1 operation {index} response processing was not acknowledged")

            previous_operation_id = operation_id
            record.update({
                "status": "completed",
                "controller_id": controller_id,
                "network_request_id": request_id,
            })

            write_runner_state({
                "run_id": run_id,
                "repo": "th3-st0v3/PASI-Engineering-Workspace",
                "started_at": started_at.isoformat(),
                "deadline_at": deadline_at.isoformat(),
                "status": "running",
                "execution_mode": EXECUTION_MODE,
                "target_operations": args.operations,
                "completed_operations": index,
                "chat_url": initial_chat_url,
                "last_operation_id": operation_id,
                "last_request_id": request_id,
                "last_updated_at": utcnow().isoformat(),
            })
            print(
                f"M1 COMPLETED {index:02d}/{args.operations}: {operation_id}",
                flush=True,
            )

        write_runner_state({
            "run_id": run_id,
            "repo": "th3-st0v3/PASI-Engineering-Workspace",
            "started_at": started_at.isoformat(),
            "deadline_at": deadline_at.isoformat(),
            "status": "completed",
            "execution_mode": "manual",
            "target_operations": args.operations,
            "completed_operations": args.operations,
            "chat_url": initial_chat_url,
            "completed_at": utcnow().isoformat(),
            "last_operation_id": previous_operation_id,
        })
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "m1_supervised_cdp_chain",
                    "operations": args.operations,
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
            "completed_operations": sum(
                1 for record in operations if record.get("status") == "completed"
            ),
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
            "completed_operations": sum(
                1 for record in operations if record.get("status") == "completed"
            ),
            "failed_at": utcnow().isoformat(),
            "error": str(exc)[:2000],
            "chat_url": initial_chat_url,
        })
        print(f"M1 FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
