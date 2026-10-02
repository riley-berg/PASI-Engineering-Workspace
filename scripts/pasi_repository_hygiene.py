#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_EXTENSION_ROOT = Path("extensions/pasi-chatgpt")
CDP_CONTROLLER = ACTIVE_EXTENSION_ROOT / "src" / "cdp-network-controller.js"

FORBIDDEN_CDP_PATTERNS = (
    r"\bMutationObserver\b",
    r"\bdocument\.(?:querySelector|querySelectorAll|getElementById|getElementsByClassName|getElementsByTagName|evaluate)\b",
    r"\bRuntime\.evaluate\b",
    r"\bDOM\.(?:getDocument|querySelector|getSearchResults|performSearch)\b",
    r"\bchrome\.scripting\.executeScript\b",
)

REMOVED_LEGACY_PATH_MARKERS = (
    "network-interceptor.js",
    "shadow-event-bus",
    "shadow_event_bus",
    "phase2-shadow",
    "dom-controller.js",
)

RETIRED_TRACKED_PATH_MARKERS = (
    "extensions/pasi-chatgpt/src/protocol.js",
    "extensions/pasi-chatgpt/src/copy-api.js",
)

SUSPICIOUS_TRACKED_NAMES = (
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "cookies.json",
    "session.json",
    "private-key.pem",
    ".bridge-token",
)

MAX_TRACKED_FILE_BYTES = 5 * 1024 * 1024


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), *args],
        text=True,
        stderr=subprocess.STDOUT,
    )


def tracked_paths() -> list[Path]:
    return [Path(p) for p in git("ls-files").splitlines() if p.strip()]


def working_tree_entries() -> list[str]:
    return [line for line in git("status", "--porcelain=v1", "--untracked-files=all").splitlines() if line.strip()]


def extension_manifests(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.name == "manifest.json" and str(p).startswith("extensions/")]


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    return payload


def check_manifest(paths: list[Path]) -> dict[str, Any]:
    manifests = extension_manifests(paths)
    named: list[dict[str, Any]] = []
    for path in manifests:
        try:
            payload = read_json(path)
        except Exception as exc:
            return {"ok": False, "reason": f"could not parse {path}: {exc}", "manifests": [str(p) for p in manifests]}
        if payload.get("name") == "PASI ChatGPT Handoff":
            named.append({
                "path": str(path),
                "version": payload.get("version"),
                "service_worker": payload.get("background", {}).get("service_worker")
                    if isinstance(payload.get("background"), dict) else None,
            })

    return {
        "ok": len(named) == 1 and named[0]["path"] == str(ACTIVE_EXTENSION_ROOT / "manifest.json"),
        "manifests": named,
        "reason": (
            "exactly one canonical PASI ChatGPT Handoff manifest"
            if len(named) == 1
            else f"expected exactly one canonical PASI ChatGPT Handoff manifest, found {len(named)}"
        ),
    }


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        normalized = str(path).replace("\\", "/")
        lower = normalized.lower()
        if normalized in RETIRED_TRACKED_PATH_MARKERS:
            findings.append(f"retired extension path remains tracked: {path}")
        if lower.startswith("scripts/test_") and lower.endswith(".py"):
            findings.append(f"test module is outside canonical tests/ tree: {path}")
        if any(marker in lower for marker in REMOVED_LEGACY_PATH_MARKERS):
            # Unit-test/document references may mention these names explicitly.
            if not (str(path).startswith("tests/") or str(path).startswith("docs/")):
                findings.append(f"removed legacy runtime path remains tracked: {path}")
        if path.name in SUSPICIOUS_TRACKED_NAMES:
            findings.append(f"suspicious credential/session artifact is tracked: {path}")
    return findings


def scan_cdp_authority() -> list[str]:
    controller = REPO_ROOT / CDP_CONTROLLER
    if not controller.is_file():
        return [f"canonical CDP controller missing: {CDP_CONTROLLER}"]
    source = controller.read_text(encoding="utf-8")
    findings = []
    for pattern in FORBIDDEN_CDP_PATTERNS:
        if re.search(pattern, source):
            findings.append(f"legacy/imperative DOM authority remains in {CDP_CONTROLLER}: {pattern}")
    return findings


def check_python(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if path.suffix != ".py":
            continue
        try:
            ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            findings.append(f"Python syntax error in {path}: {exc}")
    return findings


def check_javascript(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    node = subprocess.run(["node", "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if node.returncode != 0:
        return ["node is required for JavaScript syntax hygiene"]
    for path in paths:
        if path.suffix not in {".js", ".cjs", ".mjs"}:
            continue
        result = subprocess.run(
            ["node", "--check", str(REPO_ROOT / path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        if result.returncode != 0:
            findings.append(f"JavaScript syntax error in {path}: {result.stdout.strip()[-1000:]}")
    return findings


def check_file_sizes(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        try:
            size = (REPO_ROOT / path).stat().st_size
        except OSError:
            continue
        if size > MAX_TRACKED_FILE_BYTES:
            findings.append(f"tracked file exceeds {MAX_TRACKED_FILE_BYTES} bytes: {path} ({size})")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-destructive PASI repository-wide hygiene audit.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat a dirty/untracked working tree as a failure. CI should use this.",
    )
    args = parser.parse_args()

    paths = tracked_paths()
    dirty = working_tree_entries()

    findings = []
    findings.extend(scan_paths(paths))
    findings.extend(scan_cdp_authority())
    findings.extend(check_python(paths))
    findings.extend(check_javascript(paths))
    findings.extend(check_file_sizes(paths))

    manifest = check_manifest(paths)
    if not manifest["ok"]:
        findings.append(manifest["reason"])

    result = {
        "ok": not findings and (not args.strict or not dirty),
        "strict": args.strict,
        "tracked_file_count": len(paths),
        "working_tree": {
            "clean": not dirty,
            "entries": dirty,
        },
        "canonical_extension": manifest,
        "checks": {
            "legacy_runtime_paths": "passed" if not scan_paths(paths) else "failed",
            "cdp_dom_authority": "passed" if not scan_cdp_authority() else "failed",
            "python_syntax": "passed" if not check_python(paths) else "failed",
            "javascript_syntax": "passed" if not check_javascript(paths) else "failed",
            "tracked_file_sizes": "passed" if not check_file_sizes(paths) else "failed",
        },
        "findings": findings,
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("PASI REPOSITORY HYGIENE")
        print("=" * 23)
        print(f"Tracked files: {len(paths)}")
        print(f"Working tree: {'clean' if not dirty else 'dirty'}")
        for name, status in result["checks"].items():
            print(f"{'PASS' if status == 'passed' else 'FAIL'} {name}")
        if not dirty:
            print("PASS tracked working tree has no local modifications")
        elif args.strict:
            print("FAIL strict mode requires a clean working tree")
        else:
            print("WARN local working tree has entries; audit made no changes")
        if findings:
            print("\nFindings:")
            for finding in findings:
                print(f"- {finding}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
