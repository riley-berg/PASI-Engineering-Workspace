#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from urllib.parse import quote
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "th3-st0v3/PASI-Engineering-Workspace"
ROADMAP_FILE = Path(__file__).resolve().parents[1] / "roadmap" / "p0-p22-168h.json"
HOURS = 168.0
MAX_TASK_ATTEMPTS = int(os.environ.get("PASI_TASK_MAX_ATTEMPTS", "3"))
TASK_RETRY_BACKOFF_SECONDS = (15.0, 60.0, 300.0)
TELEMETRY_INTERVAL_SECONDS = float(os.environ.get("PASI_RESOURCE_SAMPLE_SECONDS", "60"))
TASK_DISCOVERY_TTL_SECONDS = float(os.environ.get("PASI_TASK_DISCOVERY_TTL_SECONDS", "60"))
IDLE_POLL_SECONDS = max(0.25, float(os.environ.get("PASI_TASK_IDLE_SLEEP_SECONDS", "2")))
SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if SRC_ROOT.is_dir() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
try:
    from pasi.core.failure_registry import SQLiteFailureRegistry
    from pasi.core.resource_observer import HostResourceObserver, SQLiteResourceObservationStore
except Exception:
    SQLiteFailureRegistry = None
    HostResourceObserver = None
    SQLiteResourceObservationStore = None
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


class TaskDiscoveryCache:
    def __init__(self) -> None:
        self._entries: dict[int, tuple[float, tuple[Task, ...]]] = {}

    def _load_phase(self, phase: Phase, *, force: bool = False) -> tuple[Task, ...]:
        now = time.monotonic()
        cached = self._entries.get(phase.issue)
        if cached and not force and now - cached[0] < TASK_DISCOVERY_TTL_SECONDS:
            return cached[1]
        tasks = tasks_for(phase)
        self._entries[phase.issue] = (now, tasks)
        return tasks

    def update_phase_body(self, phase: Phase, body: str) -> None:
        now = time.monotonic()
        tasks = tuple(
            Task(phase, m.group(2), m.group(3).strip(), m.group(1).lower() == "x")
            for m in TASK_RE.finditer(body)
        )
        self._entries[phase.issue] = (now, tasks)

    def all(self, phases: tuple[Phase, ...], *, force: bool = False) -> list[Task]:
        result: list[Task] = []
        for phase in phases:
            result.extend(self._load_phase(phase, force=force))
        return result


_TASK_CACHE = TaskDiscoveryCache()


def all_tasks(*, force_refresh: bool = False) -> list[Task]:
    return _TASK_CACHE.all(schedule(), force=force_refresh)


def state_dir() -> Path:
    path = Path(os.environ.get("PASI_ACCEPTANCE_STATE_DIR", "~/.pasi/engineering-workspace-168h")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def emit(event: dict) -> None:
    with (state_dir() / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()


def new_chat_audit() -> dict[str, object]:
    """Replay the durable new-chat audit chain used by the ChatGPT executor."""
    try:
        from scripts.pasi_chat import (
            AUDIT_PUBLIC_KEY_PATH,
            NEW_CHAT_DECISIONS_PATH,
            STATE_PATH,
            replay_new_chat_decisions,
        )
        result = replay_new_chat_decisions(
            NEW_CHAT_DECISIONS_PATH,
            STATE_PATH,
            AUDIT_PUBLIC_KEY_PATH,
        )
        return {**result, "public_key": str(AUDIT_PUBLIC_KEY_PATH)}
    except Exception as exc:
        return {
            "path": "",
            "public_key": "",
            "records": 0,
            "valid": False,
            "errors": [f"new-chat audit replay failed: {exc}"],
            "chain_head": "",
        }


def record_new_chat_audit(state: dict[str, object], *, required: bool = True) -> dict[str, object]:
    result = new_chat_audit()
    state["new_chat_audit_valid"] = bool(result.get("valid"))
    state["new_chat_audit_record_count"] = int(result.get("records", 0) or 0)
    state["new_chat_audit_chain_head"] = str(result.get("chain_head") or "")
    state["new_chat_audit_log"] = str(result.get("path") or "")
    state["new_chat_audit_public_key"] = str(result.get("public_key") or "")
    state["new_chat_audit_errors"] = list(result.get("errors", []))
    if required and not bool(result.get("valid")):
        error_text = "; ".join(str(item) for item in result.get("errors", [])) or "unknown audit replay failure"
        emit({
            "event": "new_chat_audit_invalid",
            "at": utcnow().isoformat(),
            "errors": list(result.get("errors", [])),
            "decision_log": result.get("path", ""),
            "public_key": result.get("public_key", ""),
        })
        raise RuntimeError(f"new-chat audit chain is invalid: {error_text}")
    return result


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
    source_ref = os.environ.get("PASI_168H_SOURCE_REF", "HEAD").strip() or "HEAD"
    worktree = worktree.expanduser().resolve()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if not (worktree / ".git").exists():
        git(root, "worktree", "add", "-B", branch, str(worktree), source_ref, timeout=120)
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
    _TASK_CACHE.update_phase_body(task.phase, updated)


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
    previous = load_run_state().get("previous_task_context", "")
    env.update({
        "PASI_ACCEPTANCE_RUN_ID": run_id,
        "PASI_TASK_PREVIOUS_CONTEXT": str(previous),
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
    pr = ensure_evidence_pr(branch, run_id)
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
        "evidence_pr": pr,
    }
    emit(evidence)
    return evidence


class RunTelemetry:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.resource_store = None
        self.resource_observer = None
        self.failure_registry = None
        if SQLiteResourceObservationStore is not None and HostResourceObserver is not None:
            db = state_dir() / "resource-snapshots.sqlite3"
            self.resource_store = SQLiteResourceObservationStore(db)
            self.resource_observer = HostResourceObserver(self.resource_store)
        if SQLiteFailureRegistry is not None:
            self.failure_registry = SQLiteFailureRegistry(state_dir() / "failure-signatures.sqlite3")

    def sample(self) -> None:
        if self.resource_observer is None:
            return
        try:
            self.resource_observer.sample(operation_id="p0.4-run", run_id=self.run_id)
        except Exception as exc:
            emit({"event": "resource_sample_failed", "at": utcnow().isoformat(), "error": str(exc)})

    def start(self) -> None:
        self.sample()
        if self.resource_observer is None:
            emit({"event": "resource_telemetry_unavailable", "at": utcnow().isoformat()})
            return
        def worker() -> None:
            while not self.stop_event.wait(max(5.0, TELEMETRY_INTERVAL_SECONDS)):
                self.sample()
        self.thread = threading.Thread(target=worker, name="pasi-p0-4-resource-sampler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.sample()

    def record_failure(self, task: Task, exc: Exception) -> str:
        message = str(exc).strip() or repr(exc)
        family = re.sub(r"\\b\\d+\\b", "<n>", message.splitlines()[0] if message else "unknown")[:200]
        if self.failure_registry is None:
            return ""
        try:
            signature = self.failure_registry.record(
                subsystem="p0.4",
                failure_code=type(exc).__name__,
                failure_family=family,
                operation_id=os.environ.get("PASI_OPERATION_ID", ""),
                evidence_ref=str(state_dir() / "last-executor-output.txt"),
            )
            return signature.signature_id
        except Exception as registry_error:
            emit({"event": "failure_registry_error", "at": utcnow().isoformat(), "task_id": task.task_id, "error": str(registry_error)})
            return ""


def load_run_state() -> dict[str, object]:
    path = state_dir() / "state.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def operation_metrics() -> dict[str, object] | None:
    path = state_dir() / "last-executor-output.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.startswith("PASI_OPERATION_METRICS:"):
            try:
                value = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                return None
            return value if isinstance(value, dict) else None
    return None


def commit_matches_task(worktree: Path, task: Task) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(worktree), "log", "-1", "--format=%s"],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    subject = result.stdout.strip()
    if result.returncode != 0 or not subject.startswith(f"pasi: {task.task_id} "):
        return None
    return git(worktree, "rev-parse", "HEAD")


def ensure_evidence_pr(branch: str, run_id: str) -> dict[str, object]:
    cached = load_run_state().get("evidence_pr")
    if isinstance(cached, dict) and cached.get("number") and cached.get("url"):
        return cached
    if not token():
        return {}
    owner = REPO.split("/", 1)[0]
    query = quote(f"{owner}:{branch}", safe="")
    try:
        pulls = github(f"https://api.github.com/repos/{REPO}/pulls?state=open&head={query}&per_page=10")
        if isinstance(pulls, list) and pulls:
            item = pulls[0]
            return {"number": item.get("number"), "url": item.get("html_url", "")}
        body = {
            "title": f"P0.4 168h acceptance evidence — {run_id[:12]}",
            "head": branch,
            "base": "main",
            "body": "Automated P0.4 168-hour acceptance evidence branch. Runtime events, resource snapshots, failure signatures, and task commits are recorded under the acceptance runtime state.",
        }
        created = github(f"https://api.github.com/repos/{REPO}/pulls", method="POST", body=json.dumps(body))
        return {"number": created.get("number"), "url": created.get("html_url", "")}
    except Exception as exc:
        emit({"event": "evidence_pr_error", "at": utcnow().isoformat(), "branch": branch, "error": str(exc)})
        return {}


def reconcile_committed_task(task: Task, worktree: Path, branch: str, run_id: str) -> dict | None:
    commit = commit_matches_task(worktree, task)
    if not commit:
        return None
    if os.environ.get("PASI_PUSH", "").strip() == "1":
        git(worktree, "push", "--set-upstream", "origin", branch, timeout=180)
    pr = ensure_evidence_pr(branch, run_id)
    mark_checked(task)
    evidence = {
        "event": "task_reconciled", "at": utcnow().isoformat(), "task_id": task.task_id,
        "phase": task.phase.id, "issue": task.phase.issue, "commit_before": commit,
        "commit_after": commit, "branch": branch, "evidence_pr": pr,
    }
    emit(evidence)
    return evidence


def task_history_context(state: dict[str, object]) -> str:
    recent = state.get("recent_tasks", [])
    if not isinstance(recent, list) or not recent:
        return ""
    rendered = []
    for item in recent[-5:]:
        if isinstance(item, dict):
            task_id = str(item.get("task_id") or "")
            title = str(item.get("title") or "")
            commit = str(item.get("commit") or "")
            detail = f"{task_id}: {title}"
            if commit:
                detail += f" (commit {commit[:12]})"
            rendered.append(f"- {detail[:500]}")
        else:
            rendered.append(f"- {str(item)[:500]}")
    return "Previous accepted tasks in this run; continue from the latest committed action:\n" + "\n".join(rendered)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=HOURS)
    parser.add_argument("--worktree", type=Path, default=None)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.hours != HOURS:
        parser.error("the acceptance supervisor is fixed to exactly 168 hours")
    if not args.smoke and not token():
        parser.error("a GitHub token is required for a real 168-hour run; use PASI_GITHUB_TOKEN or GITHUB_TOKEN")
    if not args.smoke and os.environ.get("PASI_PUSH", "").strip() != "1":
        parser.error("PASI_PUSH=1 is required for a real 168-hour run so branch evidence is durable")

    root = Path.cwd().resolve()
    existing = load_run_state()
    if args.worktree is not None:
        worktree = args.worktree.expanduser().resolve()
    else:
        worktree = Path(str(existing.get("worktree") or "~/.pasi-worktrees/pasi-engineering-workspace-168h")).expanduser().resolve()
    branch = str(args.branch or existing.get("branch") or f"pasi/p0-4-168h-run-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    same_run = (
        not args.smoke
        and existing.get("status") == "running"
        and existing.get("repo") == REPO
        and existing.get("worktree") == str(worktree)
        and existing.get("branch") == branch
        and isinstance(existing.get("deadline_at"), str)
    )
    run_id = str(existing.get("run_id")) if same_run else f"ew-168h-{uuid.uuid4().hex}"

    ensure_worktree(root, worktree, branch)
    head = git(worktree, "rev-parse", "HEAD")
    phases = schedule()
    audit_result = record_new_chat_audit(existing, required=True)
    emit({
        "event": "run_resumed" if same_run else ("run_started" if not args.smoke else "smoke_started"),
        "at": utcnow().isoformat(), "run_id": run_id, "repo": REPO, "branch": branch,
        "worktree": str(worktree), "head": head, "hours": HOURS, "phase_count": len(phases),
        "new_chat_audit_records": int(audit_result.get("records", 0) or 0),
        "new_chat_audit_chain_head": audit_result.get("chain_head", ""),
    })

    if args.smoke:
        discovered = all_tasks()
        pending = [task for task in discovered if not task.checked]
        runnable = [task for task in pending if task.task_id != "P0.4"]
        print(f"READY: {len(phases)} phases, {len(discovered)} tasks discovered, {len(pending)} unchecked")
        print(f"RUN-LEVEL GATE: P0.4 excluded from model task dispatch; {len(runnable)} other unchecked tasks are runnable during the 168-hour window")
        print(f"NEW-CHAT AUDIT: valid={audit_result['valid']} records={audit_result['records']} chain_head={audit_result['chain_head']}")
        print(f"HEAD: {head}")
        return 0

    deadline = datetime.fromisoformat(str(existing["deadline_at"]).replace("Z", "+00:00")) if same_run else utcnow() + timedelta(hours=HOURS)
    state = existing if same_run else {
        "run_id": run_id, "repo": REPO, "branch": branch, "worktree": str(worktree),
        "started_at": utcnow().isoformat(), "deadline_at": deadline.isoformat(), "status": "running",
        "completed_tasks": 0, "failed_tasks": 0, "recent_tasks": [], "deferred_tasks": {},
        "last_completed_task": None,
        "last_operation_metrics": None,
        "p0_4_started_at": utcnow().isoformat(), "p0_4_status": "running",
    }
    state["status"] = "running"
    state["deadline_at"] = deadline.isoformat()
    state.setdefault("completed_tasks", 0); state.setdefault("failed_tasks", 0); state.setdefault("recent_tasks", []); state.setdefault("deferred_tasks", {})
    state.setdefault("last_completed_task", None); state.setdefault("last_operation_metrics", None)
    state.setdefault("p0_4_started_at", utcnow().isoformat()); state.setdefault("p0_4_status", "running")
    write_state(state)
    telemetry = RunTelemetry(run_id)
    telemetry.start()
    try:
        while utcnow() < deadline:
            record_new_chat_audit(state, required=True)
            write_state(state)
            discovered = all_tasks()
            p0_4 = next((task for task in discovered if task.task_id == "P0.4"), None)
            pending = [task for task in discovered if not task.checked and task.task_id != "P0.4"]
            if not pending:
                if utcnow() < deadline:
                    telemetry.sample()
                    time.sleep(IDLE_POLL_SECONDS)
                    continue
                if p0_4 is not None and not p0_4.checked:
                    evidence_path = state_dir() / "p0.4-evidence.json"
                    started_text = str(state.get("p0_4_started_at") or utcnow().isoformat())
                    started_at = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
                    completed_at = utcnow()
                    evidence_payload = {
                        "task_id": "P0.4",
                        "run_id": run_id,
                        "started_at": started_text,
                        "completed_at": completed_at.isoformat(),
                        "deadline_at": deadline.isoformat(),
                        "elapsed_hours": max(0.0, (completed_at - started_at).total_seconds() / 3600.0),
                        "branch": branch,
                        "worktree": str(worktree),
                        "resource_telemetry": str(state_dir() / "resource-snapshots.sqlite3"),
                        "failure_registry": str(state_dir() / "failure-signatures.sqlite3"),
                        "events": str(state_dir() / "events.jsonl"),
                        "completed_tasks_during_window": int(state.get("completed_tasks", 0)),
                        "failed_tasks_during_window": int(state.get("failed_tasks", 0)),
                        "new_chat_audit_log": str(state.get("new_chat_audit_log") or ""),
                        "new_chat_audit_public_key": str(state.get("new_chat_audit_public_key") or ""),
                        "new_chat_audit_record_count": int(state.get("new_chat_audit_record_count", 0) or 0),
                        "new_chat_audit_chain_head": str(state.get("new_chat_audit_chain_head") or ""),
                    }
                    evidence_path.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    evidence_pr = ensure_evidence_pr(branch, run_id)
                    state["p0_4_status"] = "complete"
                    state["p0_4_completed_at"] = evidence_payload["completed_at"]
                    state["p0_4_evidence"] = evidence_payload
                    state["evidence_pr"] = evidence_pr
                    mark_checked(p0_4)
                    write_state(state)
                    emit({"event": "p0_4_completed", **evidence_payload, "evidence_pr": evidence_pr})
                state["status"] = "roadmap_complete" if not [task for task in discovered if not task.checked] else "deadline_reached"
                state["completed_at"] = utcnow().isoformat()
                write_state(state)
                return 0

            deferred = state.get("deferred_tasks", {})
            deferred = dict(deferred) if isinstance(deferred, dict) else {}
            now = utcnow()
            ready = []
            for task in pending:
                stamp = deferred.get(task.task_id)
                if not isinstance(stamp, str):
                    ready.append(task); continue
                try:
                    if now >= datetime.fromisoformat(stamp.replace("Z", "+00:00")):
                        ready.append(task)
                except ValueError:
                    ready.append(task)
            if not ready:
                telemetry.sample(); time.sleep(15); continue
            # P0.4 is a continuous task handoff loop, not an hourly scheduler:
            # once the prior executor has reached terminal response + verification,
            # the next unchecked canonical task is dispatched immediately.
            task = ready[0]
            state["current_task"] = task.task_id
            state["current_phase"] = task.phase.id
            state["updated_at"] = utcnow().isoformat()
            history = task_history_context(state)
            state["previous_task_context"] = history
            write_state(state)
            try:
                reconciled = reconcile_committed_task(task, worktree, branch, run_id)
                if reconciled is not None:
                    state["completed_tasks"] = int(state.get("completed_tasks", 0)) + 1
                    state["last_commit"] = reconciled["commit_after"]
                    state["evidence_pr"] = reconciled.get("evidence_pr", {})
                    state["recent_tasks"] = (list(state.get("recent_tasks", [])) + [{
                        "task_id": task.task_id,
                        "phase": task.phase.id,
                        "title": task.title,
                        "commit": reconciled["commit_after"],
                    }])[-12:]
                    state["last_completed_task"] = {
                        "task_id": task.task_id,
                        "phase": task.phase.id,
                        "title": task.title,
                        "commit": reconciled["commit_after"],
                    }
                    deferred.pop(task.task_id, None); state["deferred_tasks"] = deferred
                    remaining = [next_task for next_task in all_tasks() if not next_task.checked]
                    if remaining:
                        next_task = remaining[0]
                        emit({
                            "event": "next_task_ready",
                            "at": utcnow().isoformat(),
                            "completed_task_id": task.task_id,
                            "next_task_id": next_task.task_id,
                            "next_phase": next_task.phase.id,
                        })
                        state["next_task"] = {
                            "task_id": next_task.task_id,
                            "phase": next_task.phase.id,
                            "title": next_task.title,
                        }
                    else:
                        state["next_task"] = None
                    write_state(state)
                    continue
                evidence = run_task(task, worktree, branch, run_id)
                state["completed_tasks"] = int(state.get("completed_tasks", 0)) + 1
                state["last_commit"] = evidence["commit_after"]
                state["evidence_pr"] = evidence.get("evidence_pr", state.get("evidence_pr", {}))
                state["recent_tasks"] = (list(state.get("recent_tasks", [])) + [{
                    "task_id": task.task_id,
                    "phase": task.phase.id,
                    "title": task.title,
                    "commit": evidence["commit_after"],
                }])[-12:]
                state["last_completed_task"] = {
                    "task_id": task.task_id,
                    "phase": task.phase.id,
                    "title": task.title,
                    "commit": evidence["commit_after"],
                }
                deferred.pop(task.task_id, None)
                remaining = [next_task for next_task in all_tasks() if not next_task.checked]
                if remaining:
                    next_task = remaining[0]
                    emit({
                        "event": "next_task_ready",
                        "at": utcnow().isoformat(),
                        "completed_task_id": task.task_id,
                        "next_task_id": next_task.task_id,
                        "next_phase": next_task.phase.id,
                    })
                    state["next_task"] = {
                        "task_id": next_task.task_id,
                        "phase": next_task.phase.id,
                        "title": next_task.title,
                    }
                else:
                    state["next_task"] = None
                metrics = operation_metrics()
                if metrics:
                    state["last_operation_metrics"] = metrics
                    emit({"event": "operation_metrics", "at": utcnow().isoformat(), "task_id": task.task_id, "metrics": metrics})
                write_state(state)
            except subprocess.TimeoutExpired as exc:
                failure = f"executor timeout: {exc}"; signature = telemetry.record_failure(task, exc)
                state["failed_tasks"] = int(state.get("failed_tasks", 0)) + 1
                emit({"event": "task_failed", "at": utcnow().isoformat(), "task_id": task.task_id, "phase": task.phase.id, "error": failure, "failure_signature": signature})
                count = int(state.get("task_failure_attempts", {}).get(task.task_id, 0)) + 1
                attempts = dict(state.get("task_failure_attempts", {})); attempts[task.task_id] = count; state["task_failure_attempts"] = attempts
                delay = TASK_RETRY_BACKOFF_SECONDS[min(count - 1, len(TASK_RETRY_BACKOFF_SECONDS) - 1)]
                deferred[task.task_id] = (utcnow() + timedelta(seconds=delay)).isoformat(); state["deferred_tasks"] = deferred; state["status"] = "running"; write_state(state)
            except Exception as exc:
                signature = telemetry.record_failure(task, exc)
                state["failed_tasks"] = int(state.get("failed_tasks", 0)) + 1
                emit({"event": "task_failed", "at": utcnow().isoformat(), "task_id": task.task_id, "phase": task.phase.id, "error": str(exc), "failure_signature": signature})
                attempts = dict(state.get("task_failure_attempts", {})); count = int(attempts.get(task.task_id, 0)) + 1; attempts[task.task_id] = count; state["task_failure_attempts"] = attempts
                delay = TASK_RETRY_BACKOFF_SECONDS[min(count - 1, len(TASK_RETRY_BACKOFF_SECONDS) - 1)]
                deferred[task.task_id] = (utcnow() + timedelta(seconds=delay)).isoformat(); state["deferred_tasks"] = deferred; state["status"] = "running"; write_state(state)
                telemetry.sample()
                if delay > 0: time.sleep(min(delay, 15.0))
    finally:
        telemetry.stop()

    # The 168-hour gate completes at the elapsed-time boundary even if some
    # later roadmap tasks remain pending. Finalize P0.4 independently so the
    # acceptance result cannot be lost merely because the broader roadmap is incomplete.
    record_new_chat_audit(state, required=True)
    write_state(state)
    discovered = all_tasks(force_refresh=True)
    p0_4 = next((task for task in discovered if task.task_id == "P0.4"), None)
    if p0_4 is not None and not p0_4.checked:
        evidence_path = state_dir() / "p0.4-evidence.json"
        started_text = str(state.get("p0_4_started_at") or utcnow().isoformat())
        started_at = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
        completed_at = utcnow()
        evidence_payload = {
            "task_id": "P0.4",
            "run_id": run_id,
            "started_at": started_text,
            "completed_at": completed_at.isoformat(),
            "deadline_at": deadline.isoformat(),
            "elapsed_hours": max(0.0, (completed_at - started_at).total_seconds() / 3600.0),
            "branch": branch,
            "worktree": str(worktree),
            "resource_telemetry": str(state_dir() / "resource-snapshots.sqlite3"),
            "failure_registry": str(state_dir() / "failure-signatures.sqlite3"),
            "events": str(state_dir() / "events.jsonl"),
            "completed_tasks_during_window": int(state.get("completed_tasks", 0)),
            "failed_tasks_during_window": int(state.get("failed_tasks", 0)),
            "new_chat_audit_log": str(state.get("new_chat_audit_log") or ""),
            "new_chat_audit_public_key": str(state.get("new_chat_audit_public_key") or ""),
            "new_chat_audit_record_count": int(state.get("new_chat_audit_record_count", 0) or 0),
            "new_chat_audit_chain_head": str(state.get("new_chat_audit_chain_head") or ""),
        }
        evidence_path.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence_pr = ensure_evidence_pr(branch, run_id)
        state["p0_4_status"] = "complete"
        state["p0_4_completed_at"] = evidence_payload["completed_at"]
        state["p0_4_evidence"] = evidence_payload
        state["evidence_pr"] = evidence_pr
        mark_checked(p0_4)
        emit({"event": "p0_4_completed", **evidence_payload, "evidence_pr": evidence_pr})
    state["status"] = "deadline_reached"
    state["completed_at"] = utcnow().isoformat()
    write_state(state)
    emit({"event": "run_deadline_reached", "at": utcnow().isoformat(), "run_id": run_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
