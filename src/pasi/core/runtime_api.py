from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from pathlib import Path

from pasi.core.operation_store import OperationStateNotFound
from pasi.core.runtime_controls import (
    AuthorizationError,
    ControlError,
    IdempotencyConflict,
    RuntimeControlService,
)
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import HealthError, RuntimeHealthStore
from pasi.core.failure_registry import SQLiteFailureRegistry
from pasi.core.notifications import NotificationError, SQLiteNotificationStore
from pasi.core.runtime_projection import RuntimeProjectionService


class RuntimeAPIService:
    """Loopback-safe runtime API for projections, event feeds, health, and controls."""

    def __init__(
        self,
        *,
        projection: RuntimeProjectionService,
        event_feed: RuntimeEventFeed,
        health_store: RuntimeHealthStore,
        controls: RuntimeControlService,
        failure_registry: SQLiteFailureRegistry | None = None,
        notification_store: SQLiteNotificationStore | None = None,
        notification_scope: str = "runtime",
    ) -> None:
        self.projection = projection
        self.event_feed = event_feed
        self.health_store = health_store
        self.controls = controls
        self.failure_registry = failure_registry
        self.notification_store = notification_store
        self.notification_scope = notification_scope

    def request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(path)
        if method == "GET" and parsed.path == "/healthz":
            return HTTPStatus.OK, {"status": "ok"}

        if method == "GET" and parsed.path == "/v1/runtime/health":
            try:
                return HTTPStatus.OK, {"health": self.health_store.get().to_dict()}
            except HealthError as exc:
                return HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)}

        if method == "GET" and parsed.path.startswith("/v1/runtime/operations/"):
            operation_id = parsed.path.rsplit("/", 1)[-1]
            projection = self.projection.project_or_none(operation_id)
            if projection is None:
                return HTTPStatus.NOT_FOUND, {"error": "operation not found"}
            return HTTPStatus.OK, projection

        if method == "GET" and parsed.path.startswith("/v1/runtime/events/"):
            operation_id = parsed.path.rsplit("/", 1)[-1]
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
            except ValueError:
                return HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer"}
            try:
                return HTTPStatus.OK, self.event_feed.for_operation(
                    operation_id,
                    limit=limit,
                )
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}

        if method == "GET" and parsed.path == "/v1/runtime/ledger":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
                pr_number = (
                    int(query["pr_number"][0])
                    if query.get("pr_number")
                    else None
                )
            except ValueError:
                return HTTPStatus.BAD_REQUEST, {"error": "invalid ledger filter"}

            entries = self.projection.ledger.list(
                task_id=query.get("task_id", [None])[0],
                run_id=query.get("run_id", [None])[0],
                provider=query.get("provider", [None])[0],
                branch=query.get("branch", [None])[0],
                pr_number=pr_number,
                outcome=query.get("outcome", [None])[0],
                limit=limit,
            )
            return HTTPStatus.OK, {
                "entries": [entry.to_dict() for entry in entries],
                "count": len(entries),
            }

        if method == "GET" and parsed.path == "/v1/runtime/failures":
            if self.failure_registry is None:
                return HTTPStatus.NOT_IMPLEMENTED, {"error": "failure registry unavailable"}
            query = parse_qs(parsed.query)
            signatures = self.failure_registry.list(
                subsystem=query.get("subsystem", [None])[0]
            )
            result = []
            for signature in signatures:
                affected = self.projection.operation_store.list(
                    failure_signature=signature.signature_id,
                    limit=100,
                )
                evidence: list[str] = []
                for state in affected:
                    projection = self.projection.project_or_none(state.operation_id)
                    if projection is None:
                        continue
                    for ref in projection["evidence_refs"]:
                        if ref not in evidence:
                            evidence.append(ref)
                result.append({
                    **signature.__dict__,
                    "affected_operations": [
                        state.operation_id for state in affected
                    ],
                    "evidence_refs": evidence,
                    "current_code_head": self.projection.identity.code_head,
                })
            return HTTPStatus.OK, {
                "signatures": result,
                "count": len(result),
            }

        if method == "GET" and parsed.path == "/v1/runtime/migrations":
            return HTTPStatus.OK, self.projection.operation_store.migration_status()

        if method == "GET" and parsed.path == "/v1/runtime/notifications":
            if self.notification_store is None:
                return HTTPStatus.NOT_IMPLEMENTED, {"error": "notification store unavailable"}
            query = parse_qs(parsed.query)
            include_ack = query.get("include_acknowledged", ["false"])[0].lower() == "true"
            notifications = self.notification_store.list(
                scope=query.get("scope", [self.notification_scope])[0],
                include_acknowledged=include_ack,
            )
            return HTTPStatus.OK, {
                "notifications": [item.to_dict() for item in notifications],
                "count": len(notifications),
            }

        if method == "POST" and parsed.path.startswith("/v1/runtime/notifications/") and parsed.path.endswith("/ack"):
            if self.notification_store is None:
                return HTTPStatus.NOT_IMPLEMENTED, {"error": "notification store unavailable"}
            auth = headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return HTTPStatus.UNAUTHORIZED, {"error": "missing bearer authorization"}
            try:
                self.controls.authorize(auth[7:])
                notification_id = parsed.path.split("/")[-2]
                payload = body or {}
                expected_revision = int(payload.get("expected_revision", -1))
                result = self.notification_store.acknowledge(
                    notification_id,
                    expected_revision=expected_revision,
                )
            except AuthorizationError as exc:
                return HTTPStatus.UNAUTHORIZED, {"error": str(exc)}
            except (NotificationError, KeyError, ValueError) as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, result.to_dict()

        if method == "POST" and parsed.path == "/v1/runtime/controls":
            payload = body or {}
            auth = headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return HTTPStatus.UNAUTHORIZED, {"error": "missing bearer authorization"}
            idempotency_key = headers.get("Idempotency-Key", "")
            try:
                result = self.controls.execute(
                    token=auth[7:],
                    idempotency_key=idempotency_key,
                    operation_id=str(payload.get("operation_id", "")),
                    action=str(payload.get("action", "")),
                    expected_revision=int(payload.get("expected_revision", -1)),
                    reason=str(payload.get("reason", "")),
                )
            except AuthorizationError as exc:
                return HTTPStatus.UNAUTHORIZED, {"error": str(exc)}
            except IdempotencyConflict as exc:
                return HTTPStatus.CONFLICT, {"error": str(exc)}
            except OperationStateNotFound as exc:
                return HTTPStatus.NOT_FOUND, {"error": str(exc)}
            except (ControlError, ValueError) as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, result

        return HTTPStatus.NOT_FOUND, {"error": "not found"}


def make_handler(
    service: RuntimeAPIService,
    *,
    static_root: Path | None = None,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PASI-Runtime/0.1"

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if static_root is not None and self.path.split("?", 1)[0] in {"/", "/index.html", "/app.js", "/app.css"}:
                requested = self.path.split("?", 1)[0]
                relative = "index.html" if requested in {"/", "/index.html"} else requested.lstrip("/")
                target = (static_root / unquote(relative)).resolve()
                root = static_root.resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                if not target.is_file():
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                body = target.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            status, payload = service.request(
                method="GET",
                path=self.path,
                headers=dict(self.headers.items()),
            )
            self._send(status, payload)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    raise ValueError("invalid request body size")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("request body must be an object")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            status, payload = service.request(
                method="POST",
                path=self.path,
                headers=dict(self.headers.items()),
                body=body,
            )
            self._send(status, payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def serve(
    *,
    service: RuntimeAPIService,
    host: str = "127.0.0.1",
    port: int = 8790,
    static_root: Path | None = None,
) -> None:
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(service, static_root=static_root),
    )
    print(f"PASI runtime API listening on http://{host}:{port}", flush=True)
    server.serve_forever()
