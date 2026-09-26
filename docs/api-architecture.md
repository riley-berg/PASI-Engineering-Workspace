# PASI API architecture hardening

PASI now has a framework-neutral API boundary in `src/pasi/core/api_architecture.py`. It is dependency-free so the same operation contract can be exposed through different transports.

## REST

The local provider is contract-driven and publishes OpenAPI 3.1 at `/openapi.json`.

Request bodies are validated against declared fields. Responses support sparse fieldsets with `?fields=a,b`. POST retries can use `Idempotency-Key`; a reused key with the same request is replayed, while a different request receives a conflict. Responses carry `X-PASI-Request-ID` for correlation.

## GraphQL

`GraphQLGuardrails` enforces maximum nesting depth, field count, and cumulative cost before execution. `PersistedQueryRegistry` hashes normalized queries with SHA-256 so a gateway can use stable query identities for caching and approval.

The guardrail implementation is deliberately a pre-execution boundary; it does not pretend to be a full GraphQL parser or executor.

## gRPC

`GrpcContract` registers RPC methods independently of their concrete protobuf definitions. `encode_grpc_frame` and `decode_grpc_frames` implement the five-byte gRPC message envelope while leaving protobuf serialization and HTTP/2 handling to a future transport adapter.

## WebSockets

`WebSocketReliability` supplies bounded exponential backoff with jitter and deterministic fallback ordering to SSE and long-polling. `build_websocket_reconnect_headers` preserves connection identity, retry count, and the last event ID so a reconnecting client can resume from a durable checkpoint.

## Webhooks

`WebhookSigner` authenticates deliveries with HMAC-SHA256 over `timestamp.delivery_id.body`, enforces a replay window, uses constant-time signature comparison, and rejects duplicate delivery IDs.

## Design boundary

These features are primitives rather than four competing server stacks. PASI can expose the same operation through REST, a future GraphQL gateway, a future gRPC service, or event-driven streaming/webhook transports without making a browser UI the source of truth.

The browser extension remains a capability surface. The API contract and operation identity live below it.
