from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class ProjectSyncError(RuntimeError):
    """Base class for typed GitHub Projects synchronization failures."""


class ProjectAuthenticationError(ProjectSyncError):
    """Remote authentication/authorization failed."""


class ProjectRateLimitError(ProjectSyncError):
    """Remote provider rate-limited the sync."""


class ProjectRemoteDataError(ProjectSyncError):
    """Remote provider returned malformed data."""


class ProjectSyncConflict(ProjectSyncError):
    """Local sync revision and remote revision disagree."""


@dataclass(frozen=True)
class RemoteProjectItem:
    project_id: str
    item_id: str
    content_id: str
    content_type: str
    title: str
    updated_at: str
    remote_revision: str
    archived: bool = False


@dataclass(frozen=True)
class ProjectSyncState:
    project_ref: str
    roadmap_id: str
    local_revision: int
    last_remote_revision: str
    status: str
    last_success_at: str = ""
    last_error: str = ""
    source: str = "github-projects"


class ProjectsTransport(Protocol):
    def list_items(self, project_ref: str) -> tuple[RemoteProjectItem, ...]: ...

    def add_item(
        self,
        project_ref: str,
        *,
        content_id: str,
        content_type: str,
    ) -> RemoteProjectItem: ...


class GitHubProjectsRESTTransport:
    """Typed GitHub Projects v2 REST transport.

    The transport is intentionally isolated from synchronization policy so the
    policy can be exercised against a deterministic provider fixture.
    """

    def __init__(
        self,
        *,
        token: str,
        owner: str,
        owner_kind: str = "org",
        api_version: str = "2026-03-10",
        base_url: str = "https://api.github.com",
    ) -> None:
        if not token:
            raise ValueError("GitHub token is required")
        if owner_kind not in {"org", "user"}:
            raise ValueError("owner_kind must be org or user")
        self.token = token
        self.owner = owner
        self.owner_kind = owner_kind
        self.api_version = api_version
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "PASI-Engineering-Workspace",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise ProjectAuthenticationError(
                    f"GitHub Projects authentication/authorization failed: HTTP {exc.code}"
                ) from exc
            if exc.code == 429:
                raise ProjectRateLimitError("GitHub Projects rate limit exceeded") from exc
            if 500 <= exc.code < 600:
                raise ProjectSyncError(
                    f"GitHub Projects server failure: HTTP {exc.code}"
                ) from exc
            raise ProjectSyncError(
                f"GitHub Projects request failed: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProjectSyncError("GitHub Projects transport failure") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectRemoteDataError("GitHub Projects returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProjectRemoteDataError("GitHub Projects response must be an object")
        return payload

    def list_items(self, project_ref: str) -> tuple[RemoteProjectItem, ...]:
        try:
            project_number = int(project_ref)
        except ValueError as exc:
            raise ProjectRemoteDataError("project_ref must be a numeric project number") from exc

        owner_path = "orgs" if self.owner_kind == "org" else "users"
        payload = self._request(
            "GET",
            f"/{owner_path}/{urllib.parse.quote(self.owner, safe='')}/projectsV2/{project_number}/items?per_page=100",
        )
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ProjectRemoteDataError("GitHub Projects items response lacks items array")

        parsed: list[RemoteProjectItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise ProjectRemoteDataError("GitHub Projects item is not an object")
            item_id = raw.get("id")
            updated_at = raw.get("updated_at")
            content = raw.get("content")
            if not isinstance(item_id, str) or not item_id:
                raise ProjectRemoteDataError("project item is missing id")
            if not isinstance(updated_at, str) or not updated_at:
                raise ProjectRemoteDataError("project item is missing updated_at")

            content_id = ""
            content_type = "unknown"
            title = ""
            if isinstance(content, dict):
                content_id = str(content.get("id") or content.get("node_id") or "")
                content_type = str(content.get("type") or content.get("content_type") or "unknown")
                title = str(content.get("title") or content.get("name") or "")

            parsed.append(
                RemoteProjectItem(
                    project_id=str(project_number),
                    item_id=item_id,
                    content_id=content_id,
                    content_type=content_type,
                    title=title,
                    updated_at=updated_at,
                    remote_revision=str(updated_at),
                    archived=bool(raw.get("archived", False)),
                )
            )
        return tuple(sorted(parsed, key=lambda item: item.item_id))

    def add_item(
        self,
        project_ref: str,
        *,
        content_id: str,
        content_type: str,
    ) -> RemoteProjectItem:
        # Projects v2 supports adding an issue/PR by content node id. The
        # provider contract keeps content_type explicit so unsupported content
        # kinds can be rejected without silent coercion.
        if content_type not in {"Issue", "PullRequest", "issue", "pull_request"}:
            raise ProjectRemoteDataError(
                f"unsupported GitHub Projects content type: {content_type}"
            )
        try:
            project_number = int(project_ref)
        except ValueError as exc:
            raise ProjectRemoteDataError("project_ref must be numeric") from exc

        owner_path = "orgs" if self.owner_kind == "org" else "users"
        payload = self._request(
            "POST",
            f"/{owner_path}/{urllib.parse.quote(self.owner, safe='')}/projectsV2/{project_number}/items",
            {"type": "Issue" if content_type.lower() == "issue" else "PullRequest", "id": content_id},
        )
        item_id = payload.get("id")
        updated_at = payload.get("updated_at")
        if not isinstance(item_id, str) or not isinstance(updated_at, str):
            raise ProjectRemoteDataError("GitHub Projects add response lacks item identity")
        return RemoteProjectItem(
            project_id=str(project_number),
            item_id=item_id,
            content_id=content_id,
            content_type="Issue" if content_type.lower() == "issue" else "PullRequest",
            title="",
            updated_at=updated_at,
            remote_revision=updated_at,
        )


class SQLiteProjectSyncStore:
    def __init__(self, path: str) -> None:
        self.path = path
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_sync_state (
                    project_ref TEXT PRIMARY KEY,
                    roadmap_id TEXT NOT NULL,
                    local_revision INTEGER NOT NULL,
                    last_remote_revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_success_at TEXT NOT NULL,
                    last_error TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_item (
                    project_ref TEXT NOT NULL,
                    content_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    remote_revision TEXT NOT NULL,
                    archived INTEGER NOT NULL,
                    PRIMARY KEY(project_ref, content_id)
                )
                """
            )

    def list_states(self) -> tuple[ProjectSyncState, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT project_ref, roadmap_id, local_revision, last_remote_revision, status, last_success_at, last_error, source FROM project_sync_state ORDER BY project_ref"
            ).fetchall()
        return tuple(ProjectSyncState(*row) for row in rows)

    def list_items(self, project_ref: str) -> tuple[RemoteProjectItem, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT project_ref, content_id, item_id, content_type,
                       title, updated_at, remote_revision, archived
                FROM project_item
                WHERE project_ref = ?
                ORDER BY item_id
                """,
                (project_ref,),
            ).fetchall()
        return tuple(
            RemoteProjectItem(
                project_id=row[0],
                content_id=row[1],
                item_id=row[2],
                content_type=row[3],
                title=row[4],
                updated_at=row[5],
                remote_revision=row[6],
                archived=bool(row[7]),
            )
            for row in rows
        )

    def save_state(self, state: ProjectSyncState) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO project_sync_state(
                    project_ref, roadmap_id, local_revision,
                    last_remote_revision, status, last_success_at,
                    last_error, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_ref)
                DO UPDATE SET
                    roadmap_id=excluded.roadmap_id,
                    local_revision=excluded.local_revision,
                    last_remote_revision=excluded.last_remote_revision,
                    status=excluded.status,
                    last_success_at=excluded.last_success_at,
                    last_error=excluded.last_error,
                    source=excluded.source
                """,
                (
                    state.project_ref,
                    state.roadmap_id,
                    state.local_revision,
                    state.last_remote_revision,
                    state.status,
                    state.last_success_at,
                    state.last_error,
                    state.source,
                ),
            )

    def get_state(self, project_ref: str) -> ProjectSyncState | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM project_sync_state WHERE project_ref = ?",
                (project_ref,),
            ).fetchone()
        if row is None:
            return None
        return ProjectSyncState(*row)

    def save_item(self, project_ref: str, item: RemoteProjectItem) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO project_item(
                    project_ref, content_id, item_id, content_type,
                    title, updated_at, remote_revision, archived
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_ref, content_id)
                DO UPDATE SET
                    item_id=excluded.item_id,
                    content_type=excluded.content_type,
                    title=excluded.title,
                    updated_at=excluded.updated_at,
                    remote_revision=excluded.remote_revision,
                    archived=excluded.archived
                """,
                (
                    project_ref,
                    item.content_id,
                    item.item_id,
                    item.content_type,
                    item.title,
                    item.updated_at,
                    item.remote_revision,
                    int(item.archived),
                ),
            )

    def get_item(self, project_ref: str, content_id: str) -> RemoteProjectItem | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT project_ref, content_id, item_id, content_type,
                       title, updated_at, remote_revision, archived
                FROM project_item
                WHERE project_ref = ? AND content_id = ?
                """,
                (project_ref, content_id),
            ).fetchone()
        if row is None:
            return None
        return RemoteProjectItem(
            project_id=row[0],
            content_id=row[1],
            item_id=row[2],
            content_type=row[3],
            title=row[4],
            updated_at=row[5],
            remote_revision=row[6],
            archived=bool(row[7]),
        )


class GitHubProjectsSynchronizer:
    def __init__(
        self,
        *,
        transport: ProjectsTransport,
        store: SQLiteProjectSyncStore,
    ) -> None:
        self.transport = transport
        self.store = store

    def refresh(
        self,
        *,
        project_ref: str,
        roadmap_id: str,
        local_revision: int,
    ) -> tuple[RemoteProjectItem, ...]:
        remote_items = self.transport.list_items(project_ref)
        for item in remote_items:
            self.store.save_item(project_ref, item)

        latest_remote_revision = (
            max((item.remote_revision for item in remote_items), default="")
        )
        state = ProjectSyncState(
            project_ref=project_ref,
            roadmap_id=roadmap_id,
            local_revision=local_revision,
            last_remote_revision=latest_remote_revision,
            status="success",
            last_success_at=datetime.now(timezone.utc).isoformat(),
            last_error="",
        )
        self.store.save_state(state)
        return remote_items

    def ensure_export(
        self,
        *,
        project_ref: str,
        roadmap_id: str,
        local_revision: int,
        content_id: str,
        content_type: str,
        expected_remote_revision: str = "",
    ) -> RemoteProjectItem:
        existing = self.store.get_item(project_ref, content_id)
        if existing is not None:
            if (
                expected_remote_revision
                and existing.remote_revision != expected_remote_revision
            ):
                raise ProjectSyncConflict(
                    "local export targets a different remote revision"
                )
            return existing

        created = self.transport.add_item(
            project_ref,
            content_id=content_id,
            content_type=content_type,
        )
        self.store.save_item(project_ref, created)
        self.store.save_state(
            ProjectSyncState(
                project_ref=project_ref,
                roadmap_id=roadmap_id,
                local_revision=local_revision,
                last_remote_revision=created.remote_revision,
                status="success",
                last_success_at=datetime.now(timezone.utc).isoformat(),
                last_error="",
            )
        )
        return created
