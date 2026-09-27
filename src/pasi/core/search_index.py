from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class SearchError(ValueError):
    """Raised when search input violates bounds or scope rules."""


@dataclass(frozen=True)
class IndexedDocument:
    document_id: str
    scope: str
    kind: str
    title: str
    text: str
    deep_link: str
    freshness: str
    indexed_at: str
    revision: int = 0


@dataclass(frozen=True)
class SearchResult:
    document_id: str
    scope: str
    kind: str
    title: str
    deep_link: str
    freshness: str
    score: float
    matched_terms: tuple[str, ...]


class SQLiteSearchIndex:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_search (
                    document_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    deep_link TEXT NOT NULL,
                    freshness TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    revision INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspace_search_scope ON workspace_search(scope, kind)"
            )

    def upsert(self, document: IndexedDocument) -> None:
        if document.freshness not in {"fresh", "stale"}:
            raise SearchError("freshness must be fresh or stale")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO workspace_search(
                    document_id, scope, kind, title, text,
                    deep_link, freshness, indexed_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id)
                DO UPDATE SET
                    scope=excluded.scope,
                    kind=excluded.kind,
                    title=excluded.title,
                    text=excluded.text,
                    deep_link=excluded.deep_link,
                    freshness=excluded.freshness,
                    indexed_at=excluded.indexed_at,
                    revision=excluded.revision
                """,
                (
                    document.document_id,
                    document.scope,
                    document.kind,
                    document.title,
                    document.text,
                    document.deep_link,
                    document.freshness,
                    document.indexed_at,
                    document.revision,
                ),
            )

    def search(
        self,
        query: str,
        *,
        scope: str,
        limit: int = 20,
        include_stale: bool = True,
    ) -> tuple[SearchResult, ...]:
        if not query.strip():
            raise SearchError("query is required")
        if limit <= 0 or limit > 100:
            raise SearchError("limit must be between 1 and 100")
        terms = {
            term.lower()
            for term in re.findall(r"[A-Za-z0-9_'-]+", query)
            if len(term) >= 2
        }
        if not terms:
            raise SearchError("query has no searchable terms")

        clauses = ["scope = ?"]
        params: list[object] = [scope]
        if not include_stale:
            clauses.append("freshness = 'fresh'")
        query_sql = (
            "SELECT * FROM workspace_search WHERE "
            + " AND ".join(clauses)
            + " ORDER BY document_id"
        )
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(query_sql, params).fetchall()

        results: list[SearchResult] = []
        for row in rows:
            haystack = (row[3] + " " + row[4]).lower()
            matches = tuple(sorted(term for term in terms if term in haystack))
            if not matches:
                continue
            results.append(
                SearchResult(
                    document_id=row[0],
                    scope=row[1],
                    kind=row[2],
                    title=row[3],
                    deep_link=row[5],
                    freshness=row[6],
                    score=len(matches) / len(terms),
                    matched_terms=matches,
                )
            )

        results.sort(key=lambda result: (-result.score, result.document_id))
        return tuple(results[:limit])

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()
