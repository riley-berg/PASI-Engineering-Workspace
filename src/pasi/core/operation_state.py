from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

MAX_METADATA_KEYS = 64
MAX_METADATA_KEY_CHARS = 128
MAX_METADATA_VALUE_CHARS = 1024

OPERATION_STATE_SCHEMA_VERSION = 2
SUPPORTED_OPERATION_STATE_SCHEMA_VERSIONS = frozenset({1, 2})
OPERATION_STATUSES = frozenset({"queued", "claimed", "generating", "completed", "failed", "cancelled"})
MAX_ID_CHARS = 256
MAX_PROVIDER_CHARS = 128
MAX_PHASE_CHARS = 128
MAX_SIGNATURE_CHARS = 512

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"claimed", "failed", "cancelled"}),
    "claimed": frozenset({"generating", "failed", "cancelled"}),
    "generating": frozenset({"completed", "failed", "cancelled"}),
    "failed": frozenset({"claimed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


class InvalidOperationState(ValueError):
    """Raised when canonical operation state is malformed or unsafe to persist."""


class InvalidOperationTransition(ValueError):
    """Raised when an operation attempts an invalid lifecycle transition."""


class OperationRevisionConflict(ValueError):
    """Raised when a caller attempts to mutate a stale state revision."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_string(
    value: object,
    *,
    name: str,
    limit: int,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise InvalidOperationState(f"{name} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise InvalidOperationState(f"{name} must not be empty")
    if len(normalized) > limit:
        raise InvalidOperationState(f"{name} exceeds {limit} characters")
    if any(ord(char) < 32 and char not in "\\t" for char in normalized):
        raise InvalidOperationState(f"{name} contains control characters")
    return normalized


def digest_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("digest input must be a string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class OperationState:
    """Canonical versioned lifecycle record shared by PASI control-plane layers.

    ``schema_version`` identifies the persisted record shape. ``state_revision``
    identifies the specific lifecycle version of one operation and increases on
    every accepted transition. Full prompts/responses are represented by bounded
    SHA-256 digests so operation state cannot become a second transcript store.
    """

    operation_id: str
    operation_type: str
    status: str = "queued"
    schema_version: int = OPERATION_STATE_SCHEMA_VERSION
    state_revision: int = 0

    run_id: str = ""
    task_id: str = ""
    provider: str = ""
    phase: str = ""
    attempt: int = 0

    prompt_digest: str = ""
    response_digest: str = ""
    verification_status: str = ""
    commit_sha: str = ""
    pr_number: int | None = None
    failure_signature: str = ""
    recovery_count: int = 0
    metadata: dict[str, str] = field(default_factory=dict)

    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.operation_id = _bounded_string(
            self.operation_id, name="operation_id", limit=MAX_ID_CHARS, allow_empty=False
        )
        self.operation_type = _bounded_string(
            self.operation_type, name="operation_type", limit=MAX_ID_CHARS, allow_empty=False
        )
        self.status = _bounded_string(self.status, name="status", limit=64, allow_empty=False)
        if self.status not in OPERATION_STATUSES:
            raise InvalidOperationState(f"unsupported operation status: {self.status!r}")
        if self.schema_version not in SUPPORTED_OPERATION_STATE_SCHEMA_VERSIONS:
            raise InvalidOperationState(
                f"unsupported operation-state schema version: {self.schema_version!r}"
            )
        if not isinstance(self.metadata, dict):
            raise InvalidOperationState("metadata must be an object")
        if len(self.metadata) > MAX_METADATA_KEYS:
            raise InvalidOperationState(f"metadata exceeds {MAX_METADATA_KEYS} keys")
        normalized_metadata: dict[str, str] = {}
        for key, value in self.metadata.items():
            normalized_key = _bounded_string(
                key,
                name="metadata key",
                limit=MAX_METADATA_KEY_CHARS,
                allow_empty=False,
            )
            normalized_value = _bounded_string(
                value,
                name=f"metadata[{normalized_key}]",
                limit=MAX_METADATA_VALUE_CHARS,
            )
            normalized_metadata[normalized_key] = normalized_value
        self.metadata = normalized_metadata
        if self.schema_version == 1:
            self.schema_version = OPERATION_STATE_SCHEMA_VERSION
        if not isinstance(self.state_revision, int) or self.state_revision < 0:
            raise InvalidOperationState("state_revision must be a non-negative integer")

        self.run_id = _bounded_string(self.run_id, name="run_id", limit=MAX_ID_CHARS)
        self.task_id = _bounded_string(self.task_id, name="task_id", limit=MAX_ID_CHARS)
        self.provider = _bounded_string(self.provider, name="provider", limit=MAX_PROVIDER_CHARS)
        self.phase = _bounded_string(self.phase, name="phase", limit=MAX_PHASE_CHARS)

        if not isinstance(self.attempt, int) or self.attempt < 0:
            raise InvalidOperationState("attempt must be a non-negative integer")
        if not isinstance(self.recovery_count, int) or self.recovery_count < 0:
            raise InvalidOperationState("recovery_count must be a non-negative integer")
        if self.recovery_count > self.attempt:
            raise InvalidOperationState("recovery_count cannot exceed attempt")
        if self.pr_number is not None and (
            not isinstance(self.pr_number, int) or self.pr_number <= 0
        ):
            raise InvalidOperationState("pr_number must be a positive integer or null")

        for name, value in (("prompt_digest", self.prompt_digest), ("response_digest", self.response_digest)):
            _bounded_string(value, name=name, limit=128)
            if value and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
                raise InvalidOperationState(f"{name} must be a SHA-256 hex digest")

        self.verification_status = _bounded_string(self.verification_status, name="verification_status", limit=64)
        self.commit_sha = _bounded_string(self.commit_sha, name="commit_sha", limit=128)
        self.failure_signature = _bounded_string(self.failure_signature, name="failure_signature", limit=MAX_SIGNATURE_CHARS)
        self.created_at = _bounded_string(self.created_at, name="created_at", limit=128, allow_empty=False)
        self.updated_at = _bounded_string(self.updated_at, name="updated_at", limit=128, allow_empty=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OperationState":
        if not isinstance(value, Mapping):
            raise InvalidOperationState("operation state must be a mapping")
        payload = {
            "operation_id": value.get("operation_id", ""),
            "operation_type": value.get("operation_type", ""),
            "status": value.get("status", "queued"),
            "schema_version": value.get("schema_version", OPERATION_STATE_SCHEMA_VERSION),
            "state_revision": value.get("state_revision", 0),
            "run_id": value.get("run_id", ""),
            "task_id": value.get("task_id", ""),
            "provider": value.get("provider", ""),
            "phase": value.get("phase", ""),
            "attempt": value.get("attempt", 0),
            "prompt_digest": value.get("prompt_digest", ""),
            "response_digest": value.get("response_digest", ""),
            "verification_status": value.get("verification_status", ""),
            "commit_sha": value.get("commit_sha", ""),
            "pr_number": value.get("pr_number"),
            "failure_signature": value.get("failure_signature", ""),
            "recovery_count": value.get("recovery_count", 0),
            "metadata": value.get("metadata", {}),
            "created_at": value.get("created_at", utc_now()),
            "updated_at": value.get("updated_at", utc_now()),
        }
        return cls(**payload)

    def can_transition_to(self, status: str) -> bool:
        normalized = _bounded_string(status, name="status", limit=64, allow_empty=False)
        if normalized not in OPERATION_STATUSES:
            raise InvalidOperationTransition(f"unsupported operation status: {normalized!r}")
        return normalized in _ALLOWED_TRANSITIONS[self.status]

    def transition(self, status: str, *, expected_revision: int | None = None, phase: str | None = None, provider: str | None = None, prompt_digest: str | None = None, response_digest: str | None = None, verification_status: str | None = None, commit_sha: str | None = None, pr_number: int | None = None, failure_signature: str | None = None, metadata: dict[str, str] | None = None) -> "OperationState":
        if expected_revision is not None and expected_revision != self.state_revision:
            raise OperationRevisionConflict(f"expected revision {expected_revision}, current revision {self.state_revision}")
        if not self.can_transition_to(status):
            raise InvalidOperationTransition(f"{self.status!r} cannot transition to {status!r}")

        next_attempt = self.attempt
        next_recovery_count = self.recovery_count
        if self.status == "failed" and status == "claimed":
            next_attempt += 1
            next_recovery_count += 1

        updates: dict[str, Any] = {
            "status": status.strip(),
            "state_revision": self.state_revision + 1,
            "attempt": next_attempt,
            "recovery_count": next_recovery_count,
            "updated_at": utc_now(),
        }
        for name, value in (
            ("phase", phase), ("provider", provider), ("prompt_digest", prompt_digest),
            ("response_digest", response_digest), ("verification_status", verification_status),
            ("commit_sha", commit_sha), ("pr_number", pr_number), ("failure_signature", failure_signature), ("metadata", metadata),
        ):
            if value is not None:
                updates[name] = value
        return replace(self, schema_version=OPERATION_STATE_SCHEMA_VERSION, **updates)
