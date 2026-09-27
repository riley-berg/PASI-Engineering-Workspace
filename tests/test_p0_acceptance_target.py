from pathlib import Path

from scripts.run_p0_acceptance_target import build_service


def test_acceptance_target_seeds_real_runtime_projection(tmp_path: Path):
    service = build_service(
        tmp_path / "db",
        ingest_token="ingest-token",
        control_token="control-token",
    )
    status, health = service.request(
        method="GET",
        path="/healthz",
        headers={},
    )
    assert status == 200
    assert health["status"] == "ok"

    status, projection = service.request(
        method="GET",
        path="/v1/runtime/operations/p0-human-test",
        headers={},
    )
    assert status == 200
    assert projection["operation"]["operation_id"] == "p0-human-test"
    assert projection["operation"]["metadata"]["acceptance_fixture"] == "true"
    assert projection["health"]["connection_status"] == "connected"
