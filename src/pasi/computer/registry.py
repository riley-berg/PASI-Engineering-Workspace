from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

class CapabilityRegistryError(ValueError):
    pass

@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    version: int
    operations: tuple[str, ...]
    scope: str
    approval_class: str
    side_effect_class: str
    max_timeout_seconds: float
    max_output_bytes: int

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or self.version <= 0:
            raise CapabilityRegistryError("capability identity/version invalid")
        if not self.operations or any(not item.strip() for item in self.operations):
            raise CapabilityRegistryError("capability operations are required")
        if len(set(self.operations)) != len(self.operations):
            raise CapabilityRegistryError("capability operations contain duplicates")
        if not self.scope.strip():
            raise CapabilityRegistryError("capability scope is required")
        if self.approval_class not in {"none", "explicit"}:
            raise CapabilityRegistryError("unsupported approval class")
        if self.side_effect_class not in {"read", "write", "external"}:
            raise CapabilityRegistryError("unsupported side-effect class")
        if self.max_timeout_seconds <= 0 or self.max_timeout_seconds > 300:
            raise CapabilityRegistryError("timeout bound invalid")
        if self.max_output_bytes <= 0 or self.max_output_bytes > 10_000_000:
            raise CapabilityRegistryError("output bound invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "operations": list(self.operations),
            "scope": self.scope,
            "approval_class": self.approval_class,
            "side_effect_class": self.side_effect_class,
            "max_timeout_seconds": self.max_timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
        }

class SQLiteCapabilityRegistry:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS capability_registry (capability_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )

    def register(self, spec: CapabilitySpec) -> CapabilitySpec:
        payload = json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.path) as connection:
            try:
                connection.execute(
                    "INSERT INTO capability_registry(capability_id, payload_json) VALUES (?,?)",
                    (spec.capability_id, payload),
                )
            except sqlite3.IntegrityError as exc:
                raise CapabilityRegistryError("duplicate capability id") from exc
        return spec

    def get(self, capability_id: str) -> CapabilitySpec:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM capability_registry WHERE capability_id=?",
                (capability_id,),
            ).fetchone()
        if row is None:
            raise CapabilityRegistryError("capability not registered")
        return self._from_payload(row[0])

    def list(self) -> tuple[CapabilitySpec, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM capability_registry ORDER BY capability_id"
            ).fetchall()
        return tuple(self._from_payload(row[0]) for row in rows)

    def digest(self) -> str:
        payload = [spec.to_dict() for spec in self.list()]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _from_payload(raw: str) -> CapabilitySpec:
        value = json.loads(raw)
        return CapabilitySpec(
            capability_id=value["capability_id"],
            version=int(value["version"]),
            operations=tuple(value["operations"]),
            scope=value["scope"],
            approval_class=value["approval_class"],
            side_effect_class=value["side_effect_class"],
            max_timeout_seconds=float(value["max_timeout_seconds"]),
            max_output_bytes=int(value["max_output_bytes"]),
        )
