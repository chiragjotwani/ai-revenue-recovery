"""Executor abstractions over the simulated provider (Phase 6 completion).

Three thin, strategy-specific wrappers over
``app.decision.providers.simulated_payment_provider`` -- one per approved
``RecoveryStrategy`` that carries a real (simulated) external side effect.
``app.decision.actions.execute_action`` is the only caller; the LLM and the
policy engine never see, import, or can reach this module (ADR-003 is
unaffected -- see ``app.decision.providers``'s module docstring for the
full argument).

Each executor's ``attempt`` has the identical shape (same
``ProviderAttemptResult``) so ``execute_action`` can dispatch on
``action_type`` without a strategy-specific branch beyond the dispatch
table itself. The three are kept separate (rather than one function with a
``channel`` parameter) because Step 3 of the completion brief asks for
distinct abstractions a later, real integration could replace one at a
time -- e.g. a real payment gateway could replace ``RetryExecutor`` without
touching ``NotificationExecutor``.
"""

from __future__ import annotations

from app.decision.providers import (
    PaymentProvider,
    ProviderAttemptResult,
    simulated_payment_provider,
)


class RetryExecutor:
    """Simulated retry of a failed payment (``RecoveryStrategy.RETRY``)."""

    channel = "retry"

    def __init__(self, provider: PaymentProvider = simulated_payment_provider) -> None:
        self._provider = provider

    def attempt(
        self, *, failure_reason: str | None, attempt_no: int, correlation_id: str
    ) -> ProviderAttemptResult:
        return self._provider.attempt(
            channel=self.channel,
            failure_reason=failure_reason,
            attempt_no=attempt_no,
            correlation_id=correlation_id,
        )


class PaymentLinkExecutor:
    """Simulated payment-link flow
    (``RecoveryStrategy.REQUEST_PAYMENT_METHOD_UPDATE``).

    Does not contact a real payment gateway. ``attempt`` both generates the
    deterministic simulated link reference and simulates the customer's
    response to it in one step (no separate "customer clicked the link"
    callback exists in this repository -- a real implementation would
    split these; the simulation collapses them for the same reason Step 5
    of the completion brief asks not to over-engineer this).
    """

    channel = "payment_link"

    def __init__(self, provider: PaymentProvider = simulated_payment_provider) -> None:
        self._provider = provider

    def attempt(
        self, *, failure_reason: str | None, attempt_no: int, correlation_id: str
    ) -> ProviderAttemptResult:
        return self._provider.attempt(
            channel=self.channel,
            failure_reason=failure_reason,
            attempt_no=attempt_no,
            correlation_id=correlation_id,
        )


class NotificationExecutor:
    """Simulated customer notification (``RecoveryStrategy.CONTACT_CUSTOMER``).

    Never sends a real email/SMS/WhatsApp message. ``attempt`` simulates
    both the notification send and the customer's resulting action (e.g.
    updating a payment method after being contacted) in one step, for the
    same reason ``PaymentLinkExecutor`` does -- see its docstring. The
    outcome must never be read as "a notification was sent and payment
    succeeded merely because of that"; it is the same deterministic
    provider outcome every other executor produces, recorded honestly as
    simulated.
    """

    channel = "notification"

    def __init__(self, provider: PaymentProvider = simulated_payment_provider) -> None:
        self._provider = provider

    def attempt(
        self, *, failure_reason: str | None, attempt_no: int, correlation_id: str
    ) -> ProviderAttemptResult:
        return self._provider.attempt(
            channel=self.channel,
            failure_reason=failure_reason,
            attempt_no=attempt_no,
            correlation_id=correlation_id,
        )


#: action_type (== RecoveryStrategy value) -> executor instance. Only the
#: three strategies that carry a real (simulated) side effect are present
#: here -- ``no_action`` / ``manual_review`` remain
#: ActionExecutionOutcome.NO_SIDE_EFFECT_REQUIRED, handled entirely in
#: app.decision.actions without reaching this module.
EXECUTORS_BY_ACTION_TYPE: dict[str, RetryExecutor | PaymentLinkExecutor | NotificationExecutor] = {
    "retry": RetryExecutor(),
    "request_payment_method_update": PaymentLinkExecutor(),
    "contact_customer": NotificationExecutor(),
}

__all__ = [
    "EXECUTORS_BY_ACTION_TYPE",
    "NotificationExecutor",
    "PaymentLinkExecutor",
    "RetryExecutor",
]
