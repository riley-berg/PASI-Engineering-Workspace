from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class MemoryError(ValueError):
    """Raised when a project-memory record violates provenance or scope policy."""


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    scope: str
    kind: str
    content: str
    provenance_refs: tuple[str, ...]
    source_operation_id: str = ""
    confidence: float = 1.0
    status: MemoryStatus = MemoryStatus.ACTIVE
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.memory_id.strip():
            raise MemoryError("memory_id is required")
        if not self.scope.strip():
            raise MemoryError("scope is required")
        if not self.kind.strip():
            raise MemoryError("kind is required")
        if not self.content.strip():
            raise MemoryError("content is required")
        if not self.provenance_refs:
            raise MemoryError("memory requires provenance_refs")
        if any(not ref.strip() for ref in self.provenance_refs):
            raise MemoryError("memory provenance refs must be non-empty")
        if not isinstance(self.confidence, (int, float)) or not 0.0 <= float(self.confidence) <= 1.0:
            raise MemoryError("confidence must be between 0 and 1")
        if self.revision < 0:
            raise MemoryError("revision must be non-negative")
        if not self.created_at:
            object.__setattr__(self, "created_at", utc_now())
        if not self.updated_at:
            object.__setattr__(self, "updated_at", self.created_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope,
            "kind": self.kind,
            "content": self.content,
            "provenance_refs": list(self.provenance_refs),
            "source_operation_id": self.source_operation_id,
            "confidence": float(self.confidence),
            "status": self.status.value,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
