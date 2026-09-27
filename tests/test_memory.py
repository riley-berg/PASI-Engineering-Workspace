from pathlib import Path

import pytest

from pasi.core.memory import MemoryError, MemoryRecord, MemoryStatus
from pasi.core.memory_store import SQLiteMemoryStore, StaleMemoryRevision


def record() -> MemoryRecord:
    return MemoryRecord(
        memory_id="mem-1",
        scope="project:pasi",
        kind="decision",
        content="P2 roadmap selection is authoritative by dependency state.",
        provenance_refs=("evidence://op-1", "event://decision-1"),
        source_operation_id="op-1",
        confidence=0.95,
    )


def test_memory_requires_provenance_and_valid_confidence():
    with pytest.raises(MemoryError):
        MemoryRecord(
            memory_id="mem-1",
            scope="project:pasi",
            kind="note",
            content="unproven",
            provenance_refs=(),
        )

    with pytest.raises(MemoryError):
        MemoryRecord(
            memory_id="mem-2",
            scope="project:pasi",
            kind="note",
            content="bad confidence",
            provenance_refs=("evidence://2",),
            confidence=2.0,
        )


def test_memory_persists_updates_and_survives_restart(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    original = store.create(record())

    updated = MemoryRecord(
        memory_id=original.memory_id,
        scope=original.scope,
        kind=original.kind,
        content="Updated decision with additional evidence.",
        provenance_refs=original.provenance_refs + ("event://decision-2",),
        source_operation_id=original.source_operation_id,
        confidence=0.99,
        revision=1,
        created_at=original.created_at,
        updated_at=original.updated_at,
    )
    store.update(updated, expected_revision=0)

    restarted = SQLiteMemoryStore(tmp_path / "memory.db")
    assert restarted.get("mem-1") == updated

    with pytest.raises(StaleMemoryRevision):
        restarted.update(updated, expected_revision=0)

    archived = restarted.archive("mem-1", expected_revision=1)
    assert archived.status is MemoryStatus.ARCHIVED
    assert restarted.list(scope="project:pasi") == ()
    assert restarted.list(scope="project:pasi", include_archived=True) == (archived,)


def test_memory_scope_and_kind_queries_are_deterministic(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    first = record()
    second = MemoryRecord(
        memory_id="mem-2",
        scope="project:pasi",
        kind="constraint",
        content="No hidden manual state.",
        provenance_refs=("evidence://op-2",),
        source_operation_id="op-2",
    )
    other = MemoryRecord(
        memory_id="mem-3",
        scope="task:P2.5",
        kind="decision",
        content="Task-local evidence.",
        provenance_refs=("evidence://op-3",),
        source_operation_id="op-3",
    )
    store.create(first)
    store.create(second)
    store.create(other)

    assert [item.memory_id for item in store.list(scope="project:pasi")] == [
        "mem-2", "mem-1"
    ]
    assert [item.memory_id for item in store.list(scope="project:pasi", kind="constraint")] == [
        "mem-2"
    ]
