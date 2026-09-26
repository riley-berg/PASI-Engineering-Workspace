from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence


class APIArchitectureError(ValueError):
    """Base error for deterministic API-boundary validation."""


class SchemaValidationError(APIArchitectureError):
    pass


class SparseFieldsetError(APIArchitectureError):
    pass


class IdempotencyConflict(APIArchitectureError):
    pass


class GraphQLQueryRejected(APIArchitectureError):
    pass


class WebhookRejected(APIArchitectureError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool = False
    description: str | None = None


@dataclass(frozen=True)
class OperationSpec:
    method: str
    path: str
    summary: str
    request_fields: tuple[FieldSpec, ...] = ()
    response_fields: tuple[FieldSpec, ...] = ()
    required_request_fields: tuple[str, ...] = ()
    idempotent: bool = False
    tags: tuple[str, ...] = ()


def _python_type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate_object(payload: Mapping[str, object], fields: Sequence[FieldSpec]) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise SchemaValidationError("request body must be an object")
    field_map = {field.name: field for field in fields}
    missing = [name for name, field in field_map.items() if field.required and name not in payload]
    if missing:
        raise SchemaValidationError("missing required fields: " + ", ".join(sorted(missing)))
    unknown = sorted(set(payload) - set(field_map))
    if unknown:
        raise SchemaValidationError("unknown fields: " + ", ".join(unknown))
    checked = dict(payload)
    for field in fields:
        if field.name not in payload:
            continue
        value = payload[field.name]
        expected = field.type
        if expected == "string" and not isinstance(value, str):
            raise SchemaValidationError(f"{field.name} must be string, got {_python_type_name(value)}")
        if expected == "array" and not isinstance(value, list):
            raise SchemaValidationError(f"{field.name} must be array, got {_python_type_name(value)}")
        if expected == "object" and not isinstance(value, Mapping):
            raise SchemaValidationError(f"{field.name} must be object, got {_python_type_name(value)}")
    return checked


def parse_sparse_fieldset(raw: str | None, allowed: Iterable[str]) -> tuple[str, ...] | None:
    if raw is None or not raw.strip():
        return None
    allowed_set = {str(name) for name in allowed}
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not names:
        return None
    invalid = sorted(set(names) - allowed_set)
    if invalid:
        raise SparseFieldsetError("unknown sparse fields: " + ", ".join(invalid))
    if len(set(names)) != len(names):
        raise SparseFieldsetError("duplicate sparse fields are not allowed")
    return names


def project_fields(payload: Mapping[str, object], fields: Sequence[str] | None) -> dict[str, object]:
    if fields is None:
        return dict(payload)
    return {name: payload[name] for name in fields if name in payload}


def _schema_for_field(field: FieldSpec) -> dict[str, object]:
    if field.type in {"string", "integer", "number", "boolean", "array", "object"}:
        return {"type": field.type}
    return {"$ref": f"#/components/schemas/{field.type}"}


class ApiContract:
    """Small framework-neutral REST/OpenAPI contract used by PASI endpoints."""

    def __init__(self, *, title: str, version: str) -> None:
        self.title = title
        self.version = version
        self._operations: dict[tuple[str, str], OperationSpec] = {}

    def add(self, operation: OperationSpec) -> None:
        key = (operation.method.upper(), operation.path)
        if key in self._operations:
            raise APIArchitectureError(f"duplicate operation: {key[0]} {key[1]}")
        self._operations[key] = operation

    def operation(self, method: str, path: str) -> OperationSpec:
        try:
            return self._operations[(method.upper(), path)]
        except KeyError as exc:
            raise APIArchitectureError(f"operation not defined: {method} {path}") from exc

    def validate_request(self, method: str, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        operation = self.operation(method, path)
        return validate_object(payload, operation.request_fields)

    def openapi(self) -> dict[str, object]:
        paths: dict[str, dict[str, object]] = {}
        for operation in self._operations.values():
            response_schema = {
                "type": "object",
                "properties": {
                    field.name: _schema_for_field(field) for field in operation.response_fields
                },
                "additionalProperties": True,
            }
            entry: dict[str, object] = {
                "summary": operation.summary,
                "tags": list(operation.tags),
                "parameters": [{
                    "name": "fields",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": "Comma-separated sparse fieldset.",
                }],
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {"application/json": {"schema": response_schema}},
                    }
                },
                "x-pasi-idempotent": operation.idempotent,
                "x-pasi-response-fields": [field.name for field in operation.response_fields],
            }
            if operation.request_fields:
                entry["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                field.name: _schema_for_field(field)
                                for field in operation.request_fields
                            },
                            "required": list(operation.required_request_fields),
                            "additionalProperties": False,
                        }
                    }},
                }
            paths.setdefault(operation.path, {})[operation.method.lower()] = entry
        return {
            "openapi": "3.1.0",
            "info": {"title": self.title, "version": self.version},
            "paths": paths,
        }

    def openapi_json(self) -> str:
        return json.dumps(self.openapi(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class IdempotencyRecord:
    fingerprint: str
    status: int
    payload: dict[str, object]
    created_at: float


class IdempotencyStore:
    """Thread-safe bounded idempotency cache for retried POST operations."""

    def __init__(self, *, ttl_seconds: float = 900.0, max_entries: int = 4096) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._records: dict[tuple[str, str], IdempotencyRecord] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        expired = [
            key for key, record in self._records.items()
            if now - record.created_at >= self.ttl_seconds
        ]
        for key in expired:
            self._records.pop(key, None)
        if len(self._records) > self.max_entries:
            oldest = sorted(self._records, key=lambda key: self._records[key].created_at)
            for key in oldest[: len(self._records) - self.max_entries]:
                self._records.pop(key, None)

    def execute(
        self,
        scope: str,
        key: str,
        fingerprint: str,
        producer: Callable[[], tuple[int, dict[str, object]]],
    ) -> tuple[int, dict[str, object], bool]:
        if not key:
            status, payload = producer()
            return status, payload, False
        cache_key = (str(scope), str(key))
        with self._lock:
            now = time.time()
            self._purge(now)
            existing = self._records.get(cache_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key reused with a different request")
                return existing.status, dict(existing.payload), True
            status, payload = producer()
            self._records[cache_key] = IdempotencyRecord(
                fingerprint=fingerprint,
                status=status,
                payload=dict(payload),
                created_at=now,
            )
            return status, dict(payload), False


_GRAPHQL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class GraphQLCost:
    max_depth: int
    field_count: int
    total_cost: int


class GraphQLGuardrails:
    """Dependency-free GraphQL depth/cost guardrails for a gateway or provider."""

    def __init__(
        self,
        *,
        max_depth: int = 8,
        max_fields: int = 200,
        max_cost: int = 500,
        default_field_cost: int = 1,
    ) -> None:
        self.max_depth = int(max_depth)
        self.max_fields = int(max_fields)
        self.max_cost = int(max_cost)
        self.default_field_cost = int(default_field_cost)

    def analyze(self, query: str, field_costs: Mapping[str, int] | None = None) -> GraphQLCost:
        if not isinstance(query, str) or not query.strip():
            raise GraphQLQueryRejected("GraphQL query is empty")
        costs = {str(key): max(1, int(value)) for key, value in (field_costs or {}).items()}
        depth = 0
        max_depth = 0
        field_count = 0
        total_cost = 0
        paren_depth = 0
        tokens = _tokenize_graphql(query)
        for index, token in enumerate(tokens):
            if token == "{":
                depth += 1
                max_depth = max(max_depth, depth)
                if max_depth > self.max_depth:
                    raise GraphQLQueryRejected(
                        f"query depth {max_depth} exceeds limit {self.max_depth}"
                    )
                continue
            if token == "}":
                depth = max(0, depth - 1)
                continue
            if token == "(":
                paren_depth += 1
                continue
            if token == ")":
                paren_depth = max(0, paren_depth - 1)
                continue
            if depth == 0 or paren_depth > 0 or token in {"query", "mutation", "subscription", "fragment", "on"}:
                continue
            if token in {"(", ")", "[", "]"}:
                continue
            if not _GRAPHQL_NAME.fullmatch(token):
                continue
            next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
            if next_token == ":":
                continue
            previous = tokens[index - 1] if index else ""
            if previous in {"$", "@", ":", "..."}:
                continue
            field_count += 1
            total_cost += costs.get(token, self.default_field_cost)
            if field_count > self.max_fields:
                raise GraphQLQueryRejected(
                    f"query field count {field_count} exceeds limit {self.max_fields}"
                )
            if total_cost > self.max_cost:
                raise GraphQLQueryRejected(
                    f"query cost {total_cost} exceeds limit {self.max_cost}"
                )
        if depth != 0:
            raise GraphQLQueryRejected("GraphQL query has unbalanced braces")
        return GraphQLCost(max_depth=max_depth, field_count=field_count, total_cost=total_cost)


def _tokenize_graphql(query: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(query):
        char = query[index]
        if char.isspace() or char == ",":
            index += 1
            continue
        if char == "#":
            end = query.find("\n", index)
            index = len(query) if end == -1 else end + 1
            continue
        if query.startswith("...", index):
            tokens.append("...")
            index += 3
            continue
        if char in "{}():!@[]$":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            while index < len(query):
                if query[index] == "\\":
                    index += 2
                    continue
                if query[index] == '"':
                    index += 1
                    break
                index += 1
            continue
        match = _GRAPHQL_NAME.match(query, index)
        if match:
            tokens.append(match.group(0))
            index = match.end()
            continue
        index += 1
    return tokens


class PersistedQueryRegistry:
    """Maps SHA-256 query hashes to normalized GraphQL documents."""

    def __init__(self) -> None:
        self._queries: dict[str, str] = {}

    def register(self, query: str) -> str:
        normalized = " ".join(str(query).split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        self._queries[digest] = normalized
        return digest

    def resolve(self, digest: str) -> str:
        try:
            return self._queries[digest]
        except KeyError as exc:
            raise GraphQLQueryRejected("unknown persisted query") from exc


@dataclass(frozen=True)
class GrpcMethodSpec:
    service: str
    method: str
    request_type: str
    response_type: str
    idempotent: bool = False
    client_streaming: bool = False
    server_streaming: bool = False


class GrpcContract:
    """RPC contract registry; payload codecs remain provider-specific protobufs."""

    def __init__(self) -> None:
        self._methods: dict[tuple[str, str], GrpcMethodSpec] = {}

    def register(self, spec: GrpcMethodSpec) -> None:
        key = (spec.service, spec.method)
        if key in self._methods:
            raise APIArchitectureError(f"duplicate RPC: {spec.service}/{spec.method}")
        self._methods[key] = spec

    def method(self, service: str, method: str) -> GrpcMethodSpec:
        try:
            return self._methods[(service, method)]
        except KeyError as exc:
            raise APIArchitectureError(f"unknown RPC: {service}/{method}") from exc

    def descriptors(self) -> tuple[GrpcMethodSpec, ...]:
        return tuple(sorted(self._methods.values(), key=lambda spec: (spec.service, spec.method)))


def encode_grpc_frame(payload: bytes, *, compressed: bool = False) -> bytes:
    """Encode the standard five-byte gRPC message envelope around protobuf bytes."""
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("gRPC payload must be bytes")
    size = len(payload)
    if size > 0xFFFFFFFF:
        raise APIArchitectureError("gRPC payload exceeds 32-bit frame length")
    return bytes([1 if compressed else 0]) + size.to_bytes(4, "big") + bytes(payload)


def decode_grpc_frames(buffer: bytes) -> tuple[list[tuple[bool, bytes]], bytes]:
    frames: list[tuple[bool, bytes]] = []
    offset = 0
    while len(buffer) - offset >= 5:
        flags = buffer[offset]
        if flags & 0xFE:
            raise APIArchitectureError("unsupported gRPC compression flags")
        size = int.from_bytes(buffer[offset + 1: offset + 5], "big")
        end = offset + 5 + size
        if end > len(buffer):
            break
        frames.append((bool(flags & 1), buffer[offset + 5:end]))
        offset = end
    return frames, buffer[offset:]


@dataclass(frozen=True)
class WebSocketBackoff:
    initial_seconds: float = 0.25
    multiplier: float = 2.0
    max_seconds: float = 20.0
    jitter_ratio: float = 0.2


@dataclass(frozen=True)
class WebSocketReliability:
    backoff: WebSocketBackoff = WebSocketBackoff()
    fallback_transports: tuple[str, ...] = ("sse", "long-poll")

    def delay(self, attempt: int, *, jitter: float = 0.0) -> float:
        base = min(
            self.backoff.initial_seconds * (self.backoff.multiplier ** max(0, attempt)),
            self.backoff.max_seconds,
        )
        bounded_jitter = max(-self.backoff.jitter_ratio, min(self.backoff.jitter_ratio, jitter))
        return max(0.0, base * (1.0 + bounded_jitter))

    def transport_order(
        self,
        *,
        websocket: bool = True,
        sse: bool = True,
        long_poll: bool = True,
    ) -> tuple[str, ...]:
        candidates = []
        if websocket:
            candidates.append("websocket")
        if sse and "sse" in self.fallback_transports:
            candidates.append("sse")
        if long_poll and "long-poll" in self.fallback_transports:
            candidates.append("long-poll")
        return tuple(candidates)


def build_websocket_reconnect_headers(
    *,
    connection_id: str,
    attempt: int,
    last_event_id: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-PASI-Connection-ID": str(connection_id),
        "X-PASI-Reconnect-Attempt": str(int(attempt)),
    }
    if last_event_id:
        headers["Last-Event-ID"] = str(last_event_id)
    return headers


@dataclass(frozen=True)
class WebhookReceipt:
    delivery_id: str
    timestamp: int


class WebhookSigner:
    """HMAC-SHA256 webhook verifier with replay and duplicate-delivery guards."""

    signature_header = "X-PASI-Webhook-Signature"
    delivery_header = "X-PASI-Webhook-Delivery-ID"

    def __init__(self, secret: bytes | str, *, max_skew_seconds: int = 300) -> None:
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.max_skew_seconds = int(max_skew_seconds)
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def sign(self, body: bytes, *, timestamp: int, delivery_id: str) -> str:
        canonical = (
            f"{int(timestamp)}.{delivery_id}.".encode("utf-8") + bytes(body)
        )
        digest = hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()
        return f"t={int(timestamp)},v1={digest}"

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: int | None = None,
    ) -> WebhookReceipt:
        signature = _header_value(headers, self.signature_header)
        delivery_id = _header_value(headers, self.delivery_header)
        if not signature:
            raise WebhookRejected("missing webhook signature")
        if not delivery_id:
            raise WebhookRejected("missing webhook delivery id")
        values: dict[str, str] = {}
        for part in signature.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                values[key.strip()] = value.strip()
        try:
            timestamp = int(values["t"])
            provided = values["v1"]
        except (KeyError, ValueError) as exc:
            raise WebhookRejected("invalid webhook signature format") from exc
        current = int(time.time() if now is None else now)
        if abs(current - timestamp) > self.max_skew_seconds:
            raise WebhookRejected("webhook timestamp outside replay window")
        expected = self.sign(body, timestamp=timestamp, delivery_id=delivery_id).split("v1=", 1)[1]
        if not hmac.compare_digest(expected, provided):
            raise WebhookRejected("invalid webhook signature")
        with self._lock:
            expired = [
                key for key, seen_at in self._seen.items()
                if current - seen_at > self.max_skew_seconds
            ]
            for key in expired:
                self._seen.pop(key, None)
            if delivery_id in self._seen:
                raise WebhookRejected("duplicate webhook delivery")
            self._seen[delivery_id] = current
        return WebhookReceipt(delivery_id=delivery_id, timestamp=timestamp)


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value).strip()
    return None
