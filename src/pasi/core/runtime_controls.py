from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pasi.core.event_store import SQLiteEventStore
from pasi.core.events import DurableEvent
from pasi.core.operation_store import SQLiteOperationStateStore


class AuthorizationError(PermissionError):
    """Raised when a runtime-control caller is not authorized."""


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused for a different command."""


class ControlError(ValueError):
    """Raised when a runtime control cannot be applied."""


@dataclass(frozen=True)
class ControlCommand:
    command_id: str
    operation_id: str
    action: str
    expected_revision: int
    payload_sha256: str
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "operation_id": self.operation_id,
            "action": self.action,
            "expected_revision": self.expected_revision,
            "payload_sha256": self.payload_sha256,
            "result": self.result,
        }


class RuntimeCommandStore:
    """Durable idempotency ledger for runtime control commands."""

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
                CREATE TABLE IF NOT EXISTS runtime_command (
                    idempotency_key TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )

    def get(self, idempotency_key: str) -> ControlCommand | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_command WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return ControlCommand(
            command_id=row["command_id"],
            operation_id=row["operation_id"],
            action=row["action"],
            expected_revision=int(row["expected_revision"]),
            payload_sha256=row["payload_sha256"],
            result=json.loads(row["result_json"]),
        )

    def put(self, idempotency_key: str, command: ControlCommand) -> ControlCommand:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_command(
                    idempotency_key,
                    command_id,
                    operation_id,
                    action,
                    expected_revision,
                    payload_sha256,
                    result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    command.command_id,
                    command.operation_id,
                    command.action,
                    command.expected_revision,
                    command.payload_sha256,
                    json.dumps(command.result, sort_keys=True, separators=(",", ":")),
                ),
            )
        return command


class RuntimeControlService:
    """Typed, authorized, idempotent start/stop/retry/recover controls."""

    ACTIONS = frozenset({"start", "stop", "retry", "recover"})

    def __init__(
        self,
        *,
        operation_store: SQLiteOperationStateStore,
        event_store: SQLiteEventStore,
        command_store: RuntimeCommandStore,
        authorization_token: str,
    ) -> None:
        if not authorization_token:
            raise ValueError("authorization_token is required")
        self.operation_store = operation_store
        self.event_store = event_store
        self.command_store = command_store
        self.authorization_token = authorization_token

    def authorize(self, token: str) -> None:
        if not token or not hmac.compare_digest(token, self.authorization_token):
            raise AuthorizationError("runtime control authorization failed")

    def execute(
        self,
        *,
        token: str,
        idempotency_key: str,
        operation_id: str,
        action: str,
        expected_revision: int,
        reason: str = "",
    ) -> dict[str, Any]:
        self.authorize(token)

        if action not in self.ACTIONS:
            raise ControlError(f"unsupported runtime control action: {action}")
        if not idempotency_key.strip():
            raise ControlError("idempotency_key is required")
        if expected_revision < 0:
            raise ControlError("expected_revision must be non-negative")

        request_payload = {
            "operation_id": operation_id,
            "action": action,
            "expected_revision": expected_revision,
            "reason": reason.strip(),
        }
        payload_hash = hashlib.sha256(
            json.dumps(
                request_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        existing = self.command_store.get(idempotency_key)
        if existing is not None:
            if existing.payload_sha256 != payload_hash:
                raise IdempotencyConflict(
                    "idempotency key was already used for a different command"
                )
            return existing.result

        state = self.operation_store.get(operation_id)
        metadata = dict(state.metadata)

        if action == "start":
            next_state = self.operation_store.transition(
                operation_id,
                "claimed",
                expected_revision=expected_revision,
                phase="runtime_control.start",
                metadata={**metadata, "control_action": "start"},
            )
        elif action == "stop":
            next_state = self.operation_store.transition(
                operation_id,
                "failed",
                expected_revision=expected_revision,
                phase="runtime_control.stop",
                failure_signature="operator.stop_requested",
                metadata={**metadata, "control_action": "stop"},
            )
        elif action == "retry":
            next_state = self.operation_store.transition(
                operation_id,
                "claimed",
                expected_revision=expected_revision,
                phase="runtime_control.retry",
                metadata={**metadata, "control_action": "retry"},
            )
        else:
            next_state = self.operation_store.transition(
                operation_id,
                "claimed",
                expected_revision=expected_revision,
                phase="runtime_control.recover",
                metadata={**metadata, "control_action": "recover"},
            )

        command_id = f"cmd-{payload_hash[:24]}"
        event_id = f"event-{command_id}"
        event = DurableEvent(
            event_id=event_id,
            event_type=f"runtime.control.{action}",
            source="runtime_control",
            operation_id=operation_id,
            task_id=next_state.task_id,
            run_id=next_state.run_id,
            correlation_id=command_id,
            causation_id="",
            payload={
                "command_id": command_id,
                "action": action,
                "reason": reason.strip(),
                "from_revision": expected_revision,
                "to_revision": next_state.state_revision,
                "status": next_state.status,
            },
            evidence_refs=(
                f"runtime://command/{command_id}",
                f"runtime://operation/{operation_id}",
            ),
        )
        self.event_store.append(event)

        result = {
            "command_id": command_id,
            "operation_id": operation_id,
            "action": action,
            "status": next_state.status,
            "state_revision": next_state.state_revision,
        }
        command = ControlCommand(
            command_id=command_id,
            operation_id=operation_id,
            action=action,
            expected_revision=expected_revision,
            payload_sha256=payload_hash,
            result=result,
        )
        self.command_store.put(idempotency_key, command)
        return result
