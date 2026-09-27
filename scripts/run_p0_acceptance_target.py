#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pasi.core.event_store import SQLiteEventStore
from pasi.core.events import DurableEvent
from pasi.core.human_test_store import HumanTestStore
from pasi.core.ledger import OperationLedgerEntry
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.operation_state import OperationState
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import RuntimeHealth, RuntimeHealthStore
from pasi.core.runtime_projection import RuntimeIdentity


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"


def git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_service(db_root: Path, *, ingest_token: str, control_token: str) -> RuntimeAPIService:
    db_root.mkdir(parents=True, exist_ok=True)
    operation_store = SQLiteOperationStateStore(db_root / "operations.db")
    event_store = SQLiteEventStore(db_root / "events.db")
    ledger = SQLiteOperationLedger(db_root / "ledger.db")
    health_store = RuntimeHealthStore(db_root / "health.db")
    command_store = RuntimeCommandStore(db_root / "commands.db")
    human_tests = HumanTestStore(db_root / "human-tests.db")
    code_head = git_head()

    # This is a dedicated acceptance target database. It is disposable and
    # explicitly labels its operation/evidence as test-fixture state.
    try:
        health_store.get()
    except Exception:
        health_store.create(
            RuntimeHealth.connected(
                controller_version="pasi-acceptance-target",
                runner_version="pasi-acceptance-target",
                provider_version="pasi-acceptance-target",
                code_head=code_head,
            )
        )

    try:
        operation_store.get("p0-human-test")
    except Exception:
        state = OperationState(
            operation_id="p0-human-test",
            operation_type="human_test_fixture",
            run_id="p0-human-test-run",
            task_id="P0.5",
            provider="acceptance-target",
            phase="test_fixture",
            metadata={
                "acceptance_fixture": "true",
                "acceptance_note": "disposable live human-test target",
            },
        )
        operation_store.create(state)
        ledger.register(
            OperationLedgerEntry(
                operation_id="p0-human-test",
                task_id="P0.5",
                run_id="p0-human-test-run",
                provider="acceptance-target",
                branch="acceptance-target",
                outcome="queued",
            )
        )
        event_store.append(
            DurableEvent(
                event_id="event-p0-human-test-seeded",
                event_type="acceptance.fixture.seeded",
                source="p0_acceptance_target",
                operation_id="p0-human-test",
                task_id="P0.5",
                run_id="p0-human-test-run",
                correlation_id="p0-human-test-run",
                payload={
                    "fixture": True,
                    "code_head": code_head,
                },
                evidence_refs=("acceptance://p0-human-test/seed",),
            )
        )

    projection = __import__("pasi.core.runtime_projection", fromlist=["RuntimeProjectionService"]).RuntimeProjectionService(
        operation_store=operation_store,
        event_store=event_store,
        ledger=ledger,
        health_store=health_store,
        identity=RuntimeIdentity(
            code_head=code_head,
            runtime_version="p0-acceptance-target",
            controller_version="pasi-acceptance-target",
            runner_version="pasi-acceptance-target",
        ),
    )
    controls = RuntimeControlService(
        operation_store=operation_store,
        event_store=event_store,
        command_store=command_store,
        authorization_token=control_token,
    )
    return RuntimeAPIService(
        projection=projection,
        event_feed=RuntimeEventFeed(event_store),
        health_store=health_store,
        controls=controls,
        human_tests=human_tests,
        human_test_ingest_token=ingest_token,
    )


def serve_target(
    *,
    service: RuntimeAPIService,
    host: str,
    port: int,
) -> None:
    web_root = WEB_ROOT.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "PASI-Acceptance-Target/0.1"

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _api(self, method: str) -> None:
            body = None
            if method == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid request body size"})
                    return
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return

            status, payload = service.request(
                method=method,
                path=self.path,
                headers=dict(self.headers.items()),
                body=body,
            )
            self._send_json(status, payload)

        def _static(self) -> None:
            parsed = urlparse(self.path)
            relative = unquote(parsed.path)
            if relative in {"", "/"}:
                relative = "/index.html"
            candidate = (web_root / relative.lstrip("/")).resolve()
            if web_root not in candidate.parents and candidate != web_root:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
            }.get(candidate.suffix, "application/octet-stream")
            data = candidate.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/healthz" or self.path.startswith("/v1/"):
                self._api("GET")
                return
            self._static()

        def do_POST(self) -> None:
            if self.path.startswith("/v1/"):
                self._api("POST")
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, fmt: str, *args: object) -> None:
            print("[PASI acceptance target] " + (fmt % args))

    server = ThreadingHTTPServer((host, port), Handler)
    print(
        f"P0_ACCEPTANCE_TARGET READY: http://{host}:{port}/ "
        f"operation=p0-human-test code_head={git_head()}",
        flush=True,
    )
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument(
        "--db-root",
        type=Path,
        default=Path(".runtime/acceptance/p0-human-test-target"),
    )
    parser.add_argument(
        "--ingest-token",
        required=True,
        help="Separate token the MV3 human-test extension may use to submit evidence.",
    )
    parser.add_argument(
        "--control-token",
        required=True,
        help="Separate token the MV3 human-test extension may use for typed runtime controls.",
    )
    args = parser.parse_args()

    if not WEB_ROOT.is_dir():
        raise SystemExit(f"web root is missing: {WEB_ROOT}")
    service = build_service(
        args.db_root,
        ingest_token=args.ingest_token,
        control_token=args.control_token,
    )
    serve_target(service=service, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
