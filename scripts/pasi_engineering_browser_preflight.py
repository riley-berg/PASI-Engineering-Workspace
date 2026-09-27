#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def token_path() -> Path:
    return Path(
        os.environ.get(
            "PASI_BRIDGE_TOKEN_FILE",
            str(Path.home() / ".pasi" / "bridge-token"),
        )
    ).expanduser()


def token() -> str:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            secrets.token_urlsafe(48) + "\n",
            encoding="utf-8",
        )
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path.read_text(encoding="utf-8").strip()


def get(path: str, bridge_token: str) -> dict:
    request = Request(
        "http://127.0.0.1:8765" + path,
        headers={"Authorization": f"Bearer {bridge_token}"},
    )
    with urlopen(request, timeout=3) as response:
        return json.loads(response.read(2_000_000).decode("utf-8"))


def healthy() -> bool:
    try:
        return isinstance(get("/health", ""), dict)
    except Exception:
        return False


EXPECTED_BRIDGE_SERVICE = "pasi-engineering-workspace-chatgpt-bridge"
EXPECTED_DEPLOYMENT_ID = "pasi-engineering-workspace-handoff-v1"


def bridge_status(bridge_token: str) -> dict | None:
    try:
        payload = get("/status", bridge_token)
        return payload if isinstance(payload, dict) else None
    except HTTPError:
        return None
    except Exception:
        return None


def authorized(bridge_token: str) -> bool:
    return bridge_status(bridge_token) is not None


def is_engineering_workspace_bridge(bridge_token: str) -> bool:
    payload = bridge_status(bridge_token)
    return bool(payload and payload.get("service") == EXPECTED_BRIDGE_SERVICE)


def controller_source(root: Path) -> Path:
    for candidate in (root / "src" / "content.js", root / "content.js"):
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "PASI ChatGPT Handoff controller source not found: expected "
        "src/content.js or content.js"
    )


def version(root: Path) -> str | None:
    match = re.search(
        r"\bCONTROLLER_VERSION\s*=\s*['\"]([^'\"]+)['\"]",
        controller_source(root).read_text(encoding="utf-8"),
    )
    return match.group(1).strip() if match else None

def deployment_id(root: Path) -> str | None:
    match = re.search(
        r"\bPASI_DEPLOYMENT_ID\s*=\s*['\"]([^'\"]+)['\"]",
        controller_source(root).read_text(encoding="utf-8"),
    )
    return match.group(1).strip() if match else None


def start_bridge(runtime: Path, bridge_token: str) -> None:
    log = (runtime / "bridge.log").open("a", encoding="utf-8")
    env = {
        **os.environ,
        "PASI_BRIDGE_TOKEN": bridge_token,
        "PASI_RUNTIME_DIR": str(runtime),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "automation.orchestrator.bridge"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    (runtime / "bridge.pid").write_text(
        str(process.pid) + "\n",
        encoding="utf-8",
    )
    log.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--extension-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.extension_root.expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise SystemExit("PASI ChatGPT Handoff must be MV3")
    if manifest.get("name") != "PASI ChatGPT Handoff":
        raise SystemExit(
            f"unexpected browser extension: {manifest.get('name')!r}; "
            "the canonical runtime uses PASI ChatGPT Handoff"
        )
    controller_source(root)

    bridge_token = token()
    runtime = Path(
        os.environ.get(
            "PASI_ENGINEERING_RUNTIME_DIR",
            str(Path.home() / ".pasi" / "engineering-workspace-168h" / "runtime"),
        )
    ).expanduser().resolve()
    runtime.mkdir(parents=True, exist_ok=True)

    if healthy():
        if not authorized(bridge_token):
            raise SystemExit(
                "127.0.0.1:8765 is already serving a PASI bridge that rejects the "
                "Engineering Workspace bridge token. Stop the stale bridge process "
                "(do not rotate credentials behind a live bridge), then rerun this "
                "preflight."
            )
        if not is_engineering_workspace_bridge(bridge_token):
            raise SystemExit(
                "127.0.0.1:8765 is already serving a non-Engineering-Workspace "
                "PASI bridge. Stop the stale legacy bridge process, then rerun "
                "this preflight."
            )

    if not is_engineering_workspace_bridge(bridge_token):
        start_bridge(runtime, bridge_token)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not is_engineering_workspace_bridge(bridge_token):
        time.sleep(0.5)

    if not is_engineering_workspace_bridge(bridge_token):
        raise SystemExit(
            f"Engineering Workspace PASI bridge did not become authorized; inspect "
            f"{runtime / 'bridge.log'}"
        )

    extension_token = root / ".bridge-token"
    extension_token.write_text(bridge_token + "\n", encoding="utf-8")
    extension_token.chmod(stat.S_IRUSR | stat.S_IWUSR)

    observation = get("/browser/health", bridge_token)
    observation = observation.get("observation", {})
    data = observation.get("data", {}) if isinstance(observation, dict) else {}
    expected = version(root)
    expected_deployment = deployment_id(root)
    if data.get("kind") not in {"chatgpt_health", "chatgpt_state"}:
        raise SystemExit(
            "Engineering Workspace ChatGPT controller is not reporting a usable health state"
        )
    if expected and data.get("controller_version") != expected:
        raise SystemExit(
            f"controller version mismatch: extension={expected!r}, "
            f"browser={data.get('controller_version')!r}"
        )
    if expected_deployment and data.get("deployment_id") != expected_deployment:
        raise SystemExit(
            f"browser deployment mismatch: extension={expected_deployment!r}, "
            f"browser={data.get('deployment_id')!r}; load the canonical "
            "PASI ChatGPT Handoff deployment before running P0.4"
        )

    print(
        json.dumps(
            {
                "bridge": "healthy",
                "authorized": True,
                "extension_root": str(root),
                "controller_version": expected,
                "browser": data,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
