from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pasi.core.events import DurableEvent


class NotificationError(ValueError):
    """Raised when a notification cannot be persisted safely."""


@dataclass(frozen=True)
class Notification:
    notification_id: str
    scope: str
    source_event_id: str
    severity: str
    message: str
    entity_type: str
    entity_id: str
    occurred_at: str
    acknowledged: bool = False
    revision: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "notification_id": self.notification_id,
            "scope": self.scope,
            "source_event_id": self.source_event_id,
            "severity": self.severity,
            "message": self.message,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "occurred_at": self.occurred_at,
            "acknowledged": self.acknowledged,
            "revision": self.revision,
        }


class SQLiteNotificationStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification (
                    notification_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    source_event_id TEXT NOT NULL UNIQUE,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL,
                    revision INTEGER NOT NULL
                )
                """
            )

    def derive_from_event(
        self,
        event: DurableEvent,
        *,
        scope: str,
        severity: str,
        message: str,
        entity_type: str,
        entity_id: str,
    ) -> Notification:
        if severity not in {"info", "warning", "error", "critical"}:
            raise NotificationError("invalid notification severity")
        if not scope.strip():
            raise NotificationError("scope is required")
        notification_id = "ntf-" + hashlib.sha256(
            event.event_id.encode("utf-8")
        ).hexdigest()[:24]
        notification = Notification(
            notification_id=notification_id,
            scope=scope,
            source_event_id=event.event_id,
            severity=severity,
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            occurred_at=event.occurred_at,
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO notification(
                    notification_id, scope, source_event_id, severity,
                    message, entity_type, entity_id, occurred_at,
                    acknowledged, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    notification.notification_id,
                    notification.scope,
                    notification.source_event_id,
                    notification.severity,
                    notification.message,
                    notification.entity_type,
                    notification.entity_id,
                    notification.occurred_at,
                ),
            )
        return self.get(notification.notification_id)

    def get(self, notification_id: str) -> Notification:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM notification WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise KeyError(notification_id)
        return Notification(
            notification_id=row[0],
            scope=row[1],
            source_event_id=row[2],
            severity=row[3],
            message=row[4],
            entity_type=row[5],
            entity_id=row[6],
            occurred_at=row[7],
            acknowledged=bool(row[8]),
            revision=int(row[9]),
        )

    def list(self, *, scope: str, include_acknowledged: bool = False) -> tuple[Notification, ...]:
        query = "SELECT * FROM notification WHERE scope = ?"
        params: list[object] = [scope]
        if not include_acknowledged:
            query += " AND acknowledged = 0"
        query += " ORDER BY occurred_at DESC, notification_id ASC"
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(
            Notification(
                notification_id=row[0],
                scope=row[1],
                source_event_id=row[2],
                severity=row[3],
                message=row[4],
                entity_type=row[5],
                entity_id=row[6],
                occurred_at=row[7],
                acknowledged=bool(row[8]),
                revision=int(row[9]),
            )
            for row in rows
        )

    def acknowledge(self, notification_id: str, *, expected_revision: int) -> Notification:
        current = self.get(notification_id)
        if current.revision != expected_revision:
            raise NotificationError("notification revision conflict")
        with sqlite3.connect(self.path) as connection:
            result = connection.execute(
                """
                UPDATE notification
                SET acknowledged = 1, revision = ?
                WHERE notification_id = ? AND revision = ?
                """,
                (expected_revision + 1, notification_id, expected_revision),
            )
            if result.rowcount != 1:
                raise NotificationError("notification changed while acknowledging")
        return self.get(notification_id)
