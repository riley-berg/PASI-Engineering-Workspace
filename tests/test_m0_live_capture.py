from __future__ import annotations

import json
from pathlib import Path


def test_independent_extension_manifest() -> None:
    root = Path(__file__).resolve().parents[1] / "extensions" / "pasi-chatgpt"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "src/background.js"
    assert manifest["content_scripts"][0]["js"] == [
        "src/protocol.js",
        "src/chatgpt.js",
        "src/content.js",
    ]
    assert "self-hosted" not in json.dumps(manifest).lower()
    assert "personal-ai-system" not in json.dumps(manifest).lower()


def test_extension_files_exist() -> None:
    root = Path(__file__).resolve().parents[1] / "extensions" / "pasi-chatgpt"
    for relative in (
        "manifest.json",
        "README.md",
        "src/background.js",
        "src/chatgpt.js",
        "src/content.js",
        "src/protocol.js",
    ):
        assert (root / relative).is_file(), relative


def test_capture_bridge_is_repo_local() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m0_live_capture.py"
    assert script.is_file()
