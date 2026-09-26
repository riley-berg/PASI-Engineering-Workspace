from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = ROOT / "extensions" / "pasi-chatgpt"
CONTENT_JS = EXTENSION_ROOT / "src" / "content.js"
BACKGROUND_JS = EXTENSION_ROOT / "src" / "background.js"


class TestExtensionLifecycleSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.content = CONTENT_JS.read_text(encoding="utf-8")
        self.background = BACKGROUND_JS.read_text(encoding="utf-8")

    def test_context_invalidation_never_reloads_the_chatgpt_page(self) -> None:
        self.assertNotIn("window.location.reload()", self.content)
        self.assertNotIn("scheduleContextRecovery", self.content)
        self.assertIn("markExtensionContextDead", self.content)
        self.assertIn("mutationObserver.disconnect()", self.content)
        self.assertIn("window.clearInterval(heartbeatTimer)", self.content)

    def test_context_invalidation_stops_observation_and_preserves_state(self) -> None:
        self.assertIn('sessionStorage.setItem(', self.content)
        self.assertIn('"pasi_extension_context_recovery"', self.content)
        self.assertIn("extensionContextDead = true", self.content)
        self.assertIn("if (extensionContextDead || observationRunning)", self.content)

    def test_observer_is_throttled_and_serialized(self) -> None:
        self.assertIn("scheduleObserve(150)", self.content)
        self.assertIn("if (extensionContextDead || observeTimer !== null)", self.content)
        self.assertIn("observationRunning = true", self.content)
        self.assertIn("observationRunning = false", self.content)

    def test_no_synthetic_connection_recovery_prompt_exists(self) -> None:
        self.assertNotIn("PASI CONNECTION RECOVERY", self.content)
        self.assertNotIn("submitRecoveryPrompt", self.content)
        self.assertNotIn("recovery_via_new_prompt: true", self.content)

    def test_storage_and_runtime_message_apis_have_single_controlled_entry_points(self) -> None:
        self.assertEqual(
            len(re.findall(r"chrome\.storage\.local\.get\(", self.content)),
            1,
        )
        self.assertEqual(
            len(re.findall(r"chrome\.storage\.local\.set\(", self.content)),
            1,
        )
        self.assertEqual(
            len(re.findall(r"chrome\.runtime\.sendMessage\(", self.content)),
            1,
        )

    def test_background_tab_messages_are_guarded(self) -> None:
        self.assertIn("async function sendTabMessage(tabId, message)", self.background)
        self.assertEqual(
            len(re.findall(r"chrome\.tabs\.sendMessage\(", self.background)),
            1,
        )
        self.assertIn("prompt_delivery_failed", self.background)

    def test_extension_context_is_not_treated_as_chat_connection_loss(self) -> None:
        start = self.content.index("async function beginRecovery(reason)")
        end = self.content.index("async function handleChatLimit()", start)
        begin_recovery = self.content[start:end]
        self.assertIn("isExtensionContextInvalidated(reason)", begin_recovery)
        self.assertIn('reason === "extension context invalidated"', begin_recovery)
        self.assertNotIn("injectPrompt(", begin_recovery)


if __name__ == "__main__":
    unittest.main()
