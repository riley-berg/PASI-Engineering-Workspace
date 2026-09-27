from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.events import DurableEvent, InvalidEvent


class DuplicateEvent(ValueError):
    """Raised when an event id has already been persisted."""


class EventNotFound(KeyError):
    """Raised when a requested event is absent."""


class SQLiteEventStore:
    """Append-only durable event store with deterministic ordering."""

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
                CREATE TABLE IF NOT EXISTS durable_event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_event_operation_sequence
                ON durable_event(operation_id, sequence)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_event_correlation_sequence
                ON durable_event(correlation_id, sequence)
                """
            )

    def append(self, event: DurableEvent) -> DurableEvent:
        with self._connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO durable_event (
                        event_id,
                        event_type,
                        source,
                        operation_id,
                        task_id,
                        run_id,
                        correlation_id,
                        causation_id,
                        payload_json,
                        evidence_refs_json,
                        occurred_at,
                        schema_version,
                        payload_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.source,
                        event.operation_id,
                        event.task_id,
                        event.run_id,
                        event.correlation_id,
                        event.causation_id,
                        json.dumps(
                            event.payload,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            list(event.evidence_refs),
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                        event.occurred_at,
                        event.schema_version,
                        event.payload_sha256,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateEvent(
                    f"event already exists: {event.event_id}"
                ) from exc

            sequence = int(cursor.lastrowid)

        return DurableEvent.from_mapping(
            {**event.to_dict(), "sequence": sequence}
        )

    def get(self, event_id: str) -> DurableEvent:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM durable_event
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()

        if row is None:
            raise EventNotFound(event_id)
        return self._row_to_event(row)

    def list(
        self,
        *,
        operation_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[DurableEvent, ...]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")

        clauses: list[str] = []
        parameters: list[str | int] = []
        if operation_id is not None:
            clauses.append("operation_id = ?")
            parameters.append(operation_id)
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            parameters.append(correlation_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT *
            FROM durable_event
            {where}
            ORDER BY sequence ASC
            LIMIT ?
        """
        parameters.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return tuple(self._row_to_event(row) for row in rows)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> DurableEvent:
        try:
            payload = json.loads(row["payload_json"])
            refs = json.loads(row["evidence_refs_json"])
        except json.JSONDecodeError as exc:
            raise InvalidEvent("stored event payload is not valid JSON") from exc
        return DurableEvent.from_mapping(
            {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "source": row["source"],
                "operation_id": row["operation_id"],
                "task_id": row["task_id"],
                "run_id": row["run_id"],
                "correlation_id": row["correlation_id"],
                "causation_id": row["causation_id"],
                "payload": payload,
                "evidence_refs": refs,
                "occurred_at": row["occurred_at"],
                "schema_version": row["schema_version"],
                "payload_sha256": row["payload_sha256"],
                "sequence": row["sequence"],
            }
        )
