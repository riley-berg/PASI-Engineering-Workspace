from pathlib import Path
import json
import subprocess


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
    content_text = content.read_text(encoding="utf-8")
    assert "CONTROLLER_VERSION = '2.4.11'" in content_text
    assert "PASI_DEPLOYMENT_ID = 'pasi-engineering-workspace-handoff-v1'" in content_text


def test_computer_use_package_imports_without_historical_modules():
    from automation.computer_use.capability_gateway import CapabilityGateway
    from automation.computer_use.local_access import LocalAccessBroker

    assert CapabilityGateway is not None
    assert LocalAccessBroker is not None



def test_p0_4_runtime_import_surface_is_canonical():
    from automation.computer_use import adapters, capability_gateway, chatgpt, completion, contracts, ide_state, local_access, obstacles, preapproval, workspace_search
    from scripts import pasi_chat, pasi_chat_guard, pasi_engineering_executor, pasi_timeout_policy

    assert adapters is not None
    assert capability_gateway is not None
    assert chatgpt is not None
    assert completion is not None
    assert contracts is not None
    assert ide_state is not None
    assert local_access is not None
    assert obstacles is not None
    assert preapproval is not None
    assert workspace_search is not None
    assert pasi_chat is not None
    assert pasi_chat_guard is not None
    assert pasi_engineering_executor is not None
    assert pasi_timeout_policy.POLICY_PATH.name == "timeout-policy.json"


def test_p0_4_runtime_uses_canonical_extension_and_evolving_prompt():
    root = Path(__file__).resolve().parents[1]
    executor_source = (root / "scripts" / "pasi_engineering_executor.py").read_text(encoding="utf-8")
    guard_source = (root / "scripts" / "pasi_chat_guard.py").read_text(encoding="utf-8")
    chat_source = (root / "scripts" / "pasi_chat.py").read_text(encoding="utf-8")
    acceptance_source = (root / "scripts" / "pasi_168h_acceptance.py").read_text(encoding="utf-8")
    timeout_source = (root / "scripts" / "pasi_timeout_policy.py").read_text(encoding="utf-8")

    assert "extensions" in chat_source
    assert "src" in chat_source
    assert '"-m","scripts.pasi_chat"' in guard_source
    assert "--extension-root" in guard_source
    assert "PASI_TASK_PREVIOUS_CONTEXT" in executor_source
    assert "PASI_TASK_PREVIOUS_CONTEXT" in acceptance_source
    assert "extensions" in timeout_source
    assert "PASI_DEPLOYMENT_ID" in chat_source
    assert "deployment_id" in guard_source or "deployment_id" in chat_source
    assert "automation/chromium/pasi-chatgpt" not in executor_source
    assert "automation/chromium/pasi-chatgpt" not in guard_source
    assert "automation/chromium/pasi-chatgpt" not in chat_source
    assert "automation/chromium/pasi-chatgpt" not in timeout_source


def test_p0_4_supervisor_is_restart_safe():
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts" / "run_p0_4_168h.sh").read_text(encoding="utf-8")
    assert "PASI_168H_MAX_RESTARTS" in source
    assert "preserving run state and restarting" in source
    assert "pasi_168h_acceptance.py" in source
    result = subprocess.run(["bash", "-n", str(root / "scripts" / "run_p0_4_168h.sh")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
