from pathlib import Path

from scripts.verify_workflow_consolidation import verify


def test_repository_workflow_layout_matches_durable_policy():
    root = Path(__file__).resolve().parents[1]
    result = verify(root)
    assert result["valid"], result["errors"]
    assert result["workflow_count"] == 4
    assert result["roles"] == {
        "branch hygiene": "branch-hygiene.yml",
        "development control": "github-project-v2.yml",
        "security": "pasi-security-analysis.yml",
        "test": "test.yml",
    }


def test_unlisted_workflow_is_rejected(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "workflow-policy.json").write_text(
        """{
  "schema_version": 1,
  "allow_unlisted_workflows": false,
  "workflows": {
    "test.yml": {"name": "test", "role": "test"}
  }
}
""",
        encoding="utf-8",
    )
    (workflows / "test.yml").write_text(
        "name: test\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    (workflows / "one-off.yml").write_text(
        "name: one-off\njobs:\n  run:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )

    result = verify(tmp_path)

    assert result["valid"] is False
    assert any("unlisted workflow(s) present" in error for error in result["errors"])
