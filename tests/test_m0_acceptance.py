from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pasi.core.m0_acceptance import (
    M0AcceptanceError,
    M0Response,
    PromptProgression,
    _canonical_m0_proof_patch,
    apply_validate_commit,
    parse_response_file,
)
from scripts.run_m0_live_acceptance import persist_evidence


class TestM0Acceptance(unittest.TestCase):
    def response(self, **overrides: object) -> M0Response:
        payload = {
            "provider": "chatgpt_browser",
            "authenticated": True,
            "chat_url": "https://chatgpt.com/c/abc123",
            "task_id": "P0.1",
            "status": "complete",
            "summary": "Completed the task and produced direct evidence.",
            "evidence": "canonical validation passed",
            "patch": "diff --git a/acceptance/M0-LIVE-PROOF.txt b/acceptance/M0-LIVE-PROOF.txt",
            "runtime_evidence": {
                "fresh_chat_created_after_usage": False,
                "fresh_chat_creation_reason": "",
                "thinking_enabled": True,
                "connection_recovery": {
                    "connection_loss_detected": True,
                    "response_stopped_on_loss": True,
                    "checkpoint_preserved": True,
                    "resumed_after_reconnect": True,
                    "same_operation_resumed": True,
                    "operation_id": "op-1",
                    "resume_phase": "contract_parsing",
                },
            },
        }
        payload.update(overrides)
        return M0Response.from_mapping(payload)

    def test_authenticated_complete_response_is_accepted(self) -> None:
        value = self.response()
        self.assertEqual(value.task_id, "P0.1")
        self.assertTrue(value.authenticated)

    def test_incomplete_or_unauthenticated_response_cannot_advance(self) -> None:
        for overrides in (
            {"authenticated": False},
            {"status": "in_progress"},
            {"chat_url": "https://example.com/c/abc"},
            {"runtime_evidence": None},
        ):
            with self.assertRaises(M0AcceptanceError):
                self.response(**overrides)

    def test_live_contract_allows_same_chat_and_requires_thinking_and_connection_recovery(self) -> None:
        value = self.response()
        self.assertFalse(value.runtime_evidence.fresh_chat_created_after_usage)
        self.assertTrue(value.runtime_evidence.thinking_enabled)
        self.assertTrue(value.runtime_evidence.connection_recovery.resumed_after_reconnect)

    def test_live_contract_allows_usage_gated_fresh_chat(self) -> None:
        value = self.response(
            runtime_evidence={
                "fresh_chat_created_after_usage": True,
                "fresh_chat_creation_reason": "usage_limit",
                "thinking_enabled": True,
                "connection_recovery": {
                    "connection_loss_detected": True,
                    "response_stopped_on_loss": True,
                    "checkpoint_preserved": True,
                    "resumed_after_reconnect": True,
                    "same_operation_resumed": True,
                    "operation_id": "op-1",
                    "resume_phase": "contract_parsing",
                },
            }
        )
        self.assertTrue(value.runtime_evidence.fresh_chat_created_after_usage)
        self.assertEqual(value.runtime_evidence.fresh_chat_creation_reason, "usage_limit")

    def test_prompt_does_not_change_before_completion(self) -> None:
        state = PromptProgression(task_id="P0.1", prompt="Complete P0.1.")
        self.assertEqual(state.prompt, "Complete P0.1.")
        with self.assertRaises(M0AcceptanceError):
            state.complete(
                verified_task_id="P0.2",
                evidence="not allowed",
                next_task_id="P0.3",
                next_prompt="Complete P0.3.",
            )
        self.assertFalse(state.completed)
        self.assertEqual(state.task_id, "P0.1")

    def test_prompt_changes_exactly_once_after_verified_completion(self) -> None:
        state = PromptProgression(task_id="P0.1", prompt="Complete P0.1.")
        advanced = state.complete(
            verified_task_id="P0.1",
            evidence="commit abc and clean worktree",
            next_task_id="P0.2",
            next_prompt="Complete P0.2 using P0.1 evidence.",
        )
        self.assertEqual(advanced.task_id, "P0.2")
        self.assertNotEqual(advanced.prompt, state.prompt)
        self.assertTrue(advanced.completion_digest)
        with self.assertRaises(M0AcceptanceError):
            advanced.complete(
                verified_task_id="P0.1",
                evidence="duplicate advancement",
                next_task_id="P0.3",
                next_prompt="Complete P0.3.",
            )

    def test_roadmap_records_completion_gated_prompt_progression(self) -> None:
        roadmap = (Path(__file__).resolve().parents[1] / "roadmap" / "p0-p4.md").read_text(encoding="utf-8")
        self.assertIn("current task prompt is immutable while the task is incomplete", roadmap)
        self.assertIn("exactly one new prompt is generated after verified completion", roadmap)
        self.assertIn("create a fresh chat only after an explicit ChatGPT usage/context limit", roadmap)
        self.assertIn("Thinking enabled", roadmap)
        self.assertIn("connection-loss stop/resume", roadmap)

    def test_live_acceptance_evidence_survives_temp_worktree_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "temporary" / "m0-live.json"
            destination = root / "durable" / "m0-live.json"
            source.parent.mkdir(parents=True)
            source.write_text("{\"status\": \"PASS\"}\n", encoding="utf-8")
            persisted = persist_evidence(source, destination)
            source.unlink()
            self.assertEqual(persisted, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "{\"status\": \"PASS\"}\n")

    def test_live_acceptance_script_imports_from_repo_root(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(repo / "scripts" / "run_m0_live_acceptance.py"), "--help"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_proof_artifact_expectation_uses_a_real_trailing_newline(self) -> None:
        default = inspect.signature(apply_validate_commit).parameters["expected_proof"].default
        self.assertEqual(default, "PASI M0 LIVE PROOF\n")

    def test_m0_proof_patch_is_canonicalized_and_other_changes_are_rejected(self) -> None:
        patch = (
            "diff --git a/acceptance/M0-LIVE-PROOF.txt b/acceptance/M0-LIVE-PROOF.txt\n"
            "new file mode 100644\n"
            "index 0000000..4c6c0d6\n"
            "--- /dev/null\n"
            "+++ b/acceptance/M0-LIVE-PROOF.txt\n"
            "@@ -0,0 +1 @@\n"
            "+PASI M0 LIVE PROOF\n"
        )
        canonical = _canonical_m0_proof_patch(patch)
        self.assertEqual(canonical.splitlines()[0], "diff --git a/acceptance/M0-LIVE-PROOF.txt b/acceptance/M0-LIVE-PROOF.txt")
        self.assertEqual(canonical.splitlines()[-1], "+PASI M0 LIVE PROOF")
        with self.assertRaises(M0AcceptanceError):
            _canonical_m0_proof_patch(patch + "+unexpected\n")

    def test_response_file_round_trip(self) -> None:
        value = {
            "provider": "chatgpt_browser",
            "authenticated": True,
            "chat_url": "https://chatgpt.com/c/abc123",
            "task_id": "P0.1",
            "status": "complete",
            "summary": "done",
            "evidence": "evidence",
            "patch": "diff --git a/a b/a",
            "runtime_evidence": {
                "fresh_chat_created_after_usage": False,
                "fresh_chat_creation_reason": "",
                "thinking_enabled": True,
                "connection_recovery": {
                    "connection_loss_detected": True,
                    "response_stopped_on_loss": True,
                    "checkpoint_preserved": True,
                    "resumed_after_reconnect": True,
                    "same_operation_resumed": True,
                    "operation_id": "op-1",
                    "resume_phase": "contract_parsing",
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            parsed = parse_response_file(path)
        self.assertEqual(parsed.task_id, "P0.1")


if __name__ == "__main__":
    unittest.main()

