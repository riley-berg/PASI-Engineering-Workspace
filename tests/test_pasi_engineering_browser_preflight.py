from pathlib import Path

import pytest

from scripts import pasi_engineering_browser_preflight as preflight


def _write_extension(root: Path, *, network_authority: bool = True) -> None:
    (root / "src").mkdir(parents=True)
    (root / "manifest.json").write_text(
        """{
          "manifest_version": 3,
          "name": "PASI ChatGPT Handoff",
          "permissions": ["tabs", "debugger", "storage"],
          "host_permissions": [
            "https://chatgpt.com/*",
            "https://www.chatgpt.com/*",
            "http://127.0.0.1:8765/*"
          ],
          "background": {"service_worker": "src/background.js"}
        }""",
        encoding="utf-8",
    )
    authority = "true" if network_authority else "false"
    (root / "src" / "background.js").write_text(
        "chatgpt_health controller_version network_authority: "
        + authority
        + " native_controller: true",
        encoding="utf-8",
    )
    (root / "src" / "cdp-network-controller.js").write_text(
        """
        const createController = true;
        const requestUrlIsGeneration = true;
        const completionMarkersSatisfied = true;
        Fetch.takeResponseBodyAsStream;
        Input.dispatchKeyEvent;
        """,
        encoding="utf-8",
    )


def test_contract_uses_cdp_worker_without_legacy_content_js(tmp_path: Path):
    _write_extension(tmp_path)
    result = preflight.read_extension_contract(tmp_path)
    assert result["service_worker"] == "src/background.js"
    assert result["controller_version"] == "cdp-worker-v1"


def test_contract_rejects_legacy_dom_authority(tmp_path: Path):
    _write_extension(tmp_path)
    controller = tmp_path / "src" / "cdp-network-controller.js"
    controller.write_text(controller.read_text(encoding="utf-8") + "\nRuntime.evaluate;\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="legacy DOM authority"):
        preflight.read_extension_contract(tmp_path)


def test_browser_health_requires_native_network_authority(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "get",
        lambda path, token, timeout=5.0: {
            "observation": {
                "schema_version": "pasi-native-chromium-v2",
                "data": {
                    "kind": "chatgpt_health",
                    "controller_version": "cdp-worker-v1",
                    "native_controller": True,
                    "network_authority": True,
                    "chat_url": "https://chatgpt.com/c/example",
                    "active_operation_id": None,
                    "page_visible": True,
                }
            }
        },
    )
    result = preflight.browser_health("token")
    assert result["network_authority"] is True
    assert result["controller_version"] == "cdp-worker-v1"
