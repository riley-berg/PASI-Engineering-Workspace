import json
import sqlite3
from pathlib import Path

from pasi.core.migrations import CURRENT_OPERATION_STATE_DB_VERSION
from pasi.core.operation_state import OperationState
from pasi.core.operation_store import SQLiteOperationStateStore


def test_v1_operation_state_is_migrated_to_v2_on_store_open(tmp_path: Path):
    path = tmp_path / "operations.db"
    legacy = OperationState(
        operation_id="op-legacy",
        operation_type="roadmap_task",
        task_id="P1.7",
        schema_version=1,
    )
    payload = legacy.to_dict()
    payload["schema_version"] = 1
    payload.pop("metadata", None)

    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE operation_state (
            operation_id TEXT PRIMARY KEY,
            state_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO operation_state(
            operation_id, state_revision, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            legacy.operation_id,
            legacy.state_revision,
            json.dumps(payload),
            legacy.created_at,
            legacy.updated_at,
        ),
    )
    connection.commit()
    connection.close()

    store = SQLiteOperationStateStore(path)
    upgraded = store.get("op-legacy")

    assert upgraded.schema_version == 2
    assert upgraded.metadata == {}

    connection = sqlite3.connect(path)
    version = connection.execute(
        "SELECT version FROM pasi_schema_version WHERE component = 'operation_state'"
    ).fetchone()[0]
    connection.close()
    assert version == CURRENT_OPERATION_STATE_DB_VERSION


def test_failed_migration_rolls_back_without_mutating_legacy_payload(tmp_path: Path):
    path = tmp_path / "broken.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE operation_state (
            operation_id TEXT PRIMARY KEY,
            state_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO operation_state(
            operation_id, state_revision, payload_json, created_at, updated_at
        ) VALUES ('op-broken', 0, '{not-json}', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()

    try:
        SQLiteOperationStateStore(path)
    except Exception as exc:
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("broken legacy payload unexpectedly migrated")

    connection = sqlite3.connect(path)
    payload = connection.execute(
        "SELECT payload_json FROM operation_state WHERE operation_id = 'op-broken'"
    ).fetchone()[0]
    connection.close()
    assert payload == "{not-json}"
