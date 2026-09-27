from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


CURRENT_OPERATION_STATE_DB_VERSION = 2


class MigrationError(RuntimeError):
    """Raised when durable runtime-state migration cannot be completed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (name,),
    ).fetchone()
    return row is not None


def _read_component_version(
    connection: sqlite3.Connection,
    component: str,
) -> int:
    if not _table_exists(connection, "pasi_schema_version"):
        return 0
    row = connection.execute(
        """
        SELECT version
        FROM pasi_schema_version
        WHERE component = ?
        """,
        (component,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _write_component_version(
    connection: sqlite3.Connection,
    component: str,
    version: int,
) -> None:
    connection.execute(
        """
        INSERT INTO pasi_schema_version(component, version, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(component)
        DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
        """,
        (component, version, utc_now()),
    )


def _upgrade_operation_state_v1_to_v2(
    connection: sqlite3.Connection,
) -> None:
    rows = connection.execute(
        """
        SELECT operation_id, payload_json
        FROM operation_state
        """
    ).fetchall()
    for row in rows:
        try:
            payload: dict[str, Any] = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise MigrationError(
                f"operation {row['operation_id']} has invalid JSON"
            ) from exc

        source_version = payload.get("schema_version", 1)
        if source_version != 1:
            raise MigrationError(
                f"operation {row['operation_id']} has unexpected "
                f"schema version {source_version!r}"
            )

        payload["schema_version"] = 2
        payload.setdefault("metadata", {})
        connection.execute(
            """
            UPDATE operation_state
            SET payload_json = ?
            WHERE operation_id = ?
            """,
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                row["operation_id"],
            ),
        )


def migrate_operation_state_database(connection: sqlite3.Connection) -> None:
    """Upgrade persisted operation state transactionally to the current database version."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pasi_schema_version (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    current = _read_component_version(connection, "operation_state")
    if current > CURRENT_OPERATION_STATE_DB_VERSION:
        raise MigrationError(
            f"operation-state database version {current} is newer than "
            f"supported version {CURRENT_OPERATION_STATE_DB_VERSION}"
        )

    if current < 1:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM operation_state
            LIMIT 1
            """
        ).fetchall()
        inferred_version = 2
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise MigrationError("operation state contains invalid JSON") from exc
            inferred_version = int(payload.get("schema_version", 1))
            break
        current = 1 if inferred_version == 1 else inferred_version

    if current == 1:
        _upgrade_operation_state_v1_to_v2(connection)
        current = 2

    if current != CURRENT_OPERATION_STATE_DB_VERSION:
        raise MigrationError(
            f"operation-state migration stopped at unsupported version {current}"
        )
    _write_component_version(
        connection,
        "operation_state",
        CURRENT_OPERATION_STATE_DB_VERSION,
    )
