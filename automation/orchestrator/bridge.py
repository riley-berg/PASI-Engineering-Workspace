from __future__ import annotations

import hmac
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .config import CONFIG
from .models import ChatOperation
from pasi.core.operation_state import OperationState
from .operation_lifecycle import InvalidOperationTransition, validate_transition
from .state import TERMINAL_QUEUE_STATUSES, StateManager
from .runner_registry import (
    RunnerRegistryError,
    create_revision,
    create_runner,
    validate_revision,
    get_runner,
    list_user_runners,
    promote_revision,
    registry_snapshot,
    rollback_runner,
)
from scripts.pasi_timeout_policy import load_timeout_policy


HOST = "127.0.0.1"
PORT = 8765
MAX_RESPONSE_TEXT_CHARS = 120_000

class ControllerOwnershipConflict(InvalidOperationTransition):
    """Raised when a stale controller instance mutates another instance's operation."""

def completion_markers_satisfied(response_text: object, markers: object) -> bool:
    if not isinstance(response_text, str) or not response_text.strip():
        return False
    if not isinstance(markers, list):
        return True
    configured = [
        marker.strip()
        for marker in markers
        if isinstance(marker, str) and marker.strip()
    ]
    if not configured:
        return True
    lines: list[str] = []
    in_fence = False
    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or line.startswith("> ") or line == ">":
            continue
        lines.append(line)
    return any(
        any(line == marker or line.startswith(marker + ":") for line in lines)
        for marker in configured
    )
MAX_TRANSIENT_FAILURE_RETRIES = 3
TIMEOUT_POLICY = load_timeout_policy()
CLAIM_LEASE_SECONDS = TIMEOUT_POLICY["bridge_claim_lease_seconds"]
QUEUE_TTL_SECONDS = TIMEOUT_POLICY["queue_ttl_seconds"]
RETRY_BUDGETS = {"controller": 3, "response": 2, "context": 1}
MAX_ERROR_CHARS = 2_000
MAX_RECOVERY_CONTEXT_REPOSITORY_CHARS = 200
MAX_IDEMPOTENCY_KEY_CHARS = 128
MAX_RECOVERY_EVENTS_PER_OPERATION = 64
MAX_TIMING_KEYS = frozenset({
    "injected_at_ms",
    "ack_at_ms",
    "generation_start_ms",
    "completed_at_ms",
    "response_processed_at_ms",
    "completion_to_prompt_injected_ms",
    "response_completed_to_prompt_injected_ms",
    "user_messages_added",
    "ack_verified",
    "submission_via",
})
BRIDGE_TOKEN_FILE = Path.home() / ".pasi" / "bridge-token"
RUNNER_CAPABILITIES_PATH = Path.home() / ".pasi" / "runner" / "capabilities.json"
RUNNER_RUNTIME_DIR = Path(
    os.environ.get("PASI_RUNTIME_DIR", str(Path.home() / ".pasi" / "overnight"))
).expanduser().resolve()
# The runtime root is shared, but each supervised profile owns an isolated
# subdirectory so terminal errors cannot overwrite another runner's state.
RUNNER_STATE_PATH = RUNNER_RUNTIME_DIR / "state.json"  # legacy compatibility only
RUNNER_CONTROL_PATH = RUNNER_RUNTIME_DIR / "control.json"  # legacy compatibility only
RUNNER_LOG_DIR = CONFIG.ai_dir / "logs"
MAX_RUNNER_CAPABILITIES_BYTES = 256_000
RUNNER_START_PROBE_SECONDS = 1.5
RUNNER_START_PROBE_INTERVAL_SECONDS = 0.1


def runner_runtime_dir(profile: str) -> Path:
    return (RUNNER_RUNTIME_DIR / profile).resolve()


def runner_state_path(profile: str) -> Path:
    return runner_runtime_dir(profile) / "state.json"


def runner_control_path(profile: str) -> Path:
    return runner_runtime_dir(profile) / "control.json"


def runner_pid_path(profile: str) -> Path:
    return runner_runtime_dir(profile) / "runner.pid"
def runner_python() -> str:
    workspace_python = CONFIG.project_root / ".venv" / "bin" / "python"
    if workspace_python.is_file() and os.access(workspace_python, os.X_OK):
        return str(workspace_python)
    return sys.executable


RUNNER_PROFILES = {
    "m1": (runner_python(), str(CONFIG.project_root / "scripts" / "pasi_m1_cdp_chain.py")),
    "168h": (runner_python(), str(CONFIG.project_root / "scripts" / "pasi_168h_acceptance.py"), "--hours", "168"),
}


def runner_registry_entries() -> list[dict[str, Any]]:
    entries = [
        {
            "id": "m1",
            "name": "20-Operation Acceptance",
            "source": "builtin",
            "stable_version": 1,
            "candidate_version": None,
            "entrypoint": "scripts/pasi_m1_cdp_chain.py",
        },
        {
            "id": "168h",
            "name": "168-Hour Long-Run Acceptance",
            "source": "builtin",
            "stable_version": 1,
            "candidate_version": None,
            "entrypoint": "scripts/pasi_168h_acceptance.py",
        },
    ]
    for runner in list_user_runners():
        stable = runner.get("stable") if isinstance(runner, Mapping) else None
        candidate = runner.get("candidate") if isinstance(runner, Mapping) else None
        entries.append({
            "id": str(runner.get("id") or ""),
            "name": str(runner.get("name") or runner.get("id") or ""),
            "source": str(runner.get("source") or "user"),
            "stable_version": stable.get("version") if isinstance(stable, Mapping) else None,
            "candidate_version": candidate.get("version") if isinstance(candidate, Mapping) else None,
            "entrypoint": stable.get("entrypoint") if isinstance(stable, Mapping) else None,
        })
    return entries


def all_runner_profiles() -> tuple[str, ...]:
    return tuple(entry["id"] for entry in runner_registry_entries() if entry.get("id"))


def runner_command(profile: str) -> list[str]:
    if profile in RUNNER_PROFILES:
        return list(RUNNER_PROFILES[profile])
    definition = get_runner(profile)
    if not isinstance(definition, Mapping):
        raise ValueError("unsupported runner profile")
    stable = definition.get("stable")
    if not isinstance(stable, Mapping):
        raise ValueError("runner has no stable revision")
    entrypoint = str(stable.get("entrypoint") or "").strip()
    args = stable.get("args") if isinstance(stable.get("args"), list) else []
    return [runner_python(), str((CONFIG.project_root / entrypoint).resolve()), *[str(arg) for arg in args]]


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically persist a small JSON control/state document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
_TRANSIENT_BROWSER_ERROR_PREFIXES = (
    "PASI_CDP: CONTEXT_EXHAUSTED",
    "PASI_CDP: NETWORK_RESPONSE_CAPTURE_FAILED",
    "PASI_CDP: NETWORK_RESPONSE_INCOMPLETE",
    "PASI_CDP: RESPONSE_MARKER_NOT_FOUND",
    "PASI_CDP: network failure",
    "PASI_CDP: CDP_DEBUGGER_DETACHED",
    "PASI_CDP: NETWORK_STREAM_DISCONNECTED",
    "Could not find ChatGPT composer.",
    "Composer disappeared before submission.",
    "Could not find ChatGPT send button.",
    "ChatGPT prompt submission did not leave the composer after repeated send attempts.",
    "Could not find ChatGPT plus control",
    "ChatGPT Thinking control was not found.",
    "GitHub app was not found in the ChatGPT menu.",
    "PASI browser page reloaded during operation",
    "PASI_NATIVE: browser page reloaded during operation",
    "PASI: browser page reloaded during operation",
    "PASI_NATIVE: bridge completion failed",
    "CHAT_EXHAUSTED:",
    "PASI_NATIVE: controller dispatch unavailable",
    "PASI_NATIVE: CDP submit target unavailable:",
    "PASI_NATIVE: new chat did not reach a verified ready state",
    "PASI_NATIVE: new chat control did not change conversation identity",
    "PASI_NATIVE: prompt submission could not be verified after bounded attempts",
    "PASI_NATIVE: previous response still generating",
    "PASI_NATIVE: composer holds unrelated text",
    "PASI_NATIVE: submission accepted but generation did not start",
    "PASI_NATIVE: send control unavailable",
    "PASI_NATIVE: composer unavailable",
    "PASI_NATIVE: composer disappeared",
    "PASI_NATIVE: ChatGPT generation timed out",
    "PASI_NATIVE: response text unavailable",
)


def load_runner_capabilities() -> dict[str, Any]:
    try:
        if not RUNNER_CAPABILITIES_PATH.is_file() or RUNNER_CAPABILITIES_PATH.stat().st_size > MAX_RUNNER_CAPABILITIES_BYTES:
            return {"available": False, "reason": "capability report unavailable"}
        payload = json.loads(RUNNER_CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "reason": "capability report unreadable"}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "capability report invalid"}
    # The bridge exposes only machine-health metadata; secrets and command output are not persisted here.
    allowed = {"schema_version", "generated_at", "runner_name", "repository", "resources", "boundary", "runtime", "required_ok", "failures", "recommended_labels", "actions"}
    return {
        "available": True,
        **{key: payload[key] for key in allowed if key in payload},
        "execution_authorized": runner_execution_authorized(payload),
    }

def runner_processes() -> list[dict[str, Any]]:
    """Inspect live PASI runner processes instead of trusting stale PID files."""
    profiles_by_script = {
        str(Path(command[1]).name): profile
        for profile, command in RUNNER_PROFILES.items()
    }
    for definition in list_user_runners():
        stable = definition.get("stable")
        if isinstance(stable, Mapping) and stable.get("entrypoint"):
            profiles_by_script[Path(str(stable["entrypoint"])).name] = str(definition["id"])
    legacy_scripts = {"pasi_168h_supervisor.sh", "pasi_overnight_engine_v2.py"}
    processes: list[dict[str, Any]] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return processes

    workspace_root = str(CONFIG.project_root.resolve())
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            if pid <= 1:
                continue
            raw = (entry / "cmdline").read_bytes()
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "ignore").strip()
        except (OSError, ValueError):
            continue
        if not cmdline:
            continue

        profile: str | None = None
        script: str | None = None
        for script_name, profile_name in profiles_by_script.items():
            if script_name in cmdline:
                profile = profile_name
                script = script_name
                break
        if profile is None:
            for script_name in legacy_scripts:
                if script_name in cmdline:
                    profile = "legacy"
                    script = script_name
                    break
        if profile is None:
            continue

        processes.append({
            "pid": pid,
            "profile": profile,
            "script": script,
            "cmdline": cmdline,
            "workspace": workspace_root in cmdline,
            "recognized": profile in all_runner_profiles() and workspace_root in cmdline,
        })

    processes.sort(key=lambda item: int(item["pid"]))
    return processes


def runner_process_info(profile: str | None = None) -> dict[str, Any] | None:
    """Return a live runner process from the current Engineering Workspace."""
    candidates = [
        item for item in runner_processes()
        if item.get("recognized") is True
        and (profile is None or item.get("profile") == profile)
    ]
    return candidates[0] if candidates else None


def runner_process_is_alive(profile: str | None = None) -> bool:
    return runner_process_info(profile) is not None


def _read_runner_state_file(profile: str) -> dict[str, Any]:
    path = runner_state_path(profile)
    try:
        if not path.is_file() or path.stat().st_size > 128_000:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _augment_runner_state(
    profile: str,
    payload: dict[str, Any],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not payload:
        return {"available": False, "runner_profile": profile, "reason": "runner state unavailable"}

    allowed = {
        "schema_version", "run_id", "started_at", "deadline_at", "worktree", "branch",
        "phase", "current_task", "current_task_id", "requested_task", "task_number", "completed_tasks",
        "failed_tasks", "current_attempt", "task_retry_cycle", "same_failure_cycles",
        "last_provider", "last_result", "next_task", "stop_reason", "recent_tasks",
        "execution_mode", "runner_profile", "runner_pid", "log_path", "status", "error", "failed_at", "paused_at", "cancelled_at", "last_updated_at", "target_operations", "completed_operations", "chat_url",
        "current_operation_id", "current_operation_index", "current_operation_status",
        "last_operation_id", "last_request_id", "updated_at", "completed_at", "request_ids",
    }
    result: dict[str, Any] = {"available": True, **{key: payload[key] for key in allowed if key in payload}}
    process = runner_process_info(profile)
    result["process_alive"] = process is not None
    result["ready"] = process is not None and result.get("status") == "running"
    result["runner_profile"] = profile
    result["process"] = process
    result["process_pid"] = process.get("pid") if process else None
    result["process_profile"] = process.get("profile") if process else None
    result["process_cmdline"] = process.get("cmdline") if process else None
    result["runtime_state_path"] = str(runner_state_path(profile))

    status = str(result.get("status") or "")
    result["evidence"] = {
        "state_file_present": True,
        "live_process_present": process is not None,
        "live_process_profile": process.get("profile") if process else None,
        "live_process_matches_profile": (
            process is not None and process.get("profile") == profile
        ),
        "status": status or "unknown",
        "failure_confirmed": False,
    }

    if process is not None:
        if status in {"starting", "running", "stopping"}:
            result["state_consistency"] = "consistent"
            result["diagnostic_warning"] = None
            result["error"] = None
            result.pop("failed_at", None)
            result["evidence"]["failure_confirmed"] = False
        elif status in {"failed", "completed", "roadmap_complete", "deadline_reached", "paused", "cancelled"}:
            result["state_consistency"] = "live_process_terminal_state"
            result["diagnostic_warning"] = (
                "runner process is alive while persisted state is " + (status or "unknown")
            )
            result["evidence"]["failure_confirmed"] = status == "failed"
        else:
            result["state_consistency"] = "live_process_unknown_state"
            result["diagnostic_warning"] = "runner process is alive with an unrecognized persisted state"
    elif status == "starting":
        result["status"] = "failed"
        result["ready"] = False
        result["state_consistency"] = "persisted_starting_without_process"
        result["diagnostic_warning"] = "runner startup state remains persisted but no live runner process was detected"
        result["error"] = "runner exited before initialization completed"
        result["evidence"]["failure_confirmed"] = True
    elif status == "running":
        result["status"] = "failed"
        result["ready"] = False
        result["state_consistency"] = "persisted_running_without_process"
        result["diagnostic_warning"] = "runner state says running but no live runner process was detected"
        result["error"] = "runner process is no longer alive"
        result["evidence"]["failure_confirmed"] = True
    elif status == "failed":
        result["evidence"]["failure_confirmed"] = True
        result["state_consistency"] = "persisted_terminal_state"
        result["diagnostic_warning"] = None
    else:
        result["state_consistency"] = "consistent"
        result["diagnostic_warning"] = None
    return result


def load_runner_state(profile: str | None = None) -> dict[str, Any]:
    processes = runner_processes()
    profiles = all_runner_profiles()
    if profile is not None:
        normalized = _runner_profile(profile)
        return _augment_runner_state(normalized, _read_runner_state_file(normalized), processes)

    per_profile = {
        current_profile: _augment_runner_state(
            current_profile,
            _read_runner_state_file(current_profile),
            processes,
        )
        for current_profile in profiles
    }
    active = [
        item for item in processes
        if item.get("recognized") is True and item.get("profile") in profiles
    ]
    active_profile = active[0].get("profile") if active else None

    if active_profile:
        selected = dict(per_profile[active_profile])
    else:
        available = [state for state in per_profile.values() if state.get("available")]
        selected = max(
            available,
            key=lambda state: str(state.get("last_updated_at") or state.get("started_at") or ""),
            default={"available": False, "reason": "runner state unavailable"},
        )

    selected["profiles"] = per_profile
    selected["active_profile"] = active_profile
    selected["processes"] = processes
    selected["bridge_process"] = {
        "pid": os.getpid(),
        "cmdline": " ".join(str(part) for part in sys.argv),
        "profile": "bridge",
        "workspace": True,
        "recognized": True,
    }
    selected["available"] = any(state.get("available") for state in per_profile.values())
    return selected


def runner_diagnostics_payload(bridge_state: "BridgeState") -> dict[str, Any]:
    """Build a sanitized live diagnostic snapshot for localhost/browser inspection."""
    runner_state = load_runner_state()
    browser_health = bridge_state.get_browser_health()
    return {
        "schema_version": "pasi-runner-diagnostics-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "bridge_process": runner_state.get("bridge_process"),
        "active_profile": runner_state.get("active_profile"),
        "processes": runner_state.get("processes", []),
        "profiles": runner_state.get("profiles", {}),
        "browser_health": browser_health,
    }


def runner_execution_authorized(payload: Mapping[str, Any]) -> bool:
    profile = str(
        payload.get("runner_profile") or payload.get("active_profile") or ""
    ).strip().casefold()
    execution_mode = str(payload.get("execution_mode") or "").strip().casefold()
    if not profile and execution_mode.startswith("supervised_"):
        profile = execution_mode.removeprefix("supervised_")
    if profile not in set(all_runner_profiles()):
        return False
    profile_state = (
        payload.get("profiles", {}).get(profile)
        if isinstance(payload.get("profiles"), Mapping)
        else None
    )
    effective = profile_state if isinstance(profile_state, Mapping) else payload
    return bool(
        effective.get("status") == "running"
        and str(effective.get("execution_mode") or "") == "supervised_" + profile
        and runner_process_is_alive(profile)
    )


def _terminate_runner_process(pid: int, grace_seconds: float = 2.0) -> None:
    """Stop a supervised runner and its children without stopping the bridge."""
    try:
        process_group = os.getpgid(pid)
    except OSError as exc:
        raise RuntimeError("runner process is no longer present") from exc

    try:
        if process_group == pid:
            # _start_runner uses start_new_session=True, so the runner has an
            # isolated process group. Terminating that group stops the runner
            # and any subprocesses it spawned for the current task.
            os.killpg(process_group, signal.SIGTERM)
        else:
            # Legacy runners may not have their own process group.
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise RuntimeError("runner process signal denied") from exc

    deadline = time.monotonic() + max(0.1, grace_seconds)
    while time.monotonic() < deadline:
        if not runner_process_is_alive():
            return
        time.sleep(0.05)

    try:
        if process_group == pid:
            os.killpg(process_group, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise RuntimeError("runner process could not be force-stopped") from exc

    if runner_process_is_alive():
        raise RuntimeError("runner process did not stop after force termination")

def _runner_pid(profile: str = "m1") -> int | None:
    try:
        raw_pid = runner_pid_path(profile).read_text(encoding="utf-8").strip()
        pid = int(raw_pid)
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def _runner_profile(profile: object) -> str:
    value = str(profile or "m1").strip().casefold()
    if value not in set(all_runner_profiles()):
        raise ValueError("unsupported runner profile")
    return value


def _github_token() -> str:
    """Resolve a GitHub credential for supervised runners without storing it."""
    configured = next(
        (
            os.environ.get(name, "").strip()
            for name in (
                "PASI_PROJECTS_TOKEN",
                "PASI_GITHUB_TOKEN",
                "GH_TOKEN",
                "GITHUB_TOKEN",
            )
            if os.environ.get(name, "").strip()
        ),
        "",
    )
    if configured:
        return configured

    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        token = result.stdout.strip()
        if token:
            return token
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    for line in result.stdout.splitlines():
        if line.startswith("password="):
            return line.removeprefix("password=").strip()
    return ""


def _start_runner(profile: str) -> dict[str, Any]:
    if runner_process_is_alive():
        return {"accepted": False, "action": "start", "profile": profile, "reason": "runner already running"}

    github_token = _github_token() if profile == "168h" else ""
    if profile == "168h" and not github_token:
        return {"accepted": False, "action": "start", "profile": profile, "reason": "GitHub token is unavailable"}

    command = runner_command(profile)
    executable = Path(command[0])
    script = Path(command[1])
    if not executable.is_file():
        return {"accepted": False, "action": "start", "profile": profile, "reason": "runner Python executable is unavailable"}
    if not script.is_file():
        return {"accepted": False, "action": "start", "profile": profile, "reason": "runner script is missing from the active PASI workspace"}
    if not os.access(script, os.R_OK):
        return {"accepted": False, "action": "start", "profile": profile, "reason": "runner script is not readable"}

    runtime_dir = runner_runtime_dir(profile)
    state_path = runner_state_path(profile)
    pid_path = runner_pid_path(profile)
    try:
        RUNNER_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = RUNNER_LOG_DIR / ("runner-" + profile + ".log")
        log_handle = log_path.open("a", encoding="utf-8")
    except OSError as exc:
        return {"accepted": False, "action": "start", "profile": profile, "reason": "runner log is not writable: " + str(exc)}

    environment = os.environ.copy()
    environment.setdefault("PYTHONUNBUFFERED", "1")
    environment["PASI_RUNNER_PROFILE"] = profile
    environment["PASI_RUNTIME_DIR"] = str(runtime_dir)
    if profile == "168h":
        environment["PASI_GITHUB_TOKEN"] = github_token
        environment["PASI_PUSH"] = "1"

    starting_state = {
        "status": "starting",
        "execution_mode": "supervised_" + profile,
        "runner_profile": profile,
        "log_path": str(log_path),
        "runtime_state_path": str(state_path),
        "runner_pid": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        atomic_write_json(state_path, starting_state)
        process = subprocess.Popen(
            command, cwd=CONFIG.project_root, env=environment,
            stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.close()
        failed_state = dict(starting_state)
        failed_state.update({"status":"failed","error":"runner launch failed: "+str(exc),"failed_at":datetime.now(timezone.utc).isoformat()})
        atomic_write_json(state_path, failed_state)
        return {"accepted":False,"action":"start","profile":profile,"reason":failed_state["error"]}
    except Exception as exc:
        log_handle.close()
        failed_state = dict(starting_state)
        failed_state.update({"status":"failed","error":"runner launch failed: "+type(exc).__name__+": "+str(exc),"failed_at":datetime.now(timezone.utc).isoformat()})
        atomic_write_json(state_path, failed_state)
        return {"accepted":False,"action":"start","profile":profile,"reason":failed_state["error"]}

    log_handle.close()
    try:
        current=json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(current,dict): current={}
    except (OSError,json.JSONDecodeError):
        current={}
    if current.get("status") not in {"starting","running","failed","completed","paused","cancelled"}:
        current=dict(starting_state)
    current.update({"runner_profile":profile,"runner_pid":process.pid,"log_path":str(log_path),"runtime_state_path":str(state_path)})
    atomic_write_json(state_path,current)
    try:
        pid_path.write_text(str(process.pid)+"\n",encoding="utf-8")
    except OSError:
        pass
    # Give the supervised process a short startup probe. A successful Popen() only
    # proves that the child was created; it does not prove that the 168h supervisor
    # survived argument validation, worktree initialization, or schedule loading.
    poll = getattr(process, "poll", None)
    if callable(poll):
        deadline = time.monotonic() + RUNNER_START_PROBE_SECONDS
        while time.monotonic() < deadline:
            return_code = poll()
            if return_code is not None:
                try:
                    current = json.loads(state_path.read_text(encoding="utf-8"))
                    if not isinstance(current, dict):
                        current = {}
                except (OSError, json.JSONDecodeError):
                    current = {}
                failure = dict(current) if current else dict(starting_state)
                failure_error = str(
                    failure.get("error")
                    or f"runner exited during startup with exit code {return_code}"
                ).strip()
                failure.update({
                    "status": "failed",
                    "runner_profile": profile,
                    "runner_pid": process.pid,
                    "log_path": str(log_path),
                    "runtime_state_path": str(state_path),
                    "error": failure_error,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                })
                atomic_write_json(state_path, failure)
                return {
                    "accepted": False,
                    "action": "start",
                    "profile": profile,
                    "reason": failure_error,
                    "pid": process.pid,
                    "log_path": str(log_path),
                    "runtime_state_path": str(state_path),
                }
            time.sleep(RUNNER_START_PROBE_INTERVAL_SECONDS)

    return {"accepted":True,"action":"start","profile":profile,"pid":process.pid,"log_path":str(log_path),"runtime_state_path":str(state_path)}


def request_runner_control(action: str, profile: object = None) -> dict[str, Any]:
    normalized = str(action or "").strip().casefold()
    if normalized not in {"start", "toggle", "stop", "retry_current"}:
        raise ValueError("unsupported runner control action")

    selected_profile = _runner_profile(profile) if profile is not None else None
    live_process = runner_process_info()

    if normalized in {"start", "toggle"} and live_process is None:
        return _start_runner(selected_profile or "m1")

    if normalized == "start":
        actual_profile = live_process.get("profile") if live_process else None
        if selected_profile and actual_profile and selected_profile != actual_profile:
            return {
                "accepted": False,
                "action": "start",
                "profile": selected_profile,
                "reason": actual_profile.upper() + " runner is already running",
            }
        return {
            "accepted": False,
            "action": "start",
            "profile": selected_profile or actual_profile,
            "reason": "runner already running",
        }

    if normalized == "toggle" and live_process is not None:
        normalized = "stop"

    if normalized == "stop":
        if live_process is None:
            return {
                "accepted": False,
                "action": "stop",
                "profile": selected_profile,
                "reason": "runner is not running",
            }

        pid = int(live_process["pid"])
        actual_profile = live_process.get("profile")
        if selected_profile and actual_profile and selected_profile != actual_profile:
            return {
                "accepted": False,
                "action": "stop",
                "profile": selected_profile,
                "reason": actual_profile.upper() + " runner is the active runner",
            }

        state = load_runner_state(actual_profile or selected_profile or "m1")
        state_path = runner_state_path(actual_profile or selected_profile or "m1")
        pid_path = runner_pid_path(actual_profile or selected_profile or "m1")
        stopping = dict(state) if state.get("available") else {}
        stopping.update({
            "status": "stopping",
            "execution_mode": "manual",
            "runner_pid": pid,
            "runner_profile": actual_profile or stopping.get("runner_profile"),
            "paused_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            atomic_write_json(state_path, stopping)
            _terminate_runner_process(pid)
        except (OSError, RuntimeError) as exc:
            failed = dict(stopping)
            failed.update({
                "status": "failed",
                "error": "runner stop failed: " + str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })
            try:
                atomic_write_json(state_path, failed)
            except OSError:
                pass
            return {
                "accepted": False,
                "action": "stop",
                "pid": pid,
                "reason": failed["error"],
            }

        paused = dict(stopping)
        paused["status"] = "paused"
        paused["paused_at"] = datetime.now(timezone.utc).isoformat()
        paused["process_alive"] = False
        paused["ready"] = False
        atomic_write_json(state_path, paused)
        pid_candidates = [pid_path]
        if actual_profile == "168h":
            pid_candidates.append(
                Path.home() / ".pasi" / "engineering-workspace-168h" / "runner.pid"
            )
        for candidate in pid_candidates:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
        return {
            "accepted": True,
            "action": "stop",
            "pid": pid,
            "profile": actual_profile,
            "status": "paused",
        }

    control_profile = selected_profile or (
        live_process.get("profile") if live_process else None
    ) or "m1"
    current_state = load_runner_state(control_profile)
    current_task = str(current_state.get("current_task", "")).strip()
    if not current_state.get("available") or not current_task:
        return {"accepted": False, "action": normalized, "reason": "no current runner task"}
    control_path = runner_control_path(control_profile)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "action": normalized,
        "task_id": current_state.get("current_task_id", ""),
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = control_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(RUNNER_CONTROL_PATH)
    return {"accepted": True, "action": normalized, "task": current_task}


class BridgeState:
    """
    Thread-safe in-memory view of the ChatGPT operation queue.

    Persistent copies are written through StateManager so that a
    controller restart does not lose queued operations.
    """

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager
        self.lock = threading.RLock()
        self.operation_changed = threading.Condition(self.lock)
        self.browser_testing_changed = threading.Condition(self.lock)
        self._browser_test_requests: dict[str, dict[str, Any]] = {}
        self._browser_test_results: dict[str, dict[str, Any]] = {}
        self._queue_cache: list[dict[str, Any]] | None = None
        self._queue_cache_mtime_ns: int | None = None

    @staticmethod
    def _mtime_ns(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _load_queue(self) -> list[dict[str, Any]]:
        mtime_ns = self._mtime_ns(self.state_manager.queue_path)
        if (
            self._queue_cache is not None
            and self._queue_cache_mtime_ns == mtime_ns
        ):
            return self._queue_cache

        queue = self.state_manager.load_queue()
        self._queue_cache = queue
        self._queue_cache_mtime_ns = self._mtime_ns(self.state_manager.queue_path)
        return queue

    def _save_queue(self, queue: list[dict[str, Any]]) -> None:
        # The nested operation_state is a derived canonical view of the same
        # queue item. Refresh it on every durable queue write so it cannot
        # remain at the initial "queued" status after claim/generation/completion.
        for item in queue:
            try:
                item["operation_state"] = OperationState.from_chat_operation(item).to_dict()
            except Exception:
                item.pop("operation_state", None)

        has_inline_terminal_responses = False
        for item in queue:
            if item.get("status") not in TERMINAL_QUEUE_STATUSES:
                continue
            response_text = item.get("response_text")
            if isinstance(response_text, str) and response_text.strip():
                has_inline_terminal_responses = True
                break
        persistent_queue: list[dict[str, Any]]
        if not has_inline_terminal_responses:
            persistent_queue = queue
        else:
            persistent_queue = []
            for item in queue:
                persistent_item = dict(item)
                if item.get("status") in TERMINAL_QUEUE_STATUSES:
                    operation_id = item.get("operation_id")
                    response_text = item.get("response_text")
                    if (
                        isinstance(operation_id, str)
                        and isinstance(response_text, str)
                        and response_text.strip()
                    ):
                        self.state_manager.save_terminal_response(
                            operation_id,
                            response_text,
                        )
                    persistent_item.pop("response_text", None)
                persistent_queue.append(persistent_item)

        normalized_queue = self.state_manager.save_queue(persistent_queue)
        retained_terminal_ids: set[str] = {
            operation_id
            for item in normalized_queue
            if item.get("status") in TERMINAL_QUEUE_STATUSES
            for operation_id in [item.get("operation_id")]
            if isinstance(operation_id, str)
        }
        self.state_manager.prune_terminal_responses(retained_terminal_ids)

        # Keep the hot-path cache compact. Terminal response bodies are loaded
        # only when a specific terminal operation is inspected.
        self._queue_cache = normalized_queue
        self._queue_cache_mtime_ns = self._mtime_ns(self.state_manager.queue_path)
        self.operation_changed.notify_all()

    def _hydrate_terminal_response(self, item: dict[str, Any]) -> dict[str, Any]:
        operation_id = item.get("operation_id")
        if item.get("status") not in TERMINAL_QUEUE_STATUSES:
            return item
        if not isinstance(operation_id, str):
            return item
        response_text = item.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return item
        stored = self.state_manager.load_terminal_response(operation_id)
        if isinstance(stored, str) and stored.strip():
            item = dict(item)
            item["response_text"] = stored
            item["response_text_available"] = True
        return item

    def queue_operation(
        self,
        operation_type: str,
        prompt: str,
        idempotency_key: str | None = None,
        completion_markers: list[str] | None = None,
    ) -> ChatOperation:
        if completion_markers is not None:
            if (
                not isinstance(completion_markers, list)
                or not completion_markers
                or len(completion_markers) > 4
                or any(
                    not isinstance(marker, str)
                    or not marker.strip()
                    or len(marker.strip()) > 120
                    or "\n" in marker
                    or "\r" in marker
                    for marker in completion_markers
                )
            ):
                raise ValueError("completion_markers must be 1-4 bounded single-line strings")
            completion_markers = list(
                dict.fromkeys(marker.strip() for marker in completion_markers)
            )

        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_CHARS:
                raise ValueError("idempotency_key must be a nonblank bounded string")

        with self.lock:
            queue = self._load_queue()
            if idempotency_key is not None:
                for item in queue:
                    if (
                        item.get("idempotency_key") == idempotency_key
                        and item.get("operation_type") == operation_type
                        and item.get("prompt") == prompt
                        and item.get("status") not in {"completed", "failed", "cancelled"}
                    ):
                        fields = ChatOperation.__dataclass_fields__
                        return ChatOperation(**{key: item[key] for key in fields if key in item})

            operation = ChatOperation(
                operation_id=self._new_operation_id(),
                operation_type=operation_type,
                prompt=prompt,
                idempotency_key=idempotency_key,
                completion_markers=completion_markers,
                status="queued",
            )
            item = operation.to_dict()
            now = time.time()
            item["retry_count"] = 0
            item["retry_counts"] = {"controller": 0, "response": 0, "context": 0}
            item["expires_at"] = now + QUEUE_TTL_SECONDS
            item["updated_at"] = now
            queue.append(item)
            self._save_queue(queue)
            return operation

    def _sweep_queue_locked(self, queue: list[dict[str, Any]]) -> None:
        now = time.time()
        changed = False
        for item in queue:
            status = str(item.get("status", ""))
            expires_at = float(item.get("expires_at", 0) or 0)
            if (
                "expires_at" in item
                and now >= expires_at
                and status in {"queued", "claimed", "generating"}
            ):
                validate_transition(status, "failed")
                item["status"] = "failed"
                item["error"] = "operation queue TTL expired"
                item["failure_reason"] = "queue_ttl_expired"
                item["updated_at"] = now
                changed = True
                continue

            claimed_at = float(item.get("claimed_at", 0) or 0)
            if (
                status in {"claimed", "generating"}
                and claimed_at > 0
                and now - claimed_at >= CLAIM_LEASE_SECONDS
            ):
                validate_transition(status, "queued")
                item["status"] = "queued"
                item.pop("claimed_at", None)
                item["reclaimed_at"] = now
                item["failure_reason"] = "claim_lease_expired"
                item["updated_at"] = now
                changed = True

        if changed:
            self._save_queue(queue)

    def queue_browser_test_request(
        self,
        action: str,
        tab_id: int | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "screenshot",
            "page_state",
            "dom",
            "accessibility",
            "console_errors",
            "network",
            "click",
            "fill",
            "press_key",
            "scroll",
        }
        action = str(action or "").strip()
        if action not in allowed:
            raise ValueError("unsupported browser-test action")
        if tab_id is not None and (not isinstance(tab_id, int) or tab_id < 1):
            raise ValueError("tab_id must be a positive integer or null")
        params = dict(params or {})
        if len(json.dumps(params, ensure_ascii=False, separators=(",", ":"))) > 20_000:
            raise ValueError("browser-test params exceed the bounded request size")

        if tab_id is None:
            health = self.state_manager.load_browser_health()
            health_data = health.get("data") if isinstance(health, Mapping) else {}
            if not isinstance(health_data, Mapping) or not isinstance(health_data.get("tab_id"), int):
                state = self.state_manager.load_browser_state()
                state_data = state.get("data") if isinstance(state, Mapping) else {}
                tab_id = state_data.get("tab_id") if isinstance(state_data, Mapping) else None
            if not isinstance(tab_id, int) or tab_id < 1:
                raise ValueError("no attached ChatGPT tab is available")

        request_id = "bt-" + os.urandom(10).hex()
        request = {
            "request_id": request_id,
            "action": action,
            "tab_id": tab_id,
            "params": params,
            "created_at": time.time(),
        }
        with self.browser_testing_changed:
            if len(self._browser_test_requests) >= 64:
                oldest = min(self._browser_test_requests, key=lambda key: self._browser_test_requests[key]["created_at"])
                self._browser_test_requests.pop(oldest, None)
            self._browser_test_requests[request_id] = request
            self.browser_testing_changed.notify_all()
        return {"request_id": request_id, "tab_id": tab_id}

    def wait_for_browser_test_request(
        self,
        tab_id: int,
        timeout_ms: int = 0,
    ) -> dict[str, Any] | None:
        if not isinstance(tab_id, int) or tab_id < 1:
            raise ValueError("tab_id must be a positive integer")
        timeout_ms = min(max(int(timeout_ms or 0), 0), 10_000)
        deadline = time.monotonic() + timeout_ms / 1000.0
        with self.browser_testing_changed:
            while True:
                candidates = sorted(
                    (
                        item for item in self._browser_test_requests.values()
                        if item.get("tab_id") == tab_id
                    ),
                    key=lambda item: float(item.get("created_at", 0)),
                )
                if candidates:
                    request = dict(candidates[0])
                    self._browser_test_requests.pop(request["request_id"], None)
                    return request
                if timeout_ms <= 0:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.browser_testing_changed.wait(timeout=remaining)

    def submit_browser_test_result(
        self,
        request_id: str,
        tab_id: int,
        ok: bool,
        data: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        with self.browser_testing_changed:
            result = {
                "request_id": request_id,
                "tab_id": tab_id,
                "ok": bool(ok),
                "data": dict(data or {}) if ok else None,
                "error": (
                    dict(error) if isinstance(error, Mapping)
                    else {"code": "BROWSER_TEST_FAILED", "message": str(error or "browser test failed")}
                    if error is not None
                    else None
                ),
                "completed_at": time.time(),
            }
            if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > 12_000_000:
                raise ValueError("browser-test result exceeds the bounded response size")
            self._browser_test_results[request_id] = result
            if len(self._browser_test_results) > 64:
                oldest = min(self._browser_test_results, key=lambda key: self._browser_test_results[key]["completed_at"])
                self._browser_test_results.pop(oldest, None)
            self.browser_testing_changed.notify_all()
            return {"accepted": True, "request_id": request_id}

    def get_browser_test_result(self, request_id: str) -> dict[str, Any] | None:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id is required")
        with self.browser_testing_changed:
            result = self._browser_test_results.pop(request_id, None)
            return dict(result) if isinstance(result, Mapping) else None

    @classmethod
    def _mark_claimed(
        cls,
        item: dict[str, Any],
        controller_id: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        validate_transition(str(item.get("status", "")), "claimed")
        item["status"] = "claimed"
        item["claimed_at"] = now
        item["updated_at"] = now
        if controller_id is None:
            item.pop("controller_id", None)
        else:
            item["controller_id"] = controller_id
        return item

    def claim_operation(
        self,
        operation_id: str,
        controller_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            queue = self._load_queue()
            self._sweep_queue_locked(queue)

            if self._response_processing_pending_for_controller(queue, controller_id):
                return None

            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue
                if item.get("status") != "queued":
                    return None

                predecessor = self._latest_completed_prompt_for_controller(
                    queue,
                    controller_id,
                )
                if predecessor is not None:
                    predecessor_operation_id, predecessor_completed_at_ms = predecessor
                    item["predecessor_operation_id"] = predecessor_operation_id
                    item["predecessor_completed_at_ms"] = predecessor_completed_at_ms
                claimed = self._mark_claimed(item, controller_id)
                self._save_queue(queue)
                return dict(claimed)

        return None

    def wait_for_next_operation(
        self,
        controller_id: str | None = None,
        timeout_ms: int = 0,
    ) -> dict[str, Any] | None:
        """Wait for a queued operation that is safe to dispatch to a controller."""
        if timeout_ms < 0:
            timeout_ms = 0
        timeout_seconds = min(timeout_ms, 60_000) / 1000.0
        deadline = time.monotonic() + timeout_seconds
        with self.operation_changed:
            while True:
                operation = self.claim_next_operation(controller_id)
                if operation is not None:
                    return operation
                if timeout_seconds <= 0:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.operation_changed.wait(timeout=remaining)

    @staticmethod
    def _latest_completed_prompt_for_controller(
        queue: list[dict[str, Any]],
        controller_id: str | None,
    ) -> tuple[str, int] | None:
        if not controller_id:
            return None

        latest: tuple[str, int] | None = None
        for candidate in queue:
            if (
                candidate.get("status") != "completed"
                or candidate.get("operation_type") != "prompt"
                or candidate.get("controller_id") != controller_id
            ):
                continue
            operation_id = candidate.get("operation_id")
            timing = candidate.get("timing")
            completed_at_ms = timing.get("completed_at_ms") if isinstance(timing, dict) else None
            if (
                not isinstance(operation_id, str)
                or not operation_id.strip()
                or isinstance(completed_at_ms, bool)
                or not isinstance(completed_at_ms, (int, float))
                or completed_at_ms < 0
            ):
                continue
            value = int(completed_at_ms)
            if latest is None or value > latest[1]:
                latest = (operation_id, value)
        return latest

    def claim_next_operation(
        self,
        controller_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            queue = self._load_queue()
            self._sweep_queue_locked(queue)

            if self._response_processing_pending_for_controller(queue, controller_id):
                return None

            # A completion acknowledgement may durably claim the next
            # operation before the worker has delivered it. Allow the same
            # controller to redeliver that claimed-but-not-started operation
            # after a service-worker restart or dispatch failure. Once CDP emits
            # STARTED, the item becomes generating and this path is closed.
            now = time.time()
            for item in queue:
                if (
                    item.get("status") == "claimed"
                    and item.get("controller_id") == controller_id
                    and not item.get("network_request_id")
                    and not item.get("network_lifecycle_event")
                    and now - float(item.get("claimed_at", 0) or 0) >= 5
                ):
                    return dict(item)

            # Queue dispatch is strictly serial. A controller-ready signal may
            # arrive while the current operation is active. Never advance to a
            # second operation until the current one is durably completed.
            if any(item.get("status") in {"claimed", "generating"} for item in queue):
                return None

            for item in queue:
                if item.get("status") != "queued":
                    continue

                predecessor = self._latest_completed_prompt_for_controller(
                    queue,
                    controller_id,
                )
                if predecessor is not None:
                    predecessor_operation_id, predecessor_completed_at_ms = predecessor
                    item["predecessor_operation_id"] = predecessor_operation_id
                    item["predecessor_completed_at_ms"] = predecessor_completed_at_ms
                claimed = self._mark_claimed(item, controller_id)
                self._save_queue(queue)
                return dict(claimed)

        return None

    def get_operation(
        self,
        operation_id: str,
        *,
        repair_response: bool = True,
    ) -> dict[str, Any] | None:
        with self.lock:
            queue = self._load_queue()

            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue

                if repair_response and self._repair_response_from_browser_observation(item):
                    self._save_queue(queue)

                return self._hydrate_terminal_response(dict(item))

        return None

    def get_chained_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.lock:
            queue = self._load_queue()
            current = next(
                (item for item in queue if item.get("operation_id") == operation_id),
                None,
            )
            if not current:
                return None
            next_operation_id = current.get("next_operation_id")
            if not isinstance(next_operation_id, str) or not next_operation_id.strip():
                return None
            for item in queue:
                if (
                    item.get("operation_id") == next_operation_id
                    and item.get("status") == "claimed"
                ):
                    return dict(item)
            return None

    def complete_operation_and_claim_next(
        self,
        operation_id: str,
        chat_url: str | None = None,
        response_text: str | None = None,
        response_text_available: bool = False,
        timing: object = None,
        controller_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Complete one operation and claim exactly one next operation in one durable queue write."""
        with self.lock:
            queue = self._load_queue()
            self._sweep_queue_locked(queue)

            current = next(
                (item for item in queue if item.get("operation_id") == operation_id),
                None,
            )
            if current is None:
                return None, None

            if (
                current.get("operation_type") == "prompt"
                and current.get("network_response_authoritative") is True
                and current.get("response_text_available") is True
                and isinstance(current.get("response_text"), str)
                and current.get("response_text").strip()
            ):
                response_text = current.get("response_text")
                response_text_available = True

            if controller_id is not None:
                owner = current.get("controller_id")
                if owner is not None and owner != controller_id:
                    raise ControllerOwnershipConflict(
                        "Operation is owned by a different controller instance."
                    )
                current["controller_id"] = controller_id

            if current.get("operation_type") == "prompt":
                if not completion_markers_satisfied(
                    response_text,
                    current.get("completion_markers"),
                ):
                    raise ValueError(
                        "Prompt completion response does not satisfy the operation completion markers."
                    )
                if current.get("network_response_authoritative") is not True:
                    raise ValueError(
                        "Prompt completion requires authoritative CDP response evidence."
                    )

            if current.get("status") == "completed":
                chained = self.get_chained_operation(operation_id)
                return self._hydrate_terminal_response(dict(current)), chained

            current_status = str(current.get("status", ""))
            validate_transition(current_status, "completed")
            current["status"] = "completed"
            if current.get("operation_type") == "prompt":
                current["response_processing_required"] = True
                current["response_processing_complete"] = False

            if chat_url is not None:
                current["chat_url"] = chat_url
            if response_text is not None:
                bounded_response = response_text[:MAX_RESPONSE_TEXT_CHARS]
                current["response_text"] = bounded_response
                current["response_text_available"] = bool(
                    response_text_available and bool(bounded_response.strip())
                )
            if timing is not None:
                normalized_timing = self.normalize_timing(timing)
                if normalized_timing is None:
                    raise ValueError("invalid timing payload")
                current["timing"] = normalized_timing

            chained = None
            allow_chained_claim = (
                current.get("operation_type") != "prompt"
                or current.get("response_processing_complete") is True
            )
            if allow_chained_claim:
                for item in queue:
                    if item is current or item.get("status") != "queued":
                        continue
                    chained = dict(self._mark_claimed(item, controller_id))
                    current["next_operation_id"] = item.get("operation_id")
                    break

            self._save_queue(queue)
            return dict(current), chained

    def wait_for_operation(
        self,
        operation_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            return None
        if timeout_seconds < 0:
            return None

        deadline = time.monotonic() + timeout_seconds
        with self.operation_changed:
            while True:
                queue = self._load_queue()
                for item in queue:
                    if item.get("operation_id") != operation_id:
                        continue
                    hydrated = self._hydrate_terminal_response(dict(item))
                    if hydrated.get("status") in TERMINAL_QUEUE_STATUSES:
                        return hydrated
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return hydrated
                    self.operation_changed.wait(timeout=remaining)
                    break
                else:
                    return None

    @staticmethod
    def normalize_timing(value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if any(key not in MAX_TIMING_KEYS for key in value):
            return None
        result: dict[str, Any] = {}
        for key in ("injected_at_ms", "ack_at_ms", "generation_start_ms", "completed_at_ms", "response_processed_at_ms"):
            if key not in value:
                continue
            raw = value[key]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
                return None
            result[key] = float(raw) if isinstance(raw, float) else int(raw)
        for delta_key in ("completion_to_prompt_injected_ms", "response_completed_to_prompt_injected_ms"):
            if delta_key not in value:
                continue
            raw = value[delta_key]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
                return None
            result[delta_key] = float(raw) if isinstance(raw, float) else int(raw)

        if "user_messages_added" in value:
            raw = value["user_messages_added"]
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > 100:
                return None
            result["user_messages_added"] = raw
        if "ack_verified" in value:
            if not isinstance(value["ack_verified"], bool):
                return None
            result["ack_verified"] = value["ack_verified"]
        if "submission_via" in value:
            raw = value["submission_via"]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 64:
                return None
            result["submission_via"] = raw.strip()
        previous: float | None = None
        for key in ("injected_at_ms", "ack_at_ms", "generation_start_ms", "completed_at_ms"):
            raw = result.get(key)
            if raw is None:
                continue
            numeric = float(raw)
            if previous is not None and numeric < previous:
                return None
            previous = numeric
        processed_at = result.get("response_processed_at_ms")
        if processed_at is not None:
            if "completed_at_ms" in result and float(processed_at) < float(result["completed_at_ms"]):
                return None
        if result.get("ack_verified") is True and "ack_at_ms" not in result:
            return None
        return result

    def persist_timing(self, operation_id: str, timing: object) -> dict[str, Any] | None:
        normalized = self.normalize_timing(timing)
        if normalized is None or not operation_id.strip():
            return None
        with self.lock:
            queue = self._load_queue()
            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue
                item["timing"] = normalized
                self._save_queue(queue)
                return dict(item)
        return None

    def append_recovery_event(
        self,
        operation_id: str,
        data: dict[str, Any],
        captured_at: object,
    ) -> dict[str, Any] | None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            return None
        allowed = (
            "phase", "reason", "recovery_reason", "recovery_action",
            "replacement_reason", "reload_count", "age_ms", "idle_ms",
            "recovery_started_at_ms", "recovery_finished_at_ms",
            "recovery_duration_ms", "outcome", "observed_status", "error"
        )
        event = {key: data[key] for key in allowed if key in data}
        if isinstance(captured_at, str):
            event["captured_at"] = captured_at
        with self.lock:
            queue = self._load_queue()
            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue
                events = item.get("recovery_events")
                events = list(events) if isinstance(events, list) else []
                if not events or events[-1] != event:
                    events.append(event)
                    item["recovery_events"] = events[-MAX_RECOVERY_EVENTS_PER_OPERATION:]
                    self._save_queue(queue)
                return dict(item)
        return None

    def persist_response_evidence(
        self,
        operation_id: str,
        chat_url: str | None,
        response_text: str,
    ) -> dict[str, Any] | None:
        """Attach verified response evidence without changing terminal status."""
        bounded_response = response_text[:MAX_RESPONSE_TEXT_CHARS]
        if not bounded_response.strip():
            return None
        with self.lock:
            queue = self._load_queue()
            for item in queue:
                if item.get("operation_id") != operation_id or item.get("operation_type") != "prompt":
                    continue
                stored_response = self.state_manager.load_terminal_response(operation_id)
                current = item.get("response_text")
                authoritative = (
                    stored_response
                    if isinstance(stored_response, str) and stored_response.strip()
                    else current
                )
                if item.get("response_text_available") is True and isinstance(authoritative, str) and authoritative.strip():
                    return dict(item)
                if not completion_markers_satisfied(
                    bounded_response,
                    item.get("completion_markers"),
                ):
                    return dict(item)
                item["response_text"] = bounded_response
                item["response_text_available"] = True
                if isinstance(chat_url, str):
                    item["chat_url"] = chat_url
                item["response_source"] = "completion_ack"
                item["response_observed_at"] = time.time()
                self._save_queue(queue)
                return dict(item)
        return None

    def mark_response_processed(
        self,
        operation_id: str,
        controller_id: str,
    ) -> dict[str, Any] | None:
        """Durably release the next-operation barrier after runner response processing."""
        if not isinstance(operation_id, str) or not operation_id.strip():
            return None
        if not isinstance(controller_id, str) or not controller_id.strip():
            return None
        with self.lock:
            queue = self._load_queue()
            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue
                if item.get("operation_type") != "prompt" or item.get("status") != "completed":
                    return None
                owner = item.get("controller_id")
                if owner != controller_id.strip():
                    return None
                stored_response = self.state_manager.load_terminal_response(operation_id)
                response_text = stored_response if isinstance(stored_response, str) else item.get("response_text")
                if item.get("response_text_available") is not True or not isinstance(response_text, str) or not response_text.strip():
                    return None
                if item.get("network_response_authoritative") is not True:
                    return None

                timing = dict(item.get("timing") or {})
                processed_at_ms = int(time.time() * 1000)
                completed_at_ms = timing.get("completed_at_ms")
                if isinstance(completed_at_ms, (int, float)) and not isinstance(completed_at_ms, bool):
                    processed_at_ms = max(processed_at_ms, int(completed_at_ms))
                timing["response_processed_at_ms"] = processed_at_ms
                normalized = self.normalize_timing(timing)
                if normalized is None:
                    return None
                item["timing"] = normalized
                item["response_processing_required"] = True
                item["response_processing_complete"] = True
                item["updated_at"] = time.time()
                self._save_queue(queue)
                return dict(item)
        return None

    @staticmethod
    def _response_processing_pending_for_controller(
        queue: list[dict[str, Any]],
        controller_id: str | None,
    ) -> bool:
        if not controller_id:
            return False
        for item in queue:
            if (
                item.get("status") == "completed"
                and item.get("operation_type") == "prompt"
                and item.get("controller_id") == controller_id
                and item.get("response_processing_required") is True
                and item.get("response_processing_complete") is not True
            ):
                return True
        return False

    def cancel_operation(
        self,
        operation_id: str,
        controller_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Cancel a nonterminal operation, fencing controller ownership when present."""
        if not isinstance(operation_id, str) or not operation_id.strip():
            return None
        normalized_controller = (
            controller_id.strip()
            if isinstance(controller_id, str) and controller_id.strip()
            else None
        )
        normalized_reason = (
            reason[:MAX_ERROR_CHARS]
            if isinstance(reason, str) and reason.strip()
            else "operation cancelled"
        )
        with self.lock:
            queue = self._load_queue()
            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue

                current_status = str(item.get("status", ""))
                owner = item.get("controller_id")
                if owner is not None and owner != normalized_controller:
                    raise ControllerOwnershipConflict(
                        "operation is owned by a different controller"
                    )
                if current_status == "cancelled":
                    return dict(item)
                if current_status not in {"queued", "claimed", "generating"}:
                    return None

                validate_transition(current_status, "cancelled")
                item["status"] = "cancelled"
                item["error"] = normalized_reason
                item["failure_reason"] = "cancelled"
                item["updated_at"] = time.time()
                if normalized_controller is not None:
                    item["cancelled_by_controller_id"] = normalized_controller
                if item.get("operation_type") == "prompt":
                    item["response_processing_required"] = False
                    item["response_processing_complete"] = False
                self._save_queue(queue)
                return dict(item)
        return None

    def complete_operation(
        self,
        operation_id: str,
        chat_url: str | None = None,
        response_text: str | None = None,
        response_text_available: bool = False,
        timing: object = None,
    ) -> dict[str, Any] | None:
        return self._update_operation(
            operation_id=operation_id,
            status="completed",
            chat_url=chat_url,
            response_text=response_text,
            response_text_available=response_text_available,
            timing=timing,
        )

    def fail_operation(
        self,
        operation_id: str,
        error: str,
        recovery_context: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if self._is_transient_browser_error(error):
            return self._retry_operation(
                operation_id=operation_id,
                error=error,
                recovery_context=recovery_context,
            )

        return self._update_operation(
            operation_id=operation_id,
            status="failed",
            error=error,
        )

    def heartbeat(
        self,
        operation_id: str,
    ) -> dict[str, Any] | None:
        return self._update_operation(
            operation_id=operation_id,
            status="generating",
        )

    @staticmethod
    def _browser_observation_time(observation: dict[str, Any]) -> float | None:
        captured_at = observation.get("captured_at")
        if not isinstance(captured_at, str):
            return None
        try:
            value = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    @staticmethod
    def _browser_observation_priority(observation: dict[str, Any]) -> int:
        schema_version = observation.get("schema_version")
        data = observation.get("data")
        kind = data.get("kind") if isinstance(data, dict) else None

        if schema_version == "pasi-network-cdp-v1" and kind in {
            "chatgpt_network_lifecycle",
            "chatgpt_network_response",
        }:
            return 120
        if schema_version == "pasi-native-chromium-v2" and kind in {
            "chatgpt_health",
            "chatgpt_state",
            "chatgpt_response",
        }:
            return 100
        if schema_version == "chatgpt-controller-v2":
            return 90
        if schema_version == "pasi-chatgpt-recovery-v3":
            return 50
        return 10

    def save_browser_observation(
        self,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        with self.lock:
            self._persist_verified_response_observation(observation)
            data = observation.get("data")
            if isinstance(data, dict):
                kind = data.get("kind")
                if kind in {"chatgpt_response", "chatgpt_network_response"}:
                    timing = data.get("timing")
                    active_operation_id = data.get("active_operation_id")
                    if isinstance(active_operation_id, str) and timing is not None:
                        self.persist_timing(active_operation_id, timing)
                    self.state_manager.save_browser_response(observation)
                elif kind == "chatgpt_health":
                    self.state_manager.save_browser_health(observation)
                elif kind == "chatgpt_state":
                    self.state_manager.save_browser_state(observation)
            if isinstance(data, dict) and data.get("kind") == "chatgpt_recovery":
                operation_id = data.get("operation_id")
                if isinstance(operation_id, str):
                    self.append_recovery_event(operation_id, data, observation.get("captured_at"))

            current = self.state_manager.load_browser_results()
            incoming_priority = self._browser_observation_priority(observation)
            current_priority = self._browser_observation_priority(current)

            incoming_time = self._browser_observation_time(observation)
            current_time = self._browser_observation_time(current)
            should_replace = incoming_priority > current_priority
            if incoming_priority == current_priority:
                if current_time is None:
                    should_replace = True
                elif incoming_time is not None and incoming_time >= current_time:
                    should_replace = True

            if should_replace:
                self.state_manager.save_browser_results(observation)

        return observation

    def get_browser_health(
        self,
    ) -> dict[str, Any] | None:
        with self.lock:
            health = self.state_manager.load_browser_health()
            return health if health else None

    def get_browser_state(
        self,
    ) -> dict[str, Any] | None:
        with self.lock:
            state = self.state_manager.load_browser_state()
            return state if state else None

    def get_browser_response(
        self,
    ) -> dict[str, Any] | None:
        with self.lock:
            response = self.state_manager.load_browser_response()
            return response if response else None

    def get_browser_observation(
        self,
    ) -> dict[str, Any] | None:
        with self.lock:
            observation = (
                self.state_manager.load_browser_results()
            )

            if not isinstance(observation, dict):
                return None

            return observation

    def get_status(self) -> dict[str, Any]:
        with self.lock:
            queue = self._load_queue()
            self._sweep_queue_locked(queue)

            counts: dict[str, int] = {}

            for item in queue:
                status = str(
                    item.get("status", "unknown")
                )

                counts[status] = (
                    counts.get(status, 0) + 1
                )

            active_statuses = {
                "queued",
                "claimed",
                "generating",
            }

            queue_size = sum(
                1
                for item in queue
                if item.get("status") in active_statuses
            )

            return {
                "service": "pasi-engineering-workspace-chatgpt-bridge",
                "host": HOST,
                "port": PORT,
                "queue_size": queue_size,
                "history_size": len(queue),
                "counts": counts,
            }

    def _persist_verified_response_observation(
        self,
        observation: dict[str, Any],
    ) -> None:
        data = observation.get("data")
        if not isinstance(data, dict):
            return

        kind = data.get("kind")
        if kind not in {"chatgpt_response", "chatgpt_network_response", "chatgpt_network_lifecycle"}:
            return

        operation_id = data.get("active_operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            return

        queue = self._load_queue()
        for item in queue:
            if item.get("operation_id") != operation_id:
                continue
            if item.get("operation_type") != "prompt":
                return

            network_source = data.get("network_source")
            controller_id = data.get("controller_id")
            if network_source == "cdp_fetch":
                if (
                    isinstance(controller_id, str)
                    and controller_id.strip()
                    and item.get("controller_id") != controller_id.strip()
                ):
                    return
                event_type = str(data.get("event_type") or "")
                item["network_source"] = "cdp_fetch"
                if isinstance(controller_id, str) and controller_id.strip():
                    item["network_controller_id"] = controller_id.strip()
                request_id = data.get("request_id")
                if isinstance(request_id, str) and request_id:
                    item["network_request_id"] = request_id[:200]
                if event_type == "STARTED":
                    if item.get("status") == "claimed":
                        validate_transition("claimed", "generating")
                        item["status"] = "generating"
                    item["network_lifecycle_event"] = event_type
                elif event_type in {"COMPLETED", "INTERRUPTED", "FAILED"}:
                    item["network_terminal_event"] = event_type
                elif event_type:
                    # Lifecycle events such as STARTED are not terminal
                    # evidence. Keep them separate so response polling cannot
                    # misclassify an in-flight generation as a failure.
                    item["network_lifecycle_event"] = event_type
                    item.pop("network_terminal_event", None)
                    item.pop("network_terminal_reason", None)
                    item.pop("network_classification", None)
                    item.pop("network_failure_source", None)
                reason = data.get("reason")
                classification = data.get("classification")
                if isinstance(reason, str) and reason:
                    item["network_terminal_reason"] = reason[:200]
                if isinstance(classification, str) and classification:
                    item["network_classification"] = classification[:120]
                if event_type in {"INTERRUPTED", "FAILED"}:
                    item["network_failure_source"] = "cdp"

            response_text = data.get("response_text")
            cdp_response_complete = (
                network_source != "cdp_fetch"
                or (
                    str(data.get("event_type") or "") == "COMPLETED"
                    and data.get("stream_complete") is True
                )
            )
            response_verified = (
                cdp_response_complete
                and isinstance(response_text, str)
                and len(response_text) <= MAX_RESPONSE_TEXT_CHARS
                and bool(response_text.strip())
                and completion_markers_satisfied(
                    response_text,
                    item.get("completion_markers"),
                )
            )
            if response_verified:
                item["response_text"] = response_text
                item["response_text_available"] = True
                item["response_source"] = (
                    "cdp_fetch_stream"
                    if network_source == "cdp_fetch"
                    else "browser_observation"
                )
                item["response_observed_at"] = observation.get("captured_at", time.time())
                if network_source == "cdp_fetch":
                    item["network_response_authoritative"] = True
                    assistant_message_id = data.get("assistant_message_id")
                    if isinstance(assistant_message_id, str) and assistant_message_id:
                        item["network_assistant_message_id"] = assistant_message_id[:200]
                chat_url = data.get("chat_url") or data.get("request_url")
                if isinstance(chat_url, str) and chat_url:
                    item["chat_url"] = chat_url
            if network_source == "cdp_fetch" or kind == "chatgpt_response":
                self._save_queue(queue)
            return

    def _repair_response_from_browser_observation(
        self,
        item: dict[str, Any],
    ) -> bool:
        if item.get("operation_type") != "prompt":
            return False
        operation_id = item.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            return False

        stored_response = self.state_manager.load_terminal_response(operation_id)
        if isinstance(stored_response, str) and stored_response.strip():
            return False
        current_response = item.get("response_text")
        if (
            item.get("response_text_available") is True
            and isinstance(current_response, str)
            and current_response.strip()
        ):
            return False

        observation = self.state_manager.load_browser_response()
        if not isinstance(observation, dict):
            return False
        data = observation.get("data")
        if not isinstance(data, dict) or data.get("kind") not in {"chatgpt_response", "chatgpt_network_response"}:
            return False
        if data.get("active_operation_id") != item.get("operation_id"):
            return False

        network_source = data.get("network_source")
        if network_source == "cdp_fetch" and not (
            str(data.get("event_type") or "") == "COMPLETED"
            and data.get("stream_complete") is True
        ):
            return False

        response_text = data.get("response_text")
        if (
            not isinstance(response_text, str)
            or len(response_text) > MAX_RESPONSE_TEXT_CHARS
            or not response_text.strip()
            or not completion_markers_satisfied(
                response_text,
                item.get("completion_markers"),
            )
        ):
            return False

        item["response_text"] = response_text
        item["response_text_available"] = True
        chat_url = data.get("chat_url") or data.get("request_url")
        if isinstance(chat_url, str):
            item["chat_url"] = chat_url
        item["response_source"] = (
            "cdp_fetch_stream"
            if data.get("network_source") == "cdp_fetch"
            else "browser_observation"
        )
        item["network_response_authoritative"] = data.get("network_source") == "cdp_fetch"
        item["response_observed_at"] = observation.get("captured_at", time.time())
        return True

    @staticmethod
    def _retry_class(error: str) -> str:
        if (
            error.startswith("CHAT_EXHAUSTED:")
            or error.startswith("PASI_NATIVE: context recovery exhausted:")
            or error.startswith("PASI_CDP: CONTEXT_EXHAUSTED")
        ):
            return "context"
        if (
            error.startswith("PASI_NATIVE: ChatGPT generation timed out")
            or error.startswith("PASI_NATIVE: response text unavailable")
            or error.startswith("PASI_CDP: NETWORK_RESPONSE_CAPTURE_FAILED")
            or error.startswith("PASI_CDP: NETWORK_RESPONSE_INCOMPLETE")
            or error.startswith("PASI_CDP: NETWORK_RESPONSE_TIMEOUT")
            or error.startswith("PASI_CDP: RESPONSE_MARKER_NOT_FOUND")
            or error.startswith("PASI_CDP: network failure")
            or error.startswith("PASI_CDP: NETWORK_STREAM_DISCONNECTED")
            or error.startswith("PASI_CDP: CDP_DEBUGGER_DETACHED")
        ):
            return "response"
        return "controller"

    def _retry_operation(
        self,
        operation_id: str,
        error: str,
        recovery_context: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            queue = self._load_queue()
            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue
                current_status = str(item.get("status", ""))
                retry_counts = item.get("retry_counts")
                if not isinstance(retry_counts, dict):
                    retry_counts = {
                        "controller": int(item.get("retry_count", 0) or 0),
                        "response": 0,
                        "context": 0,
                    }
                retry_class = self._retry_class(error)
                count = int(retry_counts.get(retry_class, 0) or 0)

                if retry_class == "context":
                    if count >= RETRY_BUDGETS[retry_class]:
                        validate_transition(current_status, "failed")
                        item["status"] = "failed"
                        item["error"] = error[:MAX_ERROR_CHARS]
                        item["failure_reason"] = "context_retry_exhausted"
                        item["retry_class"] = "context"
                        item["retry_counts"] = dict(retry_counts)
                        item["retry_count"] = sum(int(value or 0) for value in retry_counts.values())
                        item["updated_at"] = time.time()
                        self._save_queue(queue)
                        return dict(item)

                    retry_counts = dict(retry_counts)
                    retry_counts["context"] = count + 1
                    item["status"] = "failed"
                    item["error"] = (
                        "CHAT_EXHAUSTED: explicit network context exhaustion; "
                        "the runner must prepare a fresh ChatGPT conversation before retrying the task."
                    )
                    item["failure_reason"] = "context_exhausted"
                    item["retry_class"] = "context"
                    item["retry_counts"] = retry_counts
                    item["retry_count"] = sum(int(value or 0) for value in retry_counts.values())
                    item["updated_at"] = time.time()
                    self._save_queue(queue)
                    return dict(item)

                if (
                    item.get("operation_type") == "prompt"
                    and item.get("network_response_authoritative") is True
                    and item.get("response_text_available") is True
                    and isinstance(item.get("response_text"), str)
                    and bool(str(item.get("response_text")).strip())
                ):
                    validate_transition(current_status, "completed")
                    item["status"] = "completed"
                    if item.get("operation_type") == "prompt":
                        item["response_processing_required"] = True
                        item["response_processing_complete"] = False
                    item["completion_recovery_reason"] = "browser_response_observation_after_transient_failure"
                    item["recovery_error"] = error[:MAX_ERROR_CHARS]
                    item["updated_at"] = time.time()
                    self._save_queue(queue)
                    return dict(item)

                if count >= RETRY_BUDGETS[retry_class]:
                    validate_transition(current_status, "failed")
                    item["status"] = "failed"
                    item["error"] = error[:MAX_ERROR_CHARS]
                    item["failure_reason"] = (
                        "transient_retry_exhausted"
                        if retry_class == "controller"
                        else f"{retry_class}_retry_exhausted"
                    )
                    item["retry_class"] = retry_class
                    item["updated_at"] = time.time()
                    self._save_queue(queue)
                    return dict(item)

                retry_counts = dict(retry_counts)
                retry_counts[retry_class] = count + 1
                item["retry_class"] = retry_class
                validate_transition(current_status, "queued")
                item["status"] = "queued"
                item["retry_counts"] = retry_counts
                item["retry_count"] = sum(int(value or 0) for value in retry_counts.values())
                item["last_retry_error"] = error[:MAX_ERROR_CHARS]
                if item.get("operation_type") == "prompt" and recovery_context:
                    item["recovery_context"] = dict(recovery_context)
                item.pop("claimed_at", None)
                item["requeued_at"] = time.time()
                item["updated_at"] = time.time()
                self._save_queue(queue)
                return dict(item)
        return None

    @staticmethod
    def _is_transient_browser_error(error: str) -> bool:
        return any(
            error.startswith(prefix)
            for prefix in _TRANSIENT_BROWSER_ERROR_PREFIXES
        )

    @staticmethod
    def _normalize_recovery_context(
        value: object,
    ) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None

        normalized: dict[str, str] = {}
        if any(key not in {"reasoning_mode", "github_repository"} for key in value):
            return None

        reasoning_mode = value.get("reasoning_mode")
        if "reasoning_mode" in value:
            if not isinstance(reasoning_mode, str):
                return None
            reasoning_mode = reasoning_mode.strip().lower()
            if reasoning_mode not in {"thinking", "think"}:
                return None
            normalized["reasoning_mode"] = "thinking"

        repository = value.get("github_repository")
        if "github_repository" in value:
            if not isinstance(repository, str):
                return None
            repository = repository.strip()
            if not (
                len(repository) <= MAX_RECOVERY_CONTEXT_REPOSITORY_CHARS
                and repository.count("/") == 1
                and all(
                    part
                    and not any(char.isspace() for char in part)
                    for part in repository.split("/", 1)
                )
            ):
                return None
            normalized["github_repository"] = repository

        return normalized or None

    def _update_operation(
        self,
        operation_id: str,
        status: str,
        chat_url: str | None = None,
        error: str | None = None,
        response_text: str | None = None,
        response_text_available: bool = False,
        timing: object = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            queue = self._load_queue()

            for item in queue:
                if item.get("operation_id") != operation_id:
                    continue

                current_status = str(item.get("status", ""))
                validate_transition(current_status, status)
                item["status"] = status

                if status == "completed" and item.get("operation_type") == "prompt":
                    item["response_processing_required"] = True
                    item["response_processing_complete"] = False

                if chat_url is not None:
                    item["chat_url"] = chat_url

                if error is not None:
                    item["error"] = error[:MAX_ERROR_CHARS]

                if response_text is not None:
                    bounded_response = response_text[:MAX_RESPONSE_TEXT_CHARS]
                    item["response_text"] = bounded_response
                    item["response_text_available"] = bool(
                        response_text_available
                        and bool(bounded_response.strip())
                    )

                if timing is not None:
                    normalized_timing = self.normalize_timing(timing)
                    if normalized_timing is None:
                        raise ValueError("invalid timing payload")
                    item["timing"] = normalized_timing

                self._save_queue(queue)

                return item

        return None

    @staticmethod
    def _new_operation_id() -> str:
        import secrets

        return (
            f"op-{time.time_ns()}-"
            f"{secrets.token_hex(4)}"
        )


class BridgeHTTPServer(ThreadingHTTPServer):
    """
    Typed HTTP server that carries the shared BridgeState.

    This avoids dynamically adding an undeclared attribute to
    ThreadingHTTPServer, which Pylance correctly flags.
    """

    bridge_state: BridgeState


def _bridge_access_log_should_emit(message: str) -> bool:
    """Return true only for non-success HTTP access responses."""
    try:
        status_code = int(message.rsplit(" ", 2)[-2])
    except (ValueError, IndexError):
        return True
    return not (0 < status_code < 400)


def runner_registry_payload() -> dict[str, Any]:
    active = runner_process_info()
    snapshot = registry_snapshot()
    snapshot["builtins"] = runner_registry_entries()[:2]
    snapshot["active_profile"] = active.get("profile") if active else None
    return snapshot


class BridgeRequestHandler(BaseHTTPRequestHandler):
    """
    Small localhost HTTP API consumed by the native MV3
    PASI Engineering Workspace ChatGPT controller.

    CORS is intentionally restricted to ChatGPT origins.
    """

    def _expected_bridge_token(self) -> str:
        configured = os.environ.get("PASI_BRIDGE_TOKEN", "").strip()
        if configured:
            return configured
        try:
            return BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _request_is_authorized(self, *, require_token: bool) -> bool:
        host = self.headers.get("Host", "")
        bound_port = self.server.server_address[1] if isinstance(self.server.server_address, tuple) else PORT
        if host != f"{HOST}:{bound_port}":
            return False
        origin = self.headers.get("Origin", "").strip()
        if origin and not origin.startswith("chrome-extension://"):
            return False
        if not require_token:
            return True
        expected = self._expected_bridge_token()
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        token = supplied[len(prefix):].strip() if supplied.startswith(prefix) else ""
        return bool(expected) and bool(token) and hmac.compare_digest(token, expected)

    server_version = "PASIEngineeringWorkspaceChatBridge/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def bridge_state(self) -> BridgeState:
        return self.server.bridge_state  # type: ignore[attr-defined]

    def _set_headers(
        self,
        status: int = HTTPStatus.OK,
        *,
        content_length: int | None = None,
    ) -> None:
        origin = self.headers.get(
            "Origin",
            "",
        )

        allowed_origins = {
            "https://chatgpt.com",
            "https://www.chatgpt.com",
        }

        self.send_response(status)

        if origin in allowed_origins:
            self.send_header(
                "Access-Control-Allow-Origin",
                origin,
            )

        self.send_header(
            "Vary",
            "Origin",
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS",
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization",
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))

        self.end_headers()

    def _send_json(
        self,
        payload: dict[str, Any],
        status: int = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self._set_headers(status, content_length=len(body))

        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get(
            "Content-Length",
            "0",
        )

        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError(
                "Invalid Content-Length."
            ) from exc

        if content_length < 0:
            raise ValueError(
                "Invalid Content-Length."
            )

        if content_length > 2_000_000:
            raise ValueError(
                "Request body is too large."
            )

        raw_body = self.rfile.read(
            content_length
        )

        if not raw_body:
            return {}

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_length and content_type != "application/json":
            raise ValueError("JSON request body requires Content-Type: application/json")

        payload = json.loads(
            raw_body.decode("utf-8")
        )

        if not isinstance(payload, dict):
            raise ValueError(
                "JSON body must be an object."
            )

        return payload

    def do_OPTIONS(self) -> None:
        self._set_headers(
            HTTPStatus.NO_CONTENT
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path not in {"/health", "/runner/diagnostics"} and not self._request_is_authorized(require_token=True):
            self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return

        if path == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "service": "pasi-engineering-workspace-chatgpt-bridge",
                }
            )
            return

        if path == "/status":
            self._send_json(
                self.bridge_state.get_status()
            )
            return

        if path == "/runner/capabilities":
            self._send_json(load_runner_capabilities())
            return

        if path == "/runner/state":
            self._send_json(load_runner_state())
            return

        if path == "/runner/registry":
            self._send_json(runner_registry_payload())
            return

        if path == "/runner/diagnostics":
            # This is intentionally read-only and token-free: the bridge binds
            # exclusively to 127.0.0.1, and no secrets/tokens are included.
            self._send_json(runner_diagnostics_payload(self.bridge_state))
            return

        if path == "/browser/observation":
            observation = (
                self.bridge_state.get_browser_observation()
            )

            self._send_json(
                {
                    "observation": observation
                }
            )
            return

        if path == "/browser/health":
            self._send_json(
                {
                    "observation": self.bridge_state.get_browser_health()
                }
            )
            return

        if path == "/browser/state":
            self._send_json(
                {
                    "observation": self.bridge_state.get_browser_state()
                }
            )
            return

        if path == "/browser/response":
            response = self.bridge_state.get_browser_response()

            self._send_json(
                {
                    "observation": response
                }
            )
            return

        if path == "/browser/testing/request":
            values = parse_qs(parsed.query).get("tab_id", [])
            if not values:
                self._send_json({"error": "tab_id is required."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                tab_id = int(values[0])
                wait_ms = min(max(int(parse_qs(parsed.query).get("wait_ms", ["0"])[0]), 0), 10_000)
            except ValueError:
                self._send_json({"error": "tab_id and wait_ms must be integers."}, HTTPStatus.BAD_REQUEST)
                return
            request = self.bridge_state.wait_for_browser_test_request(tab_id, wait_ms)
            self._send_json({"request": request})
            return

        if path == "/browser/testing/result":
            values = parse_qs(parsed.query).get("request_id", [])
            request_id = values[0].strip() if values else ""
            if not request_id or len(request_id) > 200:
                self._send_json({"error": "request_id is required."}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"result": self.bridge_state.get_browser_test_result(request_id)})
            return

        if path == "/operation":
            operation_ids = parse_qs(parsed.query).get("operation_id", [])
            operation_id = operation_ids[0] if operation_ids else ""
            if not operation_id or len(operation_id) > 200:
                self._send_json(
                    {"error": "operation_id is required."},
                    HTTPStatus.BAD_REQUEST,
                )
                return

            wait_values = parse_qs(parsed.query).get("wait_ms", [])
            wait_ms = 0
            if wait_values:
                try:
                    wait_ms = min(max(int(wait_values[0]), 0), 10000)
                except ValueError:
                    self._send_json(
                        {"error": "wait_ms must be an integer."},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return

            if wait_ms:
                operation = self.bridge_state.wait_for_operation(
                    operation_id,
                    wait_ms / 1000.0,
                )
            else:
                operation = self.bridge_state.get_operation(operation_id)
            if operation is None:
                self._send_json(
                    {"error": "Operation not found."},
                    HTTPStatus.NOT_FOUND,
                )
                return

            payload = {"operation": operation}
            chained = self.bridge_state.get_chained_operation(operation_id)
            if chained is not None:
                payload["next_operation"] = chained
            self._send_json(payload)
            return

        if path == "/next-operation":
            controller_ids = parse_qs(parsed.query).get("controller_id", [])
            wait_values = parse_qs(parsed.query).get("wait_ms", [])
            controller_id = controller_ids[0].strip() if controller_ids else ""
            if not controller_id or len(controller_id) > 200:
                self._send_json({"error": "controller_id is required."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                wait_ms = int(wait_values[0]) if wait_values else 0
            except (TypeError, ValueError):
                self._send_json({"error": "wait_ms must be an integer."}, HTTPStatus.BAD_REQUEST)
                return
            if wait_ms < 0 or wait_ms > 60_000:
                self._send_json({"error": "wait_ms must be between 0 and 60000."}, HTTPStatus.BAD_REQUEST)
                return

            runner_state = load_runner_state()
            if not runner_execution_authorized(runner_state):
                self._send_json({"operation": None, "runner_ready": False})
                return

            operation = self.bridge_state.wait_for_next_operation(controller_id, wait_ms)
            self._send_json({"operation": operation, "runner_ready": True})
            return

        self._send_json(
            {
                "error": "Not found"
            },
            HTTPStatus.NOT_FOUND,
        )

    def do_POST(self) -> None:
        path = urlparse(
            self.path
        ).path

        if not self._request_is_authorized(require_token=True):
            # Rejecting before consuming the body must close the HTTP/1.1
            # connection; otherwise unread JSON bytes can be parsed as the next
            # request line and produce misleading 501 errors.
            self.close_connection = True
            self._send_json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return

        try:
            payload = self._read_json()
        except (
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._send_json(
                {
                    "error": str(exc)
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            if path == "/browser/testing/request":
                self._browser_testing_request(payload)
                return

            if path == "/browser/testing/result":
                self._browser_testing_result(payload)
                return

            if path == "/chat/claim":
                self._claim(payload)
                return

            if path == "/browser/observation":
                self._browser_observation(payload)
                return

            if path == "/runner/registry/create":
                runner_id = payload.get("id")
                name = payload.get("name")
                entrypoint = payload.get("entrypoint")
                args = payload.get("args")
                source = str(payload.get("source") or "user")
                self._send_json({
                    "ok": True,
                    "runner": create_runner(
                        runner_id,
                        name,
                        entrypoint,
                        args,
                        source=source,
                        project_root=CONFIG.project_root,
                    ),
                })
                return

            if path == "/runner/registry/revision":
                self._send_json({
                    "ok": True,
                    "revision": create_revision(
                        payload.get("id"),
                        payload.get("entrypoint"),
                        payload.get("args"),
                        source=str(payload.get("source") or "automation"),
                        project_root=CONFIG.project_root,
                    ),
                })
                return

            if path == "/runner/registry/validate":
                self._send_json({
                    "ok": True,
                    "revision": validate_revision(
                        payload.get("id"),
                        payload.get("version"),
                        payload.get("evidence"),
                    ),
                })
                return

            if path == "/runner/registry/promote":
                self._send_json({
                    "ok": True,
                    "runner": promote_revision(
                        payload.get("id"),
                        payload.get("version"),
                        project_root=CONFIG.project_root,
                    ),
                })
                return

            if path == "/runner/registry/rollback":
                self._send_json({
                    "ok": True,
                    "runner": rollback_runner(payload.get("id")),
                })
                return

            if path == "/runner/control":
                action = payload.get("action")
                if not isinstance(action, str):
                    self._send_json({"error": "action is required."}, HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(
                    request_runner_control(
                        action.strip().casefold(),
                        payload.get("profile"),
                    )
                )
                return

            if path == "/queue":
                self._queue(payload)
                return

            if path == "/chat/heartbeat":
                self._heartbeat(payload)
                return

            if path == "/chat/finished":
                self._finished(payload)
                return

            if path == "/chat/processed":
                self._processed(payload)
                return

            if path == "/chat/failed":
                self._failed(payload)
                return

            if path == "/chat/cancel":
                self._cancel(payload)
                return

            self._send_json(
                {
                    "error": "Not found"
                },
                HTTPStatus.NOT_FOUND,
            )

        except (InvalidOperationTransition, RunnerRegistryError, ValueError) as exc:
            self._send_json(
                {
                    "error": str(exc)
                },
                HTTPStatus.CONFLICT,
            )
        except Exception as exc:
            detail = type(exc).__name__ + ": " + str(exc)
            detail = detail.strip()[:500]
            print("[Bridge] POST " + path + " failed: " + detail, file=sys.stderr, flush=True)
            self._send_json(
                {
                    "error": "Internal server error: " + detail
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _browser_testing_request(
        self,
        payload: dict[str, Any],
    ) -> None:
        action = payload.get("action")
        tab_id = payload.get("tab_id")
        params = payload.get("params")
        if not isinstance(action, str) or not action.strip():
            self._send_json({"error": "action is required."}, HTTPStatus.BAD_REQUEST)
            return
        if tab_id is not None and (not isinstance(tab_id, int) or tab_id < 1):
            self._send_json({"error": "tab_id must be a positive integer or null."}, HTTPStatus.BAD_REQUEST)
            return
        if params is not None and not isinstance(params, dict):
            self._send_json({"error": "params must be an object."}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(
            self.bridge_state.queue_browser_test_request(
                action.strip(),
                tab_id,
                params,
            )
        )

    def _browser_testing_result(
        self,
        payload: dict[str, Any],
    ) -> None:
        request_id = payload.get("request_id")
        tab_id = payload.get("tab_id")
        ok = payload.get("ok")
        data = payload.get("data")
        error = payload.get("error")
        if not isinstance(request_id, str) or not request_id.strip():
            self._send_json({"error": "request_id is required."}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(tab_id, int) or tab_id < 1:
            self._send_json({"error": "tab_id must be a positive integer."}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(ok, bool):
            self._send_json({"error": "ok must be a boolean."}, HTTPStatus.BAD_REQUEST)
            return
        if data is not None and not isinstance(data, dict):
            self._send_json({"error": "data must be an object or null."}, HTTPStatus.BAD_REQUEST)
            return
        if error is not None and not isinstance(error, (dict, str)):
            self._send_json({"error": "error must be an object, string, or null."}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(
            self.bridge_state.submit_browser_test_result(
                request_id.strip(),
                tab_id,
                ok,
                data,
                error,
            )
        )

    def _claim(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_id = payload.get("operation_id")
        controller_id = payload.get("controller_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            self._send_json(
                {"error": "operation_id is required."},
                HTTPStatus.BAD_REQUEST,
            )
            return
        if not isinstance(controller_id, str) or not controller_id.strip() or len(controller_id) > 200:
            self._send_json({"error": "controller_id is required."}, HTTPStatus.BAD_REQUEST)
            return

        operation = self.bridge_state.claim_operation(operation_id, controller_id.strip())
        if operation is None:
            self._send_json(
                {"error": "Operation is not queued."},
                HTTPStatus.CONFLICT,
            )
            return

        self._send_json({"operation": operation})

    def _browser_observation(
        self,
        payload: dict[str, Any],
    ) -> None:
        observation = payload.get(
            "observation"
        )

        if not isinstance(
            observation,
            dict,
        ):
            self._send_json(
                {
                    "error":
                        "observation must be an object."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        schema_version = observation.get(
            "schema_version"
        )

        if not isinstance(
            schema_version,
            str,
        ) or not schema_version.strip():
            self._send_json(
                {
                    "error":
                        "observation.schema_version is required."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        saved = (
            self.bridge_state.save_browser_observation(
                observation
            )
        )

        self._send_json(
            {
                "observation": saved
            },
            HTTPStatus.CREATED,
        )

    def _queue(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_type = payload.get(
            "operation_type"
        )

        prompt = payload.get(
            "prompt"
        )

        completion_markers = payload.get("completion_markers")
        if completion_markers is not None and (
            not isinstance(completion_markers, list)
            or not completion_markers
            or len(completion_markers) > 4
            or any(
                not isinstance(marker, str)
                or not marker.strip()
                or len(marker.strip()) > 120
                or "\n" in marker
                or "\r" in marker
                for marker in completion_markers
            )
        ):
            self._send_json(
                {"error": "completion_markers must be 1-4 bounded single-line strings."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        idempotency_key = payload.get("idempotency_key")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_CHARS
        ):
            self._send_json({"error": "idempotency_key must be a nonblank bounded string."}, HTTPStatus.BAD_REQUEST)
            return

        if not isinstance(
            operation_type,
            str,
        ) or not operation_type.strip():
            self._send_json(
                {
                    "error":
                        "operation_type is required."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(
            prompt,
            str,
        ):
            self._send_json(
                {
                    "error":
                        "prompt must be a string."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if operation_type != "new_chat" and not prompt.strip():
            self._send_json(
                {
                    "error":
                        "prompt is required for this operation type."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        operation = (
            self.bridge_state.queue_operation(
                operation_type=operation_type,
                prompt=prompt,
                idempotency_key=idempotency_key,
                completion_markers=completion_markers,
            )
        )

        self._send_json(
            {
                "operation": operation.to_dict()
            },
            HTTPStatus.CREATED,
        )

    def _heartbeat(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_id = payload.get(
            "operation_id"
        )

        if not isinstance(
            operation_id,
            str,
        ):
            self._send_json(
                {
                    "error":
                        "operation_id is required."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        operation = (
            self.bridge_state.heartbeat(
                operation_id
            )
        )

        if operation is None:
            self._send_json(
                {
                    "error":
                        "Operation not found."
                },
                HTTPStatus.NOT_FOUND,
            )
            return

        self._send_json(
            {
                "operation": operation
            }
        )

    def _cancel(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_id = payload.get("operation_id")
        controller_id = payload.get("controller_id")
        reason = payload.get("reason")

        if not isinstance(operation_id, str) or not operation_id.strip():
            self._send_json({"error": "operation_id is required."}, HTTPStatus.BAD_REQUEST)
            return
        if controller_id is not None and (
            not isinstance(controller_id, str) or not controller_id.strip() or len(controller_id) > 200
        ):
            self._send_json({"error": "controller_id must be a bounded string."}, HTTPStatus.BAD_REQUEST)
            return
        if reason is not None and (
            not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_ERROR_CHARS
        ):
            self._send_json({"error": "reason must be a bounded string."}, HTTPStatus.BAD_REQUEST)
            return

        operation = self.bridge_state.cancel_operation(
            operation_id=operation_id.strip(),
            controller_id=controller_id.strip() if isinstance(controller_id, str) else None,
            reason=reason,
        )
        if operation is None:
            self._send_json(
                {"error": "Operation is not cancellable."},
                HTTPStatus.CONFLICT,
            )
            return
        self._send_json({"operation": operation})

    def _finished(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_id = payload.get(
            "operation_id"
        )

        chat_url = payload.get(
            "chat_url"
        )

        response_text = payload.get(
            "response_text"
        )

        response_text_available = payload.get(
            "response_text_available",
            False,
        )
        controller_id = payload.get("controller_id")
        ack_only = payload.get("ack_only", False)
        timing = payload.get("timing")

        if not isinstance(
            operation_id,
            str,
        ):
            self._send_json(
                {
                    "error":
                        "operation_id is required."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(controller_id, str) or not controller_id.strip() or len(controller_id) > 200:
            self._send_json({"error": "controller_id is required."}, HTTPStatus.BAD_REQUEST)
            return

        if chat_url is not None and not isinstance(
            chat_url,
            str,
        ):
            self._send_json(
                {
                    "error":
                        "chat_url must be a string."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if response_text is not None and not isinstance(
            response_text,
            str,
        ):
            self._send_json(
                {
                    "error":
                        "response_text must be a string."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(
            response_text_available,
            bool,
        ):
            self._send_json(
                {
                    "error":
                        "response_text_available must be a boolean."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(ack_only, bool):
            self._send_json(
                {
                    "error":
                        "ack_only must be a boolean."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        claim_next = payload.get("claim_next", True)
        if not isinstance(claim_next, bool):
            self._send_json(
                {
                    "error":
                        "claim_next must be a boolean."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if response_text is not None and len(response_text) > MAX_RESPONSE_TEXT_CHARS:
            self._send_json(
                {
                    "error":
                        f"response_text exceeds {MAX_RESPONSE_TEXT_CHARS} characters."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        # Treat nonblank response text as the evidence itself. A stale or
        # partially updated controller may omit the availability flag, but
        # must not be able to turn already-supplied response text into an
        # apparently missing response. Blank text remains fail-closed.
        if isinstance(response_text, str) and response_text.strip():
            response_text_available = True

        normalized_timing = None
        if timing is not None:
            normalized_timing = self.bridge_state.normalize_timing(timing)
            if normalized_timing is None:
                self._send_json(
                    {"error": "invalid timing payload."},
                    HTTPStatus.BAD_REQUEST,
                )
                return

        # The acknowledgement already carries response evidence when this is
        # a prompt completion, so the repair-from-observation path would only
        # add another filesystem read to the hot completion path.
        existing_operation = self.bridge_state.get_operation(operation_id, repair_response=False)
        if existing_operation is None:
            self._send_json(
                {"error": "Operation not found."},
                HTTPStatus.NOT_FOUND,
            )
            return
        if existing_operation.get("controller_id") != controller_id.strip():
            self._send_json(
                {"error": "Operation is owned by a different controller instance."},
                HTTPStatus.CONFLICT,
            )
            return

        if (
            existing_operation.get("network_response_authoritative") is True
            and existing_operation.get("response_text_available") is True
            and isinstance(existing_operation.get("response_text"), str)
            and existing_operation.get("response_text").strip()
        ):
            response_text = existing_operation.get("response_text")
            response_text_available = True

        incoming_response_verified = (
            response_text_available is True
            and isinstance(response_text, str)
            and bool(response_text.strip())
        )
        if existing_operation.get("status") == "completed":
            # A duplicate acknowledgement is idempotent. A later verified
            # response payload is still valid evidence when the original
            # terminal state was persisted without response text.
            if existing_operation.get("operation_type") == "prompt" and incoming_response_verified:
                existing_operation = self.bridge_state.persist_response_evidence(
                    operation_id,
                    chat_url,
                    response_text or "",
                ) or existing_operation
            if normalized_timing is not None:
                existing_operation = self.bridge_state.persist_timing(
                    operation_id,
                    normalized_timing,
                ) or existing_operation
            chained_operation = self.bridge_state.get_chained_operation(operation_id)
            if ack_only:
                payload = {
                    "ok": True,
                    "operation_id": existing_operation.get("operation_id"),
                    "status": existing_operation.get("status"),
                }
                if chained_operation is not None:
                    payload["next_operation"] = chained_operation
                self._send_json(payload)
            else:
                payload = {"operation": existing_operation}
                if chained_operation is not None:
                    payload["next_operation"] = chained_operation
                self._send_json(payload)
            return

        if existing_operation.get("operation_type") == "prompt":
            completion_markers = existing_operation.get("completion_markers")
            persisted_response_verified = (
                existing_operation.get("response_text_available") is True
                and isinstance(existing_operation.get("response_text"), str)
                and bool(str(existing_operation.get("response_text")).strip())
            )
            candidate_response = (
                response_text
                if incoming_response_verified
                else existing_operation.get("response_text")
            )
            if not incoming_response_verified and not persisted_response_verified:
                self._send_json(
                    {
                        "error": "Prompt completion requires verified nonblank response_text."
                    },
                    HTTPStatus.CONFLICT,
                )
                return
            if not completion_markers_satisfied(candidate_response, completion_markers):
                self._send_json(
                    {
                        "error": "Prompt completion response does not satisfy the operation completion markers."
                    },
                    HTTPStatus.CONFLICT,
                )
                return

        if (
            not (
                response_text_available is True
                and isinstance(response_text, str)
                and bool(response_text.strip())
            )
            and existing_operation.get("response_text_available") is True
        ):
            # Preserve verified response evidence already persisted by the
            # browser observation path when the acknowledgement is retried
            # without its original response payload.
            response_text = existing_operation.get("response_text")
            response_text_available = True

        try:
            if claim_next:
                operation, chained_operation = self.bridge_state.complete_operation_and_claim_next(
                    operation_id=operation_id,
                    chat_url=chat_url,
                    response_text=response_text,
                    response_text_available=response_text_available,
                    timing=normalized_timing,
                    controller_id=controller_id.strip(),
                )
            else:
                operation = self.bridge_state.complete_operation(
                    operation_id=operation_id,
                    chat_url=chat_url,
                    response_text=response_text,
                    response_text_available=response_text_available,
                    timing=normalized_timing,
                )
                chained_operation = None
        except InvalidOperationTransition:
            # Completion acknowledgements are retried by the browser controller.
            # Once an operation is durably completed, return its persisted state
            # instead of turning a duplicate acknowledgement into a recovery loop.
            operation = self.bridge_state.get_operation(operation_id)
            if operation is None or operation.get("status") != "completed":
                raise

        if operation is None:
            self._send_json(
                {
                    "error":
                        "Operation not found."
                },
                HTTPStatus.NOT_FOUND,
            )
            return

        if ack_only:
            payload = {
                "ok": True,
                "operation_id": operation.get("operation_id"),
                "status": operation.get("status"),
            }
            if chained_operation is not None:
                payload["next_operation"] = chained_operation
            self._send_json(payload)
        else:
            self._send_json(
                {
                    "operation": operation
                }
            )

    def _processed(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_id = payload.get("operation_id")
        controller_id = payload.get("controller_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            self._send_json({"error": "operation_id is required."}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(controller_id, str) or not controller_id.strip() or len(controller_id) > 200:
            self._send_json({"error": "controller_id is required."}, HTTPStatus.BAD_REQUEST)
            return

        operation = self.bridge_state.mark_response_processed(
            operation_id,
            controller_id.strip(),
        )
        if operation is None:
            self._send_json(
                {"error": "Response processing acknowledgement rejected."},
                HTTPStatus.CONFLICT,
            )
            return

        self._send_json({
            "ok": True,
            "operation_id": operation_id,
            "status": operation.get("status"),
            "response_processing_complete": operation.get("response_processing_complete") is True,
            "timing": operation.get("timing") or {},
        })

    def _failed(
        self,
        payload: dict[str, Any],
    ) -> None:
        operation_id = payload.get(
            "operation_id"
        )

        controller_id = payload.get("controller_id")
        failure_source = payload.get("failure_source", "dom_fallback")


        error = payload.get(
            "error"
        )

        if not isinstance(
            operation_id,
            str,
        ):
            self._send_json(
                {
                    "error":
                        "operation_id is required."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(controller_id, str) or not controller_id.strip() or len(controller_id) > 200:
            self._send_json({"error": "controller_id is required."}, HTTPStatus.BAD_REQUEST)
            return

        current = self.bridge_state.get_operation(operation_id, repair_response=False)
        if current is None:
            self._send_json({"error": "Operation not found."}, HTTPStatus.NOT_FOUND)
            return
        if current.get("controller_id") != controller_id.strip():
            self._send_json({"error": "Operation is owned by a different controller instance."}, HTTPStatus.CONFLICT)
            return

        current_network = self.bridge_state.get_operation(operation_id, repair_response=False)
        if (
            failure_source == "dom_fallback"
            and isinstance(current_network, dict)
            and current_network.get("network_terminal_event")
        ):
            self._send_json(
                {
                    "operation": current_network,
                    "suppressed": True,
                    "reason": "network_authority_already_recorded"
                }
            )
            return

        if not isinstance(
            error,
            str,
        ) or not error.strip():
            self._send_json(
                {
                    "error":
                        "error is required."
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        recovery_context = payload.get("recovery_context")
        if recovery_context is not None:
            normalized_recovery_context = self.bridge_state._normalize_recovery_context(
                recovery_context
            )
            if normalized_recovery_context is None:
                # Network lifecycle observations already persist the request,
                # classification, and reason as first-class CDP evidence.
                # Older extension builds may also send those network fields in
                # recovery_context; ignore that unsupported auxiliary payload
                # rather than orphaning a terminal network failure in generating.
                if failure_source == "network":
                    recovery_context = None
                else:
                    self._send_json(
                        {
                            "error":
                                "recovery_context is invalid."
                        },
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
            else:
                recovery_context = normalized_recovery_context

        operation = (
            self.bridge_state.fail_operation(
                operation_id=operation_id,
                error=error,
                recovery_context=recovery_context,
            )
        )

        if operation is None:
            self._send_json(
                {
                    "error":
                        "Operation not found."
                },
                HTTPStatus.NOT_FOUND,
            )
            return

        self._send_json(
            {
                "operation": operation
            }
        )

    def log_message(
        self,
        format_string: str,
        *args: Any,
    ) -> None:
        """Keep successful request traffic out of the long-lived bridge log."""
        message = format_string % args
        if not _bridge_access_log_should_emit(message):
            return
        print("[Bridge] " + message, flush=True)


class ChatGPTBridge:
    def __init__(
        self,
        host: str = HOST,
        port: int = PORT,
    ):
        self.host = host
        self.port = port

        state_manager = StateManager(
            CONFIG.ai_dir
        )

        self.bridge_state = BridgeState(
            state_manager
        )

        self.server = BridgeHTTPServer(
            (self.host, self.port),
            BridgeRequestHandler,
        )

        self.server.bridge_state = self.bridge_state

    def run(self) -> None:
        print()
        print(
            "=== CHATGPT LOCAL BRIDGE ==="
        )
        print()
        print(
            f"Listening on http://{self.host}:{self.port}"
        )
        print()
        print(
            "Endpoints:"
        )
        print(
            "  GET  /health"
        )
        print(
            "  GET  /status"
        )
        print(
            "  GET  /next-operation"
        )
        print(
            "  GET  /operation?operation_id=<id>"
        )
        print(
            "  POST /queue"
        )
        print(
            "  POST /chat/heartbeat"
        )
        print(
            "  POST /chat/finished"
        )
        print(
            "  POST /chat/failed"
        )
        print()
        print(
            "Press Ctrl+C to stop."
        )
        print()

        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            print()
            print(
                "Stopping bridge..."
            )
        finally:
            self.server.server_close()


def main() -> None:
    bridge = ChatGPTBridge()
    bridge.run()


if __name__ == "__main__":
    main()
