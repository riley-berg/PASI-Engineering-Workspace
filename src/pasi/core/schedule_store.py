from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.scheduling import (
    ResourceEstimate,
    ScheduleDecision,
    SchedulerCapacity,
    ScheduledTask,
)


class ScheduleNotFound(KeyError):
    """Raised when a persisted schedule is absent."""


class SQLiteScheduleStore:
    """Durable scheduling-decision store."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_decision (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    roadmap_id TEXT NOT NULL,
                    roadmap_revision INTEGER NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def record(self, decision: ScheduleDecision) -> int:
        payload = {
            "roadmap_id": decision.roadmap_id,
            "roadmap_revision": decision.roadmap_revision,
            "capacity": {
                "cpu_millicores": decision.capacity.cpu_millicores,
                "memory_mb": decision.capacity.memory_mb,
                "time_seconds": decision.capacity.time_seconds,
            },
            "scheduled": [
                {
                    "task_id": item.task_id,
                    "estimate": {
                        "cpu_millicores": item.estimate.cpu_millicores,
                        "memory_mb": item.estimate.memory_mb,
                        "time_seconds": item.estimate.time_seconds,
                    },
                    "advisory_priority": item.advisory_priority,
                }
                for item in decision.scheduled
            ],
            "excluded": decision.excluded,
            "created_at": decision.created_at,
        }
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO schedule_decision(
                    roadmap_id, roadmap_revision, decision_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    decision.roadmap_id,
                    decision.roadmap_revision,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    decision.created_at,
                ),
            )
            return int(cursor.lastrowid)

    def latest(self, roadmap_id: str, roadmap_revision: int) -> ScheduleDecision:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT decision_json
                FROM schedule_decision
                WHERE roadmap_id = ?
                  AND roadmap_revision = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (roadmap_id, roadmap_revision),
            ).fetchone()
        if row is None:
            raise ScheduleNotFound((roadmap_id, roadmap_revision))

        payload = json.loads(row["decision_json"])
        capacity = SchedulerCapacity(**payload["capacity"])
        scheduled = tuple(
            ScheduledTask(
                task_id=item["task_id"],
                estimate=ResourceEstimate(**item["estimate"]),
                advisory_priority=float(item["advisory_priority"]),
            )
            for item in payload["scheduled"]
        )
        return ScheduleDecision(
            roadmap_id=payload["roadmap_id"],
            roadmap_revision=int(payload["roadmap_revision"]),
            capacity=capacity,
            scheduled=scheduled,
            excluded=dict(payload["excluded"]),
            created_at=payload["created_at"],
        )
