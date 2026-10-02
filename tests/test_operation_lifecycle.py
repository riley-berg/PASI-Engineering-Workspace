import pytest
from itertools import product

from automation.orchestrator.operation_lifecycle import (
    BRIDGE_ONLY_TRANSITIONS, TERMINAL_OPERATION_STATUSES,
    _ALLOWED_TRANSITIONS as BRIDGE_ALLOWED_TRANSITIONS,
    validate_status, validate_transition,
)
from pasi.core.operation_state import (
    InvalidOperationTransition as CanonicalInvalidOperationTransition,
    OPERATION_ALLOWED_TRANSITIONS, OPERATION_STATUSES, OperationState,
)

EXPECTED_CANONICAL_TRANSITIONS = {
    "queued": frozenset({"claimed", "failed", "cancelled"}),
    "claimed": frozenset({"generating", "completed", "failed", "cancelled"}),
    "generating": frozenset({"completed", "failed", "cancelled"}),
    "failed": frozenset({"claimed"}), "completed": frozenset(), "cancelled": frozenset(),
}
EXPECTED_BRIDGE_ONLY_TRANSITIONS = frozenset({
    ("claimed", "queued"), ("generating", "queued"), ("generating", "generating"),
})
EXPECTED_BRIDGE_TRANSITIONS = {
    status: frozenset(set(EXPECTED_CANONICAL_TRANSITIONS[status]) | {
        target for current, target in EXPECTED_BRIDGE_ONLY_TRANSITIONS if current == status
    }) for status in OPERATION_STATUSES
}

@pytest.mark.parametrize("current,target", product(sorted(OPERATION_STATUSES), repeat=2))
def test_canonical_transition_matrix_exhaustively_accepts_and_rejects(current, target):
    expected = target in EXPECTED_CANONICAL_TRANSITIONS[current]
    state = OperationState(operation_id=f"op-{current}-{target}", operation_type="prompt", status=current)
    assert OPERATION_ALLOWED_TRANSITIONS[current] == EXPECTED_CANONICAL_TRANSITIONS[current]
    if expected:
        assert state.can_transition_to(target) is True
        assert state.transition(target).status == target
    else:
        assert state.can_transition_to(target) is False
        with pytest.raises(CanonicalInvalidOperationTransition): state.transition(target)

@pytest.mark.parametrize("current,target", product(sorted(OPERATION_STATUSES), repeat=2))
def test_bridge_transition_matrix_exhaustively_accepts_and_rejects(current, target):
    expected = target in EXPECTED_BRIDGE_TRANSITIONS[current]
    assert BRIDGE_ALLOWED_TRANSITIONS[current] == EXPECTED_BRIDGE_TRANSITIONS[current]
    if expected: validate_transition(current, target)
    else:
        with pytest.raises(ValueError): validate_transition(current, target)

def test_bridge_only_transitions_are_exactly_the_transport_specific_exceptions():
    assert BRIDGE_ONLY_TRANSITIONS == EXPECTED_BRIDGE_ONLY_TRANSITIONS
    assert all(target not in EXPECTED_CANONICAL_TRANSITIONS[current] for current, target in BRIDGE_ONLY_TRANSITIONS)

@pytest.mark.parametrize("status", sorted(OPERATION_STATUSES))
def test_bridge_accepts_every_known_status(status): validate_status(status)

def test_bridge_rejects_unknown_current_and_target_statuses():
    with pytest.raises(ValueError, match="Unknown operation status"): validate_status("unknown")
    with pytest.raises(ValueError, match="Unknown operation status"): validate_transition("unknown", "queued")
    with pytest.raises(ValueError, match="Unknown operation status"): validate_transition("queued", "unknown")

def test_terminal_bridge_statuses_match_canonical_terminal_statuses():
    terminals = frozenset(status for status, targets in EXPECTED_CANONICAL_TRANSITIONS.items() if not targets)
    assert TERMINAL_OPERATION_STATUSES == terminals