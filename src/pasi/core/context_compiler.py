from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Sequence

from pasi.core.memory import MemoryRecord
from pasi.core.roadmap import RoadmapTask
from pasi.core.retrieval import ProvenanceAwareRetriever, RetrievedMemory


CONTEXT_COMPILER_VERSION = 1
MAX_CONTEXT_CHARS = 32_000


class ContextCompilationError(ValueError):
    """Raised when context inputs violate deterministic compilation bounds."""


@dataclass(frozen=True)
class ContextSource:
    source_id: str
    kind: str
    text: str
    provenance_refs: tuple[str, ...]


@dataclass(frozen=True)
class CompiledContext:
    compiler_version: int
    task_id: str
    scope: str
    sources: tuple[ContextSource, ...]
    context_text: str
    source_ids: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "compiler_version": self.compiler_version,
            "task_id": self.task_id,
            "scope": self.scope,
            "sources": [
                {
                    "source_id": source.source_id,
                    "kind": source.kind,
                    "text": source.text,
                    "provenance_refs": list(source.provenance_refs),
                }
                for source in self.sources
            ],
            "context_text": self.context_text,
            "source_ids": list(self.source_ids),
            "provenance_refs": list(self.provenance_refs),
            "sha256": self.sha256,
        }


class ContextCompiler:
    """Compile deterministic task context from explicit roadmap + memory sources."""

    def __init__(self, retriever: ProvenanceAwareRetriever) -> None:
        self.retriever = retriever

    def compile(
        self,
        task: RoadmapTask,
        *,
        scope: str,
        extra_sources: Sequence[ContextSource] = (),
        query: str | None = None,
        max_chars: int = MAX_CONTEXT_CHARS,
    ) -> CompiledContext:
        if max_chars <= 0 or max_chars > MAX_CONTEXT_CHARS:
            raise ContextCompilationError(
                f"max_chars must be between 1 and {MAX_CONTEXT_CHARS}"
            )
        if not scope.strip():
            raise ContextCompilationError("scope is required")

        sources: list[ContextSource] = [
            ContextSource(
                source_id=f"task:{task.id}",
                kind="roadmap_task",
                text=(
                    f"Task: {task.title}
"
                    f"Acceptance:
- "
                    + "
- ".join(task.acceptance_requirements)
                    + "
Evidence:
- "
                    + "
- ".join(task.evidence_requirements)
                ),
                provenance_refs=(f"roadmap://task/{task.id}",),
            )
        ]

        for source in sorted(extra_sources, key=lambda item: item.source_id):
            if not source.text.strip():
                raise ContextCompilationError(
                    f"context source {source.source_id} has empty text"
                )
            if not source.provenance_refs:
                raise ContextCompilationError(
                    f"context source {source.source_id} lacks provenance"
                )
            sources.append(source)

        if query:
            retrieved = self.retriever.search(
                query,
                scope=scope,
                limit=20,
            )
            for memory in retrieved:
                sources.append(
                    ContextSource(
                        source_id=f"memory:{memory.memory_id}",
                        kind=memory.kind,
                        text=memory.content,
                        provenance_refs=memory.provenance_refs,
                    )
                )

        deduped: dict[str, ContextSource] = {}
        for source in sources:
            deduped[source.source_id] = source

        ordered = tuple(sorted(deduped.values(), key=lambda item: item.source_id))
        context_parts = [
            f"[{source.source_id}]\n{source.text}\n"
            f"PROVENANCE: {', '.join(sorted(source.provenance_refs))}"
            for source in ordered
        ]
        context_text = "\n\n".join(context_parts)

        if len(context_text) > max_chars:
            raise ContextCompilationError(
                f"compiled context exceeds {max_chars} characters"
            )

        source_ids = tuple(source.source_id for source in ordered)
        provenance_refs = tuple(
            sorted(
                {
                    ref
                    for source in ordered
                    for ref in source.provenance_refs
                }
            )
        )
        digest_payload = json.dumps(
            {
                "compiler_version": CONTEXT_COMPILER_VERSION,
                "task_id": task.id,
                "scope": scope,
                "source_ids": source_ids,
                "context_text": context_text,
                "provenance_refs": provenance_refs,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()

        return CompiledContext(
            compiler_version=CONTEXT_COMPILER_VERSION,
            task_id=task.id,
            scope=scope,
            sources=ordered,
            context_text=context_text,
            source_ids=source_ids,
            provenance_refs=provenance_refs,
            sha256=digest,
        )
