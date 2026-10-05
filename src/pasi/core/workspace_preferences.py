from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PreferenceError(ValueError):
    """Raised when a preference key/value is invalid."""


class StalePreferenceRevision(PreferenceError):
    """Raised when a preference update targets an old revision."""


ALLOWED_PREFERENCES: dict[str, object] = {
    "planner.layout": "board",
    "planner.view": "overview",
    "notifications.enabled": True,
    "sync.enabled": True,
    "search.scope": "project",
}


@dataclass(frozen=True)
class WorkspacePreference:
    scope: str
    key: str
    value: object
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise PreferenceError("scope is required")
        if self.key not in ALLOWED_PREFERENCES:
            raise PreferenceError(f"unknown preference key: {self.key}")
        if self.revision < 0:
            raise PreferenceError("revision must be non-negative")
        if isinstance(self.value, (dict, list, tuple, set)):
            raise PreferenceError("preference values must be scalar")


class SQLiteWorkspacePreferenceStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_preference (
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY(scope, key)
                )
                """
            )

    def get(self, scope: str, key: str) -> WorkspacePreference:
        if key not in ALLOWED_PREFERENCES:
            raise PreferenceError(f"unknown preference key: {key}")
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT value_json, revision
                FROM workspace_preference
                WHERE scope = ? AND key = ?
                """,
                (scope, key),
            ).fetchone()
        if row is None:
            return WorkspacePreference(scope, key, ALLOWED_PREFERENCES[key], 0)
        return WorkspacePreference(
            scope,
            key,
            json.loads(row[0]),
            int(row[1]),
        )

    def set(
        self,
        scope: str,
        key: str,
        value: object,
        *,
        expected_revision: int,
    ) -> WorkspacePreference:
        current = self.get(scope, key)
        if current.revision != expected_revision:
            raise StalePreferenceRevision(
                f"{scope}/{key} expected {expected_revision}, current {current.revision}"
            )
        next_value = WorkspacePreference(
            scope,
            key,
            value,
            expected_revision + 1,
        )
        with sqlite3.connect(self.path) as connection:
            result = connection.execute(
                """
                INSERT INTO workspace_preference(scope, key, value_json, revision)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, key)
                DO UPDATE SET value_json=excluded.value_json,
                              revision=excluded.revision
                WHERE workspace_preference.revision = ?
                """,
                (
                    scope,
                    key,
                    json.dumps(next_value.value, sort_keys=True),
                    next_value.revision,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise StalePreferenceRevision(
                    f"{scope}/{key} changed while being updated"
                )
        return next_value

    def list(self, scope: str) -> tuple[WorkspacePreference, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT key, value_json, revision FROM workspace_preference WHERE scope = ? ORDER BY key",
                (scope,),
            ).fetchall()

        stored = {
            key: WorkspacePreference(
                scope,
                key,
                json.loads(value_json),
                int(revision),
            )
            for key, value_json, revision in rows
        }
        return tuple(
            stored.get(
                key,
                WorkspacePreference(scope, key, default_value, 0),
            )
            for key, default_value in sorted(ALLOWED_PREFERENCES.items())
        )
