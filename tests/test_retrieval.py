from pathlib import Path

import pytest

from pasi.core.memory import MemoryRecord
from pasi.core.memory_store import SQLiteMemoryStore
from pasi.core.retrieval import ProvenanceAwareRetriever, RetrievalError


def make_store(path: Path) -> SQLiteMemoryStore:
    store = SQLiteMemoryStore(path)
    store.create(
        MemoryRecord(
            memory_id="mem-1",
            scope="project:pasi",
            kind="decision",
            content="Roadmap completion requires durable evidence and little human repair.",
            provenance_refs=("event://1",),
        )
    )
    store.create(
        MemoryRecord(
            memory_id="mem-2",
            scope="project:pasi",
            kind="constraint",
            content="Tests alone do not constitute completion evidence.",
            provenance_refs=("event://2",),
        )
    )
    store.create(
        MemoryRecord(
            memory_id="mem-3",
            scope="other",
            kind="decision",
            content="Roadmap evidence from another project.",
            provenance_refs=("event://3",),
        )
    )
    return store


def test_retrieval_is_scoped_and_carries_provenance(tmp_path: Path):
    retriever = ProvenanceAwareRetriever(make_store(tmp_path / "memory.db"))
    results = retriever.search(
        "completion evidence",
        scope="project:pasi",
        limit=10,
    )
    assert [result.memory_id for result in results] == ["mem-1", "mem-2"]
    assert all(result.provenance_refs for result in results)
    assert all(result.scope == "project:pasi" for result in results)


def test_retrieval_is_deterministic_and_kind_filterable(tmp_path: Path):
    retriever = ProvenanceAwareRetriever(make_store(tmp_path / "memory.db"))
    first = retriever.search("roadmap evidence", scope="project:pasi")
    second = retriever.search("roadmap evidence", scope="project:pasi")
    assert first == second
    assert [item.memory_id for item in retriever.search(
        "evidence", scope="project:pasi", kind="constraint"
    )] == ["mem-2"]


@pytest.mark.parametrize(
    ("query", "scope", "limit"),
    [
        ("", "project:pasi", 10),
        ("evidence", "", 10),
        ("evidence", "project:pasi", 0),
        ("evidence", "project:pasi", 21),
    ],
)
def test_retrieval_rejects_invalid_bounds(query, scope, limit, tmp_path: Path):
    retriever = ProvenanceAwareRetriever(make_store(tmp_path / "memory.db"))
    with pytest.raises(RetrievalError):
        retriever.search(query, scope=scope, limit=limit)
