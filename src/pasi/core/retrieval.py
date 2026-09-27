from __future__ import annotations

import re
from dataclasses import dataclass

from pasi.core.memory import MemoryRecord
from pasi.core.memory_store import SQLiteMemoryStore


class RetrievalError(ValueError):
    """Raised when retrieval inputs or scope constraints are invalid."""


@dataclass(frozen=True)
class RetrievedMemory:
    memory_id: str
    scope: str
    kind: str
    content: str
    provenance_refs: tuple[str, ...]
    score: float
    matched_terms: tuple[str, ...]


class ProvenanceAwareRetriever:
    """Deterministic lexical retrieval over authoritative scoped project memory."""

    MAX_RESULTS = 20
    MAX_QUERY_CHARS = 512
    STOPWORDS = frozenset(
        {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "into",
            "must",
            "task",
            "project",
        }
    )

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self.store = store

    def search(
        self,
        query: str,
        *,
        scope: str,
        kind: str | None = None,
        limit: int = 10,
    ) -> tuple[RetrievedMemory, ...]:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalError("query is required")
        if len(query) > self.MAX_QUERY_CHARS:
            raise RetrievalError("query exceeds configured size bound")
        if not isinstance(scope, str) or not scope.strip():
            raise RetrievalError("scope is required")
        if limit <= 0 or limit > self.MAX_RESULTS:
            raise RetrievalError(
                f"limit must be between 1 and {self.MAX_RESULTS}"
            )

        query_terms = self._terms(query)
        if not query_terms:
            raise RetrievalError("query contains no searchable terms")

        candidates = self.store.list(scope=scope.strip(), kind=kind)
        results: list[RetrievedMemory] = []
        for memory in candidates:
            if not memory.provenance_refs:
                continue
            memory_terms = self._terms(memory.content)
            matches = tuple(sorted(query_terms & memory_terms))
            if not matches:
                continue
            score = len(matches) / max(1, len(query_terms))
            results.append(
                RetrievedMemory(
                    memory_id=memory.memory_id,
                    scope=memory.scope,
                    kind=memory.kind,
                    content=memory.content,
                    provenance_refs=memory.provenance_refs,
                    score=score,
                    matched_terms=matches,
                )
            )

        results.sort(key=lambda item: (-item.score, item.memory_id))
        return tuple(results[:limit])

    @classmethod
    def _terms(cls, value: str) -> set[str]:
        tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_'-]+", value)
            if len(token) >= 2
        }
        return tokens - cls.STOPWORDS
