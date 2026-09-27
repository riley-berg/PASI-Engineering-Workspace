from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.migrations import migrate_operation_state_database
from pasi.core.operation_state import (
    InvalidOperationState,
    OperationRevisionConflict,
    OperationState,
)


class OperationStateNotFound(KeyError):
    """Raised when an operation is not present in the durable store."""


class DuplicateOperationState(ValueError):
    """Raised when an operation id already exists in the durable store."""


class SQLiteOperationStateStore:
    """Durable single-record operation-state store with optimistic concurrency.

    The store deliberately owns only the current canonical state snapshot.
    Historical events and lineage remain separate control-plane concerns.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_state (
                    operation_id TEXT PRIMARY KEY,
                    state_revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            migrate_operation_state_database(connection)

    def create(self, state: OperationState) -> OperationState:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO operation_state (
                        operation_id,
                        state_revision,
                        payload_json,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.operation_id,
                        state.state_revision,
                        json.dumps(
                            state.to_dict(),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        state.created_at,
                        state.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateOperationState(
                    f"operation already exists: {state.operation_id}"
                ) from exc
        return state

    def get(self, operation_id: str) -> OperationState:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM operation_state
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()

        if row is None:
            raise OperationStateNotFound(operation_id)

        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvalidOperationState(
                f"stored operation state is not valid JSON: {operation_id}"
            ) from exc
        return OperationState.from_mapping(payload)

    def transition(
        self,
        operation_id: str,
        status: str,
        *,
        expected_revision: int,
        **updates: object,
    ) -> OperationState:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_json
                FROM operation_state
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if row is None:
                raise OperationStateNotFound(operation_id)

            try:
                current = OperationState.from_mapping(json.loads(row["payload_json"]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise InvalidOperationState(
                    f"stored operation state is not valid JSON: {operation_id}"
                ) from exc

            if current.state_revision != expected_revision:
                raise OperationRevisionConflict(
                    f"expected revision {expected_revision}, "
                    f"current revision {current.state_revision}"
                )

            next_state = current.transition(
                status,
                expected_revision=expected_revision,
                **updates,
            )
            result = connection.execute(
                """
                UPDATE operation_state
                SET state_revision = ?,
                    payload_json = ?,
                    updated_at = ?
                WHERE operation_id = ?
                  AND state_revision = ?
                """,
                (
                    next_state.state_revision,
                    json.dumps(
                        next_state.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    next_state.updated_at,
                    operation_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise OperationRevisionConflict(
                    f"operation revision changed during transition: {operation_id}"
                )
            connection.commit()

        return next_state
