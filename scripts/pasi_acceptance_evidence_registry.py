#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO = "th3-st0v3/PASI-Engineering-Workspace"
SCHEMA_VERSION = 1
_CONTROLLER_VERSION_RE = re.compile(r"\bCONTROLLER_VERSION\s*=\s*['\"]([^'\"]+)['\"]")
_DEPLOYMENT_ID_RE = re.compile(r"\bPASI_DEPLOYMENT_ID\s*=\s*['\"]([^'\"]+)['\"]")
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def state_dir() -> Path:
    path = Path(os.environ.get(
        "PASI_ACCEPTANCE_STATE_DIR",
        "~/.pasi/engineering-workspace-168h",
    )).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_path(root: Path | None = None) -> Path:
    path = Path(os.environ.get(
        "PASI_ACCEPTANCE_REGISTRY_PATH",
        str((root or state_dir()) / "acceptance-evidence-registry.jsonl"),
    )).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("could not resolve the acceptance repository code head")
    return result.stdout.strip()


def controller_info(extension_root: Path) -> tuple[str, str | None]:
    candidates = (
        extension_root / "src" / "content.js",
        extension_root / "content.js",
    )
    source_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if source_path is None:
        raise RuntimeError("PASI ChatGPT controller source not found for evidence provenance")
    source = source_path.read_text(encoding="utf-8")
    version_match = _CONTROLLER_VERSION_RE.search(source)
    if not version_match:
        raise RuntimeError("PASI ChatGPT controller version is missing from the tested source")
    deployment_match = _DEPLOYMENT_ID_RE.search(source)
    return (
        version_match.group(1).strip(),
        deployment_match.group(1).strip() if deployment_match else None,
    )


def observed_controller_version(runtime_root: Path) -> str | None:
    preflight = runtime_root / "last-preflight.txt"
    if not preflight.is_file():
        return None
    try:
        raw = preflight.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("controller_version")
    return str(value).strip() if value else None


def environment_snapshot() -> dict[str, Any]:
    github_actions = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "ci": bool(os.environ.get("CI")) or github_actions,
        "github_actions": github_actions,
        "github_run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT") or None,
    }


def runner_snapshot() -> dict[str, str]:
    runner_id = (
        os.environ.get("PASI_RUNNER_ID", "").strip()
        or os.environ.get("GITHUB_RUNNER_NAME", "").strip()
        or "local"
    )
    kind = "github-actions" if os.environ.get("GITHUB_ACTIONS", "").lower() == "true" else "local"
    return {"id": runner_id, "kind": kind}


def provider_name() -> str:
    return os.environ.get("PASI_PROVIDER", "chatgpt").strip() or "chatgpt"


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _relative_ref(path: Path, state_root: Path, repo_root: Path, runtime_root: Path) -> str:
    resolved = path.expanduser().resolve()
    for prefix, root in (("runtime", runtime_root), ("state", state_root), ("worktree", repo_root)):
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return f"{prefix}/{relative.as_posix()}" if str(relative) != "." else prefix
    raise ValueError(
        "acceptance artifact must be inside the repository, acceptance state, or runtime root"
    )


def artifact_entry(ref: Path | str, *, state_root: Path, repo_root: Path, runtime_root: Path) -> dict[str, Any]:
    if isinstance(ref, str) and ref.startswith("git:"):
        digest = ref.split(":", 1)[1].strip()
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", digest):
            raise ValueError("invalid git artifact reference")
        return {
            "ref": f"git:{digest}",
            "digest_algorithm": "git-object-id",
            "digest": digest,
            "bytes": None,
        }
    path = Path(ref).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"acceptance artifact does not exist: {ref}")
    logical_ref = _relative_ref(path, state_root, repo_root, runtime_root)
    digest, size = sha256_file(path)
    return {
        "ref": logical_ref,
        "digest_algorithm": "sha256",
        "digest": digest,
        "bytes": size,
    }


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _locked_last_record(path: Path, handle: Any) -> dict[str, Any] | None:
    if not path.exists():
        return None
    handle.flush()
    handle.seek(0)
    lines = [line.strip() for line in handle.readlines() if line.strip()]
    if not lines:
        return None
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("acceptance registry contains invalid JSON") from exc
    return value if isinstance(value, dict) else None


def _append_registry(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _canonical_json(record) + "\n"
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _register_record(path: Path, base: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line_base = dict(base)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            previous = _locked_last_record(path, handle)
            previous_digest = previous.get("record_digest") if previous else None
            line_base["previous_record_digest"] = previous_digest
            line_base["record_digest_algorithm"] = "sha256"
            line_base["record_digest"] = hashlib.sha256(
                _canonical_json(line_base).encode("utf-8")
            ).hexdigest()
            handle.seek(0, 2)
            handle.write(_canonical_json(line_base) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def register_acceptance_artifact(
    *,
    repo_root: Path,
    extension_root: Path,
    run_id: str,
    task_id: str | None,
    phase: str | None,
    branch: str,
    artifact_kind: str,
    artifact_refs: Iterable[Path | str] = (),
    code_head: str | None = None,
    commit_sha: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    metadata: Mapping[str, Any] | None = None,
    state_root: Path | None = None,
) -> str:
    if not run_id.strip():
        raise ValueError("run_id is required")
    if not branch.strip():
        raise ValueError("branch is required")
    if not artifact_kind.strip():
        raise ValueError("artifact_kind is required")

    state_root = (state_root or state_dir()).expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    extension_root = extension_root.expanduser().resolve()
    runtime_root = Path(os.environ.get(
        "PASI_ENGINEERING_RUNTIME_DIR",
        str(state_root / "runtime"),
    )).expanduser().resolve()
    state_root.mkdir(parents=True, exist_ok=True)

    code_head = code_head or git_head(repo_root)
    controller_version, deployment_id = controller_info(extension_root)
    observed_version = observed_controller_version(runtime_root)
    if observed_version is not None and observed_version != controller_version:
        raise RuntimeError(
            f"controller version provenance mismatch: source={controller_version!r}, observed={observed_version!r}"
        )

    registered_at = utcnow()
    started = started_at or registered_at
    completed = completed_at
    artifact_entries = [
        artifact_entry(ref, state_root=state_root, repo_root=repo_root, runtime_root=runtime_root)
        for ref in artifact_refs
    ]
    if commit_sha:
        artifact_entries.append(
            artifact_entry(
                f"git:{commit_sha}",
                state_root=state_root,
                repo_root=repo_root,
                runtime_root=runtime_root,
            )
        )

    artifact_id = (
        f"{artifact_kind}:{run_id}:{task_id or 'run'}:"
        f"{registered_at.strftime('%Y%m%dT%H%M%S.%fZ')}"
    )
    artifact_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", artifact_id)
    manifest_path = state_root / "evidence" / f"{artifact_key}.json"
    manifest_ref = f"state/{manifest_path.relative_to(state_root).as_posix()}"

    timestamps = {
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat() if completed else None,
        "registered_at": registered_at.isoformat(),
    }
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "repository": REPO,
        "artifact_kind": artifact_kind,
        "code_head": code_head,
        "controller_version": controller_version,
        "observed_controller_version": observed_version,
        "deployment_id": deployment_id,
        "environment": environment_snapshot(),
        "runner": runner_snapshot(),
        "provider": provider_name(),
        "timestamps": timestamps,
        "run_id": run_id,
        "task_id": task_id,
        "phase": phase,
        "branch": branch,
        "artifacts": artifact_entries,
        "metadata": dict(metadata or {}),
    }
    _write_json_atomic(manifest_path, provenance)
    manifest_digest, manifest_bytes = sha256_file(manifest_path)

    base_record = {
        "registry_schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_kind": artifact_kind,
        "artifact_ref": manifest_ref,
        "artifact_digest_algorithm": "sha256",
        "artifact_digest": manifest_digest,
        "artifact_bytes": manifest_bytes,
        "repository": REPO,
        "code_head": code_head,
        "controller_version": controller_version,
        "observed_controller_version": observed_version,
        "deployment_id": deployment_id,
        "environment": provenance["environment"],
        "runner": provenance["runner"],
        "provider": provenance["provider"],
        "timestamps": provenance["timestamps"],
        "run_id": run_id,
        "task_id": task_id,
        "phase": phase,
        "branch": branch,
    }
    _register_record(registry_path(state_root), base_record)
    return manifest_ref


if __name__ == "__main__":
    print("PASI acceptance evidence registry module; use register_acceptance_artifact() from the supervisor.")
