import json
from pathlib import Path

import pytest

from pasi.core.github_issue_intake import GitHubIssueTaskIntake, IssueIntakeError
from pasi.core.roadmap import PhaseStatus, Roadmap, RoadmapPhase


def test_github_issue_intake_requires_explicit_acceptance_and_preserves_source():
    intake = GitHubIssueTaskIntake()
    proposal = intake.parse(
        {
            "number": 123,
            "title": "Implement planner",
            "body": """
## Acceptance Criteria
- Planner emits bounded decisions.
- Decisions preserve provenance.
""",
            "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/123",
        },
        phase_id="P2",
    )
    assert proposal.task.id == "GH-123"
    assert proposal.task.acceptance_requirements == (
        "Planner emits bounded decisions.",
        "Decisions preserve provenance.",
    )
    assert proposal.source_issue_number == 123


def test_github_issue_intake_rejects_missing_acceptance_and_duplicates():
    intake = GitHubIssueTaskIntake()
    with pytest.raises(IssueIntakeError):
        intake.parse(
            {
                "number": 124,
                "title": "No acceptance",
                "body": "Just a description.",
                "html_url": "https://github.com/example/issues/124",
            },
            phase_id="P2",
        )

    with pytest.raises(IssueIntakeError):
        intake.parse(
            {
                "number": 123,
                "title": "Duplicate",
                "body": "## Acceptance\\n- done",
                "html_url": "https://github.com/example/issues/123",
            },
            phase_id="P2",
            existing_task_ids={"GH-123"},
        )


def test_github_issue_intake_applies_as_real_roadmap_task():
    intake = GitHubIssueTaskIntake()
    proposal = intake.parse(
        {
            "number": 125,
            "title": "Task",
            "body": "## Requirements
- functional proof",
            "html_url": "https://github.com/example/issues/125",
        },
        phase_id="P2",
    )
    roadmap = Roadmap(
        roadmap_id="pasi-main",
        version=3,
        revision=0,
        phases=(RoadmapPhase("P2", "Planner", status=PhaseStatus.ACTIVE),),
        tasks=(),
    )
    updated = intake.apply(roadmap, proposal)
    assert updated.task("GH-125").title == "Task"
    assert updated.task("GH-125").evidence_requirements


def test_issue_intake_schema_is_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "github-issue-task-proposal-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["version"]["const"] == 1
