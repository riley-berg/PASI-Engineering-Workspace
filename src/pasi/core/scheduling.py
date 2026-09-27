from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

from pasi.core.roadmap import Roadmap


class ScheduleError(ValueError):
    """Raised when scheduling inputs violate bounds."""


class StaleSchedule(ScheduleError):
    """Raised when a schedule no longer matches the roadmap revision."""


@dataclass(frozen=True)
class ResourceEstimate:
    cpu_millicores: int
    memory_mb: int
    time_seconds: float

    def __post_init__(self) -> None:
        if (
            self.cpu_millicores <= 0
            or self.memory_mb <= 0
            or not math.isfinite(self.time_seconds)
            or self.time_seconds <= 0
        ):
            raise ScheduleError("resource estimates must be positive finite values")


@dataclass(frozen=True)
class SchedulerCapacity:
    cpu_millicores: int
    memory_mb: int
    time_seconds: float

    def __post_init__(self) -> None:
        if (
            self.cpu_millicores <= 0
            or self.memory_mb <= 0
            or not math.isfinite(self.time_seconds)
            or self.time_seconds <= 0
        ):
            raise ScheduleError("scheduler capacity must be positive finite values")


@dataclass(frozen=True)
class ScheduledTask:
    task_id: str
    estimate: ResourceEstimate
    advisory_priority: float


@dataclass(frozen=True)
class ScheduleDecision:
    roadmap_id: str
    roadmap_revision: int
    capacity: SchedulerCapacity
    scheduled: tuple[ScheduledTask, ...]
    excluded: dict[str, str]
    created_at: str

    def assert_fresh(self, roadmap: Roadmap) -> None:
        if (
            self.roadmap_id != roadmap.roadmap_id
            or self.roadmap_revision != roadmap.revision
        ):
            raise StaleSchedule(
                "schedule was computed against a different roadmap revision"
            )


class CostAwareScheduler:
    """Greedily pack authoritative-eligible tasks within explicit capacity."""

    def schedule(
        self,
        roadmap: Roadmap,
        *,
        estimates: Mapping[str, ResourceEstimate],
        capacity: SchedulerCapacity,
        advisory_priorities: Mapping[str, float] | None = None,
    ) -> ScheduleDecision:
        priorities = dict(advisory_priorities or {})
        ready_ids = {task.id for task in roadmap.ready_tasks()}

        excluded: dict[str, str] = {}
        candidates: list[tuple[str, ResourceEstimate, float]] = []

        for task_id in sorted(ready_ids):
            estimate = estimates.get(task_id)
            if estimate is None:
                excluded[task_id] = "missing resource estimate"
                continue

            priority = priorities.get(task_id, 0.0)
            if not isinstance(priority, (int, float)) or not math.isfinite(float(priority)):
                excluded[task_id] = "invalid advisory priority"
                continue

            candidates.append((task_id, estimate, float(priority)))

        candidates.sort(
            key=lambda item: (-item[2], item[1].time_seconds, item[0])
        )

        remaining_cpu = capacity.cpu_millicores
        remaining_memory = capacity.memory_mb
        remaining_time = capacity.time_seconds
        scheduled: list[ScheduledTask] = []

        for task_id, estimate, priority in candidates:
            if (
                estimate.cpu_millicores > remaining_cpu
                or estimate.memory_mb > remaining_memory
                or estimate.time_seconds > remaining_time
            ):
                excluded[task_id] = "capacity exceeded"
                continue

            scheduled.append(
                ScheduledTask(
                    task_id=task_id,
                    estimate=estimate,
                    advisory_priority=priority,
                )
            )
            remaining_cpu -= estimate.cpu_millicores
            remaining_memory -= estimate.memory_mb
            remaining_time -= estimate.time_seconds

        return ScheduleDecision(
            roadmap_id=roadmap.roadmap_id,
            roadmap_revision=roadmap.revision,
            capacity=capacity,
            scheduled=tuple(scheduled),
            excluded=dict(sorted(excluded.items())),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
