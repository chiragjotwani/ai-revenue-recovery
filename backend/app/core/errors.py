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


class IllegalStateTransitionError(DomainError):
    """Raised when a recovery case is asked to move between two states that
    the state machine does not permit (Section 16: illegal transitions must
    raise, never be silently applied).
    """

    def __init__(self, from_state: object, to_state: object) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Illegal recovery case transition: {from_state} -> {to_state}.")
