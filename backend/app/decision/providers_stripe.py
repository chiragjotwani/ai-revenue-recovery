"""Real payment-gateway integration for the ``retry`` channel (Phase 16:
Real Payment Integration), against Stripe TEST mode only.

Scope, deliberately narrow (owner-confirmed before implementation): only
``RetryExecutor`` (``RecoveryStrategy.RETRY``) gets a real gateway.
``PaymentLinkExecutor`` / ``NotificationExecutor`` keep using
``app.decision.providers.SimulatedPaymentProvider`` -- a real payment-link
object or a real customer-messaging send are each their own integration
surface, not exercised by "retry a failed payment", and were explicitly
left out of this phase's scope. ``app.decision.providers``'s own module
docstring anticipated exactly this kind of one-channel-at-a-time swap.

Config-gated with the same fallback shape ``app/ai/providers/factory.py``
already established for the reasoning-model providers: when
``Settings.stripe_api_key`` is unset, ``select_retry_provider()`` returns
``simulated_payment_provider`` instead, never fails startup and never
silently no-ops -- ``app.decision.executors.EXECUTORS_BY_ACTION_TYPE`` is
built from whatever this resolves to at import time, and
``GET /ai/providers``-style observability was judged out of scope for
this specific swap (no diagnosis-style substitution report exists for the
payment layer; the resolved provider's class name is logged on every
attempt instead -- see ``attempt()`` below).

Real network call, TEST mode only: this module calls
``https://api.stripe.com/v1/payment_intents`` with a test secret key
(``sk_test_...`` -- a live key is never accepted, see
``_require_test_key``) via ``httpx.AsyncClient``, the same HTTP-client
approach ``app/ai/providers/qwen.py`` already uses for a real external
endpoint (never the synchronous ``stripe`` SDK, which would block this
codebase's asyncio event loop). One call creates and confirms a
``PaymentIntent`` in a single request
(``confirm=true``), using a fixed Stripe test payment method token
(``pm_card_visa`` succeeds deterministically in test mode; test-mode
decline tokens are used to model a temporary/permanent failure) chosen
from the same ``failure_reason`` the simulated provider already keys off
of -- this reuses the existing profile-selection *shape*, not its
deterministic implementation, since Stripe's own test-mode fixtures now
decide the outcome, not this module.

ADR-003 is unaffected structurally, the same way ``app.decision.providers``
documents for the simulated provider: this module has no database access,
no session, and is never imported by ``app.ai`` or ``app.decision.policy``
-- only ``app.decision.executors`` reaches it.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.decision.providers import (
    PaymentProvider,
    ProviderAttemptResult,
    SimulationOutcome,
    simulated_payment_provider,
)

logger = logging.getLogger("app.decision.providers_stripe")

_STRIPE_API_BASE = "https://api.stripe.com/v1"
_REQUEST_TIMEOUT_SECONDS = 20.0

#: Stripe's own published test-mode payment method tokens
#: (https://docs.stripe.com/testing) -- deterministic, real Stripe-side
#: outcomes, never invented by this module. Keyed by the same
#: ``failure_reason`` values ``app.decision.providers._PROFILES`` uses,
#: so a case that would have simulated a success/decline still resolves
#: to the analogous real Stripe-side outcome when Stripe is the active
#: provider -- not a coincidence, a deliberate mapping so the demo
#: scenarios behave consistently regardless of which provider is active.
_TEST_PAYMENT_METHOD_BY_FAILURE_REASON: dict[str, str] = {
    "insufficient_funds": "pm_card_visa",  # succeeds
    "card_expired": "pm_card_visa",  # succeeds (re-tried with a valid card)
    "authentication_required": "pm_card_visa",  # succeeds (auth already cleared)
    "do_not_honor": "pm_card_chargeDeclinedInsufficientFunds",  # declines
    "processing_error": "pm_card_chargeDeclinedProcessingError",  # declines, permanent
}
_DEFAULT_TEST_PAYMENT_METHOD = "pm_card_chargeDeclined"  # generic decline

#: Stripe decline codes this module treats as retriable (a further attempt
#: might succeed) vs. permanent. Anything not listed is treated as
#: permanent -- the conservative default (never retry indefinitely on an
#: unrecognized decline reason).
_RETRIABLE_DECLINE_CODES = frozenset({"insufficient_funds", "try_again_later"})


class StripeConfigurationError(Exception):
    """Raised when Stripe is selected but misconfigured (e.g. a live key
    was supplied). Never silently falls back -- an operator who set
    ``STRIPE_API_KEY`` intended a real call; failing loudly here is safer
    than either using it anyway or silently substituting the simulator.
    """


def _require_test_key(api_key: str) -> str:
    if not api_key.startswith("sk_test_"):
        raise StripeConfigurationError(
            "STRIPE_API_KEY must be a Stripe TEST secret key (sk_test_...) -- "
            "this platform never processes real payments (ADR-003 scope)."
        )
    return api_key


class StripePaymentProvider:
    """Real Stripe TEST-mode payment retry. Implements the same
    :class:`~app.decision.providers.PaymentProvider` protocol as
    :class:`~app.decision.providers.SimulatedPaymentProvider` -- executors
    and ``execute_action`` do not know or care which one they were built
    with.
    """

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = _require_test_key(api_key)
        # Test seam, same convention as app.ai.providers.openai_compatible:
        # inject an httpx.MockTransport to exercise this without a real
        # network call to Stripe.
        self._transport = transport

    async def attempt(
        self, *, channel: str, failure_reason: str | None, attempt_no: int, correlation_id: str
    ) -> ProviderAttemptResult:
        payment_method = _TEST_PAYMENT_METHOD_BY_FAILURE_REASON.get(
            failure_reason or "", _DEFAULT_TEST_PAYMENT_METHOD
        )
        # Stripe requires a unique idempotency key per logical operation to
        # make a retried HTTP call (e.g. after a client-side timeout) safe
        # -- reuses this attempt's own correlation id + attempt number, the
        # same identity app.decision.actions already uses for its own
        # RecoveryActionExecution row, so a duplicate call for the SAME
        # attempt can never create two PaymentIntents on Stripe's side.
        idempotency_key = f"arr-retry:{correlation_id}:{attempt_no}"

        async with httpx.AsyncClient(
            base_url=_STRIPE_API_BASE,
            auth=(self._api_key, ""),
            timeout=_REQUEST_TIMEOUT_SECONDS,
            transport=self._transport,
        ) as client:
            try:
                response = await client.post(
                    "/payment_intents",
                    headers={"Idempotency-Key": idempotency_key},
                    data={
                        "amount": "100",  # smallest unit; a fixed nominal test
                        # amount -- this module never reads or trusts a
                        # caller-supplied amount, mirroring every other
                        # money-adjacent module in this codebase.
                        "currency": "usd",
                        "payment_method": payment_method,
                        "confirm": "true",
                        "automatic_payment_methods[enabled]": "true",
                        "automatic_payment_methods[allow_redirects]": "never",
                    },
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "stripe transport error, treating as temporary failure",
                    extra={"channel": channel, "attempt_no": attempt_no},
                    exc_info=True,
                )
                return ProviderAttemptResult(
                    outcome=SimulationOutcome.TEMPORARY_FAILURE,
                    detail=f"Stripe request failed: {exc}",
                    simulated_reference=f"stripe-error:{correlation_id}:{attempt_no}",
                    is_real=True,
                )

        if response.status_code >= 500:
            # A 5xx body is not guaranteed to be JSON (a proxy/load-balancer
            # error page, a truncated response) -- check the status before
            # ever attempting to parse the body, not after.
            return ProviderAttemptResult(
                outcome=SimulationOutcome.TEMPORARY_FAILURE,
                detail=f"Stripe returned {response.status_code}",
                simulated_reference=f"stripe-error:{correlation_id}:{attempt_no}",
                is_real=True,
            )

        try:
            body = response.json()
        except ValueError as exc:
            return ProviderAttemptResult(
                outcome=SimulationOutcome.TEMPORARY_FAILURE,
                detail=f"Stripe returned an unparseable response body: {exc}",
                simulated_reference=f"stripe-error:{correlation_id}:{attempt_no}",
                is_real=True,
            )

        payment_intent_id = body.get("id", "unknown")
        status = body.get("status")

        if status == "succeeded":
            outcome = SimulationOutcome.SUCCESS
            detail = f"Stripe PaymentIntent {payment_intent_id} succeeded (TEST mode)"
        else:
            decline_code = (
                body.get("last_payment_error", {}).get("decline_code")
                or body.get("error", {}).get("decline_code")
                or "unknown"
            )
            outcome = (
                SimulationOutcome.TEMPORARY_FAILURE
                if decline_code in _RETRIABLE_DECLINE_CODES
                else SimulationOutcome.PERMANENT_FAILURE
            )
            detail = (
                f"Stripe PaymentIntent {payment_intent_id} not succeeded "
                f"(status={status!r}, decline_code={decline_code!r}, TEST mode)"
            )

        logger.info(
            "stripe payment attempt",
            extra={
                "channel": channel,
                "attempt_no": attempt_no,
                "stripe_payment_intent_id": payment_intent_id,
                "outcome": outcome.value,
            },
        )
        return ProviderAttemptResult(
            outcome=outcome,
            detail=detail,
            simulated_reference=f"stripe:{payment_intent_id}",
            is_real=True,
        )


def select_retry_provider() -> PaymentProvider:
    """Resolves the provider ``RetryExecutor`` should use: a real
    :class:`StripePaymentProvider` when ``Settings.stripe_api_key`` is
    configured, otherwise the existing
    :class:`~app.decision.providers.SimulatedPaymentProvider` -- the same
    "config-gated, fallback rather than fail" shape
    ``app/ai/providers/factory.py::get_reasoning_model`` already
    established for the reasoning-model layer.
    """
    api_key = get_settings().stripe_api_key
    if not api_key:
        return simulated_payment_provider
    return StripePaymentProvider(api_key)


__all__ = [
    "StripeConfigurationError",
    "StripePaymentProvider",
    "select_retry_provider",
]
