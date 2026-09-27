from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


MAX_ID_CHARS = 256
MAX_PROVIDER_CHARS = 128
MAX_BRANCH_CHARS = 256


class InvalidLedgerEntry(ValueError):
    """Raised when operation lineage metadata is malformed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: object, *, name: str, limit: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidLedgerEntry(f"{name} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise InvalidLedgerEntry(f"{name} must not be empty")
    if len(normalized) > limit:
        raise InvalidLedgerEntry(f"{name} exceeds {limit} characters")
    if any(ord(char) < 32 and char not in "\t" for char in normalized):
        raise InvalidLedgerEntry(f"{name} contains control characters")
    return normalized


@dataclass(frozen=True)
class OperationLedgerEntry:
    """Durable operation-to-task/run lineage projection."""

    operation_id: str
    task_id: str
    run_id: str
    parent_operation_id: str = ""
    provider: str = ""
    branch: str = ""
    pr_number: int | None = None
    outcome: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _bounded(self.operation_id, name="operation_id", limit=MAX_ID_CHARS, required=True)
        _bounded(self.task_id, name="task_id", limit=MAX_ID_CHARS, required=True)
        _bounded(self.run_id, name="run_id", limit=MAX_ID_CHARS, required=True)
        _bounded(self.parent_operation_id, name="parent_operation_id", limit=MAX_ID_CHARS)
        _bounded(self.provider, name="provider", limit=MAX_PROVIDER_CHARS)
        _bounded(self.branch, name="branch", limit=MAX_BRANCH_CHARS)
        _bounded(self.outcome, name="outcome", limit=128)
        if self.pr_number is not None and (not isinstance(self.pr_number, int) or self.pr_number <= 0):
            raise InvalidLedgerEntry("pr_number must be a positive integer or null")
        if self.created_at == "":
            object.__setattr__(self, "created_at", utc_now())
        if self.updated_at == "":
            object.__setattr__(self, "updated_at", self.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "parent_operation_id": self.parent_operation_id,
            "provider": self.provider,
            "branch": self.branch,
            "pr_number": self.pr_number,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
