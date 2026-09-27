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
    assert proposal.task.source_issue_number == 123
    assert proposal.task.source_url.endswith("/issues/123")


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
            "body": "\\n".join(["## Requirements", "- functional proof"]),
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
    assert updated.task("GH-125").source_issue_number == 125


def test_issue_intake_schema_is_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "github-issue-task-proposal-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["version"]["const"] == 1

def test_issue_intake_parses_checkbox_and_heading_variants():
    proposal = GitHubIssueTaskIntake().parse(
        {
            "number": 126,
            "title": "Checklist task",
            "body": "## Definition of Done\n- [x] Works end to end\n- [ ] Has durable evidence\n## Notes\n- ignored",
            "html_url": "https://github.com/example/issues/126",
        },
        phase_id="P2",
    )
    assert proposal.task.acceptance_requirements == (
        "Works end to end",
        "Has durable evidence",
    )


def test_github_issue_source_metadata_survives_roadmap_round_trip():
    intake = GitHubIssueTaskIntake()
    proposal = intake.parse(
        {
            "number": 127,
            "title": "Round-trip task",
            "body": "## Acceptance Criteria\n- Reload preserves source identity",
            "html_url": "https://github.com/example/issues/127",
        },
        phase_id="P2",
    )
    roadmap = Roadmap(
        roadmap_id="pasi-main",
        version=4,
        revision=0,
        phases=(RoadmapPhase("P2", "Planner", status=PhaseStatus.ACTIVE),),
        tasks=(),
    )
    updated = intake.apply(roadmap, proposal)
    reloaded = Roadmap.from_mapping(updated.to_dict())
    task = reloaded.task("GH-127")
    assert task.source_issue_number == 127
    assert task.source_url == "https://github.com/example/issues/127"
    assert task.source_title == "Round-trip task"
