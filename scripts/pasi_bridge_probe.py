#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from urllib.request import Request, urlopen

BRIDGE_BASE_URL = "http://127.0.0.1:8765"
EXPECTED_SERVICE = "pasi-engineering-workspace-chatgpt-bridge"
EXPECTED_CONTROLLER_VERSION = "cdp-worker-v1"
EXPECTED_SCHEMA = "pasi-native-chromium-v2"
CHATGPT_URL_RE = re.compile(r"^https://(?:www\.)?chatgpt\.com/c/")


def token_path() -> Path:
    return Path(
        os.environ.get(
            "PASI_BRIDGE_TOKEN_FILE",
            str(Path.home() / ".pasi" / "bridge-token"),
        )
    ).expanduser()


def load_token() -> str:
    path = token_path()
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"bridge token file is empty: {path}")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(f"bridge token file permissions are too broad: {path}")
    return value


def get(path: str, token: str, *, timeout: float = 5.0) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(BRIDGE_BASE_URL + path, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned a non-object JSON payload")
    return payload


def probe(token: str) -> dict:
    health = get("/health", "", timeout=3)
    if health.get("status") != "ok" or health.get("service") != EXPECTED_SERVICE:
        raise RuntimeError("localhost bridge is not the Engineering Workspace ChatGPT bridge")

    status = get("/status", token)
    browser_payload = get("/browser/health", token)
    observation = browser_payload.get("observation")
    data = observation.get("data") if isinstance(observation, dict) else None
    schema_version = observation.get("schema_version") if isinstance(observation, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("bridge returned no browser health data")

    checks = {
        "bridge_health": True,
        "bridge_authorized": True,
        "status_endpoint": True,
        "browser_health": True,
        "schema": schema_version == EXPECTED_SCHEMA,
        "controller_version": data.get("controller_version") == EXPECTED_CONTROLLER_VERSION,
        "native_controller": data.get("native_controller") is True,
        "network_authority": data.get("network_authority") is True,
        "chat_session": isinstance(data.get("chat_url"), str) and bool(CHATGPT_URL_RE.match(data["chat_url"])),
    }
    failures = [name for name, passed in checks.items() if not passed]

    result = {
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "bridge": {
            "status": health.get("status"),
            "service": health.get("service"),
            "queue_available": isinstance(status, dict),
        },
        "browser": {
            "kind": data.get("kind"),
            "schema_version": schema_version,
            "controller_version": data.get("controller_version"),
            "native_controller": data.get("native_controller"),
            "network_authority": data.get("network_authority"),
            "chat_url": data.get("chat_url"),
            "active_operation_id": data.get("active_operation_id"),
            "page_visible": data.get("page_visible"),
        },
    }
    if failures:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the PASI Engineering Workspace ChatGPT bridge.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = probe(load_token())
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("BRIDGE: OK")
        print("AUTHORIZATION: OK")
        print("BROWSER HEALTH: OK")
        print(f"CDP CONTROLLER: {result['browser']['controller_version']}")
        print(f"NETWORK AUTHORITY: {result['browser']['network_authority']}")
        print(f"CHAT SESSION: {result['browser']['chat_url']}")
        print(f"ACTIVE OPERATION: {result['browser']['active_operation_id'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
