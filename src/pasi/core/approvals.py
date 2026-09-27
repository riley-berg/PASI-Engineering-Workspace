from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ApprovalError(PermissionError):
    """Raised when an approval is missing, expired, replayed, or out of scope."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Approval:
    approval_id: str
    operation_id: str
    capability_id: str
    target: str
    issued_at: str
    expires_at: str
    token_digest: str
    used: bool = False

    @classmethod
    def issue(
        cls,
        *,
        approval_id: str,
        operation_id: str,
        capability_id: str,
        target: str,
        token: str,
        ttl_seconds: int,
    ) -> "Approval":
        if ttl_seconds <= 0 or ttl_seconds > 86_400:
            raise ApprovalError("approval ttl is out of bounds")
        issued = utc_now()
        expires = issued + timedelta(seconds=ttl_seconds)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return cls(
            approval_id=approval_id,
            operation_id=operation_id,
            capability_id=capability_id,
            target=target,
            issued_at=issued.isoformat(),
            expires_at=expires.isoformat(),
            token_digest=digest,
        )

    def verify(self, *, token: str, operation_id: str, capability_id: str, target: str) -> None:
        if self.used:
            raise ApprovalError("approval has already been used")
        if self.operation_id != operation_id:
            raise ApprovalError("approval operation identity mismatch")
        if self.capability_id != capability_id:
            raise ApprovalError("approval capability mismatch")
        if self.target != target:
            raise ApprovalError("approval target mismatch")
        if utc_now() >= datetime.fromisoformat(self.expires_at):
            raise ApprovalError("approval has expired")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, self.token_digest):
            raise ApprovalError("approval token mismatch")


class SQLiteApprovalStore:
    """Durable, single-use approval store."""

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
                CREATE TABLE IF NOT EXISTS approval (
                    approval_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    token_digest TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def create(self, approval: Approval) -> Approval:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO approval(
                    approval_id, operation_id, capability_id, target,
                    issued_at, expires_at, token_digest, used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.operation_id,
                    approval.capability_id,
                    approval.target,
                    approval.issued_at,
                    approval.expires_at,
                    approval.token_digest,
                    int(approval.used),
                ),
            )
        return approval

    def consume(
        self,
        approval_id: str,
        *,
        token: str,
        operation_id: str,
        capability_id: str,
        target: str,
    ) -> Approval:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise ApprovalError("approval not found")
            approval = Approval(
                approval_id=row["approval_id"],
                operation_id=row["operation_id"],
                capability_id=row["capability_id"],
                target=row["target"],
                issued_at=row["issued_at"],
                expires_at=row["expires_at"],
                token_digest=row["token_digest"],
                used=bool(row["used"]),
            )
            approval.verify(
                token=token,
                operation_id=operation_id,
                capability_id=capability_id,
                target=target,
            )
            result = connection.execute(
                """
                UPDATE approval
                SET used = 1
                WHERE approval_id = ? AND used = 0
                """,
                (approval_id,),
            )
            if result.rowcount != 1:
                raise ApprovalError("approval was already consumed")
            return Approval(**{**approval.__dict__, "used": True})
