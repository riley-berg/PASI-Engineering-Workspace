from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.orchestrator import runner_registry


def setup_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "registry.json"
    monkeypatch.setenv("PASI_RUNNER_REGISTRY_PATH", str(path))
    (tmp_path / "workspace" / "scripts").mkdir(parents=True)
    return path


def make_script(tmp_path: Path, name: str, body: str = "print('runner')\n") -> Path:
    path = tmp_path / "workspace" / "scripts" / name
    path.write_text(body, encoding="utf-8")
    return path


def test_create_runner_creates_one_stable_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    setup_registry(tmp_path, monkeypatch)
    make_script(tmp_path, "runner.py")

    runner = runner_registry.create_runner(
        "test-runner",
        "Test Runner",
        "scripts/runner.py",
        ["--accept"],
        project_root=tmp_path / "workspace",
    )

    assert runner["id"] == "test-runner"
    assert runner["stable"]["version"] == 1
    assert runner["stable"]["state"] == "stable"
    assert runner["candidate"] is None


def test_candidate_revision_is_staged_without_changing_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    setup_registry(tmp_path, monkeypatch)
    make_script(tmp_path, "runner.py")
    make_script(tmp_path, "runner_v2.py", "print('runner v2')\n")

    runner_registry.create_runner(
        "test-runner",
        "Test Runner",
        "scripts/runner.py",
        project_root=tmp_path / "workspace",
    )
    candidate = runner_registry.create_revision(
        "test-runner",
        "scripts/runner_v2.py",
        source="automation",
        project_root=tmp_path / "workspace",
    )

    current = runner_registry.get_runner("test-runner")
    assert candidate["version"] == 2
    assert current["stable"]["version"] == 1
    assert current["candidate"]["version"] == 2
    assert current["candidate"]["state"] == "candidate"


def test_promote_atomically_swaps_candidate_into_stable_and_keeps_previous_for_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    setup_registry(tmp_path, monkeypatch)
    make_script(tmp_path, "runner.py")
    make_script(tmp_path, "runner_v2.py", "print('runner v2')\n")

    runner_registry.create_runner(
        "test-runner",
        "Test Runner",
        "scripts/runner.py",
        project_root=tmp_path / "workspace",
    )
    runner_registry.create_revision(
        "test-runner",
        "scripts/runner_v2.py",
        project_root=tmp_path / "workspace",
    )
    promoted = runner_registry.promote_revision(
        "test-runner",
        2,
        project_root=tmp_path / "workspace",
    )

    assert promoted["stable"]["version"] == 2
    assert promoted["stable"]["state"] == "stable"
    assert promoted["candidate"] is None
    assert promoted["previous_stable"]["version"] == 1


def test_rollback_discards_candidate_before_touching_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    setup_registry(tmp_path, monkeypatch)
    make_script(tmp_path, "runner.py")
    make_script(tmp_path, "runner_v2.py", "print('runner v2')\n")

    runner_registry.create_runner(
        "test-runner",
        "Test Runner",
        "scripts/runner.py",
        project_root=tmp_path / "workspace",
    )
    runner_registry.create_revision(
        "test-runner",
        "scripts/runner_v2.py",
        project_root=tmp_path / "workspace",
    )
    discarded = runner_registry.rollback_runner("test-runner")

    assert discarded["stable"]["version"] == 1
    assert discarded["candidate"] is None

    runner_registry.create_revision(
        "test-runner",
        "scripts/runner_v2.py",
        project_root=tmp_path / "workspace",
    )
    runner_registry.promote_revision(
        "test-runner",
        2,
        project_root=tmp_path / "workspace",
    )
    rolled_back = runner_registry.rollback_runner("test-runner")
    assert rolled_back["stable"]["version"] == 1


def test_registry_rejects_entrypoints_outside_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    setup_registry(tmp_path, monkeypatch)
    make_script(tmp_path, "runner.py")

    with pytest.raises(runner_registry.RunnerRegistryError):
        runner_registry.create_runner(
            "test-runner",
            "Test Runner",
            "../outside.py",
            project_root=tmp_path / "workspace",
        )


def test_registry_is_durable_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = setup_registry(tmp_path, monkeypatch)
    make_script(tmp_path, "runner.py")

    runner_registry.create_runner(
        "test-runner",
        "Test Runner",
        "scripts/runner.py",
        project_root=tmp_path / "workspace",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["runners"]["test-runner"]["stable"]["version"] == 1
