# PASI Engineering Workspace

A clean, roadmap-driven PASI automation workspace.

## Architecture

Roadmap / Issues
       |
       v
Operation State
       |
       v
Planner
       |
       v
ModelProvider
       |
       +--> Contracted PASI API boundary
       |       +--> REST / OpenAPI
       |       +--> future GraphQL gateway
       |       +--> future gRPC transport
       |       +--> WebSocket / SSE / long-poll
       |       +--> signed webhooks
       |
       +--> PASI local API --> Ollama / local model
       |
       +--> future hosted provider

Computer capabilities stay separate:
Planner -> typed ComputerCapability -> host application

The repository contains a deliberately bounded Chromium ChatGPT handoff extension under `extensions/pasi-chatgpt/`. The extension is a browser capability boundary and is not the model transport.

## API architecture hardening

The shared contract layer lives in `src/pasi/core/api_architecture.py`.

- REST uses explicit request/response field contracts, sparse response fieldsets, OpenAPI 3.1 generation, request correlation IDs, and idempotent POST retries.
- GraphQL guardrails enforce query depth, field count, and cumulative cost before execution; persisted queries use SHA-256 identities.
- gRPC has a method-contract registry plus the standard five-byte message envelope; protobuf codecs remain transport-specific.
- WebSocket reliability provides bounded exponential backoff, connection identity, event resumption headers, and fallback ordering to SSE or long-polling.
- Webhooks use HMAC-SHA256 signatures, timestamps, constant-time comparison, and single-use delivery IDs.

These are shared primitives, not competing stacks. PASI keeps the operation contract independent of whichever network transport is used.

The local provider publishes its REST/OpenAPI contract at `GET /openapi.json`.

See `docs/api-architecture.md` for the implementation boundary.

## Local model API

Set the local model:

export PASI_OLLAMA_MODEL='nemotron-3-nano:4b'
python -m pasi.providers.local_api

The PASI API listens on 127.0.0.1:8787 by default.

## Development

python -m pip install -e '.[dev]'
python -m pytest -q

## Engineering rules

- Keep model inference independent from computer control.
- Keep operation identity and recovery state durable and bounded.
- Prefer local/free providers when they meet requirements.
- Require evidence for completion claims.
- Make one coherent change in one pull request.
- Do not rebuild the previous DOM/frontend architecture.
