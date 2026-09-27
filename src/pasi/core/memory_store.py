from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.memory import MemoryError, MemoryRecord, MemoryStatus


class MemoryNotFound(KeyError):
    """Raised when a memory record is absent."""


class DuplicateMemory(ValueError):
    """Raised when a memory id already exists."""


class StaleMemoryRevision(ValueError):
    """Raised when a memory update targets an old revision."""


class SQLiteMemoryStore:
    """Durable, scoped project-memory store with optimistic revision updates."""

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
                CREATE TABLE IF NOT EXISTS memory (
                    memory_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    provenance_refs_json TEXT NOT NULL,
                    source_operation_id TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memory_scope_kind
                ON memory(scope, kind, status, updated_at)
                """
            )

    def create(self, memory: MemoryRecord) -> MemoryRecord:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO memory(
                        memory_id, scope, kind, content,
                        provenance_refs_json, source_operation_id,
                        confidence, status, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory.memory_id,
                        memory.scope,
                        memory.kind,
                        memory.content,
                        json.dumps(list(memory.provenance_refs), separators=(",", ":")),
                        memory.source_operation_id,
                        float(memory.confidence),
                        memory.status.value,
                        memory.revision,
                        memory.created_at,
                        memory.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateMemory(memory.memory_id) from exc
        return memory

    def get(self, memory_id: str) -> MemoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise MemoryNotFound(memory_id)
        return self._row_to_memory(row)

    def update(
        self,
        memory: MemoryRecord,
        *,
        expected_revision: int,
    ) -> MemoryRecord:
        if memory.revision != expected_revision + 1:
            raise StaleMemoryRevision(
                "memory revision must advance exactly one step"
            )
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE memory
                SET scope = ?,
                    kind = ?,
                    content = ?,
                    provenance_refs_json = ?,
                    source_operation_id = ?,
                    confidence = ?,
                    status = ?,
                    revision = ?,
                    updated_at = ?
                WHERE memory_id = ?
                  AND revision = ?
                """,
                (
                    memory.scope,
                    memory.kind,
                    memory.content,
                    json.dumps(
                        list(memory.provenance_refs),
                        separators=(",", ":"),
                    ),
                    memory.source_operation_id,
                    float(memory.confidence),
                    memory.status.value,
                    memory.revision,
                    memory.updated_at,
                    memory.memory_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise StaleMemoryRevision(
                    f"memory {memory.memory_id} moved after revision {expected_revision}"
                )
        return memory

    def archive(self, memory_id: str, *, expected_revision: int) -> MemoryRecord:
        current = self.get(memory_id)
        if current.revision != expected_revision:
            raise StaleMemoryRevision(
                f"memory {memory_id} expected revision {expected_revision}, current {current.revision}"
            )
        archived = MemoryRecord(
            memory_id=current.memory_id,
            scope=current.scope,
            kind=current.kind,
            content=current.content,
            provenance_refs=current.provenance_refs,
            source_operation_id=current.source_operation_id,
            confidence=current.confidence,
            status=MemoryStatus.ARCHIVED,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )
        return self.update(archived, expected_revision=expected_revision)

    def list(
        self,
        *,
        scope: str,
        kind: str | None = None,
        include_archived: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        clauses = ["scope = ?"]
        params: list[object] = [scope]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if not include_archived:
            clauses.append("status = ?")
            params.append(MemoryStatus.ACTIVE.value)
        query = (
            "SELECT * FROM memory WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, memory_id ASC"
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_memory(row) for row in rows)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> MemoryRecord:
        try:
            refs = json.loads(row["provenance_refs_json"])
        except json.JSONDecodeError as exc:
            raise MemoryError("stored provenance refs are invalid JSON") from exc
        return MemoryRecord(
            memory_id=row["memory_id"],
            scope=row["scope"],
            kind=row["kind"],
            content=row["content"],
            provenance_refs=tuple(str(ref) for ref in refs),
            source_operation_id=row["source_operation_id"],
            confidence=float(row["confidence"]),
            status=MemoryStatus(row["status"]),
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
