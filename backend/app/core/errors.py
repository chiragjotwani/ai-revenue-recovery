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
