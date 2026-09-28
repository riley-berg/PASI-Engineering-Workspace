from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_branch_hygiene_is_hosted_and_non_overlapping() -> None:
    source = (ROOT / ".github" / "workflows" / "branch-hygiene.yml").read_text(encoding="utf-8")
    assert "runs-on: ubuntu-latest" in source
    assert "cancel-in-progress: false" in source
    assert "scripts/cleanup_duplicate_branches.py --json" in source
    assert "self-hosted" not in source


def test_security_workflow_is_fork_safe_and_hosted() -> None:
    source = (ROOT / ".github" / "workflows" / "pasi-security-analysis.yml").read_text(encoding="utf-8")
    assert "runs-on: ubuntu-latest" in source
    assert "github.event.pull_request.head.repo.full_name == github.repository" in source
    assert "cancel-in-progress: true" in source
    assert "scripts/scan_repository_secrets.py" in source


def test_project_workflow_is_manual_and_uses_the_graphql_wrapper() -> None:
    source = (ROOT / ".github" / "workflows" / "github-project-v2.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in source
    assert "scripts/github_project_v2.py" in source
    assert "PASI_PROJECTS_TOKEN" in source
    assert "runs-on: ubuntu-latest" in source
    assert "self-hosted" not in source
