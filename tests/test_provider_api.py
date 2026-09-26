from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from pasi.core.api_architecture import IdempotencyStore
from pasi.providers.local_api import LocalProviderAPI
from pasi.providers.protocol import ProviderResponse


class FakeProvider:
    name = "fake"

    def health(self):
        return {"available": True, "provider": self.name}

    def generate(self, messages, *, model=None):
        return ProviderResponse(self.name, model or "fake-model", "hello from provider", 1.0)


def start_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalProviderAPI)
    server.provider = FakeProvider()  # type: ignore[attr-defined]
    server.idempotency = IdempotencyStore()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def request(server, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    payload = response.read()
    headers_out = dict(response.getheaders())
    conn.close()
    return response.status, headers_out, json.loads(payload or b"{}")


def test_local_api_health_completion_openapi_and_sparse_fields():
    server, thread = start_server()
    try:
        status, headers, payload = request(server, "GET", "/health")
        assert status == 200
        assert payload["available"] is True
        assert "X-PASI-Request-ID" in headers

        status, _, spec = request(server, "GET", "/openapi.json")
        assert status == 200
        assert spec["openapi"] == "3.1.0"
        assert spec["paths"]["/v1/chat/completions"]["post"]["x-pasi-idempotent"] is True

        body = json.dumps({
            "model": "fake-model",
            "messages": [{"role": "user", "content": "hello"}],
        }).encode()
        headers_in = {
            "Content-Type": "application/json",
            "Idempotency-Key": "k1",
            "X-PASI-Request-ID": "req-1",
        }
        status, headers, payload = request(
            server,
            "POST",
            "/v1/chat/completions?fields=id,model",
            body=body,
            headers=headers_in,
        )
        assert status == 200
        assert set(payload) == {"id", "model"}
        assert headers["X-PASI-Request-ID"] == "req-1"

        status, _, replay = request(
            server,
            "POST",
            "/v1/chat/completions?fields=id,model",
            body=body,
            headers={"Content-Type": "application/json", "Idempotency-Key": "k1"},
        )
        assert status == 200
        assert replay["id"] == "pasi-local-completion"
        assert replay["idempotent_replay"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_local_api_rejects_idempotency_conflict():
    server, thread = start_server()
    try:
        body_a = json.dumps({
            "model": "fake-model",
            "messages": [{"role": "user", "content": "a"}],
        }).encode()
        body_b = json.dumps({
            "model": "fake-model",
            "messages": [{"role": "user", "content": "b"}],
        }).encode()

        status, _, _ = request(
            server,
            "POST",
            "/v1/chat/completions",
            body=body_a,
            headers={"Content-Type": "application/json", "Idempotency-Key": "conflict"},
        )
        assert status == 200

        status, _, payload = request(
            server,
            "POST",
            "/v1/chat/completions",
            body=body_b,
            headers={"Content-Type": "application/json", "Idempotency-Key": "conflict"},
        )
        assert status == 409
        assert payload["error"]["code"] == "conflict"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_local_api_rejects_unknown_and_invalid_fields():
    server, thread = start_server()
    try:
        for payload in [
            {
                "model": "fake-model",
                "messages": [{"role": "user", "content": "hello"}],
                "unexpected": True,
            },
            {
                "model": 42,
                "messages": [{"role": "user", "content": "hello"}],
            },
        ]:
            body = json.dumps(payload).encode()
            status, _, response = request(
                server,
                "POST",
                "/v1/chat/completions",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            assert status == 400
            assert "must be string" in response["error"]["message"] or "unknown fields" in response["error"]["message"]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
