from pathlib import Path
import json


def test_engineering_workspace_uses_pasi_chatgpt_handoff():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "automation" / "legacy").exists()
    assert not (root / "automation" / "chromium" / "pasi-chatgpt").exists()

    extension = root / "extensions" / "pasi-chatgpt"
    manifest = extension / "manifest.json"
    content = extension / "src" / "content.js"

    assert manifest.is_file()
    assert content.is_file()

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["manifest_version"] == 3
    assert data["name"] == "PASI ChatGPT Handoff"
    assert data["background"]["service_worker"] == "src/background.js"
    readme = extension.joinpath("README.md").read_text(encoding="utf-8")
    assert "Tampermonkey" in readme
    assert "does not depend on Tampermonkey or Greasemonkey" in readme
    assert "CONTROLLER_VERSION = '2.4.11'" in content.read_text(encoding="utf-8")


def test_computer_use_package_imports_without_historical_modules():
    from automation.computer_use.capability_gateway import CapabilityGateway
    from automation.computer_use.local_access import LocalAccessBroker

    assert CapabilityGateway is not None
    assert LocalAccessBroker is not None
