from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
RUNNER_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
MAX_NAME_CHARS = 120
MAX_ENTRYPOINT_CHARS = 240
MAX_ARGS = 32
MAX_ARG_CHARS = 2000


class RunnerRegistryError(ValueError):
    """Raised when a runner definition or revision cannot be safely registered."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def registry_path() -> Path:
    configured = os.environ.get("PASI_RUNNER_REGISTRY_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".pasi" / "runner" / "registry.json").resolve()


def _project_root(project_root: Path | None = None) -> Path:
    return (project_root or Path.cwd()).resolve()


def _validate_id(value: object) -> str:
    runner_id = str(value or "").strip().casefold()
    if not RUNNER_ID_RE.fullmatch(runner_id):
        raise RunnerRegistryError(
            "runner id must match ^[a-z][a-z0-9._-]{1,63}$"
        )
    return runner_id


def _validate_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or len(name) > MAX_NAME_CHARS:
        raise RunnerRegistryError("runner name is required and bounded")
    return name


def _validate_args(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ARGS:
        raise RunnerRegistryError("runner args must be a bounded string list")
    args = [str(item) for item in value]
    if any(len(item) > MAX_ARG_CHARS for item in args):
        raise RunnerRegistryError("runner argument is too long")
    return args


def _validate_entrypoint(value: object, project_root: Path) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > MAX_ENTRYPOINT_CHARS:
        raise RunnerRegistryError("runner entrypoint is required and bounded")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise RunnerRegistryError("runner entrypoint must be workspace-relative")
    if ".." in candidate.parts:
        raise RunnerRegistryError("runner entrypoint may not escape the workspace")
    resolved = (project_root / candidate).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise RunnerRegistryError("runner entrypoint must remain inside the workspace") from exc
    if not resolved.is_file() or not os.access(resolved, os.R_OK):
        raise RunnerRegistryError("runner entrypoint must be a readable workspace file")
    return resolved.relative_to(project_root).as_posix()


def _digest(entrypoint: str, args: list[str], project_root: Path) -> str:
    path = (project_root / entrypoint).resolve()
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    digest.update(b"\0")
    digest.update(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def _load_document() -> dict[str, Any]:
    path = registry_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": SCHEMA_VERSION, "runners": {}}
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerRegistryError("runner registry is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise RunnerRegistryError("runner registry schema is unsupported")
    runners = payload.get("runners")
    if not isinstance(runners, dict):
        raise RunnerRegistryError("runner registry runners must be an object")
    return {"schema_version": SCHEMA_VERSION, "runners": runners}


def _save_document(document: Mapping[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _stored_runner(document: Mapping[str, Any], runner_id: str) -> dict[str, Any] | None:
    value = document.get("runners", {}).get(runner_id)
    return dict(value) if isinstance(value, Mapping) else None


def list_user_runners() -> list[dict[str, Any]]:
    document = _load_document()
    return [
        dict(value)
        for key, value in sorted(document["runners"].items())
        if isinstance(value, Mapping) and key not in {"m1", "168h"}
    ]


def get_runner(runner_id: object) -> dict[str, Any] | None:
    normalized = _validate_id(runner_id)
    return _stored_runner(_load_document(), normalized)


def create_runner(
    runner_id: object,
    name: object,
    entrypoint: object,
    args: object = None,
    *,
    source: str = "user",
    project_root: Path | None = None,
) -> dict[str, Any]:
    normalized = _validate_id(runner_id)
    runner_name = _validate_name(name)
    root = _project_root(project_root)
    clean_entrypoint = _validate_entrypoint(entrypoint, root)
    clean_args = _validate_args(args)

    document = _load_document()
    if normalized in document["runners"] or normalized in {"m1", "168h"}:
        raise RunnerRegistryError("runner already exists")

    timestamp = _now()
    revision = {
        "version": 1,
        "state": "stable",
        "entrypoint": clean_entrypoint,
        "args": clean_args,
        "digest": _digest(clean_entrypoint, clean_args, root),
        "created_at": timestamp,
        "created_by": str(source or "user").strip()[:32] or "user",
    }
    runner = {
        "schema_version": SCHEMA_VERSION,
        "id": normalized,
        "name": runner_name,
        "source": str(source or "user").strip()[:32] or "user",
        "created_at": timestamp,
        "updated_at": timestamp,
        "stable": revision,
        "candidate": None,
        "previous_stable": None,
    }
    document["runners"][normalized] = runner
    _save_document(document)
    return runner


def create_revision(
    runner_id: object,
    entrypoint: object,
    args: object = None,
    *,
    source: str = "automation",
    project_root: Path | None = None,
) -> dict[str, Any]:
    normalized = _validate_id(runner_id)
    root = _project_root(project_root)
    clean_entrypoint = _validate_entrypoint(entrypoint, root)
    clean_args = _validate_args(args)

    document = _load_document()
    runner = _stored_runner(document, normalized)
    if runner is None:
        raise RunnerRegistryError("runner does not exist")
    if runner.get("candidate") is not None:
        raise RunnerRegistryError("runner already has a candidate revision")

    stable = runner.get("stable")
    stable_version = int(stable.get("version", 1)) if isinstance(stable, Mapping) else 1
    revision = {
        "version": stable_version + 1,
        "state": "candidate",
        "entrypoint": clean_entrypoint,
        "args": clean_args,
        "digest": _digest(clean_entrypoint, clean_args, root),
        "created_at": _now(),
        "created_by": str(source or "automation").strip()[:32] or "automation",
        "base_version": stable_version,
    }
    runner["candidate"] = revision
    runner["updated_at"] = revision["created_at"]
    document["runners"][normalized] = runner
    _save_document(document)
    return revision


def promote_revision(
    runner_id: object,
    version: object,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    normalized = _validate_id(runner_id)
    requested = int(version)
    document = _load_document()
    runner = _stored_runner(document, normalized)
    if runner is None:
        raise RunnerRegistryError("runner does not exist")
    candidate = runner.get("candidate")
    if not isinstance(candidate, Mapping) or int(candidate.get("version", -1)) != requested:
        raise RunnerRegistryError("requested candidate revision is not staged")

    root = _project_root(project_root)
    clean_entrypoint = _validate_entrypoint(candidate.get("entrypoint"), root)
    clean_args = _validate_args(candidate.get("args"))
    promoted = dict(candidate)
    promoted.update({
        "state": "stable",
        "entrypoint": clean_entrypoint,
        "args": clean_args,
        "promoted_at": _now(),
    })
    runner["previous_stable"] = runner.get("stable")
    runner["stable"] = promoted
    runner["candidate"] = None
    runner["updated_at"] = promoted["promoted_at"]
    document["runners"][normalized] = runner
    _save_document(document)
    return runner


def rollback_runner(runner_id: object) -> dict[str, Any]:
    normalized = _validate_id(runner_id)
    document = _load_document()
    runner = _stored_runner(document, normalized)
    if runner is None:
        raise RunnerRegistryError("runner does not exist")
    candidate = runner.get("candidate")
    previous = runner.get("previous_stable")
    if isinstance(candidate, Mapping):
        runner["candidate"] = None
    elif isinstance(previous, Mapping):
        current = runner.get("stable")
        restored = dict(previous)
        restored["state"] = "stable"
        restored["rolled_back_at"] = _now()
        runner["stable"] = restored
        runner["previous_stable"] = current
    else:
        raise RunnerRegistryError("runner has no candidate or previous stable revision")
    runner["updated_at"] = _now()
    document["runners"][normalized] = runner
    _save_document(document)
    return runner


def registry_snapshot() -> dict[str, Any]:
    document = _load_document()
    return {
        "schema_version": SCHEMA_VERSION,
        "runners": list(document["runners"].values()),
    }
