from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pasi.core.github_issue_tasks import load_task_catalog  # noqa: E402
from pasi.core.task_progression import TaskPromptProgression, TaskProgressionError  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP = REPO_ROOT / "roadmap" / "p0-p4.json"
PROGRESSION = REPO_ROOT / ".runtime" / "acceptance" / "task-progression.json"
OUTPUT = REPO_ROOT / ".runtime" / "acceptance" / "m0-authenticated-response.json"
ACCEPTANCE = REPO_ROOT / "scripts" / "run_live_task_acceptance.py"
EXPECTED_CHAT_PREFIX = "https://chatgpt.com/c/"
ACCEPTANCE_TIMEOUT = 900


def load_progression() -> TaskPromptProgression:
    catalog = load_task_catalog(ROADMAP, allow_fallback=False)
    if PROGRESSION.exists():
        return TaskPromptProgression.load(catalog=catalog, path=PROGRESSION)
    progression = TaskPromptProgression.start(catalog=catalog, task_id="P0.1")
    progression.save(PROGRESSION)
    return progression


def current_prompt_payload() -> dict[str, Any]:
    progression = load_progression()
    return {
        "task_id": progression.state.current_task_id,
        "prompt": progression.current_prompt(),
        "prompt_generation": progression.state.prompt_generation,
        "advance_count": progression.state.advance_count,
        "status": progression.state.status,
    }


class CaptureState:
    def __init__(self, expected_task_id: str = "P0.1") -> None:
        self.lock = threading.Lock()
        self.expected_task_id = expected_task_id
        self.chat_url = ""
        self.authenticated = False
        self.thinking_enabled = False
        self.fresh_chat_created_after_usage = False
        self.fresh_chat_creation_reason = ""
        self.operation_id = ""
        self.resume_phase = ""
        self.connection_loss_detected = False
        self.response_stopped_on_loss = False
        self.checkpoint_preserved = False
        self.resumed_after_reconnect = False
        self.same_operation_resumed = False
        self.response_text = ""
        self.response_complete = False
        self.acceptance_in_progress = False

    def apply(self, event: dict[str, Any]) -> None:
        with self.lock:
            event_type = event.get("type")

            if event_type in {"page_ready", "operation_started", "thinking_state"}:
                self.chat_url = str(event.get("chat_url") or self.chat_url)
                self.authenticated = (
                    self.authenticated
                    or event.get("authenticated_page") is True
                )
                self.thinking_enabled = (
                    self.thinking_enabled
                    or event.get("thinking_enabled") is True
                )

            if event_type == "fresh_chat":
                self.fresh_chat_created_after_usage = (
                    event.get("fresh_chat_created_after_usage") is True
                )
                self.fresh_chat_creation_reason = str(
                    event.get("fresh_chat_creation_reason") or ""
                ).strip()
                self.chat_url = str(event.get("fresh_chat_url") or self.chat_url)

            if event_type == "operation_started":
                self.operation_id = str(event.get("operation_id") or "")
                self.thinking_enabled = (
                    self.thinking_enabled
                    or event.get("thinking_enabled") is True
                )

            if event_type == "checkpoint":
                self.operation_id = str(
                    event.get("operation_id") or self.operation_id
                )
                self.resume_phase = str(
                    event.get("resume_phase") or self.resume_phase
                )

            if event_type == "connection_lost":
                self.connection_loss_detected = True
                self.response_stopped_on_loss = (
                    event.get("response_stopped_on_loss") is True
                )
                self.checkpoint_preserved = (
                    event.get("checkpoint_preserved") is True
                )
                self.operation_id = str(
                    event.get("operation_id") or self.operation_id
                )
                self.resume_phase = str(
                    event.get("resume_phase") or self.resume_phase
                )

            if event_type == "connection_restored":
                self.resumed_after_reconnect = (
                    event.get("resumed_after_reconnect") is True
                )
                self.same_operation_resumed = (
                    event.get("same_operation_resumed") is True
                )
                self.operation_id = str(
                    event.get("operation_id") or self.operation_id
                )
                self.resume_phase = str(
                    event.get("resume_phase") or self.resume_phase
                )

            if event_type == "response_progress":
                self.response_text = str(
                    event.get("response_text") or self.response_text
                )

            if event_type == "response_complete":
                self.response_complete = True
                self.response_text = str(
                    event.get("response_text") or self.response_text
                )
                self.chat_url = str(event.get("chat_url") or self.chat_url)
                self.thinking_enabled = (
                    event.get("thinking_enabled") is True
                    or self.thinking_enabled
                )
                self.fresh_chat_created_after_usage = (
                    event.get("fresh_chat_created_after_usage") is True
                    or self.fresh_chat_created_after_usage
                )

    def ready(self) -> bool:
        with self.lock:
            text = self.response_text
            task_id = extract_marker(text, "PASI_TASK_ID")
            status = extract_marker(text, "PASI_RESULT_STATUS")
            required = (
                task_id,
                status,
                extract_marker(text, "PASI_SUMMARY"),
                extract_marker(text, "PASI_EVIDENCE"),
                extract_patch(text),
            )
            if not (
                self.response_complete
                and self.authenticated
                and self.chat_url.startswith(EXPECTED_CHAT_PREFIX)
                and self.thinking_enabled
                and all(required)
                and status.strip().lower() == "complete"
                and bool(self.operation_id)
                and not self.acceptance_in_progress
            ):
                return False

            if task_id != self.expected_task_id:
                return False

            if task_id == "P0.1":
                m0_markers = (
                    extract_marker(text, "PASI_M0_NEXT_TASK_ID"),
                    extract_block(
                        text,
                        "PASI_M0_NEXT_PROMPT_START",
                        "PASI_M0_NEXT_PROMPT_END",
                    ),
                )
                valid_chat_creation_policy = (
                    not self.fresh_chat_created_after_usage
                    or self.fresh_chat_creation_reason == "usage_limit"
                )
                return (
                    valid_chat_creation_policy
                    and self.connection_loss_detected
                    and self.response_stopped_on_loss
                    and self.checkpoint_preserved
                    and self.resumed_after_reconnect
                    and self.same_operation_resumed
                    and bool(self.resume_phase)
                )

            return True

    def materialize(self) -> dict[str, Any]:
        with self.lock:
            text = self.response_text
            return {
                "provider": "chatgpt_browser",
                "authenticated": self.authenticated,
                "chat_url": self.chat_url,
                "task_id": extract_marker(text, "PASI_TASK_ID"),
                "status": extract_marker(text, "PASI_RESULT_STATUS"),
                "summary": extract_marker(text, "PASI_SUMMARY"),
                "evidence": extract_marker(text, "PASI_EVIDENCE"),
                "patch": extract_patch(text),
                "runtime_evidence": {
                    "fresh_chat_created_after_usage": self.fresh_chat_created_after_usage,
                    "fresh_chat_creation_reason": self.fresh_chat_creation_reason,
                    "thinking_enabled": self.thinking_enabled,
                    "connection_recovery": {
                        "connection_loss_detected": self.connection_loss_detected,
                        "response_stopped_on_loss": self.response_stopped_on_loss,
                        "checkpoint_preserved": self.checkpoint_preserved,
                        "resumed_after_reconnect": self.resumed_after_reconnect,
                        "same_operation_resumed": self.same_operation_resumed,
                        "operation_id": self.operation_id,
                        "resume_phase": self.resume_phase,
                    },
                },
                "next_task_id": extract_marker(text, "PASI_M0_NEXT_TASK_ID"),
                "next_prompt": extract_block(
                    text,
                    "PASI_M0_NEXT_PROMPT_START",
                    "PASI_M0_NEXT_PROMPT_END",
                ),
            }

    def reset_after_success(self, next_task_id: str | None = None) -> None:
        with self.lock:
            if next_task_id:
                self.expected_task_id = next_task_id
            self.chat_url = ""
            self.authenticated = False
            self.thinking_enabled = False
            self.fresh_chat_created_after_usage = False
            self.fresh_chat_creation_reason = ""
            self.operation_id = ""
            self.resume_phase = ""
            self.connection_loss_detected = False
            self.response_stopped_on_loss = False
            self.checkpoint_preserved = False
            self.resumed_after_reconnect = False
            self.same_operation_resumed = False
            self.response_text = ""
            self.response_complete = False
            self.acceptance_in_progress = False


def extract_marker(text: str, marker: str) -> str:
    prefix = marker + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def extract_block(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        return ""
    return text[start + len(start_marker):end].strip("\n ")


def extract_patch(text: str) -> str:
    return extract_block(text, "PASI_PATCH_START", "PASI_PATCH_END")


def run_acceptance() -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(ACCEPTANCE),
            "--response",
            str(OUTPUT),
            "--roadmap",
            str(ROADMAP),
            "--progression-state",
            str(PROGRESSION),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=ACCEPTANCE_TIMEOUT,
        check=False,
    )
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


STATE = CaptureState()
ACCEPTANCE_LOCK = threading.Lock()


def json_response(handler: BaseHTTPRequestHandler, status: int, value: dict[str, Any]) -> None:
    payload = json.dumps(value).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/prompt":
            try:
                json_response(self, 200, {"ok": True, **current_prompt_payload()})
            except TaskProgressionError as exc:
                json_response(self, 409, {"ok": False, "error": str(exc)})
            return
        if self.path == "/health":
            json_response(
                self,
                200,
                {
                    "ok": True,
                    "bridge": "pasi-m0-live-capture",
                    "ready": STATE.ready(),
                    "task": current_prompt_payload(),
                },
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/event":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            event = json.loads(raw.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("event must be an object")

            event_type = event.get("type")
            STATE.apply(event)

            if event_type == "response_complete" and STATE.ready():
                if not ACCEPTANCE_LOCK.acquire(blocking=False):
                    json_response(
                        self,
                        409,
                        {
                            "ok": False,
                            "error": "acceptance already in progress",
                        },
                    )
                    return

                try:
                    with STATE.lock:
                        STATE.acceptance_in_progress = True
                        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
                        OUTPUT.write_text(
                            json.dumps(STATE.materialize(), indent=2) + "\n",
                            encoding="utf-8",
                        )

                    acceptance = run_acceptance()
                    if acceptance["status"] == "passed":
                        task = current_prompt_payload()
                        STATE.reset_after_success(task["task_id"])
                        response = {
                            "ok": True,
                            "ready": True,
                            "acceptance": acceptance,
                            "next_task_id": task["task_id"],
                            "next_prompt": task["prompt"],
                            "prompt_generation": task["prompt_generation"],
                        }
                        json_response(self, 200, response)
                        return

                    json_response(
                        self,
                        422,
                        {
                            "ok": False,
                            "ready": False,
                            "acceptance": acceptance,
                        },
                    )
                    return
                finally:
                    ACCEPTANCE_LOCK.release()

            json_response(self, 200, {"ok": True, "ready": STATE.ready()})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            json_response(self, 400, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    STATE.expected_task_id = current_prompt_payload()["task_id"]
    OUTPUT.unlink(missing_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("PASI M0 live capture bridge: http://127.0.0.1:8765")
    print(f"Waiting for authenticated ChatGPT evidence; output={OUTPUT}")
    print(f"Current task: {current_prompt_payload()['task_id']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
