from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path

from pasi.core.event_store import SQLiteEventStore
from pasi.core.failure_registry import SQLiteFailureRegistry
from pasi.core.ledger_store import SQLiteOperationLedger
from pasi.core.notifications import SQLiteNotificationStore
from pasi.core.roadmap import Roadmap
from pasi.core.roadmap_store import SQLiteRoadmapStore
from pasi.core.selection_store import SQLiteSelectionStore
from pasi.core.schedule_store import SQLiteScheduleStore
from pasi.core.memory_store import SQLiteMemoryStore
from pasi.core.retrieval import ProvenanceAwareRetriever
from pasi.core.context_compiler import ContextCompiler
from pasi.core.projects_sync import SQLiteProjectSyncStore
from pasi.core.workspace_preferences import SQLiteWorkspacePreferenceStore
from pasi.core.search_index import SQLiteSearchIndex
from pasi.core.github_issue_intake import GitHubIssueTaskIntake
from pasi.core.planner_api import PlannerConsoleService, DashboardAPIService
from pasi.core.operation_store import SQLiteOperationStateStore
from pasi.core.runtime_api import RuntimeAPIService, serve
from pasi.core.runtime_controls import RuntimeCommandStore, RuntimeControlService
from pasi.core.runtime_events import RuntimeEventFeed
from pasi.core.runtime_health import RuntimeHealth, RuntimeHealthStore
from pasi.core.runtime_projection import RuntimeIdentity, RuntimeProjectionService


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
DEFAULT_STATE_DIR = ROOT / ".runtime" / "dashboard"


def code_head() -> str:
    configured = os.environ.get("PASI_CODE_HEAD", "").strip()
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def ensure_roadmap_seed(store: SQLiteRoadmapStore, seed_path: Path) -> None:
    if store.list():
        return
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    store.create(Roadmap.from_mapping(payload))


def seed_search_index(index: SQLiteSearchIndex, roadmap: Roadmap) -> None:
    now = index.now()
    from pasi.core.search_index import IndexedDocument

    for task in roadmap.tasks:
        index.upsert(
            IndexedDocument(
                document_id=f"task:{task.id}",
                scope="project:pasi",
                kind="roadmap_task",
                title=task.title,
                text=" ".join(
                    [
                        task.title,
                        *task.acceptance_requirements,
                        *task.evidence_requirements,
                    ]
                ),
                deep_link=(
                    f"/?roadmap_id={roadmap.roadmap_id}&task_id={task.id}"
                ),
                freshness="fresh",
                indexed_at=now,
                revision=roadmap.revision,
            )
        )


def main() -> None:
    state_dir = Path(os.environ.get("PASI_DASHBOARD_STATE_DIR", str(DEFAULT_STATE_DIR)))
    token = os.environ.get("PASI_RUNTIME_TOKEN", "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
        print("Generated ephemeral runtime-control token for this process:", token)

    operation_store = SQLiteOperationStateStore(state_dir / "operation.db")
    event_store = SQLiteEventStore(state_dir / "events.db")
    ledger = SQLiteOperationLedger(state_dir / "ledger.db")
    health_store = RuntimeHealthStore(state_dir / "health.db")
    command_store = RuntimeCommandStore(state_dir / "commands.db")
    failure_registry = SQLiteFailureRegistry(state_dir / "failures.db")
    notification_store = SQLiteNotificationStore(state_dir / "notifications.db")
    roadmap_store = SQLiteRoadmapStore(state_dir / "roadmap.db")
    selection_store = SQLiteSelectionStore(state_dir / "selection.db")
    schedule_store = SQLiteScheduleStore(state_dir / "schedule.db")
    memory_store = SQLiteMemoryStore(state_dir / "memory.db")
    projects_store = SQLiteProjectSyncStore(str(state_dir / "projects.db"))
    preferences = SQLiteWorkspacePreferenceStore(state_dir / "preferences.db")
    search_index = SQLiteSearchIndex(state_dir / "search.db")
    seed_path = Path(
        os.environ.get(
            "PASI_FRONTEND_ROADMAP_SEED",
            str(ROOT / "roadmap" / "frontend-v4.json"),
        )
    )
    ensure_roadmap_seed(roadmap_store, seed_path)
    frontend_roadmap = roadmap_store.get("pasi-frontend")
    seed_search_index(search_index, frontend_roadmap)

    try:
        health_store.get()
    except Exception:
        health_store.create(
            RuntimeHealth.connected(
                controller_version=os.environ.get("PASI_CONTROLLER_VERSION", "unknown"),
                runner_version=os.environ.get("PASI_RUNNER_VERSION", "unknown"),
                provider_version=os.environ.get("PASI_PROVIDER_VERSION", "unknown"),
                code_head=code_head(),
            )
        )

    identity = RuntimeIdentity(
        code_head=code_head(),
        runtime_version=os.environ.get("PASI_RUNTIME_VERSION", "pasi-runtime-1"),
        controller_version=os.environ.get("PASI_CONTROLLER_VERSION", "unknown"),
        runner_version=os.environ.get("PASI_RUNNER_VERSION", "unknown"),
    )
    controls = RuntimeControlService(
        operation_store=operation_store,
        event_store=event_store,
        command_store=command_store,
        authorization_token=token,
    )
    projection = RuntimeProjectionService(
        operation_store=operation_store,
        event_store=event_store,
        ledger=ledger,
        health_store=health_store,
        identity=identity,
    )
    runtime_service = RuntimeAPIService(
        projection=projection,
        event_feed=RuntimeEventFeed(event_store),
        health_store=health_store,
        controls=controls,
        failure_registry=failure_registry,
        notification_store=notification_store,
        notification_scope="runtime",
    )

    planner = PlannerConsoleService(
        roadmap_store=roadmap_store,
        selection_store=selection_store,
        schedule_store=schedule_store,
        memory_store=memory_store,
        projects_store=projects_store,
        preferences=preferences,
        search_index=search_index,
        context_compiler=ContextCompiler(
            ProvenanceAwareRetriever(memory_store)
        ),
        event_store=event_store,
        ledger=ledger,
        intake=GitHubIssueTaskIntake(),
        controls=controls,
    )
    service = DashboardAPIService(runtime=runtime_service, planner=planner)

    print("PASI runtime dashboard: http://127.0.0.1:8790/")
    print("Set ?operation_id=<operation-id> to open an operation.")
    serve(
        service=service,
        host="127.0.0.1",
        port=int(os.environ.get("PASI_DASHBOARD_PORT", "8790")),
        static_root=WEB_ROOT,
    )


if __name__ == "__main__":
    main()
