"""Pure-function tests for the recovery case state machine.

These exercise the transition rules directly, with no database, so a broken
transition map fails here immediately.
"""

import pytest

from app.core.errors import IllegalStateTransitionError
from app.models.recovery import RecoveryCaseState
from app.recovery.state_machine import (
    INITIAL_STATE,
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    assert_transition_allowed,
    can_transition,
    is_terminal,
)

_S = RecoveryCaseState

# The single linear "happy path" a case walks from open to recovered.
_HAPPY_PATH = [
    _S.DETECTED,
    _S.DIAGNOSING,
    _S.DIAGNOSED,
    _S.DECISION_PENDING,
    _S.ACTION_SCHEDULED,
    _S.ACTION_EXECUTED,
    _S.OBSERVING,
    _S.RECOVERED,
]


def test_initial_state_is_detected() -> None:
    assert INITIAL_STATE is _S.DETECTED


def test_every_state_has_a_transition_entry() -> None:
    assert set(LEGAL_TRANSITIONS) == set(_S)


def test_all_transition_targets_are_real_states() -> None:
    for targets in LEGAL_TRANSITIONS.values():
        for target in targets:
            assert isinstance(target, RecoveryCaseState)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for state in TERMINAL_STATES:
        assert LEGAL_TRANSITIONS[state] == frozenset()


def test_states_with_no_outgoing_transitions_are_exactly_the_terminal_ones() -> None:
    no_exit = {state for state, targets in LEGAL_TRANSITIONS.items() if not targets}
    assert no_exit == set(TERMINAL_STATES)


def test_no_state_transitions_to_itself() -> None:
    for state, targets in LEGAL_TRANSITIONS.items():
        assert state not in targets


def test_happy_path_is_entirely_legal() -> None:
    for frm, to in zip(_HAPPY_PATH, _HAPPY_PATH[1:], strict=False):
        assert can_transition(frm, to), f"{frm} -> {to} should be legal"
        assert_transition_allowed(frm, to)  # must not raise


def test_abandoned_reachable_from_every_non_terminal_state_before_action_executed() -> None:
    early_states = (
        _S.DETECTED,
        _S.DIAGNOSING,
        _S.DIAGNOSED,
        _S.DECISION_PENDING,
        _S.ACTION_SCHEDULED,
    )
    for state in early_states:
        assert can_transition(state, _S.ABANDONED)


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (_S.DETECTED, _S.RECOVERED),  # cannot skip the whole lifecycle
        (_S.DETECTED, _S.DIAGNOSED),  # cannot skip DIAGNOSING
        (_S.DIAGNOSED, _S.DETECTED),  # cannot go backwards
        (_S.OBSERVING, _S.DETECTED),  # cannot restart
        (_S.RECOVERED, _S.DIAGNOSING),  # nothing leaves a terminal state
        (_S.ABANDONED, _S.DETECTED),
        (_S.FAILED, _S.OBSERVING),
        (_S.ACTION_EXECUTED, _S.ABANDONED),  # abandonment not allowed once an action ran
    ],
)
def test_illegal_transitions_raise(frm: RecoveryCaseState, to: RecoveryCaseState) -> None:
    assert not can_transition(frm, to)
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(frm, to)


def test_is_terminal() -> None:
    assert is_terminal(_S.RECOVERED)
    assert is_terminal(_S.ABANDONED)
    assert is_terminal(_S.FAILED)
    assert not is_terminal(_S.DETECTED)
    assert not is_terminal(_S.OBSERVING)
