from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = ROOT / "extensions" / "pasi-chatgpt"
CONTENT_JS = EXTENSION_ROOT / "src" / "content.js"
BACKGROUND_JS = EXTENSION_ROOT / "src" / "background.js"
RECOVERY_JS = EXTENSION_ROOT / "src" / "recovery.js"


class TestExtensionLifecycleSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.content = CONTENT_JS.read_text(encoding="utf-8")
        self.background = BACKGROUND_JS.read_text(encoding="utf-8")
        self.recovery = RECOVERY_JS.read_text(encoding="utf-8")

    def test_native_controller_never_reloads_the_chatgpt_page(self) -> None:
        self.assertNotIn("window.location.reload()", self.content)
        self.assertNotIn("chrome.tabs.reload", self.background)

    def test_native_controller_uses_a_serialized_controller_lease(self) -> None:
        self.assertIn("CONTROLLER_LEASE_KEY", self.background)
        self.assertIn("controllerClaimTail", self.background)
        self.assertIn("pasi-controller-claim", self.content)
        self.assertIn("controllerClaim({ force: true })", self.content)

    def test_native_controller_uses_extension_messaging_for_the_loopback_bridge(self) -> None:
        self.assertIn("type: 'pasi-bridge-request'", self.content)
        self.assertNotIn("127.0.0.1:8765", self.content)
        self.assertIn("const BRIDGE = 'http://127.0.0.1:8765';", self.background)

    def test_completion_is_durable_before_next_prompt(self) -> None:
        self.assertIn("finishOperation(operation.operation_id, response, true, browserTiming, signatureBaseline)", self.content)
        self.assertIn("next_operation", self.content)
        self.assertIn("scheduleImmediateOperation(chainedOperation)", self.content)
        self.assertIn("'POST /chat/finished'", self.background)
        self.assertIn("next_operation", self.content)

    def test_prompt_submission_is_verified_before_completion(self) -> None:
        self.assertIn("submission.verified", self.content)
        self.assertIn("if (!submission.verified)", self.content)
        self.assertIn("response_text_available", self.content)
        self.assertIn("/chat/finished", self.content)

    def test_connection_and_context_recovery_are_bounded(self) -> None:
        self.assertIn("/chat/failed", self.content)
        self.assertIn("recovery_context", self.content)
        self.assertIn("decideRecovery", self.recovery)
        self.assertIn("RECOVERY_HARD_CEILING_MS", self.recovery)
        self.assertIn("MAX_CONTEXT_RECOVERIES", self.recovery)

    def test_security_and_auth_boundaries_do_not_auto_recover(self) -> None:
        self.assertIn("securityChallenge()", self.recovery)
        self.assertIn("manual_intervention_required", self.recovery)
        self.assertIn("auth_required", self.content)

    def test_existing_tabs_are_reused_without_navigation(self) -> None:
        self.assertIn("injectExistingChatTabs", self.background)
        self.assertIn("chrome.scripting.executeScript", self.background)
        self.assertNotIn("chrome.tabs.create", self.background)
        self.assertNotIn("chrome.tabs.reload", self.background)


if __name__ == "__main__":
    unittest.main()
