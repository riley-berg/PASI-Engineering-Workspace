from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = ROOT / "extensions" / "pasi-chatgpt"
CONTRACT = EXTENSION_ROOT / "src" / "api_contract.js"
API = EXTENSION_ROOT / "src" / "api.js"
BACKGROUND_API = EXTENSION_ROOT / "src" / "background-api.js"
MANIFEST = EXTENSION_ROOT / "manifest.json"


class TestExtensionAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = CONTRACT.read_text(encoding="utf-8")
        self.api = API.read_text(encoding="utf-8")
        self.background_api = BACKGROUND_API.read_text(encoding="utf-8")
        self.manifest = MANIFEST.read_text(encoding="utf-8")

    def test_api_is_native_extension_api_not_tampermonkey_dependent(self) -> None:
        for source in (self.contract, self.api, self.background_api):
            self.assertNotIn("GM_xmlhttpRequest", source)
            self.assertNotIn("GM_setValue", source)
            self.assertNotIn("GM_getValue", source)
            self.assertNotIn("Tampermonkey", source)
            self.assertIn("chrome.", source)

    def test_storage_is_namespaced(self) -> None:
        self.assertIn("namespacedKey", self.contract)
        self.assertIn("pasi:api:", self.contract)
        self.assertIn("normalizeNamespace", self.contract)

    def test_http_is_origin_allowlisted(self) -> None:
        self.assertIn("ALLOWED_HTTP_ORIGINS", self.background_api)
        self.assertIn('"http://127.0.0.1:8765"', self.background_api)
        self.assertIn("PASI HTTP origin is not permitted", self.background_api)

    def test_http_rejects_arbitrary_headers(self) -> None:
        self.assertIn("SAFE_HEADER_NAMES", self.background_api)
        self.assertIn("HTTP header is not permitted", self.background_api)

    def test_http_has_timeout_and_body_limits(self) -> None:
        self.assertIn("httpTimeoutMs", self.contract)
        self.assertIn("httpBodyChars", self.contract)
        self.assertIn("AbortController", self.background_api)

    def test_http_disables_credentials_and_follows_redirects(self) -> None:
        self.assertIn('credentials: "omit"', self.background_api)
        self.assertIn('redirect: "error"', self.background_api)

    def test_api_has_native_async_primitives(self) -> None:
        self.assertIn("async function request", self.api)
        self.assertIn("async function retry", self.api)
        self.assertIn("EventTarget", self.api)
        self.assertIn("runtimeInfo", self.api)

    def test_manifest_grants_required_local_bridge_permission(self) -> None:
        self.assertIn('"storage"', self.manifest)
        self.assertIn('"http://127.0.0.1:8765/*"', self.manifest)


if __name__ == "__main__":
    unittest.main()
