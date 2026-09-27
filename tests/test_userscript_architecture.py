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
    assert manifest["action"]["default_popup"] == "popup.html"
    assert "alarms" in manifest["permissions"]
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
        "popup.js",
        "editor.js",
        "userscript_vcs.js",
        "userscript_diff.js",
        "userscript_compiler.js",
        "userscript_dnr.js",
        "userscript_install_queue.js",
    ]:
        target = EXT / name if name in {"options.js", "popup.js", "editor.js"} else EXT / "src" / name
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
        "GM_fetch",
        "GM_webRequest",
        "chrome.userScripts.execute",
        "installQueue.run",
        "VCS",
    ]:
        assert token in backup + manager + runtime + api + options
    assert "menu-command" in runtime
    assert "wrapSource" in runtime
    assert "runtime.wrapSource(script.source)" in manager
    assert "chrome.permissions.request" in options


def test_userscript_runtime_contracts():
    manager = (EXT / "src" / "background-userscripts.js").read_text(encoding="utf-8")
    runtime = (EXT / "src" / "userscript_runtime.js").read_text(encoding="utf-8")
    dnr = (EXT / "src" / "userscript_dnr.js").read_text(encoding="utf-8")
    compiler = (EXT / "src" / "userscript_compiler.js").read_text(encoding="utf-8")
    vcs = (EXT / "src" / "userscript_vcs.js").read_text(encoding="utf-8")
    for token in [
        "PASIExtensionContextError",
        "safeSendMessage",
        "pagehide",
        "lifecycle.cleanup",
        "http.fetch",
        "network.add",
        "declarativeNetRequest",
        "chrome.userScripts.execute",
    ]:
        assert token in manager + runtime
    assert "normalizeRule" in dnr
    assert "pasi-typescript-lite" in compiler
    assert "github.com" in vcs
    assert "gitlab" in vcs


def test_userscript_toolchain_contracts():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["typescript"] == "7.0.2"
    assert package["devDependencies"]["typescript-browser"] == "npm:typescript@6.0.3"
    assert package["devDependencies"]["eslint"] == "10.11.0"
    assert "build:userscript-compiler" in package["scripts"]
    assert "lint:userscripts" in package["scripts"]
    assert (ROOT / "eslint.config.mjs").is_file()
    manager = (EXT / "src/background-userscripts.js").read_text(encoding="utf-8")
    for token in [
        "USERSCRIPT_MENU_LIST",
        "USERSCRIPT_MENU_INVOKE",
        "USERSCRIPT_CLOUD_PUSH",
        "USERSCRIPT_CLOUD_PULL",
        "USERSCRIPT_VCS_FETCH",
        "USERSCRIPT_SYNC_RESOLVE",
    ]:
        assert token in manager or token in (ROOT / "extensions/pasi-chatgpt/src/api_contract.js").read_text(encoding="utf-8")

def test_userscript_threat_boundaries():
    manager = (EXT / "src" / "background-userscripts.js").read_text(encoding="utf-8")
    runtime = (EXT / "src" / "userscript_runtime.js").read_text(encoding="utf-8")
    contract = (EXT / "src" / "userscript_contract.js").read_text(encoding="utf-8")
    assert "Unauthorized PASI userscript manager message" in manager
    assert "PASI userscript IPC port is not authenticated" in manager
    assert 'type: "handshake"' in runtime
    assert 'message?.type !== "handshake"' in manager
    assert '"webRequest"' in contract
    assert '"unsafeWindow"' in contract


def test_userscript_main_world_wrapper_keeps_broker_private():
    probe = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");
const sandbox = {globalThis: {}};
vm.runInNewContext(source, sandbox);
const runtime = sandbox.globalThis.PASIUserScriptRuntime || sandbox.PASIUserScriptRuntime;
const generated = runtime.buildMainWorld({
  id: "probe",
  name: "Probe",
  grants: ["http", "mainWorld"],
  auth: "TOP-SECRET",
  source: "window.__pasiProbe = GM_info.script.id;",
});
if (generated.includes("Object.defineProperties(globalThis")) process.exit(2);
if (!generated.includes("const GM_getValue = getValue")) process.exit(3);
if (!generated.includes("window.__pasiProbe = GM_info.script.id")) process.exit(4);
if (!generated.includes("TOP-SECRET")) process.exit(5);
"""
    result = subprocess.run(
        ["node", "-e", probe, str(EXT / "src" / "userscript_runtime.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_userscript_compiler_and_toolchain_versions():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    assert package["devDependencies"]["typescript"] == "7.0.2"
    assert package["devDependencies"]["eslint"] == "10.11.0"
    assert "typescript@7.0.2" in workflow


def test_opera_extension_loading_and_ui_contracts():
    background = (EXT / "src" / "background.js").read_text(encoding="utf-8")
    assert 'importScripts("vendor/typescript.js");' in background
    assert 'importScripts("api_contract.js", "userscript_contract.js", "userscript_runtime.js"' in background
    assert 'importScripts("src/api_contract.js"' not in background
    assert 'importScripts("src/vendor/typescript.js")' not in background

    options = (EXT / "options.js").read_text(encoding="utf-8")
    assert "chrome.tabs.create" in options
    assert 'window.open("editor.html' not in options

    for stylesheet in ["popup.css", "options.css", "editor.css"]:
        css = (EXT / stylesheet).read_text(encoding="utf-8")
        assert "color-scheme: light dark" in css
