#!/usr/bin/env python3
"""Run one real PASI M0 operation and record the live browser runtime result."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = "http://127.0.0.1:8765"
TOKEN_PATH = ROOT / ".runtime" / "bridge-token"
ROADMAP = ROOT / "roadmap" / "p0-p4.json"
PROGRESSION = ROOT / ".runtime" / "acceptance" / "task-progression.json"
RESPONSE = ROOT / ".runtime" / "acceptance" / "m0-authenticated-response.json"
REPORT = ROOT / ".runtime" / "acceptance" / "m0-live-runtime.json"
ACCEPTANCE = ROOT / "scripts" / "run_live_task_acceptance.py"
TIMEOUT_SECONDS = 60 * 60
POLL_SECONDS = 1.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_token() -> str:
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"empty bridge token: {TOKEN_PATH}")
    return token


def request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    token = read_token()
    payload = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(BRIDGE + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"bridge request {method} {path} failed: {exc}") from exc


def capture_time(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def git_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def queue_operation(operation_type: str, prompt: str, key: str, markers: list[str] | None = None) -> dict:
    body = {
        "operation_type": operation_type,
        "prompt": prompt,
        "idempotency_key": key,
    }
    if markers:
        body["completion_markers"] = markers
    return request("/queue", "POST", body)


def current_prompt() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from pasi.core.github_issue_tasks import load_task_catalog
    from pasi.core.task_progression import TaskPromptProgression

    catalog = load_task_catalog(ROADMAP, allow_fallback=False)
    if PROGRESSION.exists():
        progression = TaskPromptProgression.load(catalog=catalog, path=PROGRESSION)
    else:
        progression = TaskPromptProgression.start(catalog=catalog, task_id="P0.1")
        progression.save(PROGRESSION)
    if progression.state.current_task_id != "P0.1":
        raise RuntimeError(
            f"M0 runner requires current task P0.1; current task is {progression.state.current_task_id}"
        )
    return progression.current_prompt()


def extract_marker(text: str, marker: str) -> str:
    prefix = marker + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def extract_block(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    finish = text.find(end)
    if begin < 0 or finish < 0 or finish <= begin:
        return ""
    return text[begin + len(start):finish].strip("\n ")


def wait_for_real_operation(started_at: float) -> tuple[dict, dict | None]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    recovery_defaults_error: dict | None = None
    while time.monotonic() < deadline:
        observation = request("/browser/observation").get("observation") or {}
        data = observation.get("data") if isinstance(observation, dict) else {}
        if isinstance(data, dict):
            captured = capture_time(observation.get("captured_at"))
            if captured >= started_at and data.get("kind") == "chatgpt_runtime_error":
                if data.get("recovery_defaults_match") is True:
                    recovery_defaults_error = dict(observation)
                    print("FAIL: RECOVERY_DEFAULTS runtime error observed during the live operation.", flush=True)

        response = request("/browser/response").get("observation") or {}
        response_data = response.get("data") if isinstance(response, dict) else {}
        if (
            isinstance(response_data, dict)
            and response_data.get("kind") == "chatgpt_response"
            and capture_time(response.get("captured_at")) >= started_at
            and isinstance(response_data.get("response_text"), str)
            and response_data.get("response_text", "").strip()
        ):
            return response, recovery_defaults_error

        time.sleep(POLL_SECONDS)

    raise TimeoutError("timed out waiting for the real PASI ChatGPT response")


def main() -> int:
    started_wall = now_iso()
    started_at = time.time()
    result: dict = {
        "gate": "m0-live-runtime",
        "started_at": started_wall,
        "recovery_defaults_error": False,
        "recovery_defaults_observation": None,
        "acceptance": None,
        "status": "FAIL",
    }

    try:
        if not git_clean():
            raise RuntimeError("authoritative repository must be clean before starting M0")
        health = request("/health")
        if health.get("status") != "ok":
            raise RuntimeError(f"bridge health check failed: {health!r}")

        status = request("/status")
        if int(status.get("queue_size", 0)) != 0:
            raise RuntimeError(
                f"refusing to add M0 operations while bridge queue is not empty: {status!r}"
            )
        if int(status.get("runtime_error_observation_priority", 0)) < 200:
            raise RuntimeError(
                "bridge is stale: restart the Engineering Workspace bridge so runtime errors cannot be overwritten"
            )

        health_deadline = time.monotonic() + 30
        while time.monotonic() < health_deadline:
            health_observation = request("/browser/health").get("observation") or {}
            health_data = health_observation.get("data") if isinstance(health_observation, dict) else {}
            if isinstance(health_data, dict) and health_data.get("runtime_error_telemetry") is True:
                break
            time.sleep(1)
        else:
            raise RuntimeError(
                "updated PASI ChatGPT extension runtime error telemetry was not observed; redeploy/reload the extension first"
            )

        prompt = current_prompt()
        run_id = uuid.uuid4().hex
        print("PASI M0 LIVE ACCEPTANCE")
        print("Queueing a fresh chat, then exactly one P0.1 prompt operation.")
        print("Waiting for the real authenticated ChatGPT operation to finish...")
        queue_operation("new_chat", "", f"m0-live:{run_id}:new-chat")
        queue_operation(
            "prompt",
            prompt,
            f"m0-live:{run_id}:prompt",
            ["PASI_RESULT_STATUS: complete"],
        )

        response, runtime_error = wait_for_real_operation(started_at)
        result["response_captured_at"] = response.get("captured_at")
        result["operation_id"] = (response.get("data") or {}).get("active_operation_id")
        if runtime_error is not None:
            result["recovery_defaults_error"] = True
            result["recovery_defaults_observation"] = runtime_error

        data = response["data"]
        response_text = str(data.get("response_text") or "")
        task_id = extract_marker(response_text, "PASI_TASK_ID")
        status = extract_marker(response_text, "PASI_RESULT_STATUS")
        summary = extract_marker(response_text, "PASI_SUMMARY")
        evidence = extract_marker(response_text, "PASI_EVIDENCE")
        patch = extract_block(response_text, "PASI_PATCH_START", "PASI_PATCH_END")
        RESPONSE.parent.mkdir(parents=True, exist_ok=True)
        RESPONSE.write_text(
            json.dumps(
                {
                    "provider": "chatgpt_browser",
                    "authenticated": True,
                    "chat_url": data.get("chat_url"),
                    "task_id": task_id,
                    "status": status,
                    "summary": summary,
                    "evidence": evidence,
                    "patch": patch,
                    "runtime_evidence": data.get("runtime_evidence") or {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        if result["recovery_defaults_error"]:
            raise RuntimeError("RECOVERY_DEFAULTS reappeared during the real PASI operation")

        acceptance = subprocess.run(
            [
                sys.executable,
                str(ACCEPTANCE),
                "--response",
                str(RESPONSE),
                "--roadmap",
                str(ROADMAP),
                "--progression-state",
                str(PROGRESSION),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        result["acceptance"] = {
            "returncode": acceptance.returncode,
            "stdout": acceptance.stdout[-8000:],
            "stderr": acceptance.stderr[-8000:],
        }
        if acceptance.returncode != 0:
            raise RuntimeError("live M0 acceptance processor failed")

        result["status"] = "PASS"
        print("PASS: real PASI operation completed and M0 acceptance passed.")
        print("PASS: RECOVERY_DEFAULTS did not reappear during the operation.")
        return 0
    except Exception as exc:
        result["error"] = str(exc)
        print(f"M0 LIVE ACCEPTANCE FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        result["finished_at"] = now_iso()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"Evidence: {REPORT}")


if __name__ == "__main__":
    raise SystemExit(main())
