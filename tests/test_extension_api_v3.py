from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "pasi-chatgpt"


def test_api_v3_loads_after_v2():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][0]["js"]
    assert scripts.index("src/api_v2.js") < scripts.index("src/api_v3.js")


def test_api_v3_exposes_requested_improvements():
    source = (EXT / "src/api_v3.js").read_text(encoding="utf-8")
    for token in [
        "PASI.http.fetch",
        "streamResponse",
        "modifyHeaders",
        "menu_controls",
        "element_present_run_at",
        "structuredTypes",
        "register(name, definition)",
    ]:
        assert token in source


def test_api_v3_has_no_userscript_manager_dependency():
    for name in ["src/api_v3.js", "src/api_contract.js", "src/background-api.js"]:
        source = (EXT / name).read_text(encoding="utf-8")
        assert "Tampermonkey" not in source
        assert "Greasemonkey" not in source
        assert "GM_xmlhttpRequest" not in source


def test_api_v3_and_background_parse():
    for name in ["api_contract.js", "api_v2.js", "api_v3.js", "background-api.js", "background.js"]:
        result = subprocess.run(
            ["node", "--check", str(EXT / "src" / name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_manifest_contains_requested_mv3_controls():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    permissions = set(manifest["permissions"])
    assert {"tabs", "contextMenus", "notifications", "downloads", "scripting", "webRequest", "declarativeNetRequest"} <= permissions
    assert {"https://*/*", "http://*/*"} <= set(manifest["optional_host_permissions"])
