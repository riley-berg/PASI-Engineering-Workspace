#!/usr/bin/env python3
from __future__ import annotations

import argparse
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

EXPECTED_BRIDGE_SERVICE = "pasi-engineering-workspace-chatgpt-bridge"
EXPECTED_CONTROLLER_VERSION = "cdp-worker-v1"
EXPECTED_SCHEMA = "pasi-native-chromium-v2"
CHATGPT_URL_RE = re.compile(r"^https://(?:www\.)?chatgpt\.com/c/")
REQUIRED_MANIFEST_PERMISSIONS = {"tabs", "debugger", "storage"}
REQUIRED_MANIFEST_HOSTS = {
    "https://chatgpt.com/*",
    "https://www.chatgpt.com/*",
    "http://127.0.0.1:8765/*",
}
REQUIRED_CDP_EXPORTS = {
    "createController",
    "requestUrlIsGeneration",
    "completionMarkersSatisfied",
}


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
        path.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit(f"bridge token file is empty: {path}")
    return value


def get(path: str, bridge_token: str, *, timeout: float = 3.0) -> dict:
    headers = {}
    if bridge_token:
        headers["Authorization"] = f"Bearer {bridge_token}"
    request = Request(
        "http://127.0.0.1:8765" + path,
        headers=headers,
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} returned a non-object payload")
    return payload


def service_healthy() -> bool:
    try:
        payload = get("/health", "")
    except Exception:
        return False
    return payload.get("status") == "ok" and payload.get("service") == EXPECTED_BRIDGE_SERVICE


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
    (runtime / "bridge.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    log.close()


def read_extension_contract(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise SystemExit("PASI ChatGPT Handoff must be MV3")
    if manifest.get("name") != "PASI ChatGPT Handoff":
        raise SystemExit(f"unexpected browser extension: {manifest.get('name')!r}")

    permissions = set(manifest.get("permissions", []))
    missing_permissions = sorted(REQUIRED_MANIFEST_PERMISSIONS - permissions)
    if missing_permissions:
        raise SystemExit("missing required extension permissions: " + ", ".join(missing_permissions))

    hosts = set(manifest.get("host_permissions", []))
    missing_hosts = sorted(REQUIRED_MANIFEST_HOSTS - hosts)
    if missing_hosts:
        raise SystemExit("missing required extension host permissions: " + ", ".join(missing_hosts))

    background = manifest.get("background")
    service_worker = background.get("service_worker") if isinstance(background, dict) else None
    if service_worker != "src/background.js":
        raise SystemExit("canonical runtime must use src/background.js as the MV3 service worker")

    background_path = root / "src" / "background.js"
    controller_path = root / "src" / "cdp-network-controller.js"
    if not background_path.is_file():
        raise SystemExit(f"CDP background controller not found: {background_path}")
    if not controller_path.is_file():
        raise SystemExit(f"CDP network controller not found: {controller_path}")

    background_source = background_path.read_text(encoding="utf-8")
    controller_source = controller_path.read_text(encoding="utf-8")

    required_background_signals = (
        "chatgpt_health",
        "controller_version",
        "network_authority: true",
        "native_controller: true",
    )
    for signal in required_background_signals:
        if signal not in background_source:
            raise SystemExit(f"CDP background worker is missing health signal: {signal}")

    missing_exports = sorted(name for name in REQUIRED_CDP_EXPORTS if name not in controller_source)
    if missing_exports:
        raise SystemExit("CDP controller contract is incomplete: " + ", ".join(missing_exports))
    for signal in ("Fetch.takeResponseBodyAsStream", "Input.dispatchKeyEvent"):
        if signal not in controller_source:
            raise SystemExit(f"CDP controller is missing required primitive: {signal}")

    legacy_signals = (
        "MutationObserver",
        "document.querySelector(",
        "Runtime.evaluate",
    )
    forbidden = [signal for signal in legacy_signals if signal in controller_source]
    if forbidden:
        raise SystemExit("CDP controller still contains legacy DOM authority: " + ", ".join(forbidden))

    return {
        "manifest_version": manifest.get("manifest_version"),
        "service_worker": service_worker,
        "controller_version": EXPECTED_CONTROLLER_VERSION,
        "required_permissions": sorted(REQUIRED_MANIFEST_PERMISSIONS),
        "required_hosts": sorted(REQUIRED_MANIFEST_HOSTS),
    }


def browser_health(bridge_token: str) -> dict[str, object]:
    payload = get("/browser/health", bridge_token, timeout=5)
    observation = payload.get("observation")
    data = observation.get("data") if isinstance(observation, dict) else None
    schema_version = observation.get("schema_version") if isinstance(observation, dict) else None
    if not isinstance(data, dict):
        raise SystemExit("bridge returned no browser health data")
    if schema_version != EXPECTED_SCHEMA:
        raise SystemExit(f"browser schema mismatch: {schema_version!r}")
    if data.get("kind") not in {"chatgpt_health", "chatgpt_state"}:
        raise SystemExit("ChatGPT controller is not reporting a usable health state")
    if data.get("controller_version") != EXPECTED_CONTROLLER_VERSION:
        raise SystemExit(
            f"CDP controller version mismatch: expected {EXPECTED_CONTROLLER_VERSION!r}, "
            f"browser={data.get('controller_version')!r}"
        )
    if data.get("native_controller") is not True:
        raise SystemExit("browser health does not confirm native_controller=true")
    if data.get("network_authority") is not True:
        raise SystemExit("browser health does not confirm network_authority=true")

    chat_url = data.get("chat_url")
    if not isinstance(chat_url, str) or not CHATGPT_URL_RE.match(chat_url):
        raise SystemExit("browser health does not contain a valid ChatGPT conversation URL")

    return {
        "kind": data.get("kind"),
        "schema_version": schema_version,
        "controller_version": data.get("controller_version"),
        "native_controller": data.get("native_controller"),
        "network_authority": data.get("network_authority"),
        "chat_url": chat_url,
        "active_operation_id": data.get("active_operation_id"),
        "page_visible": data.get("page_visible"),
    }


def run_probe(bridge_token: str) -> dict[str, object]:
    health = get("/health", "")
    if health.get("status") != "ok" or health.get("service") != EXPECTED_BRIDGE_SERVICE:
        raise SystemExit("localhost bridge is not the Engineering Workspace bridge")

    try:
        status = get("/status", bridge_token, timeout=5)
    except HTTPError as exc:
        if exc.code == 401:
            raise SystemExit("bridge rejected the Engineering Workspace bridge token")
        raise

    browser = browser_health(bridge_token)
    return {
        "bridge": {
            "status": health.get("status"),
            "service": health.get("service"),
            "authorized": True,
        },
        "queue": {"available": isinstance(status, dict)},
        "browser": browser,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extension-root", type=Path, required=True)
    parser.add_argument("--no-start", action="store_true")
    args = parser.parse_args()

    root = args.extension_root.expanduser().resolve()
    contract = read_extension_contract(root)
    bridge_token = token()

    runtime = Path(
        os.environ.get(
            "PASI_ENGINEERING_RUNTIME_DIR",
            str(Path.home() / ".pasi" / "engineering-workspace-168h" / "runtime"),
        )
    ).expanduser().resolve()
    runtime.mkdir(parents=True, exist_ok=True)

    if not service_healthy():
        if args.no_start:
            raise SystemExit("Engineering Workspace PASI bridge is not running")
        start_bridge(runtime, bridge_token)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not service_healthy():
            time.sleep(0.5)

    if not service_healthy():
        raise SystemExit(f"Engineering Workspace PASI bridge did not become healthy; inspect {runtime / 'bridge.log'}")

    probe = run_probe(bridge_token)
    print(json.dumps({"bridge": probe["bridge"], "browser": probe["browser"], "extension": contract}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
