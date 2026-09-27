#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from pasi.core.event_store import SQLiteEventStore
from pasi.core.human_test_store import HumanTestStore
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService, serve
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import RuntimeHealth, RuntimeHealthStore
from pasi.core.runtime_projection import RuntimeIdentity


def build_service(root: Path, *, ingest_token: str) -> RuntimeAPIService:
    root.mkdir(parents=True, exist_ok=True)
    operation_store = SQLiteOperationStateStore(root / "operations.db")
    event_store = SQLiteEventStore(root / "events.db")
    ledger = SQLiteOperationLedger(root / "ledger.db")
    health_store = RuntimeHealthStore(root / "health.db")
    command_store = RuntimeCommandStore(root / "commands.db")
    human_tests = HumanTestStore(root / "human-tests.db")

    try:
        health_store.get()
    except Exception:
        health_store.create(
            RuntimeHealth.connected(
                controller_version="human-test-api",
                runner_version="human-test-api",
                provider_version="n/a",
                code_head="0" * 40,
            )
        )

    from pasi.core.runtime_projection import RuntimeProjectionService

    projection = RuntimeProjectionService(
        operation_store=operation_store,
        event_store=event_store,
        ledger=ledger,
        health_store=health_store,
        identity=RuntimeIdentity(
            code_head="0" * 40,
            runtime_version="human-test-api",
            controller_version="human-test-api",
            runner_version="human-test-api",
        ),
    )
    controls = RuntimeControlService(
        operation_store=operation_store,
        event_store=event_store,
        command_store=command_store,
        authorization_token="human-test-api-disabled-controls",
    )
    return RuntimeAPIService(
        projection=projection,
        event_feed=RuntimeEventFeed(event_store),
        health_store=health_store,
        controls=controls,
        human_tests=human_tests,
        human_test_ingest_token=ingest_token,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument(
        "--db-root",
        type=Path,
        default=Path(".runtime/acceptance/human-test-api"),
    )
    parser.add_argument("--ingest-token", required=True)
    args = parser.parse_args()

    service = build_service(args.db_root, ingest_token=args.ingest_token)
    serve(service=service, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
