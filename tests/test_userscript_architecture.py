from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "pasi-chatgpt"


def test_userscript_manifest_and_load_order():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["minimum_chrome_version"] == "120"
    assert "userScripts" in manifest["permissions"]
    scripts = manifest["content_scripts"][0]["js"]
    assert scripts.index("src/api_v3.js") < scripts.index("src/api_v4.js")


def test_userscript_architecture_files_exist_and_are_self_contained():
    for name in [
        "src/userscript_contract.js",
        "src/userscript_runtime.js",
        "src/background-userscripts.js",
        "src/api_v4.js",
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
        "@match",
        "@grant",
        "@run-at",
        "@noframes",
        "@connect",
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
        "background-userscripts.js",
        "api_v4.js",
        "background.js",
    ]:
        result = subprocess.run(
            ["node", "--check", str(EXT / "src" / name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
