from pathlib import Path

import pytest

from pasi.providers.fallback import (
    BoundedFallbackOrchestrator,
    FallbackError,
    SQLiteFallbackStore,
)
from pasi.providers.health import SQLiteProviderHealthStore, ProviderHealthSnapshot, HealthClassification
from pasi.providers.registry import ProviderCapability, SQLiteProviderRegistry
from pasi.providers.selection import ProviderRequirements


def cap(name, context=32768):
    return ProviderCapability(
        provider=name,
        registry_version=1,
        models=(f"{name}-model",),
        capabilities=("chat", "code"),
        context_window_tokens=context,
        max_output_tokens=min(4096, context - 1),
        configuration_provenance=f"test:{name}",
        metadata={},
    )


def setup(tmp_path: Path):
    registry = SQLiteProviderRegistry(tmp_path / "providers.db")
    registry.register(cap("primary"))
    registry.register(cap("fallback"))
    health = SQLiteProviderHealthStore(tmp_path / "health.db")
    decisions = SQLiteFallbackStore(tmp_path / "fallback.db")
    return registry, health, decisions


def test_fallback_preserves_operation_identity_and_is_idempotent(tmp_path: Path):
    registry, health, decisions = setup(tmp_path)
    health.record(
        ProviderHealthSnapshot(
            provider="primary",
            timestamp="2026-01-01T00:00:00+00:00",
            available=False,
            latency_ms=10,
            classification=HealthClassification.UNAVAILABLE,
            consecutive_failures=3,
            error_category="provider_unavailable",
        )
    )
    orchestrator = BoundedFallbackOrchestrator(registry, health, decisions, max_failovers=2)

    first = orchestrator.choose(
        operation_id="op-1",
        idempotency_key="op-1:fallback:1",
        attempt=1,
        requirements=ProviderRequirements(("code",)),
        failed_provider="primary",
    )
    second = orchestrator.choose(
        operation_id="op-1",
        idempotency_key="op-1:fallback:1",
        attempt=1,
        requirements=ProviderRequirements(("code",)),
        failed_provider="primary",
    )

    assert first == second
    assert first.provider == "fallback"
    assert first.previous_provider == "primary"


def test_fallback_rejects_budget_exhaustion_and_no_candidates(tmp_path: Path):
    registry, health, decisions = setup(tmp_path)
    orchestrator = BoundedFallbackOrchestrator(registry, health, decisions, max_failovers=1)

    with pytest.raises(FallbackError):
        orchestrator.choose(
            operation_id="op-1",
            idempotency_key="k",
            attempt=2,
            requirements=ProviderRequirements(("code",)),
            failed_provider="primary",
        )

    health.record(
        ProviderHealthSnapshot(
            provider="fallback",
            timestamp="2026-01-01T00:00:00+00:00",
            available=False,
            latency_ms=10,
            classification=HealthClassification.UNAVAILABLE,
            consecutive_failures=3,
            error_category="provider_unavailable",
        )
    )
    with pytest.raises(FallbackError):
        orchestrator.choose(
            operation_id="op-2",
            idempotency_key="k2",
            attempt=1,
            requirements=ProviderRequirements(("code",)),
            failed_provider="primary",
        )


def test_fallback_decision_survives_restart(tmp_path: Path):
    registry, health, decisions = setup(tmp_path)
    first = BoundedFallbackOrchestrator(registry, health, decisions).choose(
        operation_id="op-1",
        idempotency_key="op-1:fallback:1",
        attempt=1,
        requirements=ProviderRequirements(("chat",)),
        failed_provider="primary",
    )
    restarted = SQLiteFallbackStore(tmp_path / "fallback.db")
    assert restarted.get_by_key("op-1:fallback:1") == first


def test_fallback_schema_exists():
    assert (Path(__file__).resolve().parents[1] / "schemas" / "fallback-decision-v1.json").exists()
