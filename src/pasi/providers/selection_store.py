from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.providers.selection import ProviderSelection


class ProviderSelectionNotFound(KeyError):
    """Raised when a provider selection record is absent."""


class SQLiteProviderSelectionStore:
    """Durable provider-selection decisions."""

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
                CREATE TABLE IF NOT EXISTS provider_selection (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    registry_digest TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    selection_json TEXT NOT NULL,
                    selected_at TEXT NOT NULL
                )
                """
            )

    def record(self, selection: ProviderSelection) -> int:
        payload = {
            "provider": selection.provider,
            "model": selection.model,
            "registry_digest": selection.registry_digest,
            "eligible": list(selection.eligible),
            "excluded_reasons": selection.excluded_reasons,
            "preference_ranks": selection.preference_ranks,
            "selected_at": selection.selected_at,
        }
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO provider_selection(
                    registry_digest, provider, model, selection_json, selected_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    selection.registry_digest,
                    selection.provider,
                    selection.model,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    selection.selected_at,
                ),
            )
            return int(cursor.lastrowid)

    def latest(self) -> ProviderSelection:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT selection_json
                FROM provider_selection
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise ProviderSelectionNotFound()
        payload = json.loads(row["selection_json"])
        payload["eligible"] = tuple(payload["eligible"])
        return ProviderSelection(**payload)
