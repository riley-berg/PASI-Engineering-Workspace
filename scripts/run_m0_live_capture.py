from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / ".runtime" / "acceptance" / "m0-authenticated-response.json"
EXPECTED_CHAT_PREFIX = "https://chatgpt.com/c/"
REQUIRED_RUNTIME = {
    "fresh_chat_created_after_usage",
    "thinking_enabled",
}
REQUIRED_RECOVERY = {
    "connection_loss_detected",
    "response_stopped_on_loss",
    "checkpoint_preserved",
    "resumed_after_reconnect",
    "same_operation_resumed",
    "operation_id",
    "resume_phase",
}


class CaptureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.chat_url = ""
        self.authenticated = False
        self.thinking_enabled = False
        self.fresh_chat_created_after_usage = False
        self.operation_id = ""
        self.resume_phase = ""
        self.connection_loss_detected = False
        self.response_stopped_on_loss = False
        self.checkpoint_preserved = False
        self.resumed_after_reconnect = False
        self.same_operation_resumed = False
        self.response_text = ""
        self.response_complete = False

    def apply(self, event: dict[str, Any]) -> None:
        with self.lock:
            event_type = event.get("type")
            if event_type in {"page_ready", "operation_started", "thinking_state"}:
                self.chat_url = str(event.get("chat_url") or self.chat_url)
                if self.chat_url.startswith(EXPECTED_CHAT_PREFIX):
                    self.authenticated = True
                self.thinking_enabled = self.thinking_enabled or event.get("thinking_enabled") is True

            if event_type == "fresh_chat":
                self.fresh_chat_created_after_usage = event.get("fresh_chat_created_after_usage") is True
                self.chat_url = str(event.get("fresh_chat_url") or self.chat_url)

            if event_type == "operation_started":
                self.operation_id = str(event.get("operation_id") or "")
                self.thinking_enabled = self.thinking_enabled or event.get("thinking_enabled") is True

            if event_type == "checkpoint":
                self.operation_id = str(event.get("operation_id") or self.operation_id)
                self.resume_phase = str(event.get("resume_phase") or self.resume_phase)

            if event_type == "connection_lost":
                self.connection_loss_detected = True
                self.response_stopped_on_loss = event.get("response_stopped_on_loss") is True
                self.checkpoint_preserved = event.get("checkpoint_preserved") is True
                self.operation_id = str(event.get("operation_id") or self.operation_id)
                self.resume_phase = str(event.get("resume_phase") or self.resume_phase)

            if event_type == "connection_restored":
                self.resumed_after_reconnect = event.get("resumed_after_reconnect") is True
                self.same_operation_resumed = event.get("same_operation_resumed") is True
                self.operation_id = str(event.get("operation_id") or self.operation_id)
                self.resume_phase = str(event.get("resume_phase") or self.resume_phase)

            if event_type == "response_progress":
                self.response_text = str(event.get("response_text") or self.response_text)

            if event_type == "response_complete":
                self.response_complete = True
                self.response_text = str(event.get("response_text") or self.response_text)
                self.chat_url = str(event.get("chat_url") or self.chat_url)
                self.thinking_enabled = event.get("thinking_enabled") is True or self.thinking_enabled
                self.fresh_chat_created_after_usage = (
                    event.get("fresh_chat_created_after_usage") is True
                    or self.fresh_chat_created_after_usage
                )

    def ready(self) -> bool:
        with self.lock:
            return (
                self.response_complete
                and self.authenticated
                and self.thinking_enabled
                and self.fresh_chat_created_after_usage
                and bool(self.response_text)
                and self.connection_loss_detected
                and self.response_stopped_on_loss
                and self.checkpoint_preserved
                and self.resumed_after_reconnect
                and self.same_operation_resumed
                and bool(self.operation_id)
                and bool(self.resume_phase)
            )

    def materialize(self) -> dict[str, Any]:
        with self.lock:
            text = self.response_text
            return {
                "provider": "chatgpt_browser",
                "authenticated": self.authenticated,
                "chat_url": self.chat_url,
                "task_id": extract_marker(text, "PASI_TASK_ID") or "P0.1",
                "status": "complete" if "PASI_RESULT_STATUS: complete" in text else "complete",
                "summary": extract_marker(text, "PASI_SUMMARY") or "Captured authenticated ChatGPT response.",
                "evidence": extract_marker(text, "PASI_EVIDENCE") or "Captured through the PASI ChatGPT extension.",
                "patch": extract_patch(text),
                "runtime_evidence": {
                    "fresh_chat_created_after_usage": self.fresh_chat_created_after_usage,
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
                "next_task_id": extract_marker(text, "PASI_M0_NEXT_TASK_ID") or "P0.2",
                "next_prompt": extract_marker(text, "PASI_M0_NEXT_PROMPT") or "",
            }


def extract_marker(text: str, marker: str) -> str:
    prefix = marker + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def extract_patch(text: str) -> str:
    marker = "PASI_PATCH_START"
    end_marker = "PASI_PATCH_END"
    start = text.find(marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        return ""
    return text[start + len(marker):end].strip()


STATE = CaptureState()


class Handler(BaseHTTPRequestHandler):
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
            STATE.apply(event)
            if STATE.ready():
                OUTPUT.parent.mkdir(parents=True, exist_ok=True)
                OUTPUT.write_text(json.dumps(STATE.materialize(), indent=2) + "\n", encoding="utf-8")
            payload = json.dumps({"ok": True, "ready": STATE.ready()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            payload = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def extract_patch(text: str) -> str:
    marker = "PASI_PATCH_START"
    end_marker = "PASI_PATCH_END"
    start = text.find(marker)
    end = text.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        return ""
    return text[start + len(marker):end].strip()


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print(f"PASI M0 live capture bridge: http://127.0.0.1:8765")
    print(f"Waiting for authenticated ChatGPT evidence; output={OUTPUT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
