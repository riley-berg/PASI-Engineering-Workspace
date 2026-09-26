from pathlib import Path
import json
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "pasi-chatgpt"

class TestExtensionAPIV2(unittest.TestCase):
    def test_surface_files_exist(self):
        for name in [
            "src/api_contract.js",
            "src/api_v2.js",
            "src/background-api.js",
            "src/background.js",
            "src/copy-api.js",
        ]:
            self.assertTrue((EXT / name).is_file(), name)

    def test_manifest_declares_native_capabilities(self):
        manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        permissions = set(manifest["permissions"])
        for name in ["storage", "tabs", "contextMenus", "notifications", "downloads", "scripting", "webRequest", "declarativeNetRequest"]:
            self.assertIn(name, permissions)
        optional = set(manifest["optional_host_permissions"])
        self.assertIn("https://*/*", optional)
        self.assertIn("http://*/*", optional)
        self.assertIn("src/api_v2.js", manifest["content_scripts"][0]["js"])

    def test_api_is_self_contained(self):
        for name in ["src/api_contract.js", "src/api_v2.js", "src/background-api.js", "src/background.js"]:
            source = (EXT / name).read_text(encoding="utf-8")
            self.assertNotIn("Tampermonkey", source)
            self.assertNotIn("Greasemonkey", source)
            self.assertNotIn("GM_xmlhttpRequest", source)

    def test_contract_covers_requested_groups(self):
        source = (EXT / "src/api_contract.js").read_text(encoding="utf-8")
        for token in [
            "STORAGE_GET", "STORAGE_INFO", "STORAGE_WATCH",
            "DB_ENSURE", "DB_GET",
            "HTTP_REQUEST", "HTTP_STREAM_START", "HTTP_GRANT_ORIGIN",
            "MENU_REGISTER", "TAB_OPEN", "WEB_OBSERVE", "WEB_RULE_ADD",
            "NOTIFICATION_SHOW", "DOWNLOAD", "PERMISSION_REQUEST",
            "SCRIPT_REGISTER",
        ]:
            self.assertIn(token, source)

    def test_all_extension_javascript_parses(self):
        for path in sorted((EXT / "src").glob("*.js")):
            result = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

if __name__ == "__main__":
    unittest.main()
