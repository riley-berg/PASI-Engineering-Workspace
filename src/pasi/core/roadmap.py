from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


ROADMAP_SCHEMA_VERSION = 3
SUPPORTED_ROADMAP_SCHEMA_VERSIONS = frozenset({2, 3})


class RoadmapError(ValueError):
    """Raised when roadmap structure or lifecycle rules are invalid."""


class LifecycleTransitionError(RoadmapError):
    """Raised when a roadmap object cannot take the requested lifecycle transition."""


class StaleRoadmapRevision(RoadmapError):
    """Raised when a roadmap mutation targets a stale revision."""


class PhaseStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ARCHIVED = "archived"


_PHASE_TRANSITIONS = {
    PhaseStatus.PLANNED: frozenset({PhaseStatus.ACTIVE, PhaseStatus.ARCHIVED}),
    PhaseStatus.ACTIVE: frozenset({PhaseStatus.PAUSED, PhaseStatus.COMPLETED}),
    PhaseStatus.PAUSED: frozenset({PhaseStatus.ACTIVE, PhaseStatus.ARCHIVED}),
    PhaseStatus.COMPLETED: frozenset({PhaseStatus.ARCHIVED}),
    PhaseStatus.ARCHIVED: frozenset(),
}

_TASK_TRANSITIONS = {
    TaskStatus.PLANNED: frozenset({TaskStatus.ACTIVE, TaskStatus.BLOCKED, TaskStatus.ARCHIVED}),
    TaskStatus.ACTIVE: frozenset({TaskStatus.COMPLETED, TaskStatus.BLOCKED, TaskStatus.ARCHIVED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.PLANNED, TaskStatus.ARCHIVED}),
    TaskStatus.COMPLETED: frozenset({TaskStatus.ARCHIVED}),
    TaskStatus.ARCHIVED: frozenset(),
}


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _nonempty(value: object, *, name: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoadmapError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise RoadmapError(f"{name} exceeds {limit} characters")
    return normalized


def _tuple_strings(value: object, *, name: str, item_limit: int = 256) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RoadmapError(f"{name} must be an array")
    normalized = tuple(_nonempty(item, name=name, limit=item_limit) for item in value)
    if len(set(normalized)) != len(normalized):
        raise RoadmapError(f"{name} contains duplicates")
    return normalized


@dataclass(frozen=True)
class RoadmapTask:
    id: str
    title: str
    phase_id: str
    depends_on: tuple[str, ...] = ()
    acceptance_requirements: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    parent_task_id: str = ""
    source_issue_number: int | None = None
    source_url: str = ""
    source_title: str = ""
    status: TaskStatus = TaskStatus.PLANNED
    revision: int = 0

    def __post_init__(self) -> None:
        _nonempty(self.id, name="task id")
        _nonempty(self.title, name="task title", limit=512)
        _nonempty(self.phase_id, name="phase id")
        if self.revision < 0:
            raise RoadmapError("task revision must be non-negative")
        if not self.acceptance_requirements:
            raise RoadmapError(f"task {self.id} requires acceptance_requirements")
        if not self.evidence_requirements:
            raise RoadmapError(f"task {self.id} requires evidence_requirements")
        if self.parent_task_id and self.parent_task_id == self.id:
            raise RoadmapError("task cannot be its own parent")
        if self.source_issue_number is not None:
            if not isinstance(self.source_issue_number, int) or self.source_issue_number <= 0:
                raise RoadmapError("source_issue_number must be a positive integer or null")
            if not self.source_url.strip() or not self.source_title.strip():
                raise RoadmapError(
                    f"task {self.id} source metadata requires source_url and source_title"
                )
        if self.source_url and not self.source_title:
            raise RoadmapError("source_title is required when source_url is present")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "phase_id": self.phase_id,
            "depends_on": list(self.depends_on),
            "acceptance_requirements": list(self.acceptance_requirements),
            "evidence_requirements": list(self.evidence_requirements),
            "parent_task_id": self.parent_task_id,
            "source_issue_number": self.source_issue_number,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "status": self.status.value,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RoadmapTask":
        return cls(
            id=_nonempty(value.get("id"), name="task id"),
            title=_nonempty(value.get("title"), name="task title", limit=512),
            phase_id=_nonempty(value.get("phase_id"), name="phase id"),
            depends_on=_tuple_strings(value.get("depends_on", []), name="depends_on"),
            acceptance_requirements=_tuple_strings(
                value.get("acceptance_requirements", []),
                name="acceptance_requirements",
                item_limit=1024,
            ),
            evidence_requirements=_tuple_strings(
                value.get("evidence_requirements", []),
                name="evidence_requirements",
                item_limit=1024,
            ),
            parent_task_id=str(value.get("parent_task_id", "")),
            source_issue_number=(
                int(value["source_issue_number"])
                if value.get("source_issue_number") is not None
                else None
            ),
            source_url=str(value.get("source_url", "")),
            source_title=str(value.get("source_title", "")),
            status=TaskStatus(value.get("status", TaskStatus.PLANNED.value)),
            revision=int(value.get("revision", 0)),
        )


@dataclass(frozen=True)
class RoadmapPhase:
    id: str
    title: str
    depends_on: tuple[str, ...] = ()
    status: PhaseStatus = PhaseStatus.PLANNED
    revision: int = 0

    def __post_init__(self) -> None:
        _nonempty(self.id, name="phase id")
        _nonempty(self.title, name="phase title", limit=512)
        if self.revision < 0:
            raise RoadmapError("phase revision must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "revision": self.revision,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RoadmapPhase":
        return cls(
            id=_nonempty(value.get("id"), name="phase id"),
            title=_nonempty(value.get("title"), name="phase title", limit=512),
            depends_on=_tuple_strings(value.get("depends_on", []), name="phase depends_on"),
            status=PhaseStatus(value.get("status", PhaseStatus.PLANNED.value)),
            revision=int(value.get("revision", 0)),
        )


@dataclass(frozen=True)
class Roadmap:
    roadmap_id: str
    version: int
    revision: int
    phases: tuple[RoadmapPhase, ...]
    tasks: tuple[RoadmapTask, ...]
    canonical_sha256: str = ""

    def __post_init__(self) -> None:
        _nonempty(self.roadmap_id, name="roadmap id")
        if self.version not in SUPPORTED_ROADMAP_SCHEMA_VERSIONS:
            raise RoadmapError(f"unsupported roadmap schema version: {self.version}")
        if self.version == 2:
            object.__setattr__(self, "version", ROADMAP_SCHEMA_VERSION)
        if self.revision < 0:
            raise RoadmapError("roadmap revision must be non-negative")

        phase_ids = [phase.id for phase in self.phases]
        task_ids = [task.id for task in self.tasks]
        if len(set(phase_ids)) != len(phase_ids):
            raise RoadmapError("duplicate phase id")
        if len(set(task_ids)) != len(task_ids):
            raise RoadmapError("duplicate task id")

        phase_id_set = set(phase_ids)
        task_id_set = set(task_ids)

        for phase in self.phases:
            missing = set(phase.depends_on) - phase_id_set
            if missing:
                raise RoadmapError(
                    f"phase {phase.id} references missing phases: {sorted(missing)}"
                )
        for task in self.tasks:
            if task.parent_task_id and task.parent_task_id not in task_id_set:
                raise RoadmapError(
                    f"task {task.id} references missing parent task {task.parent_task_id}"
                )
            if task.phase_id not in phase_id_set:
                raise RoadmapError(
                    f"task {task.id} references missing phase {task.phase_id}"
                )
            missing = set(task.depends_on) - task_id_set
            if missing:
                raise RoadmapError(
                    f"task {task.id} references missing tasks: {sorted(missing)}"
                )

        _assert_acyclic(
            {phase.id: phase.depends_on for phase in self.phases},
            label="phase",
        )
        _assert_acyclic(
            {task.id: task.depends_on for task in self.tasks},
            label="task",
        )

        calculated = self._calculate_digest()
        if self.canonical_sha256 and self.canonical_sha256 != calculated:
            raise RoadmapError("roadmap canonical_sha256 does not match contents")
        object.__setattr__(self, "canonical_sha256", calculated)

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "roadmap_id": self.roadmap_id,
            "version": self.version,
            "revision": self.revision,
            "phases": [phase.to_dict() for phase in self.phases],
            "tasks": [task.to_dict() for task in self.tasks],
        }

    def _calculate_digest(self) -> str:
        return _digest(self._canonical_payload())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._canonical_payload(),
            "canonical_sha256": self.canonical_sha256,
        }

    def phase(self, phase_id: str) -> RoadmapPhase:
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        raise KeyError(phase_id)

    def task(self, task_id: str) -> RoadmapTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def ready_tasks(self) -> tuple[RoadmapTask, ...]:
        completed = {
            task.id for task in self.tasks
            if task.status is TaskStatus.COMPLETED
        }
        active_phases = {
            phase.id for phase in self.phases
            if phase.status is PhaseStatus.ACTIVE
        }
        ready = [
            task for task in self.tasks
            if task.status is TaskStatus.PLANNED
            and task.phase_id in active_phases
            and all(dependency in completed for dependency in task.depends_on)
        ]
        return tuple(sorted(ready, key=lambda item: item.id))

    def children_of(self, parent_task_id: str) -> tuple[RoadmapTask, ...]:
        return tuple(
            sorted(
                (task for task in self.tasks if task.parent_task_id == parent_task_id),
                key=lambda item: item.id,
            )
        )

    def add_child_tasks(
        self,
        parent_task_id: str,
        children: tuple[RoadmapTask, ...],
    ) -> "Roadmap":
        parent = self.task(parent_task_id)
        if not children:
            raise RoadmapError("at least one child task is required")

        existing_ids = {task.id for task in self.tasks}
        new_ids = {task.id for task in children}
        if len(new_ids) != len(children) or existing_ids & new_ids:
            raise RoadmapError("child task ids must be unique and new")

        for child in children:
            if child.parent_task_id != parent_task_id:
                raise RoadmapError(
                    f"child {child.id} does not preserve parent identity"
                )
            if child.phase_id != parent.phase_id:
                raise RoadmapError(
                    f"child {child.id} leaves parent roadmap phase"
                )
            if not set(parent.acceptance_requirements).issubset(
                set(child.acceptance_requirements)
            ):
                raise RoadmapError(
                    f"child {child.id} weakens parent acceptance requirements"
                )
            if not set(parent.evidence_requirements).issubset(
                set(child.evidence_requirements)
            ):
                raise RoadmapError(
                    f"child {child.id} weakens parent evidence requirements"
                )

        return Roadmap(
            roadmap_id=self.roadmap_id,
            version=ROADMAP_SCHEMA_VERSION,
            revision=self.revision + 1,
            phases=self.phases,
            tasks=self.tasks + tuple(children),
        )

    def blocked_tasks(self) -> tuple[RoadmapTask, ...]:
        completed = {
            task.id for task in self.tasks
            if task.status is TaskStatus.COMPLETED
        }
        active_phases = {
            phase.id for phase in self.phases
            if phase.status is PhaseStatus.ACTIVE
        }
        blocked: list[RoadmapTask] = []
        for task in self.tasks:
            if task.status not in {TaskStatus.PLANNED, TaskStatus.BLOCKED}:
                continue
            if task.phase_id not in active_phases or not all(
                dependency in completed for dependency in task.depends_on
            ):
                blocked.append(task)
        return tuple(sorted(blocked, key=lambda item: item.id))

    def transition_phase(
        self,
        phase_id: str,
        status: PhaseStatus,
        *,
        expected_revision: int,
    ) -> "Roadmap":
        phase = self.phase(phase_id)
        if phase.revision != expected_revision:
            raise StaleRoadmapRevision(
                f"phase {phase_id} expected revision {expected_revision}, current {phase.revision}"
            )
        if status not in _PHASE_TRANSITIONS[phase.status]:
            raise LifecycleTransitionError(
                f"phase {phase.status.value} cannot transition to {status.value}"
            )
        if status is PhaseStatus.ACTIVE:
            for dependency in phase.depends_on:
                if self.phase(dependency).status is not PhaseStatus.COMPLETED:
                    raise LifecycleTransitionError(
                        f"phase {phase_id} is blocked by {dependency}"
                    )

        updated_phase = RoadmapPhase(
            id=phase.id,
            title=phase.title,
            depends_on=phase.depends_on,
            status=status,
            revision=phase.revision + 1,
        )
        phases = tuple(
            updated_phase if current.id == phase_id else current
            for current in self.phases
        )
        return Roadmap(
            roadmap_id=self.roadmap_id,
            version=self.version,
            revision=self.revision + 1,
            phases=phases,
            tasks=self.tasks,
        )

    def transition_task(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        expected_revision: int,
        evidence_refs: tuple[str, ...] = (),
    ) -> "Roadmap":
        task = self.task(task_id)
        if task.revision != expected_revision:
            raise StaleRoadmapRevision(
                f"task {task_id} expected revision {expected_revision}, current {task.revision}"
            )
        if status not in _TASK_TRANSITIONS[task.status]:
            raise LifecycleTransitionError(
                f"task {task.status.value} cannot transition to {status.value}"
            )
        if status is TaskStatus.ACTIVE and task not in self.ready_tasks():
            raise LifecycleTransitionError(f"task {task_id} is not eligible")
        if status is TaskStatus.COMPLETED:
            if task not in {item for item in self.tasks if item.status is TaskStatus.ACTIVE}:
                raise LifecycleTransitionError(f"task {task_id} is not active")
            if not evidence_refs:
                raise LifecycleTransitionError(
                    f"task {task_id} requires durable evidence references before completion"
                )

        updated_task = RoadmapTask(
            id=task.id,
            title=task.title,
            phase_id=task.phase_id,
            depends_on=task.depends_on,
            acceptance_requirements=task.acceptance_requirements,
            evidence_requirements=task.evidence_requirements,
            status=status,
            revision=task.revision + 1,
        )
        tasks = tuple(
            updated_task if current.id == task_id else current
            for current in self.tasks
        )
        return Roadmap(
            roadmap_id=self.roadmap_id,
            version=self.version,
            revision=self.revision + 1,
            phases=self.phases,
            tasks=tasks,
        )

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Roadmap":
        if not isinstance(value, dict):
            raise RoadmapError("roadmap must be an object")
        if int(value.get("version", -1)) not in SUPPORTED_ROADMAP_SCHEMA_VERSIONS:
            raise RoadmapError("unsupported roadmap schema version")
        phases_raw = value.get("phases", [])
        tasks_raw = value.get("tasks", [])
        if not isinstance(phases_raw, list) or not isinstance(tasks_raw, list):
            raise RoadmapError("phases and tasks must be arrays")
        return cls(
            roadmap_id=_nonempty(value.get("roadmap_id"), name="roadmap id"),
            version=int(value["version"]),
            revision=int(value.get("revision", 0)),
            phases=tuple(RoadmapPhase.from_mapping(item) for item in phases_raw),
            tasks=tuple(RoadmapTask.from_mapping(item) for item in tasks_raw),
            canonical_sha256=str(value.get("canonical_sha256", "")),
        )

    def with_version(self, revision: int | None = None) -> "Roadmap":
        return Roadmap(
            roadmap_id=self.roadmap_id,
            version=self.version,
            revision=self.revision if revision is None else revision,
            phases=self.phases,
            tasks=self.tasks,
        )


def _assert_acyclic(graph: dict[str, tuple[str, ...]], *, label: str) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise RoadmapError(f"{label} dependency cycle detected at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def load_roadmap(path: Path) -> Roadmap:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Roadmap.from_mapping(payload)
