from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


EVENT_SCHEMA_VERSION = 1
MAX_EVENT_ID_CHARS = 256
MAX_OPERATION_ID_CHARS = 256
MAX_TASK_ID_CHARS = 256
MAX_RUN_ID_CHARS = 256
MAX_CORRELATION_ID_CHARS = 256
MAX_CAUSATION_ID_CHARS = 256
MAX_SOURCE_CHARS = 128
MAX_EVENT_TYPE_CHARS = 128
MAX_EVIDENCE_REF_CHARS = 512
MAX_PAYLOAD_BYTES = 256 * 1024


class InvalidEvent(ValueError):
    """Raised when a durable event is malformed or exceeds policy bounds."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bounded_string(
    value: object,
    *,
    name: str,
    limit: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InvalidEvent(f"{name} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise InvalidEvent(f"{name} must not be empty")
    if len(normalized) > limit:
        raise InvalidEvent(f"{name} exceeds {limit} characters")
    if any(ord(char) < 32 and char not in "\t" for char in normalized):
        raise InvalidEvent(f"{name} contains control characters")
    return normalized


def canonical_json(value: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidEvent("payload must be JSON serializable") from exc
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise InvalidEvent(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    return payload


def payload_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DurableEvent:
    """Immutable, versioned event record for the PASI control plane."""

    event_id: str
    event_type: str
    source: str
    operation_id: str = ""
    task_id: str = ""
    run_id: str = ""
    correlation_id: str = ""
    causation_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    occurred_at: str = field(default_factory=utc_now)
    schema_version: int = EVENT_SCHEMA_VERSION
    payload_sha256: str = ""
    sequence: int | None = None

    def __post_init__(self) -> None:
        bounded_string(
            self.event_id,
            name="event_id",
            limit=MAX_EVENT_ID_CHARS,
        )
        bounded_string(
            self.event_type,
            name="event_type",
            limit=MAX_EVENT_TYPE_CHARS,
        )
        bounded_string(
            self.source,
            name="source",
            limit=MAX_SOURCE_CHARS,
        )
        for name, value, limit in (
            ("operation_id", self.operation_id, MAX_OPERATION_ID_CHARS),
            ("task_id", self.task_id, MAX_TASK_ID_CHARS),
            ("run_id", self.run_id, MAX_RUN_ID_CHARS),
            ("correlation_id", self.correlation_id, MAX_CORRELATION_ID_CHARS),
            ("causation_id", self.causation_id, MAX_CAUSATION_ID_CHARS),
        ):
            bounded_string(value, name=name, limit=limit, allow_empty=True)

        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise InvalidEvent(
                f"unsupported event schema version: {self.schema_version!r}"
            )
        if self.sequence is not None and self.sequence <= 0:
            raise InvalidEvent("sequence must be positive when present")

        if not isinstance(self.payload, dict):
            raise InvalidEvent("payload must be an object")
        calculated_digest = payload_digest(self.payload)
        if self.payload_sha256 and self.payload_sha256 != calculated_digest:
            raise InvalidEvent("payload_sha256 does not match payload")
        object.__setattr__(self, "payload_sha256", calculated_digest)

        if not isinstance(self.evidence_refs, tuple):
            object.__setattr__(
                self,
                "evidence_refs",
                tuple(self.evidence_refs),
            )
        for ref in self.evidence_refs:
            bounded_string(
                ref,
                name="evidence_ref",
                limit=MAX_EVIDENCE_REF_CHARS,
            )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_refs"] = list(self.evidence_refs)
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DurableEvent":
        if not isinstance(value, Mapping):
            raise InvalidEvent("event must be a mapping")
        refs = value.get("evidence_refs", [])
        if not isinstance(refs, (list, tuple)):
            raise InvalidEvent("evidence_refs must be an array")
        payload = value.get("payload", {})
        if not isinstance(payload, dict):
            raise InvalidEvent("payload must be an object")
        return cls(
            event_id=value.get("event_id", ""),
            event_type=value.get("event_type", ""),
            source=value.get("source", ""),
            operation_id=value.get("operation_id", ""),
            task_id=value.get("task_id", ""),
            run_id=value.get("run_id", ""),
            correlation_id=value.get("correlation_id", ""),
            causation_id=value.get("causation_id", ""),
            payload=dict(payload),
            evidence_refs=tuple(str(ref) for ref in refs),
            occurred_at=value.get("occurred_at", utc_now()),
            schema_version=value.get("schema_version", EVENT_SCHEMA_VERSION),
            payload_sha256=value.get("payload_sha256", ""),
            sequence=value.get("sequence"),
        )
