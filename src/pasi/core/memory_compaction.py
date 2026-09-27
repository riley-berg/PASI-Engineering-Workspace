from __future__ import annotations

import re
from dataclasses import dataclass

from pasi.core.memory import MemoryRecord
from pasi.core.memory_store import SQLiteMemoryStore


class CompactionError(ValueError):
    """Raised when memory compaction cannot preserve provenance safely."""


@dataclass(frozen=True)
class CompactionGroup:
    scope: str
    kind: str
    canonical_id: str
    redundant_ids: tuple[str, ...]
    merged_provenance_refs: tuple[str, ...]


class MemoryCompactor:
    """Build lossless duplicate-compaction plans for active project memory."""

    def plan(self, records: tuple[MemoryRecord, ...]) -> tuple[CompactionGroup, ...]:
        groups: dict[tuple[str, str, str], list[MemoryRecord]] = {}
        for record in records:
            if record.status.value != "active":
                continue
            normalized = self._normalize(record.content)
            groups.setdefault((record.scope, record.kind, normalized), []).append(record)

        plans: list[CompactionGroup] = []
        for (scope, kind, _), members in sorted(groups.items()):
            if len(members) < 2:
                continue
            ordered = sorted(
                members,
                key=lambda record: (-float(record.confidence), record.memory_id),
            )
            canonical = ordered[0]
            redundant = tuple(sorted(record.memory_id for record in ordered[1:]))
            refs = tuple(
                sorted(
                    {
                        ref
                        for record in members
                        for ref in record.provenance_refs
                    }
                )
            )
            if not refs:
                raise CompactionError(
                    f"compaction group {canonical.memory_id} has no provenance"
                )
            plans.append(
                CompactionGroup(
                    scope=scope,
                    kind=kind,
                    canonical_id=canonical.memory_id,
                    redundant_ids=redundant,
                    merged_provenance_refs=refs,
                )
            )
        return tuple(plans)

    def apply(
        self,
        store: SQLiteMemoryStore,
        plan: CompactionGroup,
    ) -> MemoryRecord:
        canonical = store.get(plan.canonical_id)
        if canonical.scope != plan.scope or canonical.kind != plan.kind:
            raise CompactionError("canonical memory no longer matches compaction scope")
        for memory_id in plan.redundant_ids:
            redundant = store.get(memory_id)
            if redundant.scope != plan.scope or redundant.kind != plan.kind:
                raise CompactionError(
                    f"redundant memory {memory_id} moved to a different scope"
                )
            if self._normalize(redundant.content) != self._normalize(canonical.content):
                raise CompactionError(
                    f"redundant memory {memory_id} content no longer matches canonical record"
                )

        updated = MemoryRecord(
            memory_id=canonical.memory_id,
            scope=canonical.scope,
            kind=canonical.kind,
            content=canonical.content,
            provenance_refs=plan.merged_provenance_refs,
            source_operation_id=canonical.source_operation_id,
            confidence=max(float(canonical.confidence), 0.0),
            status=canonical.status,
            revision=canonical.revision + 1,
            created_at=canonical.created_at,
            updated_at=canonical.updated_at,
        )
        store.update(updated, expected_revision=canonical.revision)

        for memory_id in plan.redundant_ids:
            redundant = store.get(memory_id)
            store.archive(memory_id, expected_revision=redundant.revision)

        return updated

    @staticmethod
    def _normalize(content: str) -> str:
        return re.sub(r"\s+", " ", content.strip()).casefold()
