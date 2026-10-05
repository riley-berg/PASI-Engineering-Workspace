from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "pasi-chatgpt"


def test_userscript_manifest_and_load_order():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["minimum_chrome_version"] == "120"
    assert "userScripts" in manifest["permissions"]
    assert manifest["options_ui"]["page"] == "options.html"
    scripts = manifest["content_scripts"][0]["js"]
    assert scripts.index("src/api_v3.js") < scripts.index("src/api_v4.js")


def test_userscript_architecture_files_exist_and_are_self_contained():
    for name in [
        "src/userscript_contract.js",
        "src/userscript_runtime.js",
        "src/background-userscripts.js",
        "src/api_v4.js",
        "src/userscript_backup.js",
    ]:
        assert (EXT / name).is_file(), name

    for name in [
        "src/userscript_contract.js",
        "src/userscript_runtime.js",
        "src/background-userscripts.js",
        "src/api_v4.js",
    ]:
        source = (EXT / name).read_text(encoding="utf-8")
        assert "Tampermonkey" not in source
        assert "GM_xmlhttpRequest" in source or name != "src/userscript_runtime.js" or "GM_xmlhttpRequest" in source


def test_userscript_contract_declares_architecture_boundaries():
    source = (EXT / "src/userscript_contract.js").read_text(encoding="utf-8")
    for token in [
        'case "match"',
        'case "grant"',
        'case "run-at"',
        'case "noframes"',
        'case "connect"',
        "unsafeWindow",
        "mainWorld",
        "PASIUserScriptContract",
    ]:
        assert token in source

    manager = (EXT / "src/background-userscripts.js").read_text(encoding="utf-8")
    for token in [
        "chrome.userScripts.register",
        "chrome.userScripts.unregister",
        "chrome.userScripts.configureWorld",
        "onUserScriptMessage",
        "onUserScriptConnect",
        "requireGrant",
        "connectAllowed",
    ]:
        assert token in manager


def test_userscript_runtime_exposes_gm_compatibility_and_isolated_boundary():
    source = (EXT / "src/userscript_runtime.js").read_text(encoding="utf-8")
    for token in [
        "PASIUserScript",
        "GM_getValue",
        "GM_setValue",
        "GM_addValueChangeListener",
        "GM_registerMenuCommand",
        "GM_xmlhttpRequest",
        "unsafeWindow",
        "pasi.userscript.rpc",
    ]:
        assert token in source


def test_all_userscript_javascript_parses():
    for name in [
        "userscript_contract.js",
        "userscript_runtime.js",
        "userscript_backup.js",
        "background-userscripts.js",
        "api_v4.js",
        "background.js",
        "options.js",
    ]:
        target = EXT / name if name == "options.js" else EXT / "src" / name
        result = subprocess.run(
            ["node", "--check", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_userscript_productization_contracts():
    backup = (EXT / "src/userscript_backup.js").read_text(encoding="utf-8")
    manager = (EXT / "src/background-userscripts.js").read_text(encoding="utf-8")
    runtime = (EXT / "src/userscript_runtime.js").read_text(encoding="utf-8")
    api = (EXT / "src/api_v4.js").read_text(encoding="utf-8")
    options = (EXT / "options.js").read_text(encoding="utf-8")
    for token in [
        "pasi-userscript-backup",
        "encodeSync",
        "decodeSync",
        "USERSCRIPT_BACKUP",
        "USERSCRIPT_RESTORE",
        "USERSCRIPT_SYNC",
        "chrome.storage.sync",
        "hosts_granted",
        "tags",
        "group",
    ]:
        assert token in backup + manager + runtime + api + options
    assert "menu-command" in runtime
    assert "wrapSource" in runtime
    assert "runtime.wrapSource(script.source)" in manager
    assert "chrome.permissions.request" in options
