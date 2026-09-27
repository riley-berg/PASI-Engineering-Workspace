from pathlib import Path

import pytest

from pasi.core.cross_run_learning import (
    CrossRunLearning,
    LearningPromotionError,
    LearningProposal,
)
from pasi.core.memory import MemoryRecord
from pasi.core.memory_store import SQLiteMemoryStore


def source(
    memory_id: str,
    *,
    confidence: float = 0.95,
    operation_id: str = "op-1",
    status: str = "active",
) -> MemoryRecord:
    from pasi.core.memory import MemoryStatus

    return MemoryRecord(
        memory_id=memory_id,
        scope="task:P2.9",
        kind="decision",
        content="Reusable fact.",
        provenance_refs=(f"event://{memory_id}",),
        source_operation_id=operation_id,
        confidence=confidence,
        status=MemoryStatus(status),
    )


def test_cross_run_learning_promotes_only_evidence_backed_memory(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(source("mem-1"))
    store.create(source("mem-2", confidence=0.9, operation_id="op-2"))

    record = CrossRunLearning(store).promote(
        LearningProposal(
            source_memory_ids=("mem-1", "mem-2"),
            target_scope="project:pasi",
            reusable_kind="learning",
            content="Reusable fact promoted after two verified runs.",
        )
    )
    assert record.scope == "project:pasi"
    assert record.source_operation_id == "op-1,op-2"
    assert record.confidence == 0.9
    assert set(record.provenance_refs) == {"event://mem-1", "event://mem-2"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"confidence": 0.5},
        {"operation_id": ""},
        {"status": "archived"},
    ],
)
def test_cross_run_learning_rejects_weak_sources(tmp_path: Path, kwargs):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(source("mem-bad", **kwargs))

    with pytest.raises(LearningPromotionError):
        CrossRunLearning(store).promote(
            LearningProposal(
                source_memory_ids=("mem-bad",),
                target_scope="project:pasi",
                reusable_kind="learning",
                content="Should not promote.",
            )
        )


def test_learning_identity_is_deterministic(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(source("mem-1"))

    service = CrossRunLearning(store)
    proposal = LearningProposal(
        source_memory_ids=("mem-1",),
        target_scope="project:pasi",
        reusable_kind="learning",
        content="Stable learning.",
    )
    first = service.promote(proposal)
    second = service.promote(proposal)
    assert first == second


def test_learning_duplicate_collision_is_safe(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(source("mem-1"))
    service = CrossRunLearning(store)
    proposal = LearningProposal(
        source_memory_ids=("mem-1",),
        target_scope="project:pasi",
        reusable_kind="learning",
        content="Stable learning.",
    )
    first = service.promote(proposal)
    second = service.promote(proposal)
    assert second.memory_id == first.memory_id
