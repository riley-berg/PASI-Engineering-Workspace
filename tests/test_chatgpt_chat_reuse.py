from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTENT_JS = ROOT / "extensions" / "pasi-chatgpt" / "src" / "content.js"
RECOVERY_JS = ROOT / "extensions" / "pasi-chatgpt" / "src" / "recovery.js"


class TestChatGPTChatReuse(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CONTENT_JS.read_text(encoding="utf-8")
        self.recovery = RECOVERY_JS.read_text(encoding="utf-8")

    def test_completed_task_continues_via_durable_next_operation(self) -> None:
        self.assertIn("next_operation", self.source)
        self.assertIn("scheduleImmediateOperation(chainedOperation)", self.source)
        self.assertIn("else scheduleImmediatePoll()", self.source)
        self.assertNotIn("createFreshChat()", self.source)

    def test_new_chat_is_reserved_for_verified_recovery(self) -> None:
        self.assertIn("operation_type: 'new_chat'", self.recovery)
        self.assertIn("const reason = replacementReason()", self.recovery)
        self.assertIn("if (!reason)", self.recovery)
        self.assertIn("replacement_reason: reason", self.recovery)

    def test_context_exhaustion_prepares_a_new_chat_for_the_same_operation(self) -> None:
        self.assertIn("handleContextExhausted", self.recovery)
        self.assertIn("CHAT_EXHAUSTED:", self.recovery)
        self.assertIn("await queueNewChat()", self.recovery)
        self.assertIn("markRetryableFailure", self.recovery)

    def test_usage_limit_does_not_delete_or_advance_the_current_operation(self) -> None:
        self.assertIn("CHAT_USAGE_LIMITED:", self.recovery)
        self.assertIn("will not delete or replace the conversation solely because usage is exhausted", self.recovery)
        self.assertIn("retry_runner_after_provider_limit", self.recovery)

    def test_fresh_chat_recovery_is_bounded(self) -> None:
        self.assertIn("MAX_CONTEXT_RECOVERIES", self.recovery)
        self.assertIn("MAX_NEW_CHAT_WAIT_MS", self.recovery)
        self.assertIn("new_chat recovery operation did not finish within the recovery grace window", self.recovery)

    def test_same_chat_completion_does_not_require_url_change(self) -> None:
        self.assertIn("next_operation", self.source)
        self.assertIn("scheduleImmediateOperation", self.source)
        self.assertNotIn("location.assign", self.source)

    def test_recovery_preserves_the_current_operation_context(self) -> None:
        self.assertIn("recovery_context", self.source)
        self.assertIn("recoveryContextFromActiveState", self.recovery)
        self.assertIn("resume_operation_id", self.recovery)


if __name__ == "__main__":
    unittest.main()


    def test_start_waits_for_conversation_dom_to_settle_before_first_state_report(self) -> None:
        self.assertIn("async function waitForConversationDomReady()", self.source)
        self.assertIn("stableSamples >= 3", self.source)
        self.assertIn("await waitForConversationDomReady()", self.source)
        self.assertIn("lastStateReportAt = 0", self.source)

    def test_submit_prompt_fast_paths_preserve_timing_evidence(self) -> None:
        submit = self.source.split("async function submitPrompt(", 1)[1].split(
            "function freshCompletionHandoff", 1
        )[0]
        self.assertIn("user_messages_added: countNewUserMessages(userMessages(), snapshot)", submit)
        self.assertIn("ack_verified: via === 'verified'", submit)
        self.assertIn("submission_via: finalVia", submit)
        self.assertGreaterEqual(submit.count("timing: {"), 3)

    def test_completion_waits_for_exact_dom_turn_before_acknowledgement(self) -> None:
        self.assertIn("async function waitForConversationSignatureDelta(", self.source)
        self.assertIn("targetUser = Number(baseline.user) + 1", self.source)
        self.assertIn("targetAssistant = Number(baseline.assistant) + 1", self.source)
        self.assertIn("completed response did not reach exact +1/+1 conversation counts", self.source)
        self.assertIn("signatureBaseline", self.source)
        self.assertIn("await waitForConversationSignatureDelta(signatureBaseline)", self.source)

    def test_completion_publishes_fresh_conversation_signature_before_return(self) -> None:
        finish = self.source.split("async function finishOperation(", 1)[1].split(
            "async function failOperation", 1
        )[0]
        self.assertIn("await reportObservation('chatgpt_state'", finish)
        self.assertIn("conversation_signature: conversationSignature()", finish)
        self.assertIn("before the completion", finish)
