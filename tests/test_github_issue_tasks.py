from __future__ import annotations

from pasi.core.github_issue_tasks import GitHubIssueTaskCatalog


def test_issue_catalog_orders_backend_then_frontend_by_phase() -> None:
    catalog = GitHubIssueTaskCatalog.from_issues(
        [
            {
                "number": 30,
                "title": "FE-P0 — Runtime proof UI",
                "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/30",
                "body": "- [ ] P0.1 — UI task\n- [ ] P0.2 — UI task 2",
            },
            {
                "number": 3,
                "title": "P1 — Control-plane consolidation",
                "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/3",
                "body": "- [ ] P1.1 — State\n- [ ] P1.2 — Events",
            },
            {
                "number": 2,
                "title": "P0 — Runtime proof and delivery infrastructure",
                "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/2",
                "body": "- [ ] M0 — Live proof\n- [ ] M1 — Chain",
            },
        ]
    )

    assert [task.task_id for task in catalog.ordered_tasks()] == [
        "P0.1",
        "P0.2",
        "FE-P0.1",
        "FE-P0.2",
        "P1.1",
        "P1.2",
    ]


def test_issue_catalog_preserves_explicit_task_ids_and_checked_state() -> None:
    catalog = GitHubIssueTaskCatalog.from_issues(
        [
            {
                "number": 54,
                "title": "P5 — Security productionization",
                "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/54",
                "body": "- [x] **P5.1 — Persistent sessions** — Replace process-local sessions.",
            }
        ]
    )

    task = catalog.get("P5.1")
    assert task.status == "completed"
    assert "issue #54" in task.requirements[0]


def test_checked_tasks_are_skipped_when_progression_finds_the_next_task() -> None:
    catalog = GitHubIssueTaskCatalog.from_issues(
        [
            {
                "number": 2,
                "title": "P0 — Runtime proof and delivery infrastructure",
                "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/2",
                "body": "- [x] M0 — Complete\n- [ ] M1 — Continue",
            }
        ]
    )

    assert catalog.next_task("P0.1").task_id == "P0.2"
