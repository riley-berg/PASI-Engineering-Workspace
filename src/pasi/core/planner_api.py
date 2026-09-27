from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pasi.core.context_compiler import ContextCompilationError, ContextCompiler
from pasi.core.dependency_graph import DependencyGraph
from pasi.core.github_issue_intake import GitHubIssueTaskIntake, IssueIntakeError
from pasi.core.memory_compaction import MemoryCompactor, CompactionError
from pasi.core.memory_store import SQLiteMemoryStore
from pasi.core.projects_sync import SQLiteProjectSyncStore
from pasi.core.roadmap import (
    LifecycleTransitionError,
    PhaseStatus,
    RoadmapError,
    StaleRoadmapRevision,
    TaskStatus,
)
from pasi.core.roadmap_store import RoadmapNotFound, SQLiteRoadmapStore
from pasi.core.schedule_store import ScheduleNotFound, SQLiteScheduleStore
from pasi.core.search_index import SearchError, SQLiteSearchIndex
from pasi.core.selection_store import SelectionNotFound, SQLiteSelectionStore
from pasi.core.task_detail import TaskDetailNotFound, TaskDetailReadModel
from pasi.core.task_selection import StaleSelection
from pasi.core.workspace_preferences import (
    PreferenceError,
    SQLiteWorkspacePreferenceStore,
)
from pasi.core.runtime_controls import AuthorizationError, RuntimeControlService


class PlannerAPIError(ValueError):
    """Raised when planner API inputs violate contract."""


class PlannerConsoleService:
    """Authoritative planner/read-model API composed from existing P2 stores."""

    DEFAULT_ROADMAP_ID = "pasi-frontend"

    def __init__(
        self,
        *,
        roadmap_store: SQLiteRoadmapStore,
        selection_store: SQLiteSelectionStore,
        schedule_store: SQLiteScheduleStore,
        memory_store: SQLiteMemoryStore,
        projects_store: SQLiteProjectSyncStore,
        preferences: SQLiteWorkspacePreferenceStore,
        search_index: SQLiteSearchIndex,
        context_compiler: ContextCompiler,
        event_store,
        ledger,
        intake: GitHubIssueTaskIntake,
        controls: RuntimeControlService,
    ) -> None:
        self.roadmap_store = roadmap_store
        self.selection_store = selection_store
        self.schedule_store = schedule_store
        self.memory_store = memory_store
        self.projects_store = projects_store
        self.preferences = preferences
        self.search_index = search_index
        self.context_compiler = context_compiler
        self.event_store = event_store
        self.ledger = ledger
        self.intake = intake
        self.controls = controls

    def _roadmap(self, roadmap_id: str | None) -> Any:
        return self.roadmap_store.get(roadmap_id or self.DEFAULT_ROADMAP_ID)

    def _summary(self, roadmap: Any) -> dict[str, Any]:
        graph = DependencyGraph.build(roadmap)
        return {
            "roadmap": roadmap.to_dict(),
            "dependency_graph": graph.to_dict(),
            "ready_task_ids": list(graph.eligible_task_ids),
            "blocked_reasons": graph.blocked_reasons,
        }

    def request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(path)
        query = parse_qs(parsed.query)

        if method == "GET" and parsed.path == "/v1/planner/roadmaps":
            return HTTPStatus.OK, {
                "roadmaps": self._list_roadmaps(),
                "count": len(self._list_roadmaps()),
            }

        if method == "GET" and parsed.path == "/v1/planner/roadmap":
            try:
                roadmap = self._roadmap(query.get("roadmap_id", [None])[0])
                return HTTPStatus.OK, self._summary(roadmap)
            except RoadmapNotFound:
                return HTTPStatus.NOT_FOUND, {"error": "roadmap not found"}

        if method == "GET" and parsed.path.startswith("/v1/planner/tasks/"):
            roadmap = self._roadmap(query.get("roadmap_id", [None])[0])
            task_id = parsed.path.rsplit("/", 1)[-1]
            try:
                detail = TaskDetailReadModel(
                    roadmap=roadmap,
                    selection_store=self.selection_store,
                    ledger=self.ledger,
                    events=self.event_store,
                ).get(task_id)
            except TaskDetailNotFound:
                return HTTPStatus.NOT_FOUND, {"error": "task not found"}
            return HTTPStatus.OK, detail.to_dict()

        if method == "GET" and parsed.path == "/v1/planner/selection":
            roadmap = self._roadmap(query.get("roadmap_id", [None])[0])
            try:
                decision = self.selection_store.latest(
                    roadmap.roadmap_id,
                    roadmap.revision,
                )
                decision.assert_fresh(roadmap)
                return HTTPStatus.OK, {
                    "available": True,
                    "decision": decision.__dict__,
                }
            except (SelectionNotFound, StaleSelection):
                return HTTPStatus.OK, {
                    "available": False,
                    "decision": None,
                    "reason": "no_current_selection",
                }

        if method == "GET" and parsed.path == "/v1/planner/schedule":
            roadmap = self._roadmap(query.get("roadmap_id", [None])[0])
            try:
                decision = self.schedule_store.latest(
                    roadmap.roadmap_id,
                    roadmap.revision,
                )
                decision.assert_fresh(roadmap)
                return HTTPStatus.OK, {
                    "available": True,
                    "decision": {
                        "roadmap_id": decision.roadmap_id,
                        "roadmap_revision": decision.roadmap_revision,
                        "capacity": decision.capacity.__dict__,
                        "scheduled": [
                            {
                                "task_id": item.task_id,
                                "estimate": item.estimate.__dict__,
                                "advisory_priority": item.advisory_priority,
                            }
                            for item in decision.scheduled
                        ],
                        "excluded": decision.excluded,
                        "created_at": decision.created_at,
                    },
                }
            except (ScheduleNotFound, KeyError):
                return HTTPStatus.OK, {
                    "available": False,
                    "decision": None,
                }

        if method == "GET" and parsed.path == "/v1/planner/memory":
            scope = query.get("scope", ["project:pasi"])[0]
            kind = query.get("kind", [None])[0]
            memories = self.memory_store.list(
                scope=scope,
                kind=kind,
                include_archived=query.get("include_archived", ["false"])[0].lower() == "true",
            )
            return HTTPStatus.OK, {
                "scope": scope,
                "memories": [memory.to_dict() for memory in memories],
                "count": len(memories),
            }

        if method == "GET" and parsed.path.startswith("/v1/planner/context/"):
            roadmap = self._roadmap(query.get("roadmap_id", [None])[0])
            task_id = parsed.path.rsplit("/", 1)[-1]
            try:
                task = roadmap.task(task_id)
                compiled = self.context_compiler.compile(
                    task,
                    scope=query.get("scope", [f"task:{task_id}"])[0],
                    query=query.get("query", [None])[0],
                )
            except (KeyError, ContextCompilationError) as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, compiled.to_dict()

        if method == "GET" and parsed.path == "/v1/planner/search":
            try:
                result = self.search_index.search(
                    query.get("q", [""])[0],
                    scope=query.get("scope", ["project:pasi"])[0],
                    limit=int(query.get("limit", ["20"])[0]),
                    include_stale=query.get("include_stale", ["true"])[0].lower() == "true",
                )
            except (SearchError, ValueError) as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, {
                "results": [item.__dict__ for item in result],
                "count": len(result),
            }

        if method == "GET" and parsed.path == "/v1/planner/preferences":
            scope = query.get("scope", ["workspace:pasi"])[0]
            try:
                values = self.preferences.list(scope)
            except PreferenceError as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, {
                "scope": scope,
                "preferences": [
                    {
                        "key": item.key,
                        "value": item.value,
                        "revision": item.revision,
                    }
                    for item in values
                ],
            }

        if method == "GET" and parsed.path == "/v1/planner/projects":
            project_ref = query.get("project_ref", [""])[0]
            if not project_ref:
                return HTTPStatus.BAD_REQUEST, {"error": "project_ref is required"}
            state = self.projects_store.get_state(project_ref)
            items = self.projects_store.list_items(project_ref)
            return HTTPStatus.OK, {
                "state": state.__dict__ if state is not None else None,
                "items": [item.__dict__ for item in items],
                "count": len(items),
            }

        if method == "GET" and parsed.path == "/v1/planner/compaction":
            scope = query.get("scope", ["project:pasi"])[0]
            kind = query.get("kind", [None])[0]
            records = self.memory_store.list(scope=scope, kind=kind)
            try:
                plans = MemoryCompactor().plan(records)
            except CompactionError as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, {
                "scope": scope,
                "plans": [
                    {
                        "scope": plan.scope,
                        "kind": plan.kind,
                        "canonical_id": plan.canonical_id,
                        "redundant_ids": list(plan.redundant_ids),
                        "merged_provenance_refs": list(plan.merged_provenance_refs),
                    }
                    for plan in plans
                ],
            }

        if method == "POST" and parsed.path == "/v1/planner/intake/github/preview":
            try:
                proposal = self.intake.parse(
                    body or {},
                    phase_id=str((body or {}).get("phase_id", "")),
                    existing_task_ids=set((body or {}).get("existing_task_ids", [])),
                )
            except IssueIntakeError as exc:
                return HTTPStatus.BAD_REQUEST, {
                    "valid": False,
                    "error": str(exc),
                }
            return HTTPStatus.OK, {
                "valid": True,
                "task": proposal.task.to_dict(),
                "source": {
                    "issue_number": proposal.source_issue_number,
                    "url": proposal.source_url,
                    "title": proposal.source_title,
                },
            }

        if method == "POST" and parsed.path == "/v1/planner/intake/github/apply":
            auth = headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return HTTPStatus.UNAUTHORIZED, {"error": "missing bearer authorization"}
            try:
                self.controls.authorize(auth[7:])
                roadmap = self._roadmap(str((body or {}).get("roadmap_id", self.DEFAULT_ROADMAP_ID)))
                proposal_payload = dict((body or {}).get("issue") or {})
                phase_id = str((body or {}).get("phase_id", ""))
                proposal = self.intake.parse(
                    proposal_payload,
                    phase_id=phase_id,
                    existing_task_ids={task.id for task in roadmap.tasks},
                )
                updated = self.intake.apply(roadmap, proposal)
                saved = self.roadmap_store.save(
                    updated,
                    expected_revision=roadmap.revision,
                )
            except AuthorizationError as exc:
                return HTTPStatus.UNAUTHORIZED, {"error": str(exc)}
            except (IssueIntakeError, RoadmapNotFound, StaleRoadmapRevision, ValueError) as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, self._summary(saved)

        if method == "POST" and parsed.path == "/v1/planner/roadmap/transition":
            auth = headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return HTTPStatus.UNAUTHORIZED, {"error": "missing bearer authorization"}
            payload = body or {}
            try:
                self.controls.authorize(auth[7:])
                roadmap = self._roadmap(str(payload.get("roadmap_id", self.DEFAULT_ROADMAP_ID)))
                expected_revision = int(payload.get("expected_revision", -1))
                object_type = str(payload.get("object_type", ""))
                object_id = str(payload.get("object_id", ""))
                new_status = str(payload.get("status", ""))

                if object_type == "phase":
                    updated = roadmap.transition_phase(
                        object_id,
                        PhaseStatus(new_status),
                        expected_revision=expected_revision,
                    )
                elif object_type == "task":
                    evidence_refs = tuple(str(ref) for ref in payload.get("evidence_refs", []))
                    updated = roadmap.transition_task(
                        object_id,
                        TaskStatus(new_status),
                        expected_revision=expected_revision,
                        evidence_refs=evidence_refs,
                    )
                else:
                    raise LifecycleTransitionError("object_type must be phase or task")

                saved = self.roadmap_store.save(
                    updated,
                    expected_revision=roadmap.revision,
                )
            except AuthorizationError as exc:
                return HTTPStatus.UNAUTHORIZED, {"error": str(exc)}
            except (RoadmapError, ValueError, RoadmapNotFound) as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
            return HTTPStatus.OK, self._summary(saved)

        return HTTPStatus.NOT_FOUND, {"error": "not found"}

    def _list_roadmaps(self) -> list[dict[str, Any]]:
        if hasattr(self.roadmap_store, "list"):
            return [
                {
                    "roadmap_id": roadmap.roadmap_id,
                    "version": roadmap.version,
                    "revision": roadmap.revision,
                    "status": (
                        "active"
                        if any(phase.status is PhaseStatus.ACTIVE for phase in roadmap.phases)
                        else "completed"
                    ),
                    "canonical_sha256": roadmap.canonical_sha256,
                    "task_count": len(roadmap.tasks),
                }
                for roadmap in self.roadmap_store.list()
            ]
        return []


class DashboardAPIService:
    """Compose the runtime and planner APIs without duplicating domain state."""

    def __init__(
        self,
        *,
        runtime: Any,
        planner: PlannerConsoleService,
    ) -> None:
        self.runtime = runtime
        self.planner = planner

    def request(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if path.startswith("/v1/planner"):
            return self.planner.request(
                method=method,
                path=path,
                headers=headers,
                body=body,
            )
        return self.runtime.request(
            method=method,
            path=path,
            headers=headers,
            body=body,
        )
