from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from pasi.core.operation_state import OperationState


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChatOperation:
    operation_id: str
    operation_type: str
    prompt: str
    idempotency_key: str | None = None
    completion_markers: list[str] | None = None

    status: str = "queued"

    chat_url: str | None = None
    error: str | None = None
    response_text: str = ""
    response_text_available: bool = False
    recovery_context: dict[str, Any] | None = None

    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operation_state"] = OperationState.from_chat_operation(payload).to_dict()
        return payload
