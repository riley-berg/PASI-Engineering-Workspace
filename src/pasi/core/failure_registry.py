from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def signature_key(
    *,
    subsystem: str,
    failure_code: str,
    failure_family: str,
) -> str:
    normalized = "|".join(
        value.strip().lower()
        for value in (subsystem, failure_code, failure_family)
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FailureSignature:
    signature_id: str
    subsystem: str
    failure_code: str
    failure_family: str
    first_seen_at: str
    last_seen_at: str
    occurrence_count: int
    latest_operation_id: str = ""
    evidence_ref: str = ""


class SQLiteFailureRegistry:
    """Durable normalized failure-signature registry."""

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
                CREATE TABLE IF NOT EXISTS failure_signature (
                    signature_id TEXT PRIMARY KEY,
                    subsystem TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    failure_family TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    latest_operation_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL
                )
                """
            )

    def record(
        self,
        *,
        subsystem: str,
        failure_code: str,
        failure_family: str,
        operation_id: str = "",
        evidence_ref: str = "",
        seen_at: str | None = None,
    ) -> FailureSignature:
        timestamp = seen_at or utc_now()
        key = signature_key(
            subsystem=subsystem,
            failure_code=failure_code,
            failure_family=failure_family,
        )
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM failure_signature
                WHERE signature_id = ?
                """,
                (key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO failure_signature (
                        signature_id,
                        subsystem,
                        failure_code,
                        failure_family,
                        first_seen_at,
                        last_seen_at,
                        occurrence_count,
                        latest_operation_id,
                        evidence_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        key,
                        subsystem.strip(),
                        failure_code.strip(),
                        failure_family.strip(),
                        timestamp,
                        timestamp,
                        operation_id,
                        evidence_ref,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE failure_signature
                    SET last_seen_at = ?,
                        occurrence_count = occurrence_count + 1,
                        latest_operation_id = ?,
                        evidence_ref = ?
                    WHERE signature_id = ?
                    """,
                    (timestamp, operation_id, evidence_ref, key),
                )

            result = connection.execute(
                """
                SELECT *
                FROM failure_signature
                WHERE signature_id = ?
                """,
                (key,),
            ).fetchone()

        assert result is not None
        return self._row_to_signature(result)

    def get(self, signature_id: str) -> FailureSignature:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM failure_signature
                WHERE signature_id = ?
                """,
                (signature_id,),
            ).fetchone()
        if row is None:
            raise KeyError(signature_id)
        return self._row_to_signature(row)

    def list(self, *, subsystem: str | None = None) -> tuple[FailureSignature, ...]:
        query = "SELECT * FROM failure_signature"
        params: tuple[str, ...] = ()
        if subsystem is not None:
            query += " WHERE subsystem = ?"
            params = (subsystem,)
        query += " ORDER BY occurrence_count DESC, signature_id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_signature(row) for row in rows)

    @staticmethod
    def _row_to_signature(row: sqlite3.Row) -> FailureSignature:
        return FailureSignature(
            signature_id=row["signature_id"],
            subsystem=row["subsystem"],
            failure_code=row["failure_code"],
            failure_family=row["failure_family"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            occurrence_count=row["occurrence_count"],
            latest_operation_id=row["latest_operation_id"],
            evidence_ref=row["evidence_ref"],
        )
