from pathlib import Path

import pytest

from pasi.providers.health import (
    HealthClassification,
    ProviderHealthError,
    ProviderHealthMonitor,
    SQLiteProviderHealthStore,
)


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)

    def health(self):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_health_monitor_records_healthy_and_failure_transitions(tmp_path: Path):
    store = SQLiteProviderHealthStore(tmp_path / "health.db")
    monitor = ProviderHealthMonitor(
        FakeProvider(
            [
                {"available": True},
                {"available": False, "reason": "down"},
                {"available": False, "reason": "still down"},
            ]
        ),
        store,
        failure_threshold=2,
    )

    first = monitor.probe()
    second = monitor.probe()
    third = monitor.probe()

    assert first.classification == HealthClassification.HEALTHY
    assert second.classification == HealthClassification.DEGRADED
    assert third.classification == HealthClassification.UNAVAILABLE
    assert [item.provider for item in store.list("fake")] == ["fake", "fake", "fake"]


def test_health_monitor_restart_and_digest(tmp_path: Path):
    path = tmp_path / "health.db"
    first_store = SQLiteProviderHealthStore(path)
    ProviderHealthMonitor(
        FakeProvider([{"available": True}]),
        first_store,
    ).probe()
    digest = first_store.digest("fake")

    restarted = SQLiteProviderHealthStore(path)
    assert restarted.latest("fake") is not None
    assert restarted.digest("fake") == digest


def test_health_monitor_classifies_malformed_and_timeout(tmp_path: Path):
    store = SQLiteProviderHealthStore(tmp_path / "health.db")
    monitor = ProviderHealthMonitor(
        FakeProvider([{"bad": True}, TimeoutError("slow")]),
        store,
        failure_threshold=3,
    )

    malformed = monitor.probe()
    timeout = monitor.probe()
    assert malformed.error_category == "probe_error"
    assert timeout.error_category == "timeout"
    assert timeout.classification == HealthClassification.DEGRADED


def test_health_configuration_is_bounded():
    with pytest.raises(ProviderHealthError):
        ProviderHealthMonitor(
            FakeProvider([{"available": True}]),
            SQLiteProviderHealthStore("/tmp/unused-health.db"),
            timeout_seconds=0,
        )


def test_health_schema_exists():
    assert (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "provider-health-v1.json"
    ).exists()
