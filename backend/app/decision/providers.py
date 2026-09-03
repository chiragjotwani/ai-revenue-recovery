"""Simulated payment/recovery provider (Phase 6 completion).

This module provides the ``SimulatedPaymentProvider`` -- a deterministic,
in-process stand-in for a real payment gateway / customer-messaging system.
It makes NO network call and integrates with NO real Razorpay (or any
other) payment provider. Every outcome is a pure function of
``(failure_reason, attempt_no)``, so the same case replayed twice always
produces the same sequence of outcomes -- required for repeatable tests and
a repeatable buildathon demo alike.

Scope boundary (mirrors every prior phase's own docstring): this module has
no database access, no session, and is never imported by ``app.ai`` or
``app.decision.policy`` -- only ``app.decision.executors`` (Phase 6's
executor layer) calls it. It cannot be reached from the diagnosis or
decision path, so it cannot become a channel through which an LLM (or
anything upstream of the policy engine) could trigger a "payment" --
ADR-003's boundary is unaffected by this module's existence.

The interface (:class:`PaymentProvider`) is deliberately narrow so a real
provider could implement it later without any change to
``app.decision.executors`` or ``app.decision.actions`` -- swapping the
module-level ``simulated_payment_provider`` singleton for a real
implementation is the only change a future "real Razorpay integration"
phase would need to make here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol


class SimulationOutcome(str, enum.Enum):
    """What one simulated attempt produced."""

    SUCCESS = "success"
    TEMPORARY_FAILURE = "temporary_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True)
class ProviderAttemptResult:
    """The result of one simulated attempt. Never claims a real external
    effect occurred -- ``detail`` and ``simulated_reference`` are always
    clearly synthetic (prefixed ``sim:``), never formatted to resemble a
    real payment-gateway reference.
    """

    outcome: SimulationOutcome
    detail: str
    simulated_reference: str


class PaymentProvider(Protocol):
    """The interface ``app.decision.executors`` depends on. A future real
    provider (a Phase 15+ concern, explicitly out of scope here) implements
    this same shape.
    """

    def attempt(
        self, *, channel: str, failure_reason: str | None, attempt_no: int, correlation_id: str
    ) -> ProviderAttemptResult: ...


# Deterministic outcome sequences, keyed by the original payment's
# ``failure_reason``. Index 0 is attempt 1; a sequence shorter than the
# number of attempts made repeats its last entry (bounded separately by
# app.decision.executors' retry cap, never by this table running out).
#
# Chosen to cover every disposition Phase 5's policy engine can approve a
# real-side-effect strategy for (RETRIABLE_TRANSIENT -> retry;
# CUSTOMER_ACTION_REQUIRED -> request_payment_method_update /
# contact_customer -- see app/ai/schema.py's outcome->disposition map) with
# a spread of outcomes: an immediate success (the canonical ₹4,999
# insufficient-funds scenario), a success that only arrives on a second
# attempt, and a permanent failure -- so the bounded-retry and
# failure-handling paths are exercised by the same deterministic mechanism
# used for the happy path, not a separate ad hoc stub. ``card_not_supported``
# is deliberately absent (falls through to ``_DEFAULT_PROFILE``) so the
# retry-cap-exhaustion path has a real, policy-reachable exercise via
# ``request_payment_method_update`` -- every RETRY-strategy reason already
# has a defined profile above, so the cap-exhaustion scenario is modelled
# on this strategy instead, not invented as a special case.
_PROFILES: dict[str, list[SimulationOutcome]] = {
    "insufficient_funds": [SimulationOutcome.SUCCESS],
    "do_not_honor": [SimulationOutcome.TEMPORARY_FAILURE, SimulationOutcome.SUCCESS],
    "processing_error": [SimulationOutcome.PERMANENT_FAILURE],
    "card_expired": [SimulationOutcome.SUCCESS],
    "authentication_required": [SimulationOutcome.SUCCESS],
}

# Anything not in _PROFILES (including failure_reason=None and
# "card_not_supported") never succeeds within the bounded attempt window --
# a deterministic "always fails" profile, exercised by the
# retry-cap-exhausted test scenario.
_DEFAULT_PROFILE: list[SimulationOutcome] = [SimulationOutcome.TEMPORARY_FAILURE]


def _resolve_outcome(failure_reason: str | None, attempt_no: int) -> SimulationOutcome:
    profile = _PROFILES.get(failure_reason or "", _DEFAULT_PROFILE)
    index = min(attempt_no, len(profile)) - 1
    return profile[index]


class SimulatedPaymentProvider:
    """Deterministic simulated payment/recovery execution environment.

    Explicitly simulated: no network call is made, and no field on
    :class:`ProviderAttemptResult` is ever formatted to resemble a real
    payment-gateway or messaging-provider reference. ``channel``
    distinguishes which executor called this (``retry`` /
    ``payment_link`` / ``notification``) for audit purposes only -- it does
    not change the deterministic outcome sequence, which is a function of
    ``failure_reason`` and ``attempt_no`` alone.
    """

    def attempt(
        self, *, channel: str, failure_reason: str | None, attempt_no: int, correlation_id: str
    ) -> ProviderAttemptResult:
        outcome = _resolve_outcome(failure_reason, attempt_no)
        reference = f"sim:{channel}:{correlation_id}:{attempt_no}"
        detail = (
            f"simulated {outcome.value} on {channel} attempt {attempt_no} "
            f"for failure_reason={failure_reason!r} (no external system contacted)"
        )
        return ProviderAttemptResult(outcome=outcome, detail=detail, simulated_reference=reference)


#: The one provider instance the executors use -- a module-level singleton
#: (same convention as app.events.publisher.outbox_publisher), since it
#: holds no state and no connection.
simulated_payment_provider = SimulatedPaymentProvider()

__all__ = [
    "PaymentProvider",
    "ProviderAttemptResult",
    "SimulatedPaymentProvider",
    "SimulationOutcome",
    "simulated_payment_provider",
]
