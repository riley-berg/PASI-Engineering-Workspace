from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from pasi.providers.health import HealthClassification, SQLiteProviderHealthStore
from pasi.providers.registry import SQLiteProviderRegistry
from pasi.providers.selection import (
    DeterministicProviderSelector,
    ProviderRequirements,
    ProviderSelectionError,
)


class FallbackError(ValueError):
    """Raised when fallback cannot produce a valid bounded decision."""


@dataclass(frozen=True)
class FallbackDecision:
    operation_id: str
    idempotency_key: str
    attempt: int
    provider: str
    model: str
    reason: str
    registry_digest: str
    health_classification: str
    previous_provider: str
    selected_at: str

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.idempotency_key.strip():
            raise FallbackError("operation_id and idempotency_key are required")
        if self.attempt <= 0:
            raise FallbackError("attempt must be positive")


class SQLiteFallbackStore:
    """Durable failover decisions keyed by logical operation/idempotency."""

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
                CREATE TABLE IF NOT EXISTS fallback_decision (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    selected_at TEXT NOT NULL
                )
                """
            )

    def get_by_key(self, idempotency_key: str) -> FallbackDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM fallback_decision
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return FallbackDecision(**json.loads(row["payload_json"]))

    def record(self, decision: FallbackDecision) -> FallbackDecision:
        existing = self.get_by_key(decision.idempotency_key)
        if existing is not None:
            if existing != decision:
                raise FallbackError(
                    "idempotency key already maps to different fallback decision"
                )
            return existing
        payload = json.dumps(
            decision.__dict__,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO fallback_decision(
                    operation_id, idempotency_key, payload_json, selected_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    decision.operation_id,
                    decision.idempotency_key,
                    payload,
                    decision.selected_at,
                ),
            )
        return decision


class BoundedFallbackOrchestrator:
    """Fail over without changing logical operation identity or exceeding a finite budget."""

    def __init__(
        self,
        registry: SQLiteProviderRegistry,
        health: SQLiteProviderHealthStore,
        decisions: SQLiteFallbackStore,
        *,
        max_failovers: int = 2,
    ) -> None:
        if max_failovers <= 0 or max_failovers > 10:
            raise FallbackError("max_failovers out of bounds")
        self.registry = registry
        self.health = health
        self.decisions = decisions
        self.max_failovers = max_failovers

    def choose(
        self,
        *,
        operation_id: str,
        idempotency_key: str,
        attempt: int,
        requirements: ProviderRequirements,
        failed_provider: str,
        excluded_providers: tuple[str, ...] = (),
        advisory_preferences: Mapping[str, int] | None = None,
    ) -> FallbackDecision:
        if attempt <= 0 or attempt > self.max_failovers:
            raise FallbackError("fallback attempt exceeds configured budget")

        prior = self.decisions.get_by_key(idempotency_key)
        if prior is not None:
            if prior.operation_id != operation_id:
                raise FallbackError(
                    "idempotency key belongs to a different logical operation"
                )
            return prior

        excluded = set(excluded_providers) | {failed_provider}
        preferences = dict(advisory_preferences or {})
        last_error = ""

        for provider in self.registry.list():
            if provider.provider in excluded:
                continue
            latest = self.health.latest(provider.provider)
            if latest is not None and latest.classification == HealthClassification.UNAVAILABLE:
                excluded.add(provider.provider)
                last_error = f"{provider.provider}: unavailable"
                continue

        filtered_preferences = {
            provider: rank
            for provider, rank in preferences.items()
            if provider not in excluded
        }

        # Build a temporary registry view containing only providers still considered.
        candidates = [
            provider
            for provider in self.registry.list()
            if provider.provider not in excluded
        ]
        if not candidates:
            raise FallbackError(last_error or "no fallback providers remain")

        selector = DeterministicProviderSelector()
        selected = selector.select(
            _RegistryView(candidates),
            requirements,
            advisory_preferences=filtered_preferences,
        )
        reason = "primary_unavailable" if failed_provider else "provider_failure"
        decision = FallbackDecision(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            attempt=attempt,
            provider=selected.provider,
            model=selected.model,
            reason=reason,
            registry_digest=selected.registry_digest,
            health_classification=(
                self.health.latest(selected.provider).classification
                if self.health.latest(selected.provider) is not None
                else "unknown"
            ),
            previous_provider=failed_provider,
            selected_at=datetime.now(timezone.utc).isoformat(),
        )
        return self.decisions.record(decision)


class _RegistryView:
    """Read-only filtered registry view implementing the selector's registry contract."""

    def __init__(self, capabilities) -> None:
        self._capabilities = tuple(capabilities)

    def list(self):
        return self._capabilities

    def digest(self) -> str:
        payload = [
            capability.to_dict()
            for capability in self._capabilities
        ]
        return __import__("hashlib").sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
