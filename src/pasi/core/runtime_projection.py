from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pasi.core.event_store import SQLiteEventStore
from pasi.core.ledger_store import LedgerEntryNotFound, SQLiteOperationLedger
from pasi.core.operation_store import OperationStateNotFound, SQLiteOperationStateStore
from pasi.core.runtime_health import RuntimeHealthStore


@dataclass(frozen=True)
class RuntimeIdentity:
    code_head: str
    runtime_version: str
    controller_version: str
    runner_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code_head": self.code_head,
            "runtime_version": self.runtime_version,
            "controller_version": self.controller_version,
            "runner_version": self.runner_version,
        }


class RuntimeProjectionService:
    """Build the canonical UI rehydration view from durable control-plane sources."""

    def __init__(
        self,
        *,
        operation_store: SQLiteOperationStateStore,
        event_store: SQLiteEventStore,
        ledger: SQLiteOperationLedger,
        health_store: RuntimeHealthStore,
        identity: RuntimeIdentity,
    ) -> None:
        self.operation_store = operation_store
        self.event_store = event_store
        self.ledger = ledger
        self.health_store = health_store
        self.identity = identity

    def project(self, operation_id: str, *, event_limit: int = 100) -> dict[str, Any]:
        state = self.operation_store.get(operation_id)
        events = self.event_store.list(operation_id=operation_id, limit=event_limit)

        try:
            lineage = self.ledger.lineage(operation_id)
        except LedgerEntryNotFound:
            lineage = ()

        evidence_refs: list[str] = []
        for event in events:
            for ref in event.evidence_refs:
                if ref not in evidence_refs:
                    evidence_refs.append(ref)

        health = self.health_store.get()
        return {
            "operation": state.to_dict(),
            "lineage": [entry.to_dict() for entry in lineage],
            "events": [event.to_dict() for event in events],
            "evidence_refs": evidence_refs,
            "runtime": self.identity.to_dict(),
            "health": health.to_dict(),
        }

    def project_or_none(self, operation_id: str) -> dict[str, Any] | None:
        try:
            return self.project(operation_id)
        except OperationStateNotFound:
            return None
