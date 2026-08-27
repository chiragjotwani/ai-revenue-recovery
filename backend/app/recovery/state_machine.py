"""The recovery case state machine (Section 16).

All legal transitions between :class:`RecoveryCaseState` values are declared
here in one place. Application code never assigns ``RecoveryCase.state``
directly -- it calls the transition service, which consults this module and
raises :class:`IllegalStateTransitionError` for anything not listed.

Phase 3 only *drives* the earliest transitions (opening a case into
``DETECTED``, abandoning it, and the manual API path). The rest of the
lifecycle is defined now so later phases extend behaviour, not the schema:

    DETECTED -> DIAGNOSING -> DIAGNOSED -> DECISION_PENDING
             -> ACTION_SCHEDULED -> ACTION_EXECUTED -> OBSERVING -> RECOVERED

with ABANDONED and FAILED reachable as terminal outcomes from the
non-terminal states where they make sense.
"""

from app.core.errors import IllegalStateTransitionError
from app.models.recovery import RecoveryCaseState

_S = RecoveryCaseState

#: The state every new recovery case starts in.
INITIAL_STATE: RecoveryCaseState = _S.DETECTED

#: States with no outgoing transitions. A case in one of these is closed and
#: immutable.
TERMINAL_STATES: frozenset[RecoveryCaseState] = frozenset({_S.RECOVERED, _S.ABANDONED, _S.FAILED})

#: Legal transitions: mapping of a state to the set of states it may move to.
#: Kept linear (no retry back-edges) for Phase 3; a retry loop from
#: OBSERVING is expected to be added when Phase 6/7 needs it.
LEGAL_TRANSITIONS: dict[RecoveryCaseState, frozenset[RecoveryCaseState]] = {
    _S.DETECTED: frozenset({_S.DIAGNOSING, _S.ABANDONED}),
    _S.DIAGNOSING: frozenset({_S.DIAGNOSED, _S.ABANDONED, _S.FAILED}),
    _S.DIAGNOSED: frozenset({_S.DECISION_PENDING, _S.ABANDONED}),
    _S.DECISION_PENDING: frozenset({_S.ACTION_SCHEDULED, _S.ABANDONED, _S.FAILED}),
    _S.ACTION_SCHEDULED: frozenset({_S.ACTION_EXECUTED, _S.ABANDONED, _S.FAILED}),
    _S.ACTION_EXECUTED: frozenset({_S.OBSERVING, _S.FAILED}),
    _S.OBSERVING: frozenset({_S.RECOVERED, _S.FAILED}),
    _S.RECOVERED: frozenset(),
    _S.ABANDONED: frozenset(),
    _S.FAILED: frozenset(),
}


def is_terminal(state: RecoveryCaseState) -> bool:
    return state in TERMINAL_STATES


def can_transition(from_state: RecoveryCaseState, to_state: RecoveryCaseState) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def assert_transition_allowed(from_state: RecoveryCaseState, to_state: RecoveryCaseState) -> None:
    """Raise :class:`IllegalStateTransitionError` unless ``from_state`` may
    move to ``to_state``.
    """
    if not can_transition(from_state, to_state):
        raise IllegalStateTransitionError(from_state.value, to_state.value)
