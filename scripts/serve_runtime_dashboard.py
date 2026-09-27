from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

from pasi.core.event_store import SQLiteEventStore
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService, serve
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import RuntimeHealth, RuntimeHealthStore
from pasi.core.runtime_projection import RuntimeIdentity, RuntimeProjectionService


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
DEFAULT_STATE_DIR = ROOT / ".runtime" / "dashboard"


def code_head() -> str:
    configured = os.environ.get("PASI_CODE_HEAD", "").strip()
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    state_dir = Path(os.environ.get("PASI_DASHBOARD_STATE_DIR", str(DEFAULT_STATE_DIR)))
    token = os.environ.get("PASI_RUNTIME_TOKEN", "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        print("Generated ephemeral runtime-control token for this process:", token)

    operation_store = SQLiteOperationStateStore(state_dir / "operation.db")
    event_store = SQLiteEventStore(state_dir / "events.db")
    ledger = SQLiteOperationLedger(state_dir / "ledger.db")
    health_store = RuntimeHealthStore(state_dir / "health.db")
    command_store = RuntimeCommandStore(state_dir / "commands.db")

    try:
        health_store.get()
    except Exception:
        health_store.create(
            RuntimeHealth.connected(
                controller_version=os.environ.get("PASI_CONTROLLER_VERSION", "unknown"),
                runner_version=os.environ.get("PASI_RUNNER_VERSION", "unknown"),
                provider_version=os.environ.get("PASI_PROVIDER_VERSION", "unknown"),
                code_head=code_head(),
            )
        )

    identity = RuntimeIdentity(
        code_head=code_head(),
        runtime_version=os.environ.get("PASI_RUNTIME_VERSION", "pasi-runtime-1"),
        controller_version=os.environ.get("PASI_CONTROLLER_VERSION", "unknown"),
        runner_version=os.environ.get("PASI_RUNNER_VERSION", "unknown"),
    )
    controls = RuntimeControlService(
        operation_store=operation_store,
        event_store=event_store,
        command_store=command_store,
        authorization_token=token,
    )
    projection = RuntimeProjectionService(
        operation_store=operation_store,
        event_store=event_store,
        ledger=ledger,
        health_store=health_store,
        identity=identity,
    )
    service = RuntimeAPIService(
        projection=projection,
        event_feed=RuntimeEventFeed(event_store),
        health_store=health_store,
        controls=controls,
    )

    print("PASI runtime dashboard: http://127.0.0.1:8790/")
    print("Set ?operation_id=<operation-id> to open an operation.")
    serve(
        service=service,
        host="127.0.0.1",
        port=int(os.environ.get("PASI_DASHBOARD_PORT", "8790")),
        static_root=WEB_ROOT,
    )


if __name__ == "__main__":
    main()
