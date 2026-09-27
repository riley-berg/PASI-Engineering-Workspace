from pathlib import Path

from pasi.core.memory import MemoryRecord, MemoryStatus
from pasi.core.memory_compaction import MemoryCompactor
from pasi.core.memory_store import SQLiteMemoryStore


def make_record(
    memory_id: str,
    content: str,
    refs: tuple[str, ...],
    confidence: float,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        scope="project:pasi",
        kind="decision",
        content=content,
        provenance_refs=refs,
        confidence=confidence,
    )


def test_compaction_unions_provenance_and_archives_duplicates(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(
        make_record("mem-a", "Same fact.", ("event://a",), 0.8)
    )
    store.create(
        make_record("mem-b", " same   fact ", ("event://b",), 0.9)
    )
    store.create(
        make_record("mem-c", "Same fact.", ("event://c",), 0.7)
    )

    records = store.list(scope="project:pasi")
    plan = MemoryCompactor().plan(records)
    assert len(plan) == 1
    assert plan[0].canonical_id == "mem-b"
    assert plan[0].redundant_ids == ("mem-a", "mem-c")
    assert plan[0].merged_provenance_refs == ("event://a", "event://b", "event://c")

    updated = MemoryCompactor().apply(store, plan[0])
    assert updated.provenance_refs == ("event://a", "event://b", "event://c")
    assert store.get("mem-a").status is MemoryStatus.ARCHIVED
    assert store.get("mem-c").status is MemoryStatus.ARCHIVED
    assert store.get("mem-b").status is MemoryStatus.ACTIVE


def test_compaction_does_not_group_distinct_content(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(make_record("a", "one", ("event://a",), 0.5))
    store.create(make_record("b", "two", ("event://b",), 0.5))
    assert MemoryCompactor().plan(store.list(scope="project:pasi")) == ()
