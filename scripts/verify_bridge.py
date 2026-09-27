#!/usr/bin/env python3
"""Verify the repository-local PASI bridge and extension runtime contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_URL = "http://127.0.0.1:8765"
RUNTIME_TOKEN = ROOT / ".runtime" / "bridge-token"
EXTENSION_TOKEN = ROOT / "extensions" / "pasi-chatgpt" / ".bridge-token"
EXTENSION_MANIFEST = ROOT / "extensions" / "pasi-chatgpt" / "manifest.json"


def git_sha() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def read_token(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"missing bridge token: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(f"empty bridge token: {path}")
    return token


def main() -> int:
    expected_sha = git_sha()
    runtime_token = read_token(RUNTIME_TOKEN)
    extension_token = read_token(EXTENSION_TOKEN)
    if runtime_token != extension_token:
        raise SystemExit("runtime and extension bridge tokens differ")

    manifest = json.loads(EXTENSION_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise SystemExit("source extension manifest is not Manifest V3")

    request = Request(
        BRIDGE_URL + "/health",
        headers={"Authorization": f"Bearer {runtime_token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"bridge health check failed: {exc}") from exc

    if payload.get("status") != "ok":
        raise SystemExit(f"unexpected bridge health response: {payload!r}")
    if payload.get("m1_checkpoint_schema_version") != 1:
        raise SystemExit(
            "running bridge is stale: expected M1 checkpoint schema 1; "
            "restart it from this checkout with scripts/run_bridge.sh"
        )
    if payload.get("m2_recovery_schema_version") != 1:
        raise SystemExit(
            "running bridge is stale: expected M2 recovery schema 1; "
            "restart it from this checkout with scripts/run_bridge.sh"
        )

    print("PASI bridge verification PASSED")
    print(f"  source commit : {expected_sha}")
    print(f"  bridge        : {BRIDGE_URL}")
    print("  token sync    : shared")
    print(f"  extension     : {manifest.get('name')} {manifest.get('version')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
