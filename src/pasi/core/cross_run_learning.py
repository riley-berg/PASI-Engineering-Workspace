from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pasi.core.memory import MemoryError, MemoryRecord
from pasi.core.memory_store import DuplicateMemory, SQLiteMemoryStore


class LearningPromotionError(MemoryError):
    """Raised when reusable knowledge lacks sufficient evidence."""


@dataclass(frozen=True)
class LearningProposal:
    source_memory_ids: tuple[str, ...]
    target_scope: str
    reusable_kind: str
    content: str
    minimum_confidence: float = 0.8


class CrossRunLearning:
    """Promote only evidence-backed active memory into reusable project knowledge."""

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self.store = store

    def promote(self, proposal: LearningProposal) -> MemoryRecord:
        if not proposal.source_memory_ids:
            raise LearningPromotionError("at least one source memory is required")
        if not proposal.target_scope.strip():
            raise LearningPromotionError("target_scope is required")
        if not proposal.content.strip():
            raise LearningPromotionError("promoted content is required")
        if not 0.0 <= proposal.minimum_confidence <= 1.0:
            raise LearningPromotionError("minimum_confidence must be between 0 and 1")

        sources = [self.store.get(memory_id) for memory_id in proposal.source_memory_ids]
        for source in sources:
            if source.status.value != "active":
                raise LearningPromotionError(
                    f"source memory {source.memory_id} is not active"
                )
            if not source.provenance_refs:
                raise LearningPromotionError(
                    f"source memory {source.memory_id} has no provenance"
                )
            if not source.source_operation_id:
                raise LearningPromotionError(
                    f"source memory {source.memory_id} lacks operation provenance"
                )
            if float(source.confidence) < proposal.minimum_confidence:
                raise LearningPromotionError(
                    f"source memory {source.memory_id} confidence is below promotion threshold"
                )

        confidence = min(float(source.confidence) for source in sources)
        refs = tuple(
            sorted(
                {
                    ref
                    for source in sources
                    for ref in source.provenance_refs
                }
            )
        )
        source_ops = tuple(
            sorted({source.source_operation_id for source in sources})
        )

        digest = hashlib.sha256(
            "|".join(
                (
                    proposal.target_scope.strip(),
                    proposal.reusable_kind.strip(),
                    proposal.content.strip(),
                    *proposal.source_memory_ids,
                )
            ).encode("utf-8")
        ).hexdigest()

        memory_id = f"learn-{digest[:32]}"
        record = MemoryRecord(
            memory_id=memory_id,
            scope=proposal.target_scope,
            kind=proposal.reusable_kind,
            content=proposal.content,
            provenance_refs=refs,
            source_operation_id=",".join(source_ops),
            confidence=confidence,
        )
        try:
            return self.store.create(record)
        except DuplicateMemory:
            existing = self.store.get(memory_id)
            if (
                existing.scope != record.scope
                or existing.content != record.content
                or existing.provenance_refs != record.provenance_refs
            ):
                raise LearningPromotionError(
                    f"deterministic learning id collided with different content: {memory_id}"
                ) from exc
            return existing
