from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


HEALTH_SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_FAILURE_THRESHOLD = 3


class ProviderHealthError(ValueError):
    """Raised when health-monitoring configuration is invalid."""


class HealthClassification:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: str
    timestamp: str
    available: bool
    latency_ms: float
    classification: str
    consecutive_failures: int
    error_category: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ProviderHealthError("provider is required")
        if self.latency_ms < 0:
            raise ProviderHealthError("latency_ms must be non-negative")
        if self.consecutive_failures < 0:
            raise ProviderHealthError("consecutive_failures must be non-negative")
        if self.classification not in {
            HealthClassification.HEALTHY,
            HealthClassification.DEGRADED,
            HealthClassification.UNAVAILABLE,
        }:
            raise ProviderHealthError("invalid health classification")
        if self.available and self.classification == HealthClassification.UNAVAILABLE:
            raise ProviderHealthError("available provider cannot be unavailable")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "timestamp": self.timestamp,
            "available": self.available,
            "latency_ms": self.latency_ms,
            "classification": self.classification,
            "consecutive_failures": self.consecutive_failures,
            "error_category": self.error_category,
            "error_message": self.error_message,
        }


class HealthProvider(Protocol):
    name: str

    def health(self) -> dict[str, object]: ...


class SQLiteProviderHealthStore:
    """Durable bounded health history."""

    def __init__(self, path: Path | str, *, max_snapshots: int = 10_000) -> None:
        if max_snapshots <= 0 or max_snapshots > 1_000_000:
            raise ProviderHealthError("max_snapshots out of bounds")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_snapshots = max_snapshots
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_provider_health_provider
                ON provider_health(provider, id)
                """
            )

    def record(self, snapshot: ProviderHealthSnapshot) -> int:
        payload = json.dumps(
            snapshot.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO provider_health(provider, timestamp, payload_json)
                VALUES (?, ?, ?)
                """,
                (snapshot.provider, snapshot.timestamp, payload),
            )
            connection.execute(
                """
                DELETE FROM provider_health
                WHERE provider = ?
                  AND id NOT IN (
                    SELECT id
                    FROM provider_health
                    WHERE provider = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (snapshot.provider, snapshot.provider, self.max_snapshots),
            )
            return int(cursor.lastrowid)

    def list(self, provider: str, *, limit: int = 100) -> tuple[ProviderHealthSnapshot, ...]:
        if limit <= 0 or limit > self.max_snapshots:
            raise ProviderHealthError("health history limit out of bounds")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM provider_health
                WHERE provider = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (provider, limit),
            ).fetchall()
        return tuple(
            ProviderHealthSnapshot(**json.loads(row["payload_json"]))
            for row in rows
        )

    def latest(self, provider: str) -> ProviderHealthSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM provider_health
                WHERE provider = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (provider,),
            ).fetchone()
        if row is None:
            return None
        return ProviderHealthSnapshot(**json.loads(row["payload_json"]))

    def digest(self, provider: str) -> str:
        snapshots = [snapshot.to_dict() for snapshot in self.list(provider)]
        canonical = json.dumps(
            snapshots,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProviderHealthMonitor:
    """Probe a real provider adapter with bounded timing and deterministic classification."""

    def __init__(
        self,
        provider: HealthProvider,
        store: SQLiteProviderHealthStore,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ProviderHealthError("timeout_seconds out of bounds")
        if failure_threshold <= 0 or failure_threshold > 100:
            raise ProviderHealthError("failure_threshold out of bounds")
        self.provider = provider
        self.store = store
        self.timeout_seconds = timeout_seconds
        self.failure_threshold = failure_threshold

    def probe(self) -> ProviderHealthSnapshot:
        started = time.perf_counter()
        previous = self.store.latest(self.provider.name)
        previous_failures = previous.consecutive_failures if previous else 0
        try:
            result = self.provider.health()
            latency_ms = (time.perf_counter() - started) * 1000
            if not isinstance(result, dict):
                raise ValueError("provider health response must be an object")
            available = result.get("available")
            if not isinstance(available, bool):
                raise ValueError("provider health response requires boolean available")
            if available:
                snapshot = ProviderHealthSnapshot(
                    provider=self.provider.name,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    available=True,
                    latency_ms=latency_ms,
                    classification=HealthClassification.HEALTHY,
                    consecutive_failures=0,
                )
            else:
                failures = previous_failures + 1
                classification = (
                    HealthClassification.UNAVAILABLE
                    if failures >= self.failure_threshold
                    else HealthClassification.DEGRADED
                )
                snapshot = ProviderHealthSnapshot(
                    provider=self.provider.name,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    available=False,
                    latency_ms=latency_ms,
                    classification=classification,
                    consecutive_failures=failures,
                    error_category="provider_unavailable",
                    error_message=str(result.get("reason", ""))[:500],
                )
        except TimeoutError as exc:
            failures = previous_failures + 1
            snapshot = ProviderHealthSnapshot(
                provider=self.provider.name,
                timestamp=datetime.now(timezone.utc).isoformat(),
                available=False,
                latency_ms=min(
                    self.timeout_seconds * 1000,
                    (time.perf_counter() - started) * 1000,
                ),
                classification=(
                    HealthClassification.UNAVAILABLE
                    if failures >= self.failure_threshold
                    else HealthClassification.DEGRADED
                ),
                consecutive_failures=failures,
                error_category="timeout",
                error_message=str(exc)[:500],
            )
        except Exception as exc:
            failures = previous_failures + 1
            snapshot = ProviderHealthSnapshot(
                provider=self.provider.name,
                timestamp=datetime.now(timezone.utc).isoformat(),
                available=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                classification=(
                    HealthClassification.UNAVAILABLE
                    if failures >= self.failure_threshold
                    else HealthClassification.DEGRADED
                ),
                consecutive_failures=failures,
                error_category="probe_error",
                error_message=str(exc)[:500],
            )

        self.store.record(snapshot)
        return snapshot
