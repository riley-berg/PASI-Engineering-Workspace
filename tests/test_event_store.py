from pathlib import Path

import pytest

from pasi.core.event_store import DuplicateEvent, EventNotFound, SQLiteEventStore
from pasi.core.events import DurableEvent, InvalidEvent, payload_digest


def make_event(event_id: str, *, sequence: int | None = None) -> DurableEvent:
    return DurableEvent(
        event_id=event_id,
        event_type="operation.completed",
        source="runner",
        operation_id="op-1",
        task_id="P1.2",
        run_id="run-1",
        correlation_id="corr-1",
        causation_id="cause-1",
        payload={"status": "completed", "sequence": sequence or 0},
        evidence_refs=("evidence://run-1/op-1",),
    )


def test_event_is_immutable_and_payload_digest_is_canonical():
    event = make_event("event-1")
    assert event.payload_sha256 == payload_digest(event.payload)
    with pytest.raises(Exception):
        event.status = "changed"


def test_event_rejects_payload_digest_tampering():
    with pytest.raises(InvalidEvent):
        DurableEvent(
            event_id="event-1",
            event_type="operation.completed",
            source="runner",
            payload={"status": "completed"},
            payload_sha256="0" * 64,
        )


def test_sqlite_event_store_is_append_only_and_ordered(tmp_path: Path):
    path = tmp_path / "events.db"
    store = SQLiteEventStore(path)

    first = store.append(make_event("event-1"))
    second = store.append(
        DurableEvent(
            event_id="event-2",
            event_type="verification.completed",
            source="verifier",
            operation_id="op-1",
            task_id="P1.2",
            run_id="run-1",
            correlation_id="corr-1",
            causation_id="event-1",
            payload={"status": "PASS"},
            evidence_refs=("evidence://run-1/op-1/verification",),
        )
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert [event.event_id for event in store.list(operation_id="op-1")] == [
        "event-1",
        "event-2",
    ]

    restarted = SQLiteEventStore(path)
    assert restarted.get("event-1").sequence == 1
    assert restarted.get("event-2").causation_id == "event-1"

    with pytest.raises(DuplicateEvent):
        restarted.append(make_event("event-1"))

    with pytest.raises(EventNotFound):
        restarted.get("missing")


def test_event_queries_are_scoped_and_bounded(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.db")
    store.append(make_event("event-1"))
    store.append(
        DurableEvent(
            event_id="event-2",
            event_type="task.completed",
            source="planner",
            task_id="other",
            run_id="run-2",
            correlation_id="corr-2",
            payload={"status": "completed"},
        )
    )

    assert [event.event_id for event in store.list(correlation_id="corr-1")] == ["event-1"]

    with pytest.raises(ValueError):
        store.list(limit=0)

    with pytest.raises(ValueError):
        store.list(limit=10_001)
