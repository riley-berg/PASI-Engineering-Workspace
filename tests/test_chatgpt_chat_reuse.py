from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTENT_JS = ROOT / "extensions" / "pasi-chatgpt" / "src" / "content.js"


class TestChatGPTChatReuse(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CONTENT_JS.read_text(encoding="utf-8")

    def test_used_chat_switches_back_to_automation_chat_without_creating_one(self) -> None:
        start = self.source.index("async function ensureTaskChat()")
        end = self.source.index("async function observe()", start)
        ensure_task_chat = self.source[start:end]

        self.assertNotIn("createFreshChat()", ensure_task_chat)
        self.assertIn('pasi_automation_chat_url', ensure_task_chat)
        self.assertIn("if (automationChatUrl !== url)", ensure_task_chat)
        self.assertIn("chat_switch_requested: true", ensure_task_chat)
        self.assertIn("location.assign(automationChatUrl)", ensure_task_chat)

    def test_fresh_chat_requires_usage_limit(self) -> None:
        start = self.source.index("async function handleChatLimit()")
        end = self.source.index("async function ensureTaskChat()", start)
        handle_limit = self.source[start:end]

        self.assertIn("chatgpt.chatLimitReached()", handle_limit)
        self.assertIn("chatgpt.ensureThinkingEnabled()", handle_limit)
        self.assertIn("createFreshChat()", handle_limit)
        self.assertIn("without advancing the current task", handle_limit)

    def test_chat_limit_is_the_fresh_chat_trigger(self) -> None:
        start = self.source.index("async function handleChatLimit()")
        end = self.source.index("async function ensureTaskChat()", start)
        handle_limit = self.source[start:end]

        self.assertIn("chatgpt.chatLimitReached()", handle_limit)
        self.assertIn("createFreshChat()", handle_limit)
        self.assertIn("pasi_automation_chat_url", handle_limit)

    def test_completed_task_continues_in_same_chat(self) -> None:
        start = self.source.index('if (message.type === "pasi.acceptance_passed")')
        end = self.source.index("async function handleOffline()", start)
        completion_handler = self.source[start:end]

        self.assertNotIn("createFreshChat()", completion_handler)
        self.assertIn("same_chat_continuation: true", completion_handler)
        self.assertIn("protocol.TYPES.CHAT_READY", completion_handler)

    def test_fresh_chat_event_records_usage_limit_reason(self) -> None:
        observe = self.source[self.source.index("async function performObserve()"):]
        self.assertIn('fresh_chat_creation_reason: "usage_limit"', observe)

    def test_fresh_chat_updates_automation_chat_identity(self) -> None:
        start = self.source.index("if (awaitingFreshChat && url !== priorChatUrl)")
        end = self.source.index("await handleChatLimit()", start)
        fresh_chat_handler = self.source[start:end]

        self.assertIn('pasi_automation_chat_url: url', fresh_chat_handler)
        self.assertIn("protocol.TYPES.FRESH_CHAT", fresh_chat_handler)


if __name__ == "__main__":
    unittest.main()
