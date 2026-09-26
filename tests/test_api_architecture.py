from __future__ import annotations

import hashlib

import pytest

from pasi.core.api_architecture import (
    ApiContract,
    FieldSpec,
    GraphQLGuardrails,
    GraphQLQueryRejected,
    GrpcContract,
    GrpcMethodSpec,
    IdempotencyConflict,
    IdempotencyStore,
    OperationSpec,
    PersistedQueryRegistry,
    SparseFieldsetError,
    WebSocketReliability,
    WebhookRejected,
    WebhookSigner,
    build_websocket_reconnect_headers,
    decode_grpc_frames,
    encode_grpc_frame,
    parse_sparse_fieldset,
    project_fields,
)


def test_sparse_fieldsets_and_openapi_contract():
    contract = ApiContract(title="test", version="1")
    contract.add(
        OperationSpec(
            method="GET",
            path="/items",
            summary="List items",
            response_fields=(FieldSpec("id", "string"), FieldSpec("name", "string")),
        )
    )
    assert parse_sparse_fieldset("id,name", ["id", "name"]) == ("id", "name")
    assert project_fields({"id": "1", "name": "x", "secret": "no"}, ("id",)) == {"id": "1"}
    with pytest.raises(SparseFieldsetError):
        parse_sparse_fieldset("secret", ["id", "name"])
    document = contract.openapi()
    assert document["openapi"] == "3.1.0"
    assert document["paths"]["/items"]["get"]["x-pasi-idempotent"] is False


def test_idempotency_replays_and_detects_conflicts():
    store = IdempotencyStore()
    calls = []

    def produce():
        calls.append(1)
        return 200, {"value": len(calls)}

    assert store.execute("scope", "same", "fingerprint-a", produce) == (200, {"value": 1}, False)
    assert store.execute("scope", "same", "fingerprint-a", produce) == (200, {"value": 1}, True)
    assert len(calls) == 1
    with pytest.raises(IdempotencyConflict):
        store.execute("scope", "same", "fingerprint-b", produce)
    assert len(calls) == 1


def test_graphql_depth_cost_and_persisted_queries():
    guard = GraphQLGuardrails(max_depth=3, max_fields=5, max_cost=6)
    result = guard.analyze(
        "query GetUser { user(id: 1) { id profile { name email } } }",
        {"user": 2, "profile": 2},
    )
    assert result.max_depth == 3
    assert result.field_count == 5
    assert result.total_cost == 6

    registry = PersistedQueryRegistry()
    digest = registry.register("query { health }")
    assert digest == hashlib.sha256(b"query { health }").hexdigest()
    assert registry.resolve(digest) == "query { health }"

    with pytest.raises(GraphQLQueryRejected):
        guard.analyze("query { a { b { c { d } } } }")

    with pytest.raises(GraphQLQueryRejected):
        GraphQLGuardrails(max_cost=2).analyze("query { expensive }", {"expensive": 3})


def test_grpc_contract_and_frame_codec():
    contract = GrpcContract()
    contract.register(
        GrpcMethodSpec(
            service="Provider",
            method="Generate",
            request_type="GenerateRequest",
            response_type="GenerateResponse",
            idempotent=True,
            server_streaming=True,
        )
    )
    assert contract.method("Provider", "Generate").server_streaming is True

    encoded = encode_grpc_frame(b"hello")
    frames, remainder = decode_grpc_frames(encoded[:3])
    assert frames == []
    assert remainder == encoded[:3]
    frames, remainder = decode_grpc_frames(remainder + encoded)
    assert frames == [(False, b"hello")]
    assert remainder == b""


def test_websocket_backoff_and_reconnect_identity():
    reliability = WebSocketReliability()
    assert reliability.transport_order() == ("websocket", "sse", "long-poll")
    assert reliability.delay(0) == 0.25
    assert reliability.delay(3, jitter=0.2) == 2.4
    headers = build_websocket_reconnect_headers(
        connection_id="c1",
        attempt=3,
        last_event_id="evt-9",
    )
    assert headers["X-PASI-Connection-ID"] == "c1"
    assert headers["Last-Event-ID"] == "evt-9"


def test_webhook_signing_authenticates_delivery_and_blocks_replay():
    signer = WebhookSigner("secret")
    body = b'{"event":"ok"}'
    signature = signer.sign(body, timestamp=1000, delivery_id="d1")
    headers = {
        "X-PASI-Webhook-Signature": signature,
        "X-PASI-Webhook-Delivery-ID": "d1",
    }
    receipt = signer.verify(headers, body, now=1001)
    assert receipt.delivery_id == "d1"

    with pytest.raises(WebhookRejected):
        signer.verify(headers, body, now=1001)

    swapped = dict(headers)
    swapped["X-PASI-Webhook-Delivery-ID"] = "d2"
    with pytest.raises(WebhookRejected):
        WebhookSigner("secret").verify(swapped, body, now=1001)

    stale = dict(headers)
    stale["X-PASI-Webhook-Signature"] = signer.sign(
        body,
        timestamp=0,
        delivery_id="d3",
    )
    stale["X-PASI-Webhook-Delivery-ID"] = "d3"
    with pytest.raises(WebhookRejected):
        WebhookSigner("secret").verify(stale, body, now=1001)
