import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from pasi.core.event_store import SQLiteEventStore
from pasi.core.failure_registry import SQLiteFailureRegistry, signature_key
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.operation_state import OperationState
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService, make_handler
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import RuntimeHealth, RuntimeHealthStore
from pasi.core.runtime_projection import RuntimeIdentity, RuntimeProjectionService


def test_frontend_runtime_dashboard_assets_exist_and_reference_real_runtime_contract():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="operation-id"' in html
    assert 'id="timeline"' in html
    assert 'data-action="recover"' in html
    assert "/v1/runtime/health" in js
    assert "/v1/runtime/operations/" in js
    assert "/v1/runtime/controls" in js


def test_dashboard_server_script_uses_loopback_runtime_api():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "serve_runtime_dashboard.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in script
    assert "static_root=WEB_ROOT" in script


def test_dashboard_is_exercised_against_the_real_runtime_api(tmp_path):
    root = Path(__file__).resolve().parents[1]
    operation_store = SQLiteOperationStateStore(tmp_path / "operation.db")
    event_store = SQLiteEventStore(tmp_path / "events.db")
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    health_store = RuntimeHealthStore(tmp_path / "health.db")
    command_store = RuntimeCommandStore(tmp_path / "commands.db")

    health_store.create(
        RuntimeHealth.connected(
            controller_version="ctrl-test",
            runner_version="runner-test",
            provider_version="provider-test",
            code_head="test-head",
        )
    )
    operation_store.create(
        OperationState(
            operation_id="op-fe-p0",
            operation_type="roadmap_task",
            run_id="run-fe-p0",
            task_id="P0.5",
            provider="local",
            phase="queued",
        )
    )

    service = RuntimeAPIService(
        projection=RuntimeProjectionService(
            operation_store=operation_store,
            event_store=event_store,
            ledger=ledger,
            health_store=health_store,
            identity=RuntimeIdentity(
                code_head="test-head",
                runtime_version="pasi-runtime-test",
                controller_version="ctrl-test",
                runner_version="runner-test",
            ),
        ),
        event_feed=RuntimeEventFeed(event_store),
        health_store=health_store,
        controls=RuntimeControlService(
            operation_store=operation_store,
            event_store=event_store,
            command_store=command_store,
            authorization_token="test-token",
        ),
    )

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(service, static_root=root / "web"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert "PASI Runtime Dashboard" in html

        with urllib.request.urlopen(base + "/app.js", timeout=5) as response:
            js = response.read().decode("utf-8")
            assert response.status == 200
            assert "/v1/runtime/operations/" in js

        with urllib.request.urlopen(base + "/v1/runtime/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["health"]["connection_status"] == "connected"
            assert payload["health"]["code_head"] == "test-head"

        request = urllib.request.Request(
            base + "/v1/runtime/operations/op-fe-p0",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["operation"]["operation_id"] == "op-fe-p0"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_fe_p1_console_endpoints_return_durable_control_plane_data(tmp_path):
    operation_store = SQLiteOperationStateStore(tmp_path / "operation.db")
    event_store = SQLiteEventStore(tmp_path / "events.db")
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    health_store = RuntimeHealthStore(tmp_path / "health.db")
    command_store = RuntimeCommandStore(tmp_path / "commands.db")
    failure_registry = SQLiteFailureRegistry(tmp_path / "failures.db")

    health_store.create(
        RuntimeHealth.connected(
            controller_version="ctrl-test",
            runner_version="runner-test",
            provider_version="provider-test",
            code_head="head-test",
        )
    )
    operation_store.create(
        OperationState(
            operation_id="op-failure-1",
            operation_type="roadmap_task",
            run_id="run-1",
            task_id="P1.7",
            provider="local",
            failure_signature="sig-1",
        )
    )

    from pasi.core.ledger import OperationLedgerEntry

    ledger.register(
        OperationLedgerEntry(
            operation_id="op-failure-1",
            task_id="P1.7",
            run_id="run-1",
            provider="local",
            branch="pasi/p1",
            pr_number=101,
            outcome="failed",
        )
    )
    failure = failure_registry.record(
        subsystem="controller",
        failure_code="connection_lost",
        failure_family="connection",
        operation_id="op-failure-1",
        evidence_ref="evidence://op-failure-1",
        seen_at="2026-01-01T00:00:00+00:00",
    )
    assert failure.signature_id == signature_key(
        subsystem="controller",
        failure_code="connection_lost",
        failure_family="connection",
    )

    service = RuntimeAPIService(
        projection=RuntimeProjectionService(
            operation_store=operation_store,
            event_store=event_store,
            ledger=ledger,
            health_store=health_store,
            identity=RuntimeIdentity(
                code_head="head-test",
                runtime_version="runtime-test",
                controller_version="ctrl-test",
                runner_version="runner-test",
            ),
        ),
        event_feed=RuntimeEventFeed(event_store),
        health_store=health_store,
        controls=RuntimeControlService(
            operation_store=operation_store,
            event_store=event_store,
            command_store=command_store,
            authorization_token="token",
        ),
        failure_registry=failure_registry,
    )

    status, ledger_payload = service.request(
        method="GET",
        path="/v1/runtime/ledger?task_id=P1.7&provider=local",
        headers={},
    )
    assert status == 200
    assert ledger_payload["count"] == 1
    assert ledger_payload["entries"][0]["operation_id"] == "op-failure-1"

    status, failure_payload = service.request(
        method="GET",
        path="/v1/runtime/failures",
        headers={},
    )
    assert status == 200
    assert failure_payload["count"] == 1
    assert failure_payload["signatures"][0]["affected_operations"] == ["op-failure-1"]
    assert failure_payload["signatures"][0]["current_code_head"] == "head-test"

    status, migration_payload = service.request(
        method="GET",
        path="/v1/runtime/migrations",
        headers={},
    )
    assert status == 200
    assert migration_payload["migration_required"] is False
    assert migration_payload["stored_version"] == 2
