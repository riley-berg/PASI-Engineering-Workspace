import subprocess

from scripts.pasi_engineering_executor import (
    extract_canonical_task_context,
    quarantine_failed_candidate,
)


def test_task_context_excludes_completed_sibling_tasks():
    body = """# P0 — Runtime proof & delivery infrastructure
Dependency: Immediate priority; all later phases depend on this evidence.

## Tasks

- [x] **P0.1 — M0 live task acceptance** — old completed task
- [ ] **P0.4 — 168-hour long-run acceptance** — current task description
- [ ] **P0.6 — CI/workflow consolidation** — another pending sibling
- [x] **P0.5 — Acceptance evidence registry** — completed sibling

## Completion rule
Each task requires implementation evidence appropriate to its scope.
"""
    context = extract_canonical_task_context(body, "P0.4")

    assert "CANONICAL TASK P0.4" in context
    assert "current task description" in context
    assert "P0.1" not in context
    assert "P0.5" not in context
    assert "P0.6" not in context
    assert "COMPLETION RULE:" in context


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_failed_candidate_is_quarantined_and_known_good_restored(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "PASI Test")
    _git(repo, "config", "user.email", "pasi-test@example.invalid")
    (repo / "program.txt").write_text("known-good\n", encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-q", "-m", "baseline")
    baseline = _git(repo, "rev-parse", "HEAD")

    (repo / "program.txt").write_text("failed-candidate\n", encoding="utf-8")
    (repo / "new.txt").write_text("candidate-only\n", encoding="utf-8")
    monkeypatch.setenv("PASI_FAILED_CANDIDATE_DIR", str(tmp_path / "failures"))

    result = quarantine_failed_candidate(
        repo,
        baseline,
        "P0.1",
        "diff --git a/program.txt b/program.txt\n",
        pytest_output="FAILED example",
        failure_reason="pytest failed",
    )

    assert result["restored_head"] == baseline
    assert result["restored_clean"] == "True"
    assert result["quarantine_commit"]
    assert (tmp_path / "failures").glob("p0.1-*")

    _git(repo, "show", f'{result["quarantine_commit"]}:program.txt') == "failed-candidate\n"
    candidate_new = subprocess.run(
        ["git", "show", f'{result["quarantine_commit"]}:new.txt'],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    assert candidate_new == "candidate-only\n"
