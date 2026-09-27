from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from pasi.core.roadmap import Roadmap, RoadmapTask


class SelectionError(ValueError):
    """Raised when evidence-aware selection inputs are invalid."""


class StaleSelection(SelectionError):
    """Raised when a selection no longer matches the roadmap revision."""


@dataclass(frozen=True)
class EvidenceSnapshot:
    task_id: str
    roadmap_revision: int
    status: str
    refs: tuple[str, ...] = ()
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if self.roadmap_revision < 0:
            raise SelectionError("evidence roadmap_revision must be non-negative")
        if self.status not in {"verified", "missing", "stale", "contradictory", "failed"}:
            raise SelectionError(f"unsupported evidence status: {self.status!r}")

    @property
    def is_current(self) -> bool:
        return self.status == "verified" and bool(self.refs)


@dataclass(frozen=True)
class SelectionDecision:
    roadmap_id: str
    roadmap_revision: int
    selected_task_id: str | None
    eligible_task_ids: tuple[str, ...]
    excluded_reasons: dict[str, str]
    advisory_scores: dict[str, float]
    selected_at: str

    def assert_fresh(self, roadmap: Roadmap) -> None:
        if (
            roadmap.roadmap_id != self.roadmap_id
            or roadmap.revision != self.roadmap_revision
        ):
            raise StaleSelection(
                "selection was computed against a different roadmap revision"
            )


class EvidenceAwareTaskSelector:
    """Select only authoritative-eligible tasks; advisory signals only rank survivors."""

    def select(
        self,
        roadmap: Roadmap,
        *,
        evidence: Mapping[str, EvidenceSnapshot],
        advisory_scores: Mapping[str, float] | None = None,
    ) -> SelectionDecision:
        scores = dict(advisory_scores or {})
        excluded: dict[str, str] = {}

        ready_ids = {task.id for task in roadmap.ready_tasks()}
        for task in roadmap.tasks:
            if task.id not in ready_ids:
                continue

            if task.depends_on:
                for dependency_id in task.depends_on:
                    snapshot = evidence.get(dependency_id)
                    if snapshot is None:
                        excluded[task.id] = (
                            f"missing evidence for dependency {dependency_id}"
                        )
                        break
                    if snapshot.task_id != dependency_id:
                        excluded[task.id] = "evidence task identity mismatch"
                        break
                    if snapshot.roadmap_revision != roadmap.revision:
                        excluded[task.id] = (
                            f"stale evidence for dependency {dependency_id}"
                        )
                        break
                    if not snapshot.is_current:
                        excluded[task.id] = (
                            f"non-verified evidence for dependency {dependency_id}"
                        )
                        break

            if task.id in excluded:
                continue

            score = scores.get(task.id, 0.0)
            if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
                excluded[task.id] = "invalid advisory score"
                continue

        eligible = tuple(
            sorted(task.id for task in roadmap.ready_tasks() if task.id not in excluded)
        )
        ranked = sorted(
            eligible,
            key=lambda task_id: (-float(scores.get(task_id, 0.0)), task_id),
        )
        selected = ranked[0] if ranked else None

        return SelectionDecision(
            roadmap_id=roadmap.roadmap_id,
            roadmap_revision=roadmap.revision,
            selected_task_id=selected,
            eligible_task_ids=eligible,
            excluded_reasons=dict(sorted(excluded.items())),
            advisory_scores={
                task_id: float(scores[task_id])
                for task_id in sorted(scores)
                if isinstance(scores[task_id], (int, float))
                and math.isfinite(float(scores[task_id]))
            },
            selected_at=datetime.now(timezone.utc).isoformat(),
        )
