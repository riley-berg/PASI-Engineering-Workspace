#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "th3-st0v3/PASI-Engineering-Workspace"
ROADMAP_FILE = Path(__file__).resolve().parents[1] / "roadmap" / "p0-p22-168h.json"
HOURS = 168.0
TASK_RE = re.compile(r"^\s*- \[([ xX])\] \*\*(P[0-9]+\.[0-9]+) — ([^*]+)\*\*", re.MULTILINE)


@dataclass(frozen=True)
class Phase:
    id: str
    issue: int
    quarter: str
    start: str
    end: str


@dataclass(frozen=True)
class Task:
    phase: Phase
    task_id: str
    title: str
    checked: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def token() -> str:
    return os.environ.get("PASI_GITHUB_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()


def github(url: str, *, method: str = "GET", body: str | None = None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "pasi-engineering-workspace-168h"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    data = body.encode("utf-8") if body else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def schedule() -> tuple[Phase, ...]:
    payload = json.loads(ROADMAP_FILE.read_text(encoding="utf-8"))
    if payload["source_repository"] != REPO or payload["duration_hours"] != 168:
        raise RuntimeError("168-hour schedule is not anchored to Engineering Workspace")
    return tuple(Phase(**item) for item in payload["phases"])


def tasks_for(phase: Phase) -> tuple[Task, ...]:
    issue = github(f"https://api.github.com/repos/{REPO}/issues/{phase.issue}")
    body = str(issue.get("body", ""))
    return tuple(
        Task(phase, m.group(2), m.group(3).strip(), m.group(1).lower() == "x")
        for m in TASK_RE.finditer(body)
    )


def all_tasks() -> list[Task]:
    result: list[Task] = []
    for phase in schedule():
        result.extend(tasks_for(phase))
    return result


def state_dir() -> Path:
    path = Path(os.environ.get("PASI_ACCEPTANCE_STATE_DIR", "~/.pasi/engineering-workspace-168h")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def emit(event: dict) -> None:
    with (state_dir() / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()


def write_state(payload: dict) -> None:
    path = state_dir() / "state.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def git(cwd: Path, *args: str, timeout: float = 60.0, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    output = (result.stdout + result.stderr).strip()
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {output}")
    return output


def ensure_worktree(root: Path, worktree: Path, branch: str) -> None:
    git(root, "fetch", "origin", "main", timeout=120)
    worktree = worktree.expanduser().resolve()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if not (worktree / ".git").exists():
        git(root, "worktree", "add", "-B", branch, str(worktree), "origin/main", timeout=120)
    elif git(worktree, "branch", "--show-current") != branch:
        if git(worktree, "status", "--porcelain", check=False):
            raise RuntimeError("acceptance worktree is not clean")
        result = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
            cwd=worktree,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            git(worktree, "checkout", branch)
        else:
            # A reusable acceptance worktree may still point at an older
            # timestamped branch. New acceptance runs must always start from
            # the freshly fetched canonical origin/main.
            git(worktree, "checkout", "--detach", "origin/main")
            git(worktree, "checkout", "-b", branch)
    if git(worktree, "status", "--porcelain", check=False):
        raise RuntimeError("acceptance worktree is not clean")


def executor() -> list[str]:
    raw = os.environ.get("PASI_ENGINEERING_EXECUTOR_CMD", "").strip()
    if raw:
        return shlex.split(raw)
    return [sys.executable, str(Path(__file__).resolve().with_name("pasi_engineering_executor.py"))]


def mark_checked(task: Task) -> None:
    if not token():
        raise RuntimeError("a GitHub token is required so canonical issue checkboxes remain authoritative")
    url = f"https://api.github.com/repos/{REPO}/issues/{task.phase.issue}"
    issue = github(url)
    body = str(issue.get("body", ""))
    pattern = re.compile(
        rf"^(- \[)[ xX](\] \*\*{re.escape(task.task_id)} — [^*]+\*\*)",
        re.MULTILINE,
    )
    updated, count = pattern.subn(r"\1x\2", body, count=1)
    if count != 1 or updated == body:
        raise RuntimeError(f"could not mark canonical GitHub task {task.task_id} complete")
    github(url, method="PATCH", body=json.dumps({"body": updated}))


def run_task(task: Task, worktree: Path, branch: str, run_id: str) -> dict:
    context = state_dir() / "current-task.json"
    context.write_text(
        json.dumps({
            "run_id": run_id,
            "phase": task.phase.id,
            "task_id": task.task_id,
            "title": task.title,
            "source_issue": task.phase.issue,
            "source_url": f"https://github.com/{REPO}/issues/{task.phase.issue}",
            "quarter": task.phase.quarter,
            "phase_start": task.phase.start,
            "phase_end": task.phase.end,
            "worktree": str(worktree),
            "branch": branch,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    before = git(worktree, "rev-parse", "HEAD")
    env = os.environ.copy()
    env.update({
        "PASI_ACCEPTANCE_RUN_ID": run_id,
        "PASI_TASK_ID": task.task_id,
        "PASI_TASK_PHASE": task.phase.id,
        "PASI_TASK_TITLE": task.title,
        "PASI_TASK_SOURCE_ISSUE": str(task.phase.issue),
        "PASI_TASK_SOURCE_URL": f"https://github.com/{REPO}/issues/{task.phase.issue}",
        "PASI_TASK_CONTEXT_FILE": str(context),
        "PASI_ACCEPTANCE_WORKTREE": str(worktree),
        "PASI_ACCEPTANCE_BRANCH": branch,
    })
    timeout = float(os.environ.get("PASI_TASK_TIMEOUT_SECONDS", "1800"))
    started = utcnow()
    emit({"event": "task_started", "at": started.isoformat(), "task_id": task.task_id, "phase": task.phase.id})
    result = subprocess.run(executor(), cwd=worktree, env=env, text=True, check=False, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"executor failed for {task.task_id}: exit {result.returncode}")
    if git(worktree, "status", "--porcelain", check=False):
        raise RuntimeError(f"executor left uncommitted changes for {task.task_id}")
    after = git(worktree, "rev-parse", "HEAD")
    if before == after:
        raise RuntimeError(f"executor completed {task.task_id} without a new commit")
    if os.environ.get("PASI_PUSH", "").strip() == "1":
        git(worktree, "push", "--set-upstream", "origin", branch, timeout=180)
    mark_checked(task)
    evidence = {
        "event": "task_completed",
        "at": utcnow().isoformat(),
        "task_id": task.task_id,
        "phase": task.phase.id,
        "issue": task.phase.issue,
        "commit_before": before,
        "commit_after": after,
        "branch": branch,
    }
    emit(evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=HOURS)
    parser.add_argument("--worktree", type=Path, default=Path("~/.pasi-worktrees/pasi-engineering-workspace-168h"))
    parser.add_argument("--branch", default=f"pasi/p0-4-168h-run-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if args.hours != HOURS:
        parser.error("the acceptance supervisor is fixed to exactly 168 hours")
    if not args.smoke:
        if not token():
            parser.error("a GitHub token is required for a real 168-hour run; use PASI_GITHUB_TOKEN or GITHUB_TOKEN")
        if os.environ.get("PASI_PUSH", "").strip() != "1":
            parser.error("PASI_PUSH=1 is required for a real 168-hour run so branch evidence is durable")

    root = Path.cwd().resolve()
    run_id = f"ew-168h-{uuid.uuid4().hex}"
    worktree = args.worktree.expanduser().resolve()
    ensure_worktree(root, worktree, args.branch)
    head = git(worktree, "rev-parse", "HEAD")
    phases = schedule()

    emit({
        "event": "run_started" if not args.smoke else "smoke_started",
        "at": utcnow().isoformat(),
        "run_id": run_id,
        "repo": REPO,
        "branch": args.branch,
        "worktree": str(worktree),
        "head": head,
        "hours": HOURS,
        "phase_count": len(phases),
    })

    if args.smoke:
        discovered = all_tasks()
        pending = [task for task in discovered if not task.checked]
        print(f"READY: {len(phases)} phases, {len(discovered)} tasks discovered, {len(pending)} unchecked")
        print(f"HEAD: {head}")
        return 0

    deadline = utcnow() + timedelta(hours=HOURS)
    write_state({
        "run_id": run_id,
        "repo": REPO,
        "branch": args.branch,
        "worktree": str(worktree),
        "started_at": utcnow().isoformat(),
        "deadline_at": deadline.isoformat(),
        "status": "running",
    })

    while utcnow() < deadline:
        pending = [task for task in all_tasks() if not task.checked]
        if not pending:
            write_state({
                "run_id": run_id, "repo": REPO, "branch": args.branch,
                "worktree": str(worktree), "status": "roadmap_complete",
                "completed_at": utcnow().isoformat(),
            })
            return 0
        task = pending[0]
        try:
            evidence = run_task(task, worktree, args.branch, run_id)
            write_state({
                "run_id": run_id, "repo": REPO, "branch": args.branch,
                "worktree": str(worktree), "deadline_at": deadline.isoformat(),
                "status": "running", "current_task": task.task_id,
                "current_phase": task.phase.id, "last_commit": evidence["commit_after"],
                "updated_at": utcnow().isoformat(),
            })
        except subprocess.TimeoutExpired:
            emit({"event": "task_timeout", "at": utcnow().isoformat(), "task_id": task.task_id})
            write_state({
                "run_id": run_id, "repo": REPO, "branch": args.branch,
                "worktree": str(worktree), "deadline_at": deadline.isoformat(),
                "status": "failed", "task_id": task.task_id, "error": "executor timeout",
            })
            return 1
        except Exception as exc:
            emit({"event": "task_failed", "at": utcnow().isoformat(), "task_id": task.task_id, "error": str(exc)})
            write_state({
                "run_id": run_id, "repo": REPO, "branch": args.branch,
                "worktree": str(worktree), "deadline_at": deadline.isoformat(),
                "status": "failed", "task_id": task.task_id, "error": str(exc),
            })
            return 1

    write_state({
        "run_id": run_id, "repo": REPO, "branch": args.branch,
        "worktree": str(worktree), "deadline_at": deadline.isoformat(),
        "status": "deadline_reached", "completed_at": utcnow().isoformat(),
    })
    emit({"event": "run_deadline_reached", "at": utcnow().isoformat(), "run_id": run_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
