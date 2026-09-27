from __future__ import annotations

import sqlite3
from pathlib import Path

from pasi.core.ledger import OperationLedgerEntry


class DuplicateLedgerEntry(ValueError):
    """Raised when operation lineage already exists."""


class LedgerEntryNotFound(KeyError):
    """Raised when operation lineage is absent."""


class LineageConflict(ValueError):
    """Raised when an operation would introduce invalid lineage."""


class SQLiteOperationLedger:
    """Durable operation/task/run lineage store."""

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
                CREATE TABLE IF NOT EXISTS operation_ledger (
                    operation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    parent_operation_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    pr_number INTEGER,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(parent_operation_id)
                        REFERENCES operation_ledger(operation_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_task
                ON operation_ledger(task_id, created_at, operation_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_run
                ON operation_ledger(run_id, created_at, operation_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_parent
                ON operation_ledger(parent_operation_id)
                """
            )

    def register(self, entry: OperationLedgerEntry) -> OperationLedgerEntry:
        with self._connect() as connection:
            if entry.parent_operation_id:
                parent = connection.execute(
                    """
                    SELECT task_id, run_id
                    FROM operation_ledger
                    WHERE operation_id = ?
                    """,
                    (entry.parent_operation_id,),
                ).fetchone()
                if parent is None:
                    raise LineageConflict(
                        f"parent operation does not exist: {entry.parent_operation_id}"
                    )
                if parent["task_id"] != entry.task_id:
                    raise LineageConflict("parent operation belongs to a different task")
                if parent["run_id"] != entry.run_id:
                    raise LineageConflict("parent operation belongs to a different run")

            try:
                connection.execute(
                    """
                    INSERT INTO operation_ledger (
                        operation_id,
                        task_id,
                        run_id,
                        parent_operation_id,
                        provider,
                        branch,
                        pr_number,
                        outcome,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.operation_id,
                        entry.task_id,
                        entry.run_id,
                        entry.parent_operation_id,
                        entry.provider,
                        entry.branch,
                        entry.pr_number,
                        entry.outcome,
                        entry.created_at,
                        entry.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateLedgerEntry(
                    f"operation already exists: {entry.operation_id}"
                ) from exc

        return entry

    def get(self, operation_id: str) -> OperationLedgerEntry:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM operation_ledger
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            raise LedgerEntryNotFound(operation_id)
        return self._row_to_entry(row)

    def list_task(self, task_id: str) -> tuple[OperationLedgerEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM operation_ledger
                WHERE task_id = ?
                ORDER BY created_at ASC, operation_id ASC
                """,
                (task_id,),
            ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def list_run(self, run_id: str) -> tuple[OperationLedgerEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM operation_ledger
                WHERE run_id = ?
                ORDER BY created_at ASC, operation_id ASC
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def list(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        provider: str | None = None,
        branch: str | None = None,
        pr_number: int | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> tuple[OperationLedgerEntry, ...]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")

        clauses: list[str] = []
        params: list[object] = []
        for column, value in (
            ("task_id", task_id),
            ("run_id", run_id),
            ("provider", provider),
            ("branch", branch),
            ("pr_number", pr_number),
            ("outcome", outcome),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT *
            FROM operation_ledger
            {where}
            ORDER BY created_at DESC, operation_id ASC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def children(self, operation_id: str) -> tuple[OperationLedgerEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM operation_ledger
                WHERE parent_operation_id = ?
                ORDER BY created_at ASC, operation_id ASC
                """,
                (operation_id,),
            ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def lineage(self, operation_id: str) -> tuple[OperationLedgerEntry, ...]:
        current = self.get(operation_id)
        chain = [current]
        seen = {operation_id}
        while current.parent_operation_id:
            parent_id = current.parent_operation_id
            if parent_id in seen:
                raise LineageConflict("cycle detected in stored lineage")
            current = self.get(parent_id)
            chain.append(current)
            seen.add(parent_id)
        chain.reverse()
        return tuple(chain)

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> OperationLedgerEntry:
        return OperationLedgerEntry(
            operation_id=row["operation_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            parent_operation_id=row["parent_operation_id"],
            provider=row["provider"],
            branch=row["branch"],
            pr_number=row["pr_number"],
            outcome=row["outcome"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
