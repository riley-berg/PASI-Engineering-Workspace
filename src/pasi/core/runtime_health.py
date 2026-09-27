from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class HealthError(ValueError):
    """Raised when runtime-health state is invalid."""


class ConnectionStatus(StrEnum):
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    RECOVERING = "recovering"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeHealth:
    connection_status: ConnectionStatus
    heartbeat_at: str
    degraded_reason: str = ""
    recovery_phase: str = ""
    controller_version: str = ""
    runner_version: str = ""
    provider_version: str = ""
    code_head: str = ""
    revision: int = 0
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise HealthError("health revision must be non-negative")
        if not self.heartbeat_at or not isinstance(self.heartbeat_at, str):
            raise HealthError("heartbeat_at must be a non-empty string")
        if not self.updated_at:
            object.__setattr__(self, "updated_at", utc_now())
        elif not isinstance(self.updated_at, str):
            raise HealthError("updated_at must be a string")

    def to_dict(self) -> dict[str, object]:
        return {
            "connection_status": self.connection_status.value,
            "heartbeat_at": self.heartbeat_at,
            "degraded_reason": self.degraded_reason,
            "recovery_phase": self.recovery_phase,
            "controller_version": self.controller_version,
            "runner_version": self.runner_version,
            "provider_version": self.provider_version,
            "code_head": self.code_head,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def connected(
        cls,
        *,
        controller_version: str,
        runner_version: str,
        provider_version: str = "",
        code_head: str = "",
    ) -> "RuntimeHealth":
        now = utc_now()
        return cls(
            connection_status=ConnectionStatus.CONNECTED,
            heartbeat_at=now,
            controller_version=controller_version,
            runner_version=runner_version,
            provider_version=provider_version,
            code_head=code_head,
            revision=0,
            updated_at=now,
        )

    def heartbeat(self, *, observed_at: str | None = None) -> "RuntimeHealth":
        now = observed_at or utc_now()
        return RuntimeHealth(
            connection_status=ConnectionStatus.CONNECTED,
            heartbeat_at=now,
            degraded_reason="",
            recovery_phase="",
            controller_version=self.controller_version,
            runner_version=self.runner_version,
            provider_version=self.provider_version,
            code_head=self.code_head,
            revision=self.revision + 1,
            updated_at=now,
        )

    def mark_degraded(self, reason: str) -> "RuntimeHealth":
        now = utc_now()
        return RuntimeHealth(
            connection_status=ConnectionStatus.DEGRADED,
            heartbeat_at=self.heartbeat_at,
            degraded_reason=reason.strip(),
            recovery_phase=self.recovery_phase,
            controller_version=self.controller_version,
            runner_version=self.runner_version,
            provider_version=self.provider_version,
            code_head=self.code_head,
            revision=self.revision + 1,
            updated_at=now,
        )

    def mark_recovering(self, phase: str) -> "RuntimeHealth":
        now = utc_now()
        return RuntimeHealth(
            connection_status=ConnectionStatus.RECOVERING,
            heartbeat_at=self.heartbeat_at,
            degraded_reason=self.degraded_reason,
            recovery_phase=phase.strip(),
            controller_version=self.controller_version,
            runner_version=self.runner_version,
            provider_version=self.provider_version,
            code_head=self.code_head,
            revision=self.revision + 1,
            updated_at=now,
        )

    def mark_disconnected(self, reason: str = "") -> "RuntimeHealth":
        now = utc_now()
        return RuntimeHealth(
            connection_status=ConnectionStatus.DISCONNECTED,
            heartbeat_at=self.heartbeat_at,
            degraded_reason=reason.strip(),
            recovery_phase=self.recovery_phase,
            controller_version=self.controller_version,
            runner_version=self.runner_version,
            provider_version=self.provider_version,
            code_head=self.code_head,
            revision=self.revision + 1,
            updated_at=now,
        )


class RuntimeHealthStore:
    """Durable singleton runtime-health snapshot with optimistic revisions."""

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
                CREATE TABLE IF NOT EXISTS runtime_health (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create(self, health: RuntimeHealth) -> RuntimeHealth:
        import json

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_health(id, payload_json, revision, updated_at)
                VALUES (1, ?, ?, ?)
                """,
                (
                    json.dumps(health.to_dict(), sort_keys=True, separators=(",", ":")),
                    health.revision,
                    health.updated_at,
                ),
            )
        return health

    def get(self) -> RuntimeHealth:
        import json

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_health WHERE id = 1"
            ).fetchone()
        if row is None:
            raise HealthError("runtime health has not been initialized")
        payload = json.loads(row["payload_json"])
        payload["connection_status"] = ConnectionStatus(payload["connection_status"])
        return RuntimeHealth(**payload)

    def save(self, health: RuntimeHealth, *, expected_revision: int) -> RuntimeHealth:
        import json

        if health.revision != expected_revision + 1:
            raise HealthError("health revision must advance exactly one step")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE runtime_health
                SET payload_json = ?, revision = ?, updated_at = ?
                WHERE id = 1 AND revision = ?
                """,
                (
                    json.dumps(health.to_dict(), sort_keys=True, separators=(",", ":")),
                    health.revision,
                    health.updated_at,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise HealthError(
                    f"runtime health moved after revision {expected_revision}"
                )
        return health
