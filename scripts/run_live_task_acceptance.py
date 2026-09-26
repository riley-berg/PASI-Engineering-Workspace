from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pasi.core.github_issue_tasks import load_task_catalog
from pasi.core.task_progression import TaskPromptProgression, TaskProgressionError


CHAT_URL_PATTERN = re.compile(r"^https://chatgpt\.com/c/[A-Za-z0-9_-]+$")
MAX_PATCH_CHARS = 200_000
ACCEPTANCE_TIMEOUT = 900


class LiveTaskAcceptanceError(ValueError):
    """Raised when a live task response cannot be safely accepted."""


@dataclass(frozen=True)
class LiveTaskResponse:
    provider: str
    authenticated: bool
    chat_url: str
    task_id: str
    status: str
    summary: str
    evidence: str
    patch: str
    runtime_evidence: dict[str, object]


def parse_response(path: Path) -> LiveTaskResponse:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveTaskAcceptanceError(f"invalid live response JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LiveTaskAcceptanceError("live response must be a JSON object")

    required = ("provider", "authenticated", "chat_url", "task_id", "status", "summary", "evidence", "patch")
    missing = [name for name in required if name not in payload]
    if missing:
        raise LiveTaskAcceptanceError(f"missing live response fields: {', '.join(missing)}")

    if payload["provider"] != "chatgpt_browser":
        raise LiveTaskAcceptanceError("live task acceptance requires the ChatGPT browser provider")
    if payload["authenticated"] is not True:
        raise LiveTaskAcceptanceError("live task acceptance requires an authenticated page")
    if not isinstance(payload["chat_url"], str) or not CHAT_URL_PATTERN.fullmatch(payload["chat_url"]):
        raise LiveTaskAcceptanceError("live task acceptance requires a verified ChatGPT conversation URL")
    if payload["status"] != "complete":
        raise LiveTaskAcceptanceError("task progression advances only from a complete response")

    values = {name: payload[name] for name in ("task_id", "summary", "evidence", "patch")}
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise LiveTaskAcceptanceError(f"{name} must be a non-empty string")
    if len(payload["patch"]) > MAX_PATCH_CHARS:
        raise LiveTaskAcceptanceError("live patch is too large")

    return LiveTaskResponse(
        provider=str(payload["provider"]),
        authenticated=True,
        chat_url=str(payload["chat_url"]),
        task_id=str(payload["task_id"]).strip(),
        status=str(payload["status"]),
        summary=str(payload["summary"]).strip(),
        evidence=str(payload["evidence"]).strip(),
        patch=str(payload["patch"]),
        runtime_evidence=dict(payload.get("runtime_evidence") or {}),
    )


def _run(command: list[str], cwd: Path, *, timeout: float) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise LiveTaskAcceptanceError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout[-8000:]}{result.stderr[-8000:]}"
        )
    return result.stdout


def _verify_patch_paths(worktree: Path) -> None:
    changed = _run(["git", "diff", "--cached", "--name-only"], worktree, timeout=10).splitlines()
    forbidden_prefixes = (".git/", ".runtime/")
    forbidden = [path for path in changed if path.startswith(forbidden_prefixes)]
    if forbidden:
        raise LiveTaskAcceptanceError(
            "live task patch may not modify runtime evidence or git metadata: "
            + ", ".join(forbidden)
        )


def _generic_acceptance(
    *,
    repo: Path,
    response: LiveTaskResponse,
    progression: TaskPromptProgression,
) -> tuple[str, Path]:
    if _run(["git", "status", "--porcelain", "--untracked-files=all"], repo, timeout=10).strip():
        raise LiveTaskAcceptanceError("authoritative repository must be clean before live task application")

    before = _run(["git", "rev-parse", "HEAD"], repo, timeout=10).strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"pasi/live-task-{stamp}"
    worktree = (REPO_ROOT / ".runtime" / f"live-task-worktree-{stamp}").resolve()
    patch_file = worktree / ".runtime-live-task.patch"

    worktree.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(["git", "worktree", "add", "--quiet", "-b", branch, str(worktree), before], repo, timeout=30)
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        patch_file.write_text(response.patch.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")

        _run(["git", "apply", "--check", str(patch_file)], worktree, timeout=30)
        _run(["git", "apply", "--index", str(patch_file)], worktree, timeout=30)
        _verify_patch_paths(worktree)

        _run(["bash", "scripts/check_all.sh"], worktree, timeout=ACCEPTANCE_TIMEOUT)
        _run(["git", "commit", "-m", f"accept(live-task): {response.task_id}"], worktree, timeout=60)

        after = _run(["git", "rev-parse", "HEAD"], worktree, timeout=10).strip()
        parents = _run(["git", "rev-list", "--parents", "-n", "1", after], worktree, timeout=10).strip().split()
        if parents != [after, before]:
            raise LiveTaskAcceptanceError("live task commit is not a direct child of the repository head")

        if _run(["git", "status", "--porcelain", "--untracked-files=all"], worktree, timeout=10).strip():
            raise LiveTaskAcceptanceError("live task worktree is not clean after validation")

        _run(["git", "merge", "--ff-only", after], repo, timeout=60)

        evidence_dir = repo / ".runtime" / "acceptance"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence = evidence_dir / f"live-{response.task_id.replace('/', '_')}.json"
        evidence.write_text(
            json.dumps(
                {
                    "gate": "live-task",
                    "status": "PASS",
                    "task_id": response.task_id,
                    "provider": response.provider,
                    "authenticated": response.authenticated,
                    "chat_url": response.chat_url,
                    "summary": response.summary,
                    "evidence": response.evidence,
                    "commit": after,
                    "parent": before,
                    "branch": branch,
                    "prompt_advance_rule": "advance only after validated patch, authoritative checks, commit, and clean worktree",
                    "runtime_evidence": response.runtime_evidence,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return after, evidence
    finally:
        patch_file.unlink(missing_ok=True)
        if worktree.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept one authenticated PASI live task response.")
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--progression-state", type=Path, default=REPO_ROOT / ".runtime" / "acceptance" / "task-progression.json")
    parser.add_argument("--roadmap", type=Path, default=REPO_ROOT / "roadmap" / "p0-p4.json")
    args = parser.parse_args()

    response = parse_response(args.response)

    if response.task_id == "P0.1":
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_m0_live_acceptance.py"),
                "--response",
                str(args.response),
                "--roadmap",
                str(args.roadmap),
                "--progression-state",
                str(args.progression_state),
            ],
            cwd=REPO_ROOT,
            check=False,
        )
        return result.returncode

    catalog = load_task_catalog(args.roadmap)
    progression_path = args.progression_state.expanduser().resolve()
    if progression_path.exists():
        progression = TaskPromptProgression.load(catalog=catalog, path=progression_path)
    else:
        progression = TaskPromptProgression.start(catalog=catalog, task_id=response.task_id)

    if progression.state.current_task_id != response.task_id:
        raise TaskProgressionError(
            f"live response task {response.task_id} does not match durable current task "
            f"{progression.state.current_task_id}"
        )

    commit, evidence = _generic_acceptance(
        repo=REPO_ROOT,
        response=response,
        progression=progression,
    )
    evidence_text = (
        f"{response.evidence}\ncommit={commit}\n"
        "authoritative_validation=passed\nclean_worktree=true"
    )
    progression, receipt = progression.complete(
        verified_task_id=response.task_id,
        evidence=evidence_text,
    )
    progression.save(progression_path)

    print("LIVE TASK PASS: response -> patch -> authoritative validation -> commit -> clean worktree")
    print(f"task_id={response.task_id}")
    print(f"commit={commit}")
    print(f"evidence={evidence}")
    if receipt.next_task_id:
        print(f"next_task={receipt.next_task_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
