from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.task_selection import SelectionDecision


class SelectionNotFound(KeyError):
    """Raised when a persisted selection is absent."""


class SQLiteSelectionStore:
    """Durable selection-decision store keyed by roadmap revision."""

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
                CREATE TABLE IF NOT EXISTS task_selection (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    roadmap_id TEXT NOT NULL,
                    roadmap_revision INTEGER NOT NULL,
                    selected_task_id TEXT,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_selection_roadmap
                ON task_selection(roadmap_id, roadmap_revision, id)
                """
            )

    def record(self, decision: SelectionDecision) -> int:
        payload = {
            "roadmap_id": decision.roadmap_id,
            "roadmap_revision": decision.roadmap_revision,
            "selected_task_id": decision.selected_task_id,
            "eligible_task_ids": list(decision.eligible_task_ids),
            "excluded_reasons": decision.excluded_reasons,
            "advisory_scores": decision.advisory_scores,
            "selected_at": decision.selected_at,
        }
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_selection(
                    roadmap_id,
                    roadmap_revision,
                    selected_task_id,
                    decision_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    decision.roadmap_id,
                    decision.roadmap_revision,
                    decision.selected_task_id,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    decision.selected_at,
                ),
            )
            return int(cursor.lastrowid)

    def latest(self, roadmap_id: str, roadmap_revision: int) -> SelectionDecision:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT decision_json
                FROM task_selection
                WHERE roadmap_id = ?
                  AND roadmap_revision = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (roadmap_id, roadmap_revision),
            ).fetchone()
        if row is None:
            raise SelectionNotFound((roadmap_id, roadmap_revision))
        return SelectionDecision(**json.loads(row["decision_json"]))
