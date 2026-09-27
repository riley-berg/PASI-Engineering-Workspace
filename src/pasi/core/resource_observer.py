from __future__ import annotations

import os
import resource
import sqlite3
import time
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class ResourceObservationError(ValueError):
    """Raised when host/resource observation cannot be represented safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ResourceSnapshot:
    operation_id: str
    run_id: str
    timestamp: str
    rss_bytes: int
    cpu_seconds: float
    cpu_percent: float
    disk_used_bytes: int
    disk_free_bytes: int
    file_descriptor_count: int | None
    sampling_error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "rss_bytes": self.rss_bytes,
            "cpu_seconds": self.cpu_seconds,
            "cpu_percent": self.cpu_percent,
            "disk_used_bytes": self.disk_used_bytes,
            "disk_free_bytes": self.disk_free_bytes,
            "file_descriptor_count": self.file_descriptor_count,
            "sampling_error": self.sampling_error,
        }


class SQLiteResourceObservationStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_resource_operation
                ON resource_snapshot(operation_id, id)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, snapshot: ResourceSnapshot) -> ResourceSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO resource_snapshot(
                    operation_id, run_id, timestamp, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.operation_id,
                    snapshot.run_id,
                    snapshot.timestamp,
                    json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":")),
                ),
            )
        return snapshot

    def list(
        self,
        *,
        operation_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[ResourceSnapshot, ...]:
        clauses: list[str] = []
        params: list[str] = []
        if operation_id is not None:
            clauses.append("operation_id = ?")
            params.append(operation_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM resource_snapshot {where} ORDER BY id ASC",
                params,
            ).fetchall()
        return tuple(
            ResourceSnapshot(**json.loads(row["payload_json"]))
            for row in rows
        )


class HostResourceObserver:
    """Bounded local process/host sampler using standard-library facilities."""

    def __init__(self, store: SQLiteResourceObservationStore) -> None:
        self.store = store
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()

    def sample(self, *, operation_id: str, run_id: str) -> ResourceSnapshot:
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        wall_delta = max(now_wall - self._last_wall, 1e-9)
        cpu_delta = max(now_cpu - self._last_cpu, 0.0)
        self._last_wall = now_wall
        self._last_cpu = now_cpu

        errors: list[str] = []
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_bytes = int(usage.ru_maxrss * 1024) if os.name == "posix" else int(usage.ru_maxrss)
        disk = shutil.disk_usage(Path.cwd())
        try:
            fd_count = len(os.listdir("/proc/self/fd"))
        except (FileNotFoundError, PermissionError, OSError):
            fd_count = None
            errors.append("file_descriptor_count_unavailable")

        snapshot = ResourceSnapshot(
            operation_id=operation_id,
            run_id=run_id,
            timestamp=utc_now(),
            rss_bytes=rss_bytes,
            cpu_seconds=float(now_cpu),
            cpu_percent=(cpu_delta / wall_delta) * 100.0,
            disk_used_bytes=int(disk.used),
            disk_free_bytes=int(disk.free),
            file_descriptor_count=fd_count,
            sampling_error=";".join(errors),
        )
        return self.store.record(snapshot)
