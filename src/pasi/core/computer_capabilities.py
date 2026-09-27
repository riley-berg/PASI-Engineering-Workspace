from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CapabilityError(ValueError):
    """Raised when a computer capability violates the registry contract."""


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    version: int
    operation: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    authorization: str
    side_effect: str
    resource_limits: dict[str, float]
    scope: str

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise CapabilityError("capability_id is required")
        if self.version <= 0:
            raise CapabilityError("version must be positive")
        if not self.operation.strip():
            raise CapabilityError("operation is required")
        if not isinstance(self.input_schema, dict) or not isinstance(self.output_schema, dict):
            raise CapabilityError("input_schema and output_schema must be objects")
        if self.authorization not in {"none", "operator", "approval"}:
            raise CapabilityError("invalid authorization class")
        if self.side_effect not in {"read", "write", "external_write"}:
            raise CapabilityError("invalid side-effect classification")
        if not self.scope.strip():
            raise CapabilityError("scope is required")
        for name, value in self.resource_limits.items():
            if not isinstance(name, str) or not name.strip():
                raise CapabilityError("resource limit names must be non-empty strings")
            if not isinstance(value, (int, float)) or value < 0:
                raise CapabilityError("resource limits must be finite non-negative numbers")
        if self.side_effect != "read" and self.authorization == "none":
            raise CapabilityError("mutating capabilities require authorization")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "operation": self.operation,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "authorization": self.authorization,
            "side_effect": self.side_effect,
            "resource_limits": self.resource_limits,
            "scope": self.scope,
        }

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SQLiteCapabilityRegistry:
    """Durable, digestable computer-capability registry."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS capability (
                    capability_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    digest TEXT NOT NULL
                )
                """
            )

    def register(self, descriptor: CapabilityDescriptor) -> CapabilityDescriptor:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version, payload_json FROM capability WHERE capability_id = ?",
                (descriptor.capability_id,),
            ).fetchone()
            if row is not None:
                existing = json.loads(row["payload_json"])
                if int(row["version"]) == descriptor.version and existing == descriptor.to_dict():
                    return descriptor
                raise CapabilityError(
                    f"conflicting capability registration: {descriptor.capability_id}"
                )
            connection.execute(
                """
                INSERT INTO capability(capability_id, version, payload_json, digest)
                VALUES (?, ?, ?, ?)
                """,
                (
                    descriptor.capability_id,
                    descriptor.version,
                    json.dumps(descriptor.to_dict(), sort_keys=True, separators=(",", ":")),
                    descriptor.digest,
                ),
            )
        return descriptor

    def get(self, capability_id: str) -> CapabilityDescriptor:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capability WHERE capability_id = ?",
                (capability_id,),
            ).fetchone()
        if row is None:
            raise KeyError(capability_id)
        return CapabilityDescriptor(**json.loads(row["payload_json"]))

    def list(self) -> tuple[CapabilityDescriptor, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM capability ORDER BY capability_id, version"
            ).fetchall()
        return tuple(
            CapabilityDescriptor(**json.loads(row["payload_json"]))
            for row in rows
        )

    def digest(self) -> str:
        payload = [item.to_dict() for item in self.list()]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
