from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .task_progression import RoadmapTask, RoadmapTaskCatalog, TaskProgressionError


GITHUB_REPOSITORY = "th3-st0v3/PASI-Engineering-Workspace"
GITHUB_ISSUES_URL = (
    "https://api.github.com/repos/"
    f"{GITHUB_REPOSITORY}/issues?state=all&per_page=100&page=1"
)
PHASE_ORDER = tuple(range(23))
ISSUE_FETCH_TIMEOUT = 20.0

_BACKEND_TITLE = re.compile(r"^P(?P<phase>\d+)\s+—\s+")
_FRONTEND_TITLE = re.compile(r"^FE-P(?P<phase>\d+)\s+—\s+")
_CHECKBOX = re.compile(r"^\s*-\s*\[(?P<checked>[ xX])\]\s+(?P<text>.+?)\s*$")
_EXPLICIT_ID = re.compile(r"^(?P<id>P\d+\.[A-Za-z0-9_-]+)\s*[—:-]\s*(?P<title>.+)$")
_BOLD_EXPLICIT_ID = re.compile(
    r"^\*\*(?P<id>P\d+\.[A-Za-z0-9_-]+)\s*[—:-]\s*(?P<title>[^*]+)\*\*\s*(?:—\s*)?(?P<rest>.*)$"
)


class GitHubIssueTaskError(TaskProgressionError):
    """Raised when the GitHub issue task source cannot be interpreted safely."""


@dataclass(frozen=True)
class IssueTaskSource:
    issue_number: int
    issue_title: str
    issue_url: str
    phase: int
    frontend: bool
    requirement: str


def _fetch_issues() -> list[dict[str, Any]]:
    request = urllib.request.Request(
        GITHUB_ISSUES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "pasi-engineering-workspace-live-runner",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=ISSUE_FETCH_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubIssueTaskError(f"unable to load GitHub issue plan: {exc}") from exc

    if not isinstance(payload, list):
        raise GitHubIssueTaskError("GitHub issue response must be a list")

    return [
        item
        for item in payload
        if isinstance(item, dict) and "pull_request" not in item
    ]


def _task_parts(text: str, *, phase: int, frontend: bool, ordinal: int) -> tuple[str, str]:
    candidate = text.strip()
    match = _BOLD_EXPLICIT_ID.match(candidate)
    if match:
        task_id = match.group("id")
        title = match.group("title").strip()
        rest = match.group("rest").strip(" —:-")
        return (
            f"FE-{task_id}" if frontend and not task_id.startswith("FE-") else task_id,
            f"{title}{f' — {rest}' if rest else ''}",
        )

    match = _EXPLICIT_ID.match(candidate)
    if match:
        task_id = match.group("id")
        return (
            f"FE-{task_id}" if frontend and not task_id.startswith("FE-") else task_id,
            match.group("title").strip(),
        )

    prefix = "FE-" if frontend else ""
    return f"{prefix}P{phase}.{ordinal}", candidate


def _issue_tasks(issue: Mapping[str, Any]) -> tuple[tuple[RoadmapTask, IssueTaskSource], ...]:
    title = str(issue.get("title", "")).strip()
    body = str(issue.get("body", "") or "")
    issue_number = int(issue.get("number", 0))
    issue_url = str(issue.get("html_url", "")).strip()

    backend = _BACKEND_TITLE.match(title)
    frontend = _FRONTEND_TITLE.match(title)
    match = backend or frontend
    if match is None:
        return ()

    phase = int(match.group("phase"))
    is_frontend = frontend is not None
    if phase not in PHASE_ORDER:
        return ()

    tasks: list[tuple[RoadmapTask, IssueTaskSource]] = []
    ordinal = 0
    for line in body.splitlines():
        checkbox = _CHECKBOX.match(line)
        if checkbox is None:
            continue
        task_text = checkbox.group("text").strip()
        if not task_text:
            continue
        ordinal += 1
        task_id, task_title = _task_parts(
            task_text,
            phase=phase,
            frontend=is_frontend,
            ordinal=ordinal,
        )
        requirement = (
            f"Implement this issue task exactly as specified in GitHub issue #{issue_number}: "
            f"{task_title}.\n"
            f"Source issue: #{issue_number} — {title} ({issue_url}).\n"
            f"Task checklist state when the plan was loaded: "
            f"{'completed' if checkbox.group('checked').lower() == 'x' else 'incomplete'}."
        )
        task = RoadmapTask(
            task_id=task_id,
            title=task_title,
            status="completed" if checkbox.group("checked").lower() == "x" else "planned",
            requirements=(requirement,),
        )
        source = IssueTaskSource(
            issue_number=issue_number,
            issue_title=title,
            issue_url=issue_url,
            phase=phase,
            frontend=is_frontend,
            requirement=task_title,
        )
        tasks.append((task, source))

    return tuple(tasks)


class GitHubIssueTaskCatalog(RoadmapTaskCatalog):
    """Canonical task catalog derived from the PASI Engineering Workspace issues."""

    def __init__(self, tasks: Mapping[str, RoadmapTask], sources: Mapping[str, IssueTaskSource]) -> None:
        super().__init__(tasks)
        self._sources = dict(sources)

    @classmethod
    def from_issues(cls, issues: list[Mapping[str, Any]]) -> "GitHubIssueTaskCatalog":
        task_entries: list[tuple[int, int, int, RoadmapTask, IssueTaskSource]] = []
        backend_rank = 0
        frontend_rank = 1

        for issue in issues:
            parsed = _issue_tasks(issue)
            for ordinal, (task, source) in enumerate(parsed, start=1):
                task_entries.append(
                    (
                        source.phase,
                        frontend_rank if source.frontend else backend_rank,
                        ordinal,
                        task,
                        source,
                    )
                )

        if not task_entries:
            raise GitHubIssueTaskError("no phase tasks were found in GitHub issues")

        task_entries.sort(key=lambda item: (item[0], item[1], item[2]))
        tasks: dict[str, RoadmapTask] = {}
        sources: dict[str, IssueTaskSource] = {}
        for _, _, _, task, source in task_entries:
            if task.task_id in tasks:
                raise GitHubIssueTaskError(f"duplicate GitHub task id: {task.task_id}")
            tasks[task.task_id] = task
            sources[task.task_id] = source

        return cls(tasks, sources)

    @classmethod
    def from_github(cls) -> "GitHubIssueTaskCatalog":
        return cls.from_issues(_fetch_issues())

    def source(self, task_id: str) -> IssueTaskSource:
        try:
            return self._sources[task_id]
        except KeyError as exc:
            raise GitHubIssueTaskError(f"unknown GitHub task: {task_id}") from exc


def load_task_catalog(fallback_roadmap: Any) -> RoadmapTaskCatalog:
    """Load the live GitHub issue plan, falling back to the repository roadmap when offline."""
    try:
        return GitHubIssueTaskCatalog.from_github()
    except GitHubIssueTaskError:
        if fallback_roadmap is None:
            raise
        return RoadmapTaskCatalog.from_roadmap_file(fallback_roadmap)


__all__ = [
    "GITHUB_REPOSITORY",
    "GitHubIssueTaskCatalog",
    "GitHubIssueTaskError",
    "IssueTaskSource",
    "load_task_catalog",
]
