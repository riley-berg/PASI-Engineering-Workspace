from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pasi.core.human_testing import (
    HumanTestRun,
    HumanTestTrustCertificate,
    HumanTestTrustEvaluator,
    HumanTestTrustPolicy,
)


class HumanTestRunNotFound(KeyError):
    """Raised when a human-test run does not exist."""


class HumanTestStore:
    """Durable human-test evidence store; the extension cannot write trust state."""

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
                CREATE TABLE IF NOT EXISTS human_test_run (
                    run_id TEXT PRIMARY KEY,
                    suite_id TEXT NOT NULL,
                    suite_version INTEGER NOT NULL,
                    code_head TEXT NOT NULL,
                    status TEXT NOT NULL,
                    negative_control INTEGER NOT NULL,
                    policy_violations_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    ended_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS human_test_trust (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    certificate_json TEXT NOT NULL,
                    issued_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_human_test_end
                ON human_test_run(ended_at, run_id)
                """
            )

    def record_run(self, run: HumanTestRun) -> HumanTestRun:
        payload = run.to_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO human_test_run(
                    run_id,
                    suite_id,
                    suite_version,
                    code_head,
                    status,
                    negative_control,
                    policy_violations_json,
                    evidence_json,
                    ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.suite_id,
                    run.suite_version,
                    run.code_head,
                    run.status.value,
                    int(run.negative_control),
                    json.dumps(list(run.policy_violations), separators=(",", ":")),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    run.ended_at,
                ),
            )
        return run

    def get_run(self, run_id: str) -> HumanTestRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT evidence_json FROM human_test_run WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise HumanTestRunNotFound(run_id)
        return HumanTestRun.from_mapping(json.loads(row["evidence_json"]))

    def list_runs(self, *, limit: int = 1000) -> tuple[HumanTestRun, ...]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_json
                FROM human_test_run
                ORDER BY ended_at ASC, run_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            HumanTestRun.from_mapping(json.loads(row["evidence_json"]))
            for row in rows
        )

    def evaluate_and_store_trust(
        self,
        *,
        policy: HumanTestTrustPolicy | None = None,
    ) -> HumanTestTrustCertificate:
        runs = list(self.list_runs(limit=10_000))
        certificate = HumanTestTrustEvaluator().evaluate(runs, policy=policy)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO human_test_trust(id, certificate_json, issued_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id)
                DO UPDATE SET certificate_json=excluded.certificate_json,
                              issued_at=excluded.issued_at
                """,
                (
                    json.dumps(
                        certificate.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    certificate.issued_at,
                ),
            )
        return certificate

    def get_trust(self) -> HumanTestTrustCertificate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT certificate_json FROM human_test_trust WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["certificate_json"])
        policy = HumanTestTrustPolicy(**payload["policy"])
        return HumanTestTrustCertificate(
            status=payload["status"],
            issued_at=payload["issued_at"],
            policy=policy,
            successful_runs=payload["successful_runs"],
            distinct_code_heads=payload["distinct_code_heads"],
            negative_controls=payload["negative_controls"],
            policy_violations=payload["policy_violations"],
            trailing_successes=payload["trailing_successes"],
            source_run_ids=tuple(payload["source_run_ids"]),
            certificate_sha256=payload["certificate_sha256"],
        )
