from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pasi.core.event_store import SQLiteEventStore
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.roadmap import Roadmap, RoadmapTask
from pasi.core.selection_store import SQLiteSelectionStore


class TaskDetailNotFound(KeyError):
    """Raised when a requested roadmap task is absent."""


@dataclass(frozen=True)
class TaskDetail:
    roadmap_id: str
    roadmap_revision: int
    task: dict[str, Any]
    phase: dict[str, Any]
    prerequisites: tuple[str, ...]
    dependents: tuple[str, ...]
    selected: bool | None
    queued: bool
    operation_history: tuple[dict[str, Any], ...]
    event_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "roadmap_id": self.roadmap_id,
            "roadmap_revision": self.roadmap_revision,
            "task": self.task,
            "phase": self.phase,
            "prerequisites": list(self.prerequisites),
            "dependents": list(self.dependents),
            "selected": self.selected,
            "queued": self.queued,
            "operation_history": list(self.operation_history),
            "event_count": self.event_count,
        }


class TaskDetailReadModel:
    def __init__(
        self,
        *,
        roadmap: Roadmap,
        selection_store: SQLiteSelectionStore | None = None,
        ledger: SQLiteOperationLedger | None = None,
        events: SQLiteEventStore | None = None,
    ) -> None:
        self.roadmap = roadmap
        self.selection_store = selection_store
        self.ledger = ledger
        self.events = events

    def get(self, task_id: str) -> TaskDetail:
        try:
            task = self.roadmap.task(task_id)
            phase = self.roadmap.phase(task.phase_id)
        except KeyError as exc:
            raise TaskDetailNotFound(task_id) from exc

        dependents = tuple(
            sorted(
                item.id for item in self.roadmap.tasks
                if task.id in item.depends_on
            )
        )

        selected: bool | None = None
        if self.selection_store is not None:
            try:
                decision = self.selection_store.latest(
                    self.roadmap.roadmap_id,
                    self.roadmap.revision,
                )
                selected = decision.selected_task_id == task.id
            except KeyError:
                selected = None

        history: tuple[dict[str, Any], ...] = ()
        queued = False
        event_count = 0
        if self.ledger is not None:
            history = tuple(
                entry.to_dict()
                for entry in self.ledger.list_task(task.id)
            )
            queued = any(
                entry.outcome in {"queued", "claimed", "generating"}
                for entry in self.ledger.list_task(task.id)
            )
        if self.events is not None and history:
            event_count = sum(
                len(self.events.list(operation_id=item["operation_id"], limit=1000))
                for item in history
            )

        return TaskDetail(
            roadmap_id=self.roadmap.roadmap_id,
            roadmap_revision=self.roadmap.revision,
            task=task.to_dict(),
            phase=phase.to_dict(),
            prerequisites=tuple(sorted(task.depends_on)),
            dependents=dependents,
            selected=selected,
            queued=queued,
            operation_history=history,
            event_count=event_count,
        )
