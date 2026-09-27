from __future__ import annotations

from pathlib import Path

import pytest

from pasi.core.dependency_graph import DependencyGraph, StaleDependencyGraph
from pasi.core.event_store import SQLiteEventStore
from pasi.core.events import DurableEvent
from pasi.core.ledger import OperationLedgerEntry
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.notifications import NotificationError, SQLiteNotificationStore
from pasi.core.projects_sync import (
    GitHubProjectsSynchronizer,
    ProjectSyncConflict,
    ProjectSyncState,
    RemoteProjectItem,
    SQLiteProjectSyncStore,
)
from pasi.core.ranking_explanation import RankingExplanation, SQLiteRankingExplanationStore
from pasi.core.roadmap import (
    PhaseStatus,
    Roadmap,
    RoadmapPhase,
    RoadmapTask,
    TaskStatus,
)
from pasi.core.search_index import IndexedDocument, SQLiteSearchIndex
from pasi.core.selection_store import SQLiteSelectionStore
from pasi.core.task_detail import TaskDetailNotFound, TaskDetailReadModel
from pasi.core.task_selection import EvidenceAwareTaskSelector
from pasi.core.workspace_preferences import (
    PreferenceError,
    SQLiteWorkspacePreferenceStore,
    StalePreferenceRevision,
)


def make_roadmap() -> Roadmap:
    return Roadmap(
        roadmap_id="pasi-main",
        version=4,
        revision=0,
        phases=(
            RoadmapPhase("P2", "Planner", status=PhaseStatus.ACTIVE),
        ),
        tasks=(
            RoadmapTask(
                "P2.11",
                "Dependency graph",
                "P2",
                acceptance_requirements=("graph has a real read path",),
                evidence_requirements=("graph evidence",),
                status=TaskStatus.PLANNED,
            ),
            RoadmapTask(
                "P2.12",
                "Project sync",
                "P2",
                depends_on=("P2.11",),
                acceptance_requirements=("sync is idempotent",),
                evidence_requirements=("sync evidence",),
                status=TaskStatus.PLANNED,
            ),
            RoadmapTask(
                "P2.13",
                "Task detail",
                "P2",
                acceptance_requirements=("read model is authoritative",),
                evidence_requirements=("read-model evidence",),
                status=TaskStatus.PLANNED,
            ),
        ),
    )


class FakeProjectsTransport:
    def __init__(self) -> None:
        self.items: dict[str, RemoteProjectItem] = {}
        self.add_calls = 0

    def list_items(self, project_ref: str) -> tuple[RemoteProjectItem, ...]:
        return tuple(
            sorted(
                item for item in self.items.values()
                if item.project_id == project_ref
            )
        )

    def add_item(
        self,
        project_ref: str,
        *,
        content_id: str,
        content_type: str,
    ) -> RemoteProjectItem:
        self.add_calls += 1
        existing = next(
            (
                item
                for item in self.items.values()
                if item.project_id == project_ref
                and item.content_id == content_id
            ),
            None,
        )
        if existing is not None:
            return existing
        item = RemoteProjectItem(
            project_id=project_ref,
            item_id=f"item-{self.add_calls}",
            content_id=content_id,
            content_type=content_type,
            title="Imported issue",
            updated_at="2026-09-27T10:00:00+00:00",
            remote_revision="r1",
        )
        self.items[item.item_id] = item
        return item


def test_p2_11_dependency_graph_is_revision_bound_and_deterministic():
    roadmap = make_roadmap()
    graph = DependencyGraph.build(roadmap)

    assert graph.prerequisites["P2.12"] == ("P2.11",)
    assert graph.dependents["P2.11"] == ("P2.12",)
    assert "P2.11" in graph.eligible_task_ids
    assert graph.blocked_reasons["P2.12"] == "waiting_on:P2.11"

    reloaded = Roadmap.from_mapping(roadmap.to_dict())
    assert DependencyGraph.build(reloaded).canonical_sha256 == graph.canonical_sha256

    changed = roadmap.transition_task(
        "P2.11",
        TaskStatus.ACTIVE,
        expected_revision=0,
    )
    with pytest.raises(StaleDependencyGraph):
        graph.assert_fresh(changed)


def test_p2_13_task_detail_is_authoritative_and_rehydrates(tmp_path: Path):
    roadmap = make_roadmap()

    selection_store = SQLiteSelectionStore(tmp_path / "selection.db")
    selection = EvidenceAwareTaskSelector().select(roadmap, evidence={})
    selection_store.record(selection)

    ledger = SQLiteOperationLedger(tmp_path / "ledger.db")
    ledger.register(
        OperationLedgerEntry(
            operation_id="op-task-detail",
            task_id="P2.13",
            run_id="run-task-detail",
            provider="local",
            branch="pasi/p2",
            outcome="queued",
        )
    )

    events = SQLiteEventStore(tmp_path / "events.db")
    events.append(
        DurableEvent(
            event_id="event-task-detail",
            event_type="operation.queued",
            source="runner",
            operation_id="op-task-detail",
            task_id="P2.13",
            run_id="run-task-detail",
            payload={"status": "queued"},
            evidence_refs=("evidence://P2.13",),
        )
    )

    detail = TaskDetailReadModel(
        roadmap=roadmap,
        selection_store=selection_store,
        ledger=ledger,
        events=events,
    ).get("P2.13")
    assert detail.roadmap_revision == roadmap.revision
    assert detail.task["id"] == "P2.13"
    assert detail.prerequisites == ()
    assert detail.queued is True
    assert detail.event_count == 1

    with pytest.raises(TaskDetailNotFound):
        detail_read = TaskDetailReadModel(roadmap=roadmap)
        detail_read.get("missing")


def test_p2_14_ranking_explanation_is_deterministic_and_persisted(tmp_path: Path):
    roadmap = make_roadmap()
    selection = EvidenceAwareTaskSelector().select(
        roadmap,
        evidence={},
        advisory_scores={"P2.11": 1.0, "P2.13": 1.0},
    )
    explanation = RankingExplanation.from_decision(
        selection,
        manual_order={"P2.13": 1, "P2.11": 2},
    )
    assert explanation.selected_task_id == "P2.11"
    assert explanation.canonical_sha256

    store = SQLiteRankingExplanationStore(tmp_path / "ranking.db")
    assert store.record(explanation) == 1


def test_p2_15_preferences_are_scoped_and_revision_protected(tmp_path: Path):
    store = SQLiteWorkspacePreferenceStore(tmp_path / "prefs.db")
    default = store.get("project:pasi", "planner.view")
    assert default.value == "overview"

    updated = store.set(
        "project:pasi",
        "planner.view",
        "dependencies",
        expected_revision=0,
    )
    assert updated.revision == 1
    assert store.get("project:pasi", "planner.view").value == "dependencies"

    with pytest.raises(StalePreferenceRevision):
        store.set(
            "project:pasi",
            "planner.view",
            "board",
            expected_revision=0,
        )
    with pytest.raises(PreferenceError):
        store.get("project:pasi", "planner.unknown")


def test_p2_16_search_is_scoped_deterministic_and_freshness_aware(tmp_path: Path):
    store = SQLiteSearchIndex(tmp_path / "search.db")
    store.upsert(
        IndexedDocument(
            document_id="task-1",
            scope="project:pasi",
            kind="task",
            title="Dependency graph",
            text="canonical dependency graph for planner",
            deep_link="pasi://task/P2.11",
            freshness="fresh",
            indexed_at="2026-09-27T10:00:00+00:00",
        )
    )
    store.upsert(
        IndexedDocument(
            document_id="task-other",
            scope="project:other",
            kind="task",
            title="Dependency graph",
            text="other project",
            deep_link="pasi://task/OTHER",
            freshness="fresh",
            indexed_at="2026-09-27T10:00:00+00:00",
        )
    )
    store.upsert(
        IndexedDocument(
            document_id="file-stale",
            scope="project:pasi",
            kind="file",
            title="Old graph notes",
            text="dependency graph stale notes",
            deep_link="pasi://file/old",
            freshness="stale",
            indexed_at="2026-09-26T10:00:00+00:00",
        )
    )

    results = store.search("dependency graph", scope="project:pasi")
    assert [result.document_id for result in results] == ["file-stale", "task-1"]
    assert all(result.scope == "project:pasi" for result in results)
    assert results[-1].deep_link == "pasi://task/P2.11"

    fresh_only = store.search(
        "dependency graph",
        scope="project:pasi",
        include_stale=False,
    )
    assert [result.document_id for result in fresh_only] == ["task-1"]


def test_p2_12_project_sync_import_export_conflict_and_restart(tmp_path: Path):
    transport = FakeProjectsTransport()
    store = SQLiteProjectSyncStore(str(tmp_path / "projects.db"))
    sync = GitHubProjectsSynchronizer(transport=transport, store=store)

    created = sync.ensure_export(
        project_ref="7",
        roadmap_id="pasi-main",
        local_revision=3,
        content_id="issue-123",
        content_type="Issue",
    )
    assert created.content_id == "issue-123"
    assert transport.add_calls == 1

    replay = sync.ensure_export(
        project_ref="7",
        roadmap_id="pasi-main",
        local_revision=3,
        content_id="issue-123",
        content_type="Issue",
    )
    assert replay.item_id == created.item_id
    assert transport.add_calls == 1

    with pytest.raises(ProjectSyncConflict):
        sync.ensure_export(
            project_ref="7",
            roadmap_id="pasi-main",
            local_revision=3,
            content_id="issue-123",
            content_type="Issue",
            expected_remote_revision="stale",
        )

    refreshed = sync.refresh(
        project_ref="7",
        roadmap_id="pasi-main",
        local_revision=3,
    )
    assert refreshed == (created,)

    restarted = SQLiteProjectSyncStore(str(tmp_path / "projects.db"))
    state = restarted.get_state("7")
    assert isinstance(state, ProjectSyncState)
    assert state.status == "success"


def test_p2_17_notifications_are_event_derived_idempotent_and_acknowledgeable(tmp_path: Path):
    event_store = SQLiteEventStore(tmp_path / "events.db")
    event = event_store.append(
        DurableEvent(
            event_id="event-notify",
            event_type="verification.completed",
            source="verifier",
            operation_id="op-notify",
            task_id="P2.17",
            run_id="run-notify",
            payload={"status": "PASS"},
            evidence_refs=("evidence://P2.17",),
        )
    )
    notifications = SQLiteNotificationStore(tmp_path / "notifications.db")

    first = notifications.derive_from_event(
        event,
        scope="project:pasi",
        severity="info",
        message="Verification completed.",
        entity_type="task",
        entity_id="P2.17",
    )
    second = notifications.derive_from_event(
        event,
        scope="project:pasi",
        severity="info",
        message="Verification completed.",
        entity_type="task",
        entity_id="P2.17",
    )
    assert first.notification_id == second.notification_id
    assert len(notifications.list(scope="project:pasi")) == 1

    acknowledged = notifications.acknowledge(
        first.notification_id,
        expected_revision=0,
    )
    assert acknowledged.acknowledged is True
    assert notifications.list(scope="project:pasi") == ()

    with pytest.raises(NotificationError):
        notifications.acknowledge(
            first.notification_id,
            expected_revision=0,
        )


def test_p2_12_typed_projects_transport_distinguishes_auth_rate_and_bad_data(monkeypatch):
    import urllib.error

    from pasi.core.projects_sync import (
        GitHubProjectsRESTTransport,
        ProjectAuthenticationError,
        ProjectRateLimitError,
        ProjectRemoteDataError,
    )

    transport = GitHubProjectsRESTTransport(
        token="token",
        owner="owner",
        owner_kind="org",
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"[]"

    def auth_failure(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com", 401, "unauthorized", {}, None)

    monkeypatch.setattr(
        "pasi.core.projects_sync.urllib.request.urlopen",
        auth_failure,
    )
    with pytest.raises(ProjectAuthenticationError):
        transport._request("GET", "/failure")

    def rate_failure(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com", 429, "rate", {}, None)

    monkeypatch.setattr(
        "pasi.core.projects_sync.urllib.request.urlopen",
        rate_failure,
    )
    with pytest.raises(ProjectRateLimitError):
        transport._request("GET", "/failure")

    def malformed(*args, **kwargs):
        return Response()

    monkeypatch.setattr(
        "pasi.core.projects_sync.urllib.request.urlopen",
        malformed,
    )
    with pytest.raises(ProjectRemoteDataError):
        transport._request("GET", "/malformed")
