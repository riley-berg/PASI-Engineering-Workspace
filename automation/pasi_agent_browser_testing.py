from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping


BRIDGE_URL = os.environ.get("PASI_BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/")
BRIDGE_TOKEN_FILE = Path.home() / ".pasi" / "bridge-token"
MAX_POLL_SECONDS = 15.0


class BrowserTestingError(RuntimeError):
    """Raised when a read-only browser-test request cannot be completed."""


def _token() -> str:
    try:
        token = BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise BrowserTestingError("PASI bridge token is unavailable") from exc
    if not token:
        raise BrowserTestingError("PASI bridge token is empty")
    return token


def _request(
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    url = BRIDGE_URL + path
    headers = {
        "Authorization": "Bearer " + _token(),
        "Accept": "application/json",
    }
    body = None
    if payload is not None:
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise BrowserTestingError("PASI browser-test bridge returned an invalid JSON object")
            return value
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise BrowserTestingError(
            f"PASI browser-test bridge returned HTTP {exc.code}" + (f": {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise BrowserTestingError(f"PASI browser-test bridge unavailable: {exc}") from exc


class BrowserTestingClient:
    """RPC client to the extension's bounded CDP browser-testing boundary."""

    def request(
        self,
        action: str,
        *,
        tab_id: int | None = None,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float = MAX_POLL_SECONDS,
    ) -> dict[str, Any]:
        queued = _request(
            "POST",
            "/browser/testing/request",
            {
                "action": action,
                "tab_id": tab_id,
                "params": dict(params or {}),
            },
            timeout=5.0,
        )
        request_id = str(queued.get("request_id") or "").strip()
        if not request_id:
            raise BrowserTestingError("PASI browser-test bridge did not return a request id")

        deadline = time.monotonic() + max(1.0, min(timeout_seconds, MAX_POLL_SECONDS))
        encoded = urllib.parse.quote(request_id, safe="")
        while time.monotonic() < deadline:
            result = _request(
                "GET",
                "/browser/testing/result?request_id=" + encoded,
                timeout=3.0,
            )
            value = result.get("result")
            if isinstance(value, Mapping):
                if value.get("ok") is not True:
                    error = value.get("error")
                    message = error.get("message") if isinstance(error, Mapping) else str(error or "browser test failed")
                    raise BrowserTestingError(message)
                data = value.get("data")
                return dict(data) if isinstance(data, Mapping) else {}
            time.sleep(0.15)

        raise BrowserTestingError(
            f"PASI browser-test request {request_id} timed out waiting for the extension"
        )
