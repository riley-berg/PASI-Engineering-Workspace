from pathlib import Path

from pasi.core.failure_registry import SQLiteFailureRegistry, signature_key


def test_failure_registry_aggregates_normalized_signatures(tmp_path: Path):
    registry = SQLiteFailureRegistry(tmp_path / "failures.db")
    first = registry.record(
        subsystem="Provider",
        failure_code=" TIMEOUT ",
        failure_family="transient_provider",
        operation_id="op-1",
        evidence_ref="evidence://1",
        seen_at="2026-01-01T00:00:00+00:00",
    )
    second = registry.record(
        subsystem="provider",
        failure_code="timeout",
        failure_family="TRANSIENT_PROVIDER",
        operation_id="op-2",
        evidence_ref="evidence://2",
        seen_at="2026-01-01T00:01:00+00:00",
    )

    assert first.signature_id == signature_key(
        subsystem="Provider",
        failure_code=" TIMEOUT ",
        failure_family="transient_provider",
    )
    assert second.occurrence_count == 2
    assert second.first_seen_at == first.first_seen_at
    assert second.latest_operation_id == "op-2"

    restarted = SQLiteFailureRegistry(tmp_path / "failures.db")
    assert restarted.get(first.signature_id).occurrence_count == 2
