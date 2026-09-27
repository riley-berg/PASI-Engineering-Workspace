from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence

from pasi.core.roadmap import RoadmapTask
from pasi.core.retrieval import ProvenanceAwareRetriever


CONTEXT_COMPILER_VERSION = 1
MAX_CONTEXT_CHARS = 32_000
MAX_SOURCE_CHARS = 8_000


class ContextCompilationError(ValueError):
    """Raised when context inputs violate deterministic compilation bounds."""


@dataclass(frozen=True)
class ContextSource:
    source_id: str
    kind: str
    text: str
    provenance_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ContextCompilationError("context source id is required")
        if not self.kind.strip():
            raise ContextCompilationError("context source kind is required")
        if not self.text.strip():
            raise ContextCompilationError(
                f"context source {self.source_id} has empty text"
            )
        if len(self.text) > MAX_SOURCE_CHARS:
            raise ContextCompilationError(
                f"context source {self.source_id} exceeds {MAX_SOURCE_CHARS} characters"
            )
        if not self.provenance_refs or any(
            not ref.strip() for ref in self.provenance_refs
        ):
            raise ContextCompilationError(
                f"context source {self.source_id} lacks valid provenance"
            )


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
    """Compile deterministic task context from roadmap and provenanced memory."""

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
        normalized_scope = scope.strip()
        if not normalized_scope:
            raise ContextCompilationError("scope is required")
        if max_chars <= 0 or max_chars > MAX_CONTEXT_CHARS:
            raise ContextCompilationError(
                f"max_chars must be between 1 and {MAX_CONTEXT_CHARS}"
            )

        sources: list[ContextSource] = [
            ContextSource(
                source_id=f"task:{task.id}",
                kind="roadmap_task",
                text=(
                    f"Task: {task.title}\n"
                    f"Acceptance:\n- {'\n- '.join(task.acceptance_requirements)}\n"
                    f"Evidence:\n- {'\n- '.join(task.evidence_requirements)}"
                ),
                provenance_refs=(f"roadmap://task/{task.id}",),
            )
        ]

        sources.extend(sorted(extra_sources, key=lambda item: item.source_id))

        if query is not None and query.strip():
            retrieved = self.retriever.search(
                query.strip(),
                scope=normalized_scope,
                limit=20,
            )
            sources.extend(
                ContextSource(
                    source_id=f"memory:{memory.memory_id}",
                    kind=memory.kind,
                    text=memory.content,
                    provenance_refs=memory.provenance_refs,
                )
                for memory in retrieved
            )

        deduped: dict[str, ContextSource] = {}
        for source in sources:
            existing = deduped.get(source.source_id)
            if existing is not None and existing != source:
                raise ContextCompilationError(
                    f"duplicate source id with conflicting content: {source.source_id}"
                )
            deduped[source.source_id] = source

        ordered = tuple(sorted(deduped.values(), key=lambda item: item.source_id))
        context_parts = [
            f"[{source.source_id}]\n"
            f"{source.text}\n"
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
                "scope": normalized_scope,
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
            scope=normalized_scope,
            sources=ordered,
            context_text=context_text,
            source_ids=source_ids,
            provenance_refs=provenance_refs,
            sha256=digest,
        )
