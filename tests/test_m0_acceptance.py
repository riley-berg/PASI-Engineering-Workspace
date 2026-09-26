from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pasi.core.m0_acceptance import (
    M0AcceptanceError,
    M0Response,
    PromptProgression,
    parse_response_file,
)


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
            "next_task_id": "P0.2",
            "next_prompt": "Now complete P0.2 using the verified P0.1 result.",
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
        ):
            with self.assertRaises(M0AcceptanceError):
                self.response(**overrides)

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
        self.assertIn("generated exactly once after verified completion", roadmap)

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
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "response.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            parsed = parse_response_file(path)
        self.assertEqual(parsed.task_id, "P0.1")


if __name__ == "__main__":
    unittest.main()

