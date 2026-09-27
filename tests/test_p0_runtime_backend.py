import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from pasi.core.event_store import SQLiteEventStore
from pasi.core.events import DurableEvent
from pasi.core.ledger import OperationLedgerEntry
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.operation_state import OperationState
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService, make_handler
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import (
    ConnectionStatus,
    RuntimeHealth,
    RuntimeHealthStore,
)
from pasi.core.runtime_projection import RuntimeIdentity, RuntimeProjectionService


def build_runtime(tmp_path: Path) -> tuple[RuntimeAPIService, SQLiteOperationStateStore]:
    operation_store = SQLiteOperationStateStore(tmp_path / "operation.db")
    event_store = SQLiteEventStore(tmp_path / "events.db")
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    health_store = RuntimeHealthStore(tmp_path / "health.db")
    command_store = RuntimeCommandStore(tmp_path / "commands.db")

    health_store.create(
        RuntimeHealth.connected(
            controller_version="ctrl-1",
            runner_version="runner-1",
            provider_version="provider-1",
            code_head="abc123",
        )
    )

    state = OperationState(
        operation_id="op-1",
        operation_type="roadmap_task",
        run_id="run-1",
        task_id="P0.5",
        provider="local",
        phase="queued",
    )
    operation_store.create(state)
    ledger.register(
        OperationLedgerEntry(
            operation_id="op-1",
            task_id="P0.5",
            run_id="run-1",
            provider="local",
            branch="pasi/p0",
            outcome="queued",
        )
    )
    event_store.append(
        DurableEvent(
            event_id="event-1",
            event_type="operation.queued",
            source="runner",
            operation_id="op-1",
            task_id="P0.5",
            run_id="run-1",
            correlation_id="run-1",
            payload={"status": "queued"},
            evidence_refs=("evidence://op-1/queued",),
        )
    )

    controls = RuntimeControlService(
        operation_store=operation_store,
        event_store=event_store,
        command_store=command_store,
        authorization_token="secret-token",
    )
    projection = RuntimeProjectionService(
        operation_store=operation_store,
        event_store=event_store,
        ledger=ledger,
        health_store=health_store,
        identity=RuntimeIdentity(
            code_head="abc123",
            runtime_version="p0-runtime-1",
            controller_version="ctrl-1",
            runner_version="runner-1",
        ),
    )
    return (
        RuntimeAPIService(
            projection=projection,
            event_feed=RuntimeEventFeed(event_store),
            health_store=health_store,
            controls=controls,
        ),
        operation_store,
    )


def http_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=payload,
        method=method,
        headers={
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def start_server(service: RuntimeAPIService):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_p0_runtime_projection_rehydrates_operation_lineage_events_evidence_health(tmp_path):
    service, _ = build_runtime(tmp_path)
    status, body = service.request(
        method="GET",
        path="/v1/runtime/operations/op-1",
        headers={},
    )

    assert status == 200
    assert body["operation"]["operation_id"] == "op-1"
    assert body["operation"]["task_id"] == "P0.5"
    assert body["lineage"][0]["operation_id"] == "op-1"
    assert body["events"][0]["event_type"] == "operation.queued"
    assert body["evidence_refs"] == ["evidence://op-1/queued"]
    assert body["runtime"]["code_head"] == "abc123"
    assert body["health"]["connection_status"] == "connected"


def test_p0_event_feed_is_ordered_and_collects_evidence(tmp_path):
    service, _ = build_runtime(tmp_path)
    service.event_feed.event_store.append(
        DurableEvent(
            event_id="event-2",
            event_type="verification.completed",
            source="verifier",
            operation_id="op-1",
            task_id="P0.5",
            run_id="run-1",
            causation_id="event-1",
            payload={"status": "PASS"},
            evidence_refs=("evidence://op-1/verify", "evidence://op-1/queued"),
        )
    )

    status, body = service.request(
        method="GET",
        path="/v1/runtime/events/op-1?limit=10",
        headers={},
    )
    assert status == 200
    assert [event["sequence"] for event in body["events"]] == [1, 2]
    assert body["evidence_refs"] == [
        "evidence://op-1/queued",
        "evidence://op-1/verify",
    ]


def test_p0_controls_are_authorized_typed_idempotent_and_preserve_operation_identity(tmp_path):
    service, store = build_runtime(tmp_path)

    status, unauthorized = service.request(
        method="POST",
        path="/v1/runtime/controls",
        headers={
            "Authorization": "Bearer wrong",
            "Idempotency-Key": "key-1",
        },
        body={
            "operation_id": "op-1",
            "action": "start",
            "expected_revision": 0,
        },
    )
    assert status == 401
    assert "authorization" in unauthorized["error"]

    headers = {
        "Authorization": "Bearer secret-token",
        "Idempotency-Key": "key-1",
    }
    status, first = service.request(
        method="POST",
        path="/v1/runtime/controls",
        headers=headers,
        body={
            "operation_id": "op-1",
            "action": "start",
            "expected_revision": 0,
        },
    )
    assert status == 200
    assert first["operation_id"] == "op-1"
    assert first["status"] == "claimed"
    assert first["state_revision"] == 1

    status, replay = service.request(
        method="POST",
        path="/v1/runtime/controls",
        headers=headers,
        body={
            "operation_id": "op-1",
            "action": "start",
            "expected_revision": 0,
        },
    )
    assert status == 200
    assert replay == first
    assert store.get("op-1").operation_id == "op-1"

    status, conflict = service.request(
        method="POST",
        path="/v1/runtime/controls",
        headers=headers,
        body={
            "operation_id": "op-1",
            "action": "stop",
            "expected_revision": 1,
        },
    )
    assert status == 409
    assert "different command" in conflict["error"]

    status, stopped = service.request(
        method="POST",
        path="/v1/runtime/controls",
        headers={
            "Authorization": "Bearer secret-token",
            "Idempotency-Key": "key-2",
        },
        body={
            "operation_id": "op-1",
            "action": "stop",
            "expected_revision": 1,
            "reason": "operator requested stop",
        },
    )
    assert status == 200
    assert stopped["status"] == "failed"
    assert store.get("op-1").operation_id == "op-1"


def test_p0_health_state_tracks_degrade_recovery_reconnect_and_restart(tmp_path):
    path = tmp_path / "health.db"
    store = RuntimeHealthStore(path)
    store.create(
        RuntimeHealth.connected(
            controller_version="ctrl-1",
            runner_version="runner-1",
        )
    )

    degraded = store.get().mark_degraded("heartbeat gap")
    store.save(degraded, expected_revision=0)
    recovering = store.get().mark_recovering("reconnect")
    store.save(recovering, expected_revision=1)
    disconnected = store.get().mark_disconnected("bridge unavailable")
    store.save(disconnected, expected_revision=2)

    restarted = RuntimeHealthStore(path)
    assert restarted.get().connection_status is ConnectionStatus.DISCONNECTED
    assert restarted.get().recovery_phase == "reconnect"


def test_p0_runtime_api_works_over_loopback_http(tmp_path):
    service, _ = build_runtime(tmp_path)
    server, thread = start_server(service)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        status, body = http_json(base, "GET", "/healthz")
        assert status == 200
        assert body["status"] == "ok"

        status, body = http_json(base, "GET", "/v1/runtime/operations/op-1")
        assert status == 200
        assert body["operation"]["operation_id"] == "op-1"

        status, body = http_json(
            base,
            "POST",
            "/v1/runtime/controls",
            body={
                "operation_id": "op-1",
                "action": "start",
                "expected_revision": 0,
            },
            headers={
                "Authorization": "Bearer secret-token",
                "Idempotency-Key": "http-key-1",
            },
        )
        assert status == 200
        assert body["state_revision"] == 1

        status, body = http_json(
            base,
            "GET",
            "/v1/runtime/health",
        )
        assert status == 200
        assert body["health"]["controller_version"] == "ctrl-1"
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/runtime/operations/missing",
        "/v1/runtime/events/missing?limit=0",
    ],
)
def test_p0_api_rejects_missing_or_invalid_resources(tmp_path, path):
    service, _ = build_runtime(tmp_path)
    status, _ = service.request(method="GET", path=path, headers={})
    assert status in {404, 400}
