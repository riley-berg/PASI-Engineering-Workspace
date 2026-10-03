"""Bridge-side lifecycle policy for the live ChatGPT handoff boundary.

The canonical persisted operation-state shape lives in pasi.core.operation_state. This bridge policy intentionally remains separate because browser handoff recovery allows transport-specific requeue transitions that the durable core lifecycle does not need to permit globally.
"""

from __future__ import annotations

from typing import Final

from pasi.core.operation_state import OPERATION_ALLOWED_TRANSITIONS, OPERATION_STATUSES


TERMINAL_OPERATION_STATUSES: Final[frozenset[str]] = frozenset({"completed", "cancelled"})

BRIDGE_ONLY_TRANSITIONS: Final[frozenset[tuple[str, str]]] = frozenset({
    ("claimed", "queued"),
    ("generating", "queued"),
    ("generating", "generating"),
})

_BRIDGE_TRANSITION_TARGETS: dict[str, frozenset[str]] = {
    status: frozenset(
        target for current, target in BRIDGE_ONLY_TRANSITIONS if current == status
    )
    for status in OPERATION_STATUSES
}

_ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    status: frozenset(OPERATION_ALLOWED_TRANSITIONS[status] | _BRIDGE_TRANSITION_TARGETS[status])
    for status in OPERATION_STATUSES
}


class InvalidOperationTransition(ValueError):
    """Raised when an operation attempts an illegal status transition."""


def validate_transition(current: str, target: str) -> None:
    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is None:
        raise InvalidOperationTransition(f"Unknown operation status: {current!r}")
    if target not in _ALLOWED_TRANSITIONS:
        raise InvalidOperationTransition(f"Unknown operation status: {target!r}")
    if target not in allowed:
        raise InvalidOperationTransition(
            f"Unsupported operation transition: {current!r} -> {target!r}"
        )


def validate_status(status: str) -> None:
    if status not in _ALLOWED_TRANSITIONS:
        raise InvalidOperationTransition(f"Unknown operation status: {status!r}")
