from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.roadmap import Roadmap, RoadmapError, StaleRoadmapRevision


class RoadmapNotFound(KeyError):
    """Raised when a roadmap is absent."""


class DuplicateRoadmap(ValueError):
    """Raised when a roadmap id already exists."""


class SQLiteRoadmapStore:
    """Durable versioned roadmap store with optimistic revision protection."""

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
                CREATE TABLE IF NOT EXISTS roadmap (
                    roadmap_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    canonical_sha256 TEXT NOT NULL
                )
                """
            )

    def create(self, roadmap: Roadmap) -> Roadmap:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO roadmap(
                        roadmap_id, revision, payload_json, canonical_sha256
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        roadmap.roadmap_id,
                        roadmap.revision,
                        json.dumps(
                            roadmap.to_dict(),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        roadmap.canonical_sha256,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateRoadmap(roadmap.roadmap_id) from exc
        return roadmap

    def get(self, roadmap_id: str) -> Roadmap:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM roadmap WHERE roadmap_id = ?",
                (roadmap_id,),
            ).fetchone()
        if row is None:
            raise RoadmapNotFound(roadmap_id)
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise RoadmapError("stored roadmap payload is invalid JSON") from exc
        return Roadmap.from_mapping(payload)

    def save(self, roadmap: Roadmap, *, expected_revision: int) -> Roadmap:
        if roadmap.revision != expected_revision + 1:
            raise StaleRoadmapRevision(
                "saved roadmap revision must be exactly one greater than expected_revision"
            )
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE roadmap
                SET revision = ?,
                    payload_json = ?,
                    canonical_sha256 = ?
                WHERE roadmap_id = ?
                  AND revision = ?
                """,
                (
                    roadmap.revision,
                    json.dumps(
                        roadmap.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    roadmap.canonical_sha256,
                    roadmap.roadmap_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise StaleRoadmapRevision(
                    f"roadmap {roadmap.roadmap_id} has moved since revision {expected_revision}"
                )
        return roadmap
