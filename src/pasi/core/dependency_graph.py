from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pasi.core.roadmap import Roadmap, TaskStatus


class StaleDependencyGraph(ValueError):
    """Raised when a graph is used against a different roadmap revision."""


@dataclass(frozen=True)
class DependencyGraph:
    roadmap_id: str
    roadmap_revision: int
    prerequisites: dict[str, tuple[str, ...]]
    dependents: dict[str, tuple[str, ...]]
    eligible_task_ids: tuple[str, ...]
    blocked_reasons: dict[str, str]
    canonical_sha256: str

    @classmethod
    def build(cls, roadmap: Roadmap) -> "DependencyGraph":
        prerequisites = {
            task.id: tuple(sorted(task.depends_on))
            for task in sorted(roadmap.tasks, key=lambda item: item.id)
        }
        reverse: dict[str, list[str]] = {task.id: [] for task in roadmap.tasks}
        for task in roadmap.tasks:
            for dependency in task.depends_on:
                reverse.setdefault(dependency, []).append(task.id)
        dependents = {
            key: tuple(sorted(value))
            for key, value in sorted(reverse.items())
        }

        completed = {
            task.id for task in roadmap.tasks if task.status is TaskStatus.COMPLETED
        }
        active_phases = {
            phase.id for phase in roadmap.phases if phase.status.value == "active"
        }
        eligible: list[str] = []
        blocked: dict[str, str] = {}
        for task in roadmap.tasks:
            if task.status is not TaskStatus.PLANNED:
                continue
            if task.phase_id not in active_phases:
                blocked[task.id] = "phase_not_active"
                continue
            missing = sorted(set(task.depends_on) - completed)
            if missing:
                blocked[task.id] = "waiting_on:" + ",".join(missing)
                continue
            eligible.append(task.id)

        payload: dict[str, Any] = {
            "roadmap_id": roadmap.roadmap_id,
            "roadmap_revision": roadmap.revision,
            "prerequisites": prerequisites,
            "dependents": dependents,
            "eligible_task_ids": tuple(sorted(eligible)),
            "blocked_reasons": dict(sorted(blocked.items())),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            roadmap_id=roadmap.roadmap_id,
            roadmap_revision=roadmap.revision,
            prerequisites=prerequisites,
            dependents=dependents,
            eligible_task_ids=tuple(sorted(eligible)),
            blocked_reasons=dict(sorted(blocked.items())),
            canonical_sha256=digest,
        )

    def assert_fresh(self, roadmap: Roadmap) -> None:
        if (
            roadmap.roadmap_id != self.roadmap_id
            or roadmap.revision != self.roadmap_revision
        ):
            raise StaleDependencyGraph(
                "dependency graph was built against a different roadmap revision"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "roadmap_id": self.roadmap_id,
            "roadmap_revision": self.roadmap_revision,
            "prerequisites": {
                key: list(value) for key, value in self.prerequisites.items()
            },
            "dependents": {
                key: list(value) for key, value in self.dependents.items()
            },
            "eligible_task_ids": list(self.eligible_task_ids),
            "blocked_reasons": self.blocked_reasons,
            "canonical_sha256": self.canonical_sha256,
        }
