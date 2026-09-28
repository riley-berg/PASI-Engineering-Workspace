from pathlib import Path

from pasi.core.event_store import SQLiteEventStore
from pasi.core.human_testing import (
    HumanTestRun,
    HumanTestStatus,
    HumanTestStepResult,
)
from pasi.core.human_test_store import HumanTestStore
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.operation_state import OperationState
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import RuntimeHealthStore, RuntimeHealth
from pasi.core.runtime_projection import RuntimeIdentity, RuntimeProjectionService


def make_service(tmp_path: Path) -> tuple[RuntimeAPIService, HumanTestStore]:
    operation_store = SQLiteOperationStateStore(tmp_path / "operation.db")
    event_store = SQLiteEventStore(tmp_path / "events.db")
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    health_store = RuntimeHealthStore(tmp_path / "health.db")
    command_store = RuntimeCommandStore(tmp_path / "commands.db")
    human_tests = HumanTestStore(tmp_path / "human-tests.db")

    health_store.create(
        RuntimeHealth.connected(
            controller_version="ctrl-1",
            runner_version="runner-1",
            provider_version="provider-1",
            code_head="1" * 40,
        )
    )
    state = OperationState(
        operation_id="op-1",
        operation_type="roadmap_task",
        run_id="run-1",
        task_id="P0",
        provider="local",
        phase="queued",
    )
    operation_store.create(state)

    controls = RuntimeControlService(
        operation_store=operation_store,
        event_store=event_store,
        command_store=command_store,
        authorization_token="control-token",
    )
    projection = RuntimeProjectionService(
        operation_store=operation_store,
        event_store=event_store,
        ledger=ledger,
        health_store=health_store,
        identity=RuntimeIdentity(
            code_head="1" * 40,
            runtime_version="runtime-1",
            controller_version="ctrl-1",
            runner_version="runner-1",
        ),
    )
    return (
        RuntimeAPIService(
            projection=projection,
            event_feed=RuntimeEventFeed(event_store),
            health_store=health_store,
            controls=controls,
            human_tests=human_tests,
            human_test_ingest_token="ingest-token",
        ),
        human_tests,
    )


def test_human_test_run_ingest_requires_separate_authorization_and_persists(tmp_path):
    service, store = make_service(tmp_path)
    run = HumanTestRun(
        run_id="htr-1",
        suite_id="pasi-p0-smoke",
        suite_version=1,
        code_head="1" * 40,
        browser_name="Chromium",
        browser_version="136",
        extension_version="0.1.0",
        execution_source="mv3-human-test-extension",
        target_origin="http://127.0.0.1:3000",
        started_at="2026-09-27T00:00:00+00:00",
        ended_at="2026-09-27T00:00:01+00:00",
        status=HumanTestStatus.PASS,
        steps=(
            HumanTestStepResult(
                step_id="health",
                action="api_get_json",
                status=HumanTestStatus.PASS,
                started_at="2026-09-27T00:00:00+00:00",
                ended_at="2026-09-27T00:00:01+00:00",
                observed={"status": "ok"},
            ),
        ),
    )
    status, _ = service.request(
        method="POST",
        path="/v1/human-tests/runs",
        headers={"Authorization": "Bearer wrong"},
        body=run.to_dict(),
    )
    assert status == 401

    status, body = service.request(
        method="POST",
        path="/v1/human-tests/runs",
        headers={"Authorization": "Bearer ingest-token"},
        body=run.to_dict(),
    )
    assert status == 200
    assert body["run_id"] == "htr-1"
    assert store.get_run("htr-1").status is HumanTestStatus.PASS

    status, body = service.request(
        method="GET",
        path="/v1/human-tests/trust",
        headers={},
    )
    assert status == 200
    assert body["trusted"] is False
