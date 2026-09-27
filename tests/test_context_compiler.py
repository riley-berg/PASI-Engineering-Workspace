import json
from pathlib import Path

import pytest

from pasi.core.context_compiler import ContextCompilationError, ContextCompiler, ContextSource
from pasi.core.memory import MemoryRecord
from pasi.core.memory_store import SQLiteMemoryStore
from pasi.core.retrieval import ProvenanceAwareRetriever
from pasi.core.roadmap import PhaseStatus, RoadmapTask


def make_task() -> RoadmapTask:
    return RoadmapTask(
        id="P2.7",
        title="Context compiler",
        phase_id="P2",
        acceptance_requirements=("context must be deterministic",),
        evidence_requirements=("compiled context digest",),
    )


def test_context_compiler_is_deterministic_and_provenance_linked(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.create(
        MemoryRecord(
            memory_id="mem-1",
            scope="project:pasi",
            kind="decision",
            content="Deterministic context requires stable ordering.",
            provenance_refs=("event://1",),
        )
    )
    compiler = ContextCompiler(ProvenanceAwareRetriever(store))
    first = compiler.compile(
        make_task(),
        scope="project:pasi",
        query="deterministic context",
        extra_sources=(
            ContextSource(
                "extra:b",
                "constraint",
                "No hidden manual repair.",
                ("event://2",),
            ),
            ContextSource(
                "extra:a",
                "constraint",
                "Tests alone are insufficient.",
                ("event://3",),
            ),
        ),
    )
    second = compiler.compile(
        make_task(),
        scope="project:pasi",
        query="deterministic context",
        extra_sources=(
            ContextSource(
                "extra:b",
                "constraint",
                "No hidden manual repair.",
                ("event://2",),
            ),
            ContextSource(
                "extra:a",
                "constraint",
                "Tests alone are insufficient.",
                ("event://3",),
            ),
        ),
    )
    assert first == second
    assert first.sha256
    assert first.provenance_refs == tuple(sorted(first.provenance_refs))
    assert all(source.provenance_refs for source in first.sources)


def test_context_compiler_rejects_unprovenanced_or_oversized_inputs(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    compiler = ContextCompiler(ProvenanceAwareRetriever(store))

    with pytest.raises(ContextCompilationError):
        compiler.compile(
            make_task(),
            scope="project:pasi",
            extra_sources=(ContextSource("bad", "note", "unproven", ()),),
        )

    with pytest.raises(ContextCompilationError):
        compiler.compile(
            make_task(),
            scope="project:pasi",
            extra_sources=(
                ContextSource("huge", "note", "x" * 100, ("event://huge",)),
            ),
            max_chars=50,
        )


def test_context_schema_is_present_and_versioned():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "compiled-context-v1.json")
        .read_text(encoding="utf-8")
    )
    assert schema["properties"]["compiler_version"]["const"] == 1
