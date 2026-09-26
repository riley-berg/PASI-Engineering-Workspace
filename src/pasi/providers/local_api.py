from __future__ import annotations

import hashlib
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pasi.core.api_architecture import (
    ApiContract,
    FieldSpec,
    IdempotencyConflict,
    IdempotencyStore,
    OperationSpec,
    SchemaValidationError,
    SparseFieldsetError,
    parse_sparse_fieldset,
    project_fields,
)
from .ollama import OllamaProvider
from .protocol import ChatMessage


CHAT_COMPLETION_CONTRACT = ApiContract(title="PASI Local Provider API", version="0.2.0")
CHAT_COMPLETION_CONTRACT.add(
    OperationSpec(
        method="GET",
        path="/health",
        summary="Inspect provider health",
        response_fields=(FieldSpec("available", "boolean"), FieldSpec("provider", "string")),
        tags=("system",),
    )
)
CHAT_COMPLETION_CONTRACT.add(
    OperationSpec(
        method="POST",
        path="/v1/chat/completions",
        summary="Generate a chat completion",
        request_fields=(
            FieldSpec("model", "string"),
            FieldSpec("messages", "array", required=True),
        ),
        response_fields=(
            FieldSpec("id", "string"),
            FieldSpec("object", "string"),
            FieldSpec("model", "string"),
            FieldSpec("choices", "array"),
            FieldSpec("provider", "string"),
            FieldSpec("latency_ms", "number"),
        ),
        required_request_fields=("messages",),
        idempotent=True,
        tags=("model",),
    )
)


class LocalProviderAPI(BaseHTTPRequestHandler):
    """Local PASI model API with typed contracts and safe retry semantics."""

    server_version = "PASI-Provider/0.2"

    def _json(self, status: int, payload: dict[str, Any], *, request_id: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if request_id:
            self.send_header("X-PASI-Request-ID", request_id)
        self.end_headers()
        self.wfile.write(body)

    def _request_id(self) -> str:
        value = self.headers.get("X-PASI-Request-ID", "").strip()
        return value[:200] if value else f"local-{id(self)}"

    def _error(self, status: int, message: str) -> None:
        self._json(
            status,
            {
                "error": {
                    "code": HTTPStatus(status).phrase.lower().replace(" ", "_"),
                    "message": message,
                }
            },
            request_id=self._request_id(),
        )

    def do_GET(self) -> None:
        request_id = self._request_id()
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._json(HTTPStatus.OK, self.server.provider.health(), request_id=request_id)  # type: ignore[attr-defined]
            return
        if parsed.path == "/openapi.json":
            self._json(HTTPStatus.OK, CHAT_COMPLETION_CONTRACT.openapi(), request_id=request_id)
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        request_id = self._request_id()
        parsed = urlsplit(self.path)
        if parsed.path != "/v1/chat/completions":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise SchemaValidationError("invalid request body size")
            raw_body = self.rfile.read(length)
            payload = json.loads(raw_body.decode("utf-8"))
            validated = CHAT_COMPLETION_CONTRACT.validate_request(
                "POST",
                "/v1/chat/completions",
                payload,
            )
            raw_messages = validated["messages"]
            if not isinstance(raw_messages, list) or not raw_messages:
                raise SchemaValidationError("messages must be a non-empty list")

            parsed_messages = []
            for item in raw_messages:
                if not isinstance(item, dict) or "role" not in item or "content" not in item:
                    raise SchemaValidationError("each message needs role and content")
                parsed_messages.append(
                    ChatMessage(role=str(item["role"]), content=str(item["content"]))
                )

            model_value = validated.get("model")
            if model_value is not None and not isinstance(model_value, str):
                raise SchemaValidationError("model must be string")

            query = parse_qs(parsed.query, keep_blank_values=True)
            operation = CHAT_COMPLETION_CONTRACT.operation("POST", "/v1/chat/completions")
            requested_fields = parse_sparse_fieldset(
                query.get("fields", [None])[0],
                [field.name for field in operation.response_fields],
            )
            fingerprint = hashlib.sha256(
                b"POST /v1/chat/completions\n" + raw_body
            ).hexdigest()
            idempotency_key = self.headers.get("Idempotency-Key", "").strip()

            def produce() -> tuple[int, dict[str, object]]:
                result = self.server.provider.generate(  # type: ignore[attr-defined]
                    parsed_messages,
                    model=model_value,
                )
                return HTTPStatus.OK, {
                    "id": "pasi-local-completion",
                    "object": "chat.completion",
                    "model": result.model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": result.text},
                        "finish_reason": "stop",
                    }],
                    "provider": result.provider,
                    "latency_ms": result.latency_ms,
                }

            status, response, replayed = self.server.idempotency.execute(  # type: ignore[attr-defined]
                "POST /v1/chat/completions",
                idempotency_key,
                fingerprint,
                produce,
            )
            response = project_fields(response, requested_fields)
            if replayed:
                response = {**response, "idempotent_replay": True}
            self._json(status, response, request_id=request_id)
        except IdempotencyConflict as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (SchemaValidationError, SparseFieldsetError, ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc)[:500])

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str | None = None, port: int | None = None) -> None:
    bind_host = host or os.environ.get("PASI_PROVIDER_HOST", "127.0.0.1")
    bind_port = int(port if port is not None else os.environ.get("PASI_PROVIDER_PORT", "8787"))
    provider = OllamaProvider()
    server = ThreadingHTTPServer((bind_host, bind_port), LocalProviderAPI)
    server.provider = provider  # type: ignore[attr-defined]
    server.idempotency = IdempotencyStore()  # type: ignore[attr-defined]
    print(f"PASI local provider API listening on http://{bind_host}:{bind_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    serve()
