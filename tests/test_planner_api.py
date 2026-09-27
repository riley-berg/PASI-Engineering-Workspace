import json
from pathlib import Path

import pytest

from pasi.core.context_compiler import ContextCompiler
from pasi.core.event_store import SQLiteEventStore
from pasi.core.github_issue_intake import GitHubIssueTaskIntake
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.memory_store import SQLiteMemoryStore
from pasi.core.planner_api import PlannerConsoleService
from pasi.core.projects_sync import SQLiteProjectSyncStore
from pasi.core.retrieval import ProvenanceAwareRetriever
from pasi.core.roadmap import Roadmap
from pasi.core.roadmap_store import SQLiteRoadmapStore
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.schedule_store import SQLiteScheduleStore
from pasi.core.search_index import SQLiteSearchIndex
from pasi.core.selection_store import SQLiteSelectionStore
from pasi.core.workspace_preferences import SQLiteWorkspacePreferenceStore


ROOT = Path(__file__).resolve().parents[1]


def build_service(tmp_path: Path) -> PlannerConsoleService:
    roadmap_store = SQLiteRoadmapStore(tmp_path / "roadmap.db")
    roadmap = Roadmap.from_mapping(
        json.loads(
            (ROOT / "roadmap" / "frontend-v4.json").read_text(encoding="utf-8")
        )
    )
    roadmap_store.create(roadmap)

    memory_store = SQLiteMemoryStore(tmp_path / "memory.db")
    event_store = SQLiteEventStore(tmp_path / "events.db")
    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    command_store = RuntimeCommandStore(tmp_path / "commands.db")
    controls = RuntimeControlService(
        operation_store=__import__("pasi.core.operation_store", fromlist=["SQLiteOperationStateStore"]).SQLiteOperationStateStore(tmp_path / "operation.db"),
        event_store=event_store,
        command_store=command_store,
        authorization_token="token",
    )
    return PlannerConsoleService(
        roadmap_store=roadmap_store,
        selection_store=SQLiteSelectionStore(tmp_path / "selection.db"),
        schedule_store=SQLiteScheduleStore(tmp_path / "schedule.db"),
        memory_store=memory_store,
        projects_store=SQLiteProjectSyncStore(str(tmp_path / "projects.db")),
        preferences=SQLiteWorkspacePreferenceStore(tmp_path / "preferences.db"),
        search_index=SQLiteSearchIndex(tmp_path / "search.db"),
        context_compiler=ContextCompiler(ProvenanceAwareRetriever(memory_store)),
        event_store=event_store,
        ledger=ledger,
        intake=GitHubIssueTaskIntake(),
        controls=controls,
    )


def test_planner_api_returns_roadmap_dependency_graph_and_task_detail(tmp_path):
    service = build_service(tmp_path)

    status, roadmap = service.request(
        method="GET",
        path="/v1/planner/roadmap?roadmap_id=pasi-frontend",
        headers={},
    )
    assert status == 200
    assert roadmap["roadmap"]["roadmap_id"] == "pasi-frontend"
    assert roadmap["roadmap"]["canonical_sha256"]
    assert "FE-P2.1" in roadmap["blocked_reasons"] or "FE-P2.1" not in roadmap["ready_task_ids"]

    status, detail = service.request(
        method="GET",
        path="/v1/planner/tasks/FE-P2.1?roadmap_id=pasi-frontend",
        headers={},
    )
    assert status == 200
    assert detail["task"]["id"] == "FE-P2.1"
    assert detail["task"]["acceptance_requirements"]
    assert detail["task"]["source_issue_number"] == 28


def test_planner_api_context_and_search_are_provenance_bound(tmp_path):
    service = build_service(tmp_path)
    memory_store = service.memory_store
    memory_store.create(
        __import__("pasi.core.memory", fromlist=["MemoryRecord"]).MemoryRecord(
            memory_id="mem-planner",
            scope="task:FE-P2.1",
            kind="decision",
            content="Roadmap dependencies remain authoritative.",
            provenance_refs=("evidence://planner/1",),
            source_operation_id="op-planner",
        )
    )
    service.search_index.upsert(
        __import__("pasi.core.search_index", fromlist=["IndexedDocument"]).IndexedDocument(
            document_id="doc-p2.1",
            scope="project:pasi",
            kind="roadmap_task",
            title="Roadmap dependency validation",
            text="dependency graph eligibility blocked readiness",
            deep_link="/?task_id=FE-P2.1",
            freshness="fresh",
            indexed_at=service.search_index.now(),
            revision=0,
        )
    )

    status, context = service.request(
        method="GET",
        path="/v1/planner/context/FE-P2.1?roadmap_id=pasi-frontend&scope=task:FE-P2.1&query=dependency",
        headers={},
    )
    assert status == 200
    assert "task:FE-P2.1" in context["source_ids"]
    assert context["sha256"]

    status, search = service.request(
        method="GET",
        path="/v1/planner/search?q=dependency&scope=project:pasi",
        headers={},
    )
    assert status == 200
    assert search["count"] == 1
    assert search["results"][0]["deep_link"] == "/?task_id=FE-P2.1"


def test_planner_api_enforces_revision_safe_task_transition(tmp_path):
    service = build_service(tmp_path)

    status, response = service.request(
        method="POST",
        path="/v1/planner/roadmap/transition",
        headers={"Authorization": "Bearer token"},
        body={
            "roadmap_id": "pasi-frontend",
            "object_type": "task",
            "object_id": "FE-P2.2",
            "status": "active",
            "expected_revision": 0,
        },
    )
    assert status == 400
    assert "eligible" in response["error"]


def test_github_issue_preview_rejects_missing_acceptance_section(tmp_path):
    service = build_service(tmp_path)
    status, payload = service.request(
        method="POST",
        path="/v1/planner/intake/github/preview",
        headers={},
        body={
            "number": 123,
            "title": "Incomplete issue",
            "html_url": "https://github.com/th3-st0v3/PASI-Engineering-Workspace/issues/123",
            "body": "no acceptance section here",
            "phase_id": "FE-P2",
            "existing_task_ids": [],
        },
    )
    assert status == 400
    assert payload["valid"] is False
