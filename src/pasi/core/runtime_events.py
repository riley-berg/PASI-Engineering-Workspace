from __future__ import annotations

from typing import Any

from pasi.core.event_store import SQLiteEventStore


class RuntimeEventFeed:
    """Ordered runtime-event and evidence feed for UI consumers."""

    def __init__(self, event_store: SQLiteEventStore) -> None:
        self.event_store = event_store

    def for_operation(
        self,
        operation_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        events = self.event_store.list(operation_id=operation_id, limit=limit)
        evidence: list[str] = []
        for event in events:
            for ref in event.evidence_refs:
                if ref not in evidence:
                    evidence.append(ref)

        return {
            "operation_id": operation_id,
            "events": [event.to_dict() for event in events],
            "evidence_refs": evidence,
            "event_count": len(events),
        }
