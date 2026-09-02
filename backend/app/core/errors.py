class DomainError(Exception):
    """Base class for known, expected application errors.

    Per Section 13 of the engineering prompt, exceptions must never be
    silently swallowed. Expected failure modes are raised as a DomainError
    subclass so callers (e.g. API routes) can translate them into a
    meaningful response instead of a generic 500.
    """


class PaymentReferenceConflictError(DomainError):
    """Raised when an external payment reference is reused with a
    different idempotency key than the one it was first ingested under.

    This indicates either a source-system bug or a spoofed/replayed event
    with tampered identifiers, and must never be silently resolved by
    overwriting the existing payment.
    """

    def __init__(self, external_reference: str) -> None:
        self.external_reference = external_reference
        super().__init__(
            f"Payment external_reference {external_reference!r} already exists "
            "under a different idempotency key."
        )


class PaymentNotFoundError(DomainError):
    """Raised when an operation references a payment id that does not exist."""

    def __init__(self, payment_id: object) -> None:
        self.payment_id = payment_id
        super().__init__(f"Payment {payment_id!r} does not exist.")


class PaymentNotRecoverableError(DomainError):
    """Raised when a recovery case is requested for a payment that is not in
    a state that can be recovered (only ``failed`` payments can).
    """

    def __init__(self, payment_id: object, status: str) -> None:
        self.payment_id = payment_id
        self.status = status
        super().__init__(
            f"Payment {payment_id!r} has status {status!r}; a recovery case can "
            "only be opened for a failed payment."
        )


class RecoveryCaseNotFoundError(DomainError):
    """Raised when an operation references a recovery case id that does not exist."""

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(f"Recovery case {case_id!r} does not exist.")


class CaseNotDiagnosableError(DomainError):
    """Raised when a diagnosis is requested for a case that is not in a
    state where diagnosis is valid (only ``detected`` or, on a retry after
    a failed attempt, ``diagnosing``).
    """

    def __init__(self, state: object) -> None:
        self.state = state
        super().__init__(
            f"Recovery case is in state {state!r}; a diagnosis can only be run "
            "from 'detected' or 'diagnosing'."
        )


class CaseNotDecidableError(DomainError):
    """Raised when a decision is requested for a case that is not in a
    state where deciding is valid (only ``diagnosed``, Phase 5).
    """

    def __init__(self, state: object) -> None:
        self.state = state
        super().__init__(
            f"Recovery case is in state {state!r}; a decision can only be made from 'diagnosed'."
        )


class NoDiagnosisToDecideError(DomainError):
    """Raised when a case is in ``diagnosed`` but no persisted ``Diagnosis``
    can be found for it. Should not occur under the normal state machine
    (a case can only reach ``diagnosed`` via a persisted diagnosis --
    ``app/recovery/preconditions.py``); this is a defensive check, not an
    expected path.
    """

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(f"Recovery case {case_id!r} has no diagnosis to decide against.")


class IllegalStateTransitionError(DomainError):
    """Raised when a recovery case is asked to move between two states that
    the state machine does not permit (Section 16: illegal transitions must
    raise, never be silently applied).
    """

    def __init__(self, from_state: object, to_state: object) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Illegal recovery case transition: {from_state} -> {to_state}.")


class CaseNotSchedulableError(DomainError):
    """Raised when scheduling an action is requested for a case that is not
    in a state where scheduling is valid (only ``decision_pending``, and
    only when its decision exists -- Phase 6).
    """

    def __init__(self, state: object) -> None:
        self.state = state
        super().__init__(
            f"Recovery case is in state {state!r}; an action can only be scheduled "
            "from 'decision_pending'."
        )


class NoApprovedDecisionError(DomainError):
    """Raised when a case is in ``decision_pending`` but no persisted
    ``DecisionResult`` can be found for its current diagnosis. Should not
    occur under the normal state machine (a case can only reach
    ``decision_pending`` via a persisted decision --
    ``app/recovery/preconditions.py``); this is a defensive check, not an
    expected path.
    """

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(f"Recovery case {case_id!r} has no decision to schedule an action for.")


class DecisionNotApprovedError(DomainError):
    """Raised when scheduling an action is attempted against a decision
    that is not ``approved`` (Phase 6). An escalated or rejected decision
    must never reach action scheduling -- that would bypass the policy
    engine's own verdict (ADR-003).
    """

    def __init__(self, decision_status: object) -> None:
        self.decision_status = decision_status
        super().__init__(
            f"Decision status is {decision_status!r}, not 'approved'; an action can only "
            "be scheduled for an approved decision."
        )


class CaseNotExecutableError(DomainError):
    """Raised when executing an action is requested for a case that is not
    in a state where execution is valid (only ``action_scheduled``, Phase
    6).
    """

    def __init__(self, state: object) -> None:
        self.state = state
        super().__init__(
            f"Recovery case is in state {state!r}; an action can only be executed "
            "from 'action_scheduled'."
        )


class NoScheduledActionError(DomainError):
    """Raised when a case is in ``action_scheduled`` but no persisted
    ``RecoveryAction`` can be found for its current decision. Defensive --
    should not occur under the normal state machine.
    """

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(f"Recovery case {case_id!r} has no scheduled action to execute.")


class CaseNotObservableError(DomainError):
    """Raised when observing an outcome is requested for a case that is
    not in a state where observation is valid (only ``action_executed``
    or ``observing``, Phase 7).
    """

    def __init__(self, state: object) -> None:
        self.state = state
        super().__init__(
            f"Recovery case is in state {state!r}; an outcome can only be observed "
            "from 'action_executed' or 'observing'."
        )


class NoExecutedActionError(DomainError):
    """Raised when a case is in ``action_executed``/``observing`` but no
    persisted ``RecoveryAction`` can be found for its current decision.
    Defensive -- should not occur under the normal state machine.
    """

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(f"Recovery case {case_id!r} has no executed action to observe.")


class CaseNotMeasurableError(DomainError):
    """Raised when measuring a case is requested before it has any Phase 7
    outcome observation to measure -- a measurement must be traceable to
    an observed outcome (Phase 8).
    """

    def __init__(self, case_id: object) -> None:
        self.case_id = case_id
        super().__init__(
            f"Recovery case {case_id!r} has no observed outcome yet; a measurement "
            "can only be taken once an outcome has been observed."
        )


class TransitionPreconditionError(DomainError):
    """Raised when a transition is shape-legal but the artifact it depends
    on does not exist yet (e.g. moving to ``diagnosed`` with no persisted
    ``Diagnosis``). Only raised when a caller opts into precondition
    enforcement (Phase 4.1 WS-C: the default remains the Phase 3
    shape-only behaviour). See ``app/recovery/preconditions.py``.
    """

    def __init__(self, from_state: object, to_state: object, reason: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(f"Transition {from_state} -> {to_state} is not yet legitimate: {reason}.")
