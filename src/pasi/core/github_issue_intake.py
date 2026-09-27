from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pasi.core.roadmap import Roadmap, RoadmapError, RoadmapTask


class IssueIntakeError(RoadmapError):
    """Raised when a GitHub issue cannot safely become roadmap work."""


@dataclass(frozen=True)
class GitHubIssueTaskProposal:
    task: RoadmapTask
    source_issue_number: int
    source_url: str
    source_title: str


class GitHubIssueTaskIntake:
    """Convert structured GitHub issues into bounded roadmap task proposals."""

    ACCEPTANCE_HEADINGS = (
        "acceptance",
        "acceptance criteria",
        "requirements",
        "definition of done",
    )

    def parse(
        self,
        issue: dict[str, Any],
        *,
        phase_id: str,
        existing_task_ids: set[str] | None = None,
    ) -> GitHubIssueTaskProposal:
        number = issue.get("number")
        title = issue.get("title")
        body = issue.get("body") or ""
        if isinstance(body, str):
            body = body.replace("\\n", "\n")
        source_url = issue.get("html_url") or issue.get("url") or ""

        if not isinstance(number, int) or number <= 0:
            raise IssueIntakeError("GitHub issue number is required")
        if not isinstance(title, str) or not title.strip():
            raise IssueIntakeError("GitHub issue title is required")
        if not isinstance(body, str):
            raise IssueIntakeError("GitHub issue body must be text")
        if not isinstance(source_url, str) or not source_url.strip():
            raise IssueIntakeError("GitHub issue source URL is required")

        task_id = f"GH-{number}"
        if existing_task_ids and task_id in existing_task_ids:
            raise IssueIntakeError(f"roadmap task already exists for issue #{number}")

        acceptance = self._extract_acceptance(body)
        if not acceptance:
            raise IssueIntakeError(
                f"issue #{number} has no explicit acceptance requirements"
            )

        evidence = (
            "durable acceptance evidence",
            "functional vertical proof",
            f"source GitHub issue #{number} remains traceable",
        )

        task = RoadmapTask(
            id=task_id,
            title=title.strip(),
            phase_id=phase_id,
            acceptance_requirements=tuple(acceptance),
            evidence_requirements=evidence,
            source_issue_number=number,
            source_url=source_url.strip(),
            source_title=title.strip(),
        )
        return GitHubIssueTaskProposal(
            task=task,
            source_issue_number=number,
            source_url=source_url.strip(),
            source_title=title.strip(),
        )

    def apply(
        self,
        roadmap: Roadmap,
        proposal: GitHubIssueTaskProposal,
    ) -> Roadmap:
        if proposal.task.source_issue_number != proposal.source_issue_number:
            raise IssueIntakeError("proposal source issue metadata mismatch")
        if proposal.task.source_url != proposal.source_url:
            raise IssueIntakeError("proposal source URL metadata mismatch")
        if proposal.task.phase_id not in {phase.id for phase in roadmap.phases}:
            raise IssueIntakeError(
                f"phase {proposal.task.phase_id} does not exist"
            )
        try:
            return roadmap.add_tasks((proposal.task,))
        except RoadmapError as exc:
            raise IssueIntakeError(str(exc)) from exc

    def _extract_acceptance(self, body: str) -> list[str]:
        lines = body.splitlines()
        collected: list[str] = []
        in_acceptance = False

        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("#"):
                heading = re.sub(r"^#+", "", stripped)
                heading = re.sub(r"[^a-z0-9 ]+", " ", heading.casefold()).strip()
                if any(heading == candidate for candidate in self.ACCEPTANCE_HEADINGS):
                    in_acceptance = True
                    continue
                if in_acceptance:
                    break

            if not in_acceptance:
                continue

            match = re.match(r"^[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$", stripped)
            if match:
                value = match.group(1).strip()
                if value and value not in collected:
                    collected.append(value)

        return collected
