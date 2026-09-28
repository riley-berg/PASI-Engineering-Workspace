import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import pasi_168h_acceptance as acceptance
from scripts import pasi_acceptance_evidence_registry as registry


def _controller_tree(root: Path) -> Path:
    extension_root = root / "extensions" / "pasi-chatgpt"
    source = extension_root / "src"
    source.mkdir(parents=True)
    (source / "content.js").write_text(
        "const CONTROLLER_VERSION = '2.4.11';\n"
        "const PASI_DEPLOYMENT_ID = 'pasi-engineering-workspace-handoff-v1';\n",
        encoding="utf-8",
    )
    return extension_root


def test_registry_binds_exact_provenance_and_integrity(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    extension_root = _controller_tree(repo)
    state.mkdir()
    (state / "events.jsonl").write_text('{"event":"run_started"}\n', encoding="utf-8")
    proof = repo / "acceptance" / "proof.txt"
    proof.parent.mkdir(parents=True)
    proof.write_text("PASI evidence\n", encoding="utf-8")

    monkeypatch.setenv("PASI_ACCEPTANCE_STATE_DIR", str(state))
    monkeypatch.setenv("PASI_RUNNER_ID", "runner-test")
    monkeypatch.setenv("PASI_PROVIDER", "chatgpt")
    monkeypatch.setenv("CI", "1")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    started = datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 9, 28, 5, 1, tzinfo=timezone.utc)
    manifest_ref = registry.register_acceptance_artifact(
        repo_root=repo,
        extension_root=extension_root,
        run_id="ew-test-run",
        task_id="P0.5",
        phase="P0",
        branch="pasi/p0-5-test",
        artifact_kind="task-completion",
        artifact_refs=[state / "events.jsonl", proof],
        code_head="deadbeef",
        commit_sha="deadbeef",
        started_at=started,
        completed_at=completed,
        state_root=state,
    )

    registry_file = state / "acceptance-evidence-registry.jsonl"
    record = json.loads(registry_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["code_head"] == "deadbeef"
    assert record["controller_version"] == "2.4.11"
    assert record["observed_controller_version"] is None
    assert record["deployment_id"] == "pasi-engineering-workspace-handoff-v1"
    assert record["provider"] == "chatgpt"
    assert record["runner"] == {"id": "runner-test", "kind": "local"}
    assert record["run_id"] == "ew-test-run"
    assert record["task_id"] == "P0.5"
    assert record["phase"] == "P0"
    assert record["branch"] == "pasi/p0-5-test"
    assert record["timestamps"]["started_at"] == started.isoformat()
    assert record["timestamps"]["completed_at"] == completed.isoformat()
    assert manifest_ref == record["artifact_ref"]

    manifest_path = state / record["artifact_ref"].removeprefix("state/")
    manifest_bytes = manifest_path.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == record["artifact_digest"]
    manifest = json.loads(manifest_bytes)
    refs = {item["ref"] for item in manifest["artifacts"]}
    assert "state/events.jsonl" in refs
    assert "worktree/acceptance/proof.txt" in refs
    assert "git:deadbeef" in refs
    assert record["record_digest_algorithm"] == "sha256"
    assert record["previous_record_digest"] is None

    registry.register_acceptance_artifact(
        repo_root=repo,
        extension_root=extension_root,
        run_id="ew-test-run-2",
        task_id="P0.5",
        phase="P0",
        branch="pasi/p0-5-test",
        artifact_kind="task-completion",
        code_head="cafebabe",
        state_root=state,
    )
    records = [
        json.loads(line)
        for line in registry_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 2
    assert records[1]["previous_record_digest"] == records[0]["record_digest"]


def test_registry_binds_observed_controller_version(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    extension_root = _controller_tree(repo)
    runtime = state / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "last-preflight.txt").write_text(
        json.dumps({"controller_version": "2.4.11"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PASI_ACCEPTANCE_STATE_DIR", str(state))
    registry.register_acceptance_artifact(
        repo_root=repo,
        extension_root=extension_root,
        run_id="run-1",
        task_id=None,
        phase="P0",
        branch="main",
        artifact_kind="run-start",
        code_head="deadbeef",
        state_root=state,
    )

    record = json.loads((state / "acceptance-evidence-registry.jsonl").read_text().splitlines()[0])
    assert record["observed_controller_version"] == "2.4.11"


def test_registry_rejects_controller_version_mismatch(tmp_path):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    extension_root = _controller_tree(repo)
    runtime = state / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "last-preflight.txt").write_text(
        json.dumps({"controller_version": "2.4.10"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="controller version provenance mismatch"):
        registry.register_acceptance_artifact(
            repo_root=repo,
            extension_root=extension_root,
            run_id="run-1",
            task_id=None,
            phase="P0",
            branch="main",
            artifact_kind="run-start",
            code_head="deadbeef",
            state_root=state,
        )


def test_registry_rejects_artifacts_outside_trusted_roots(tmp_path):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    extension_root = _controller_tree(repo)
    state.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inside the repository"):
        registry.register_acceptance_artifact(
            repo_root=repo,
            extension_root=extension_root,
            run_id="run-1",
            task_id=None,
            phase="P0",
            branch="main",
            artifact_kind="run-start",
            artifact_refs=[outside],
            state_root=state,
        )


def test_runner_registers_task_manifest_with_commit_and_changed_files(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    state = tmp_path / "state"
    extension_root = _controller_tree(worktree)
    state.mkdir()
    runtime = state / "runtime"
    runtime.mkdir()
    (runtime / "last-preflight.txt").write_text(
        json.dumps({"controller_version": "2.4.11"}) + "\n",
        encoding="utf-8",
    )
    (runtime / "last-executor-output.txt").write_text("executor ok\n", encoding="utf-8")
    (state / "events.jsonl").write_text('{"event":"task_started"}\n', encoding="utf-8")
    changed = worktree / "acceptance" / "proof.txt"
    changed.parent.mkdir(parents=True)
    changed.write_text("proof\n", encoding="utf-8")

    monkeypatch.setenv("PASI_ACCEPTANCE_STATE_DIR", str(state))
    monkeypatch.setenv("PASI_ENGINEERING_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("PASI_PUSH", raising=False)
    monkeypatch.setattr(acceptance, "state_dir", lambda: state)
    monkeypatch.setattr(acceptance, "mark_checked", lambda task: None)
    monkeypatch.setattr(acceptance, "git", lambda cwd, *args, **kwargs: (
        "before" if args[:2] == ("rev-parse", "HEAD") else
        "acceptance/proof.txt\n" if args[:2] == ("diff", "--name-only") else
        "" if args[:2] == ("status", "--porcelain") else
        (_ for _ in ()).throw(AssertionError(args))
    ))

    monkeypatch.setattr(
        acceptance.subprocess,
        "run",
        lambda *args, **kwargs: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    before_sha = "a" * 40
    after_sha = "b" * 40
    heads = iter((before_sha, after_sha))
    def fake_git(cwd, *args, **kwargs):
        if args[:2] == ("rev-parse", "HEAD"):
            return next(heads)
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args[:2] == ("diff", "--name-only"):
            return "acceptance/proof.txt\n"
        raise AssertionError(args)
    monkeypatch.setattr(acceptance, "git", fake_git)

    phase = acceptance.Phase("P0", 108, "Q3-2026", "2026-09-22", "2026-10-04")
    task = acceptance.Task(phase, "P0.5", "Acceptance evidence registry", False)
    evidence = acceptance.run_task(task, worktree, "pasi/p0-5-test", "ew-test-run")
    record = json.loads((state / "acceptance-evidence-registry.jsonl").read_text().splitlines()[0])
    assert evidence["commit_after"] == after_sha
    assert evidence["evidence_artifact"].startswith("state/evidence/")
    assert record["code_head"] == after_sha
    manifest = json.loads((state / record["artifact_ref"].removeprefix("state/")).read_text())
    refs = {item["ref"] for item in manifest["artifacts"]}
    assert f"git:{after_sha}" in refs
    assert "worktree/acceptance/proof.txt" in refs
    assert "runtime/last-preflight.txt" in refs
    assert "runtime/last-executor-output.txt" in refs
