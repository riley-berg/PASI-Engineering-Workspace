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
    ) -> None:
        self.projection = projection
        self.event_feed = event_feed
        self.health_store = health_store
        self.controls = controls

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
