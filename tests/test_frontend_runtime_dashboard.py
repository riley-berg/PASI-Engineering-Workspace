import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from pasi.core.event_store import SQLiteEventStore
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
