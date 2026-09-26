from __future__ import annotations

import json

import pytest

from pasi.core.task_progression import (
    RoadmapTaskCatalog,
    TaskPromptProgression,
    TaskProgressionError,
)


ROADMAP = "roadmap/p0-p4.json"


def catalog() -> RoadmapTaskCatalog:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return RoadmapTaskCatalog.from_roadmap_file(root / ROADMAP)


def test_start_derives_prompt_from_current_roadmap_task() -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.1")
    assert progression.state.current_task_id == "P0.1"
    assert "[PASI TASK P0.1]" in progression.current_prompt()
    assert "Create a fresh chat after prior chat usage." in progression.current_prompt()
    assert progression.state.prompt_generation == 1


def test_completion_derives_next_prompt_from_roadmap_exactly_once() -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.1")

    advanced, receipt = progression.complete(
        verified_task_id="P0.1",
        evidence="commit 123; clean worktree; runtime evidence recorded",
    )

    assert receipt.next_task_id == "P0.2"
    assert advanced.state.current_task_id == "P0.2"
    assert advanced.state.advance_count == 1
    assert advanced.state.prompt_generation == 2
    assert "20 uniquely marked consecutive browser operations" in advanced.current_prompt()
    assert "Create a fresh chat after prior chat usage." not in advanced.current_prompt()

    with pytest.raises(TaskProgressionError):
        advanced.complete(
            verified_task_id="P0.1",
            evidence="duplicate completion",
        )
    with pytest.raises(TaskProgressionError):
        progression.complete(
            verified_task_id="P0.1",
            evidence="replayed completion against the same controller",
        )


def test_connection_loss_resume_returns_exact_same_prompt_and_checkpoint() -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.1")
    prompt = progression.current_prompt()

    interrupted = progression.checkpoint(
        "response_generation: authenticated response partially received"
    ).interrupt()

    assert interrupted.state.status == "interrupted"
    assert interrupted.current_prompt() == prompt

    resumed = interrupted.resume()

    assert resumed.state.status == "active"
    assert resumed.current_prompt() == prompt
    assert resumed.state.current_task_id == "P0.1"
    assert resumed.state.advance_count == 0
    assert resumed.state.checkpoint == (
        "response_generation: authenticated response partially received"
    )


def test_persisted_state_round_trip_preserves_prompt_and_exactly_once_guard(tmp_path) -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.1")
    progression = progression.checkpoint("patch_application").interrupt()

    state_path = tmp_path / "progression.json"
    progression.save(state_path)

    restored = TaskPromptProgression.load(catalog=catalog(), path=state_path)

    assert restored.current_prompt() == progression.current_prompt()
    assert restored.state.to_dict() == progression.state.to_dict()

    resumed = restored.resume()
    advanced, receipt = resumed.complete(
        verified_task_id="P0.1",
        evidence="verified completion after reconnect",
    )

    assert receipt.advance_count == 1
    assert advanced.state.current_task_id == "P0.2"

    state_json = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_json["current_task_id"] == "P0.1"
    assert state_json["status"] == "interrupted"


def test_cannot_advance_without_verified_completion_evidence() -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.1")

    with pytest.raises(TaskProgressionError):
        progression.complete(verified_task_id="P0.1", evidence="")

    with pytest.raises(TaskProgressionError):
        progression.complete(verified_task_id="P0.2", evidence="completion")


def test_roadmap_task_ids_are_unique_and_nonempty() -> None:
    tasks = catalog().ordered_tasks()
    ids = [task.task_id for task in tasks]
    assert ids == ["P0.1", "P0.2"]
    assert len(ids) == len(set(ids))


def test_p01_prompt_contains_exact_machine_readable_completion_contract() -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.1")
    prompt = progression.current_prompt()

    assert "PASI_TASK_ID: P0.1" in prompt
    assert "PASI_RESULT_STATUS: complete" in prompt
    assert "PASI_PATCH_START" in prompt
    assert "PASI M0 LIVE PROOF" in prompt
    assert "PASI_M0_NEXT_TASK_ID: P0.2" in prompt
    assert "PASI_M0_NEXT_PROMPT_START" in prompt
    assert "[PASI TASK P0.2]" in prompt
    assert "PASI_M0_NEXT_PROMPT_END" in prompt


def test_p02_prompt_is_canonical_and_not_m0_marker_contract() -> None:
    progression = TaskPromptProgression.start(catalog=catalog(), task_id="P0.2")
    prompt = progression.current_prompt()

    assert prompt.startswith("[PASI TASK P0.2]")
    assert "20 uniquely marked consecutive browser operations" in prompt
    assert "PASI_PATCH_START" in prompt
    assert "PASI_TASK_ID: P0.2" in prompt
    assert "PASI_RESULT_STATUS: complete" in prompt
