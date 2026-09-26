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

    def test_operation_identity_exists_before_prompt_injection(self) -> None:
        start = self.content.index("async function injectCurrentPrompt(")
        end = self.content.index("async function markRecoveryPending(", start)
        block = self.content[start:end]
        self.assertLess(block.index("await startOperation()"), block.index("await chatgpt.injectPrompt(prompt)"))
        self.assertIn("pasi_prompt_injection_state", self.content)

    def test_context_recovery_preserves_automation_chat_identity(self) -> None:
        self.assertIn("sessionAutomationChatUrl", self.content)
        self.assertIn("setSessionAutomationChatUrl", self.content)
        self.assertIn("automation_chat_url: sessionAutomationChatUrl()", self.content)

    def test_prompt_delivery_is_verified_against_user_message(self) -> None:
        self.assertIn("hasUserMessageText(prompt)", self.content)
        self.assertIn('sessionInjection.status === "sending"', self.content)
        self.assertIn('sessionInjection.status === "sent"', self.content)

    def test_reconnect_only_marks_restored_after_generation_resumes(self) -> None:
        start = self.content.index("async function handleOnline()")
        end = self.content.index("function restoreContextRecoveryState()", start)
        online = self.content[start:end]
        self.assertNotIn("CONNECTION_RESTORED", online)
        self.assertIn("RESUME_REQUEST", online)
        self.assertIn("resumeRequestSent", online)
        self.assertIn("resumed_after_reconnect: true", self.content)

    def test_background_receives_real_prompt_delivery_ack(self) -> None:
        self.assertIn("sendResponse", self.content)
        self.assertIn("response?.ok === true", self.background)

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
